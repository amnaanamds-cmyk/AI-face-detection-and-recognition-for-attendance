package com.faceattend.app

import android.app.Activity
import android.os.Bundle
import android.text.InputType
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import java.net.HttpURLConnection
import java.net.URL
import javax.net.ssl.SSLException
import kotlin.concurrent.thread

/** First start / "Change server": scan the QR code from the computer or type the address. */
class SetupActivity : AppCompatActivity() {
    private lateinit var prefs: Prefs
    private lateinit var address: EditText
    private lateinit var status: TextView
    private lateinit var connect: Button

    private val scan = registerForActivityResult(ScanContract()) { result ->
        val text = result.contents ?: return@registerForActivityResult
        val parsed = Prefs.parseScan(text)
        if (parsed == null) {
            status.text = "That QR code is not a FaceAttend address."
            return@registerForActivityResult
        }
        if (parsed.token != null) {   // the SMS pairing code also tells us the server
            prefs.gatewayServer = parsed.server
            prefs.gatewayToken = parsed.token
        }
        address.setText(parsed.server)
        check(parsed.server)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        val pad = (20 * resources.displayMetrics.density).toInt()
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
        }
        root.addView(TextView(this).apply { text = getString(R.string.setup_title); textSize = 22f })
        root.addView(TextView(this).apply { text = getString(R.string.setup_intro); setPadding(0, pad / 2, 0, pad) })
        if (BuildConfig.CLOUD_URL.isNotEmpty()) {     // hosted service: one tap, then log in with the school account
            root.addView(Button(this).apply {
                text = "Use FaceAttend online (school account)"
                setOnClickListener { check(BuildConfig.CLOUD_URL) }
            })
            root.addView(TextView(this).apply { text = "or your school's own server:"; setPadding(0, pad, 0, pad / 2) })
        }
        root.addView(Button(this).apply {
            text = getString(R.string.scan_qr)
            setOnClickListener {
                scan.launch(ScanOptions().setPrompt("Scan the QR code on the computer").setBeepEnabled(false)
                    .setOrientationLocked(false).setDesiredBarcodeFormats(ScanOptions.QR_CODE))
            }
        })
        address = EditText(this).apply {
            hint = getString(R.string.server_hint)
            inputType = InputType.TYPE_TEXT_VARIATION_URI
            setText(prefs.serverUrl)
            isSingleLine = true
        }
        root.addView(address, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        connect = Button(this).apply {
            text = getString(R.string.connect)
            setOnClickListener { check(Prefs.normalizeUrl(address.text.toString())) }
        }
        root.addView(connect)
        status = TextView(this).apply { setPadding(0, pad / 2, 0, 0) }
        root.addView(status)
        setContentView(root)
    }

    /** Make sure the address answers like a FaceAttend server before saving it. */
    private fun check(url: String) {
        if (url.isEmpty()) {
            status.text = "Enter the address shown on the computer, e.g. https://xyz.trycloudflare.com"
            return
        }
        connect.isEnabled = false
        status.text = "Connecting to $url …"
        thread {
            val error = try {
                val conn = URL("$url/login").openConnection() as HttpURLConnection
                conn.connectTimeout = 10000
                conn.readTimeout = 10000
                val code = conn.responseCode
                conn.disconnect()
                if (code == 200) null else "The server answered with error $code."
            } catch (e: SSLException) {
                "This phone does not trust the server's certificate. Use the online link (Share online) " +
                    "or install the certificate from the computer's Connect phones page."
            } catch (e: Exception) {
                "Cannot reach $url. Is FaceAttend running, and is this phone online / on the same Wi-Fi?"
            }
            runOnUiThread {
                connect.isEnabled = true
                if (error == null) {
                    prefs.serverUrl = url
                    setResult(Activity.RESULT_OK)
                    finish()
                } else {
                    status.text = error
                }
            }
        }
    }
}
