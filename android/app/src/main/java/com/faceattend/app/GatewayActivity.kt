package com.faceattend.app

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import kotlin.concurrent.thread

/**
 * Turns this phone into the free SMS sender for parent absence messages: pair it with the
 * pairing code from Admin > Parent messages, then it sends the queued messages from its SIM.
 */
class GatewayActivity : AppCompatActivity() {
    private lateinit var prefs: Prefs
    private lateinit var info: TextView
    private lateinit var toggle: Button

    private val scan = registerForActivityResult(ScanContract()) { result ->
        val parsed = result.contents?.let { Prefs.parseScan(it) }
        if (parsed?.token == null) {
            info.text = "That is not a pairing code. Open Admin > Parent messages on the computer and scan the code there."
            return@registerForActivityResult
        }
        info.text = "Checking pairing code…"
        thread {
            val message = try {
                val org = Api(parsed.server, parsed.token).ping()
                prefs.gatewayServer = parsed.server
                prefs.gatewayToken = parsed.token
                prefs.gatewayOrg = org
                null
            } catch (e: Exception) {
                "Pairing failed: ${e.message}"
            }
            runOnUiThread { if (message != null) info.text = message else refresh() }
        }
    }

    private val permissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { granted ->
        if (granted[Manifest.permission.SEND_SMS] == true) enable() else info.text = "SMS permission is needed to send parent messages."
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        val pad = (20 * resources.displayMetrics.density).toInt()
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(pad, pad, pad, pad) }
        root.addView(TextView(this).apply { text = getString(R.string.gateway_title); textSize = 22f })
        root.addView(TextView(this).apply {
            setPadding(0, pad / 2, 0, pad)
            text = "This phone sends the absence messages for parents as normal SMS from its own SIM card " +
                "(your normal SMS charges or bundle apply). Parents can also text STATUS, REPORT or LEAVE to " +
                "this number and get an automatic answer. Keep it charged and connected to the internet."
        })
        info = TextView(this).apply { setPadding(0, 0, 0, pad) }
        root.addView(info)
        root.addView(Button(this).apply {
            text = "Scan pairing code"
            setOnClickListener {
                scan.launch(ScanOptions().setPrompt("Admin > Parent messages > pairing code").setBeepEnabled(false)
                    .setOrientationLocked(false).setDesiredBarcodeFormats(ScanOptions.QR_CODE))
            }
        })
        toggle = Button(this).apply { setOnClickListener { if (prefs.gatewayEnabled) disable() else askAndEnable() } }
        root.addView(toggle)
        setContentView(root)
        refresh()
    }

    override fun onResume() {
        super.onResume()
        refresh()
    }

    private fun refresh() {
        info.text = if (!prefs.paired) "Not paired yet."
        else buildString {
            append("Paired with ${prefs.gatewayOrg.ifEmpty { prefs.gatewayServer }}\n")
            append(if (prefs.gatewayEnabled) "Sending: ON" else "Sending: off")
            append("\nMessages sent from this phone: ${prefs.sentCount}")
            if (prefs.lastStatus.isNotEmpty()) append("\nLast check: ${prefs.lastStatus}")
        }
        toggle.text = if (prefs.gatewayEnabled) "Stop sending" else "Start sending messages"
        toggle.isEnabled = prefs.paired
    }

    private fun askAndEnable() {
        val needed = mutableListOf(Manifest.permission.SEND_SMS, Manifest.permission.RECEIVE_SMS)
        if (Build.VERSION.SDK_INT >= 33) needed += Manifest.permission.POST_NOTIFICATIONS
        val missing = needed.filter { ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED }
        if (missing.isEmpty()) enable() else permissions.launch(missing.toTypedArray())
    }

    private fun enable() {
        prefs.gatewayEnabled = true
        SmsGatewayService.start(this)
        refresh()
    }

    private fun disable() {
        prefs.gatewayEnabled = false
        SmsGatewayService.stop(this)
        refresh()
    }
}
