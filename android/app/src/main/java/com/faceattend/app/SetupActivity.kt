package com.faceattend.app

import android.app.Activity
import android.graphics.Color
import android.graphics.Typeface
import android.net.Uri
import android.os.Bundle
import android.text.InputType
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.google.android.material.button.MaterialButton
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import javax.net.ssl.SSLException
import kotlin.concurrent.thread

/** First start / "Change school": connect the app to the school's FaceAttend server, once. */
class SetupActivity : AppCompatActivity() {
    private lateinit var prefs: Prefs
    private lateinit var address: EditText
    private lateinit var status: TextView
    private lateinit var buttons: List<Button>

    private val scan = registerForActivityResult(ScanContract()) { result ->
        result.contents?.let { useCode(it) }
    }

    /** A QR code saved as a picture (screenshot, or received on WhatsApp). */
    private val pickImage = registerForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri == null) return@registerForActivityResult
        say("Reading the QR code …")
        thread {
            val text = try { QrImage.decode(contentResolver, uri) } catch (e: Exception) { null }
            runOnUiThread {
                if (text == null) say("No QR code found in that picture. Try a clearer screenshot of the QR code.", error = true)
                else useCode(text)
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        Tls.load(prefs)
        val pad = dp(20)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(pad, pad, pad, pad) }
        root.addView(TextView(this).apply { text = getString(R.string.setup_title); textSize = 24f; setTypeface(typeface, Typeface.BOLD) })
        root.addView(TextView(this).apply { text = getString(R.string.setup_intro); setPadding(0, pad / 2, 0, pad); textSize = 15f })

        val all = mutableListOf<Button>()
        if (BuildConfig.CLOUD_URL.isNotEmpty()) {
            all += big("Use FaceAttend online", primary = true) { check(BuildConfig.CLOUD_URL, null) }.also { root.addView(it) }
        }
        all += big(getString(R.string.scan_qr), primary = BuildConfig.CLOUD_URL.isEmpty()) {
            scan.launch(ScanOptions().setPrompt("Scan the QR code on the school computer (Connect phones)")
                .setBeepEnabled(false).setOrientationLocked(false).setDesiredBarcodeFormats(ScanOptions.QR_CODE))
        }.also { root.addView(it) }
        all += big("Choose QR picture from gallery") { pickImage.launch("image/*") }.also { root.addView(it) }

        root.addView(TextView(this).apply { text = "or type the address"; setPadding(0, pad, 0, 0); setTextColor(Color.GRAY) })
        address = EditText(this).apply {
            hint = getString(R.string.server_hint)
            inputType = InputType.TYPE_TEXT_VARIATION_URI
            setText(prefs.serverUrl)
            isSingleLine = true
        }
        root.addView(address, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        all += big(getString(R.string.connect)) { check(Prefs.normalizeUrl(address.text.toString()), null) }.also { root.addView(it) }
        buttons = all

        status = TextView(this).apply { setPadding(0, pad, 0, pad); textSize = 15f }
        root.addView(status)
        root.addView(TextView(this).apply {
            text = getString(R.string.setup_help)
            setTextColor(Color.DKGRAY); textSize = 13f
            setBackgroundColor(Color.parseColor("#F1F4F8")); setPadding(pad / 2, pad / 2, pad / 2, pad / 2)
        })
        setContentView(ScrollView(this).apply { addView(root) })
    }

    private fun big(label: String, primary: Boolean = false, click: () -> Unit) = MaterialButton(this).apply {
        text = label
        isAllCaps = false
        textSize = 16f
        if (!primary) {
            setBackgroundColor(Color.parseColor("#E8EEF5"))
            setTextColor(ContextCompat.getColor(this@SetupActivity, R.color.brand))
        }
        setOnClickListener { click() }
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(56)).apply { topMargin = dp(6) }
    }

    private fun say(text: String, error: Boolean = false) {
        status.text = text
        status.setTextColor(if (error) Color.parseColor("#B02A37") else Color.DKGRAY)
    }

    private fun useCode(text: String) {
        val parsed = Prefs.parseScan(text)
        if (parsed == null) {
            say("This QR code is not a FaceAttend server. Use the QR code on the school computer: FaceAttend icon " +
                "next to the clock → Connect phones (the download QR code only installs the app).", error = true)
            return
        }
        if (parsed.token != null) {   // the SMS pairing code also tells us the server
            prefs.gatewayServer = parsed.server
            prefs.gatewayToken = parsed.token
        }
        address.setText(parsed.server)
        check(parsed.server, parsed.pin)
    }

    /** Make sure the address answers like a FaceAttend server before saving it, and explain what is wrong if not. */
    private fun check(url: String, pin: String?) {
        if (url.isEmpty()) {
            say("Scan the QR code, or type the address shown on the school computer.", error = true)
            return
        }
        buttons.forEach { it.isEnabled = false }
        say("Connecting to $url …")
        thread {
            val host = Uri.parse(url).host ?: ""
            val local = isLocal(host)
            val error = try {
                if (pin != null) Tls.fetchAndPin(url, pin, prefs)
                val conn = Tls.open("$url/login")
                conn.connectTimeout = 10000
                conn.readTimeout = 10000
                val code = conn.responseCode
                conn.disconnect()
                when {
                    code == 200 -> null
                    host.endsWith("trycloudflare.com") -> "This online link is no longer active. On the school computer open " +
                        "Connect phones, press Share online and scan the new QR code."
                    else -> "The server answered with error $code. Is this the FaceAttend address?"
                }
            } catch (e: SecurityException) {
                "Not connected: ${e.message}."
            } catch (e: SSLException) {
                if (local) "This is the school computer's own secure address. Scan its QR code (Connect phones page) " +
                    "instead of typing it: the QR code lets the app trust it safely."
                else "The secure connection to $host failed. Check the address."
            } catch (e: UnknownHostException) {
                "Cannot find $host. Check the address and that this phone has internet."
            } catch (e: Exception) {
                if (local && (e is ConnectException || e is SocketTimeoutException))
                    "Cannot reach the school computer ($host).\n\n1. Is FaceAttend running on it? (icon next to the clock)\n" +
                        "2. Is this phone on the same Wi-Fi as the computer?\n3. Windows Firewall must allow FaceAttend " +
                        "(choose Allow when Windows asks, or reinstall and tick \"Allow phones on the Wi-Fi\").\n\n" +
                        "Easier: on the computer use Share online - it works from any network."
                else "Cannot reach $host. Is the FaceAttend server running, and is this phone online?"
            }
            runOnUiThread {
                buttons.forEach { it.isEnabled = true }
                if (error == null) {
                    prefs.serverUrl = url
                    setResult(Activity.RESULT_OK)
                    finish()
                } else {
                    say(error, error = true)
                }
            }
        }
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    companion object {
        fun isLocal(host: String): Boolean = host.startsWith("192.168.") || host.startsWith("10.") ||
            Regex("^172\\.(1[6-9]|2\\d|3[01])\\.").containsMatchIn(host) || host.endsWith(".local") || host == "localhost"
    }
}
