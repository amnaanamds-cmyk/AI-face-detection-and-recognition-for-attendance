package com.faceattend.app

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.provider.Telephony
import kotlin.concurrent.thread

/**
 * A parent texted the school phone (e.g. "STATUS" or "LEAVE fever"): forward it to the FaceAttend
 * server, which queues the answer; then wake the SMS sender so the answer goes out within seconds.
 */
class SmsReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Telephony.Sms.Intents.SMS_RECEIVED_ACTION) return
        val prefs = Prefs(context)
        if (!prefs.paired || !prefs.gatewayEnabled) return
        Tls.load(prefs)
        val parts = Telephony.Sms.Intents.getMessagesFromIntent(intent) ?: return
        if (parts.isEmpty()) return
        val sender = parts[0].originatingAddress ?: return
        val body = parts.joinToString("") { it.messageBody ?: "" }
        val pending = goAsync()
        thread {
            try {
                Api(prefs.gatewayServer, prefs.gatewayToken).incoming(sender, body)
                SmsGatewayService.pollNow()
            } catch (e: Exception) {
                prefs.lastStatus = "could not forward a parent's SMS: ${e.message}"
            } finally {
                pending.finish()
            }
        }
    }
}
