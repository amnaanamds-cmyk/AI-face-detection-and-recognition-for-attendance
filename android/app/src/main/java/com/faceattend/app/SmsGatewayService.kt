package com.faceattend.app

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.telephony.SmsManager
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger

/**
 * Foreground service: every 20 s asks the server for queued parent messages, sends each as SMS
 * from this phone's SIM and reports the result back (sent / failed with the reason).
 */
class SmsGatewayService : Service() {
    private lateinit var prefs: Prefs
    @Volatile private var running = false
    private var worker: Thread? = null
    private val requestCodes = AtomicInteger(1)

    /** Results of the SMS parts of each message, filled by the "sent" broadcasts. */
    private class Pending(val parts: Int) {
        val results = ConcurrentHashMap<Int, Int>()
    }
    private val pending = ConcurrentHashMap<Long, Pending>()

    private val sentReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val id = intent.getLongExtra("id", -1)
            val part = intent.getIntExtra("part", 0)
            pending[id]?.results?.put(part, resultCode)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        prefs = Prefs(this)
        ContextCompat.registerReceiver(this, sentReceiver, IntentFilter(ACTION_SENT), ContextCompat.RECEIVER_NOT_EXPORTED)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        createChannel()
        val type = if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE else 0
        ServiceCompat.startForeground(this, NOTIFICATION_ID, notification("Waiting for messages…"), type)
        if (!running) {
            running = true
            worker = Thread(::loop, "sms-gateway").also { it.start() }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        running = false
        worker?.interrupt()
        unregisterReceiver(sentReceiver)
        super.onDestroy()
    }

    private fun loop() {
        while (running && prefs.gatewayEnabled) {
            val now = SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date())
            try {
                val api = Api(prefs.gatewayServer, prefs.gatewayToken)
                val messages = api.pull()
                for (m in messages) {
                    val error = send(m)
                    api.report(m.id, error == null, error)
                    if (error == null) prefs.sentCount = prefs.sentCount + 1
                }
                prefs.lastStatus = "$now - ok" + if (messages.isNotEmpty()) ", sent ${messages.size}" else ""
                update("Last check $now · ${prefs.sentCount} sent")
            } catch (e: Api.ApiException) {
                prefs.lastStatus = "$now - ${e.message}"
                update("Server: ${e.message}")
                if (e.code == 401) {   // pairing code replaced on the server: stop until paired again
                    prefs.gatewayEnabled = false
                    break
                }
            } catch (e: Exception) {
                prefs.lastStatus = "$now - no connection"
                update("No connection to the server - retrying")
            }
            // idle until the next check; a parent's SMS (pollNow) wakes the loop early
            var waited = 0L
            try {
                while (running && !wake.getAndSet(false) && waited < POLL_MS) {
                    Thread.sleep(500)
                    waited += 500
                }
            } catch (e: InterruptedException) {
                break
            }
        }
        stopSelf()
    }

    /** Sends one message (split into parts if long). Returns null when sent, otherwise the reason. */
    private fun send(m: Api.Message): String? {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.SEND_SMS) != PackageManager.PERMISSION_GRANTED) {
            return "SMS permission not granted on the phone"
        }
        val sms: SmsManager = if (Build.VERSION.SDK_INT >= 31) getSystemService(SmsManager::class.java)
        else @Suppress("DEPRECATION") SmsManager.getDefault()
        val parts = sms.divideMessage(m.body)
        val state = Pending(parts.size)
        pending[m.id] = state
        val intents = ArrayList<PendingIntent>()
        for (i in parts.indices) {
            val intent = Intent(ACTION_SENT).setPackage(packageName).putExtra("id", m.id).putExtra("part", i)
            intents += PendingIntent.getBroadcast(this, requestCodes.getAndIncrement(), intent,
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_ONE_SHOT)
        }
        try {
            sms.sendMultipartTextMessage(m.to, null, parts, intents, null)
        } catch (e: Exception) {
            pending.remove(m.id)
            return "phone error: ${e.message}"
        }
        // wait up to 60 s for the network to confirm every part
        val deadline = System.currentTimeMillis() + 60_000
        while (state.results.size < state.parts && System.currentTimeMillis() < deadline) Thread.sleep(250)
        pending.remove(m.id)
        if (state.results.size < state.parts) return "no confirmation from the mobile network"
        val bad = state.results.values.firstOrNull { it != android.app.Activity.RESULT_OK }
        return when (bad) {
            null -> null
            SmsManager.RESULT_ERROR_NO_SERVICE -> "no mobile network signal"
            SmsManager.RESULT_ERROR_RADIO_OFF -> "airplane mode / radio off"
            SmsManager.RESULT_ERROR_NULL_PDU, SmsManager.RESULT_ERROR_GENERIC_FAILURE -> "SMS failed (credit or number?)"
            else -> "SMS failed (code $bad)"
        }
    }

    private fun createChannel() {
        if (Build.VERSION.SDK_INT >= 26) {
            val nm = getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel(CHANNEL, getString(R.string.channel_name), NotificationManager.IMPORTANCE_LOW))
        }
    }

    private fun notification(text: String): Notification {
        val open = PendingIntent.getActivity(this, 0, Intent(this, GatewayActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        return NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_notify_chat)
            .setContentTitle("FaceAttend SMS sender")
            .setContentText(text)
            .setContentIntent(open)
            .setOngoing(true)
            .build()
    }

    private fun update(text: String) {
        getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, notification(text))
    }

    companion object {
        private const val CHANNEL = "parent_messages"
        private const val NOTIFICATION_ID = 7
        private const val POLL_MS = 20_000L
        private const val ACTION_SENT = "com.faceattend.app.SMS_SENT"
        /** Set by SmsReceiver: a parent's SMS was answered, send the reply now instead of in 20 s. */
        private val wake = java.util.concurrent.atomic.AtomicBoolean(false)

        fun start(context: Context) {
            ContextCompat.startForegroundService(context, Intent(context, SmsGatewayService::class.java))
        }

        fun pollNow() {
            wake.set(true)
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, SmsGatewayService::class.java))
        }
    }
}
