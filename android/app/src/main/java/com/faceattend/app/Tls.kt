package com.faceattend.app

import android.net.http.SslCertificate
import android.os.Build
import android.util.Base64
import java.io.ByteArrayInputStream
import java.net.HttpURLConnection
import java.net.URL
import java.security.KeyStore
import java.security.MessageDigest
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager

/**
 * Trusting a school's own FaceAttend computer on the Wi-Fi, without installing anything on the phone.
 *
 * The computer makes its own small certificate authority (CA). The QR code on its "Connect phones"
 * page carries the SHA-256 fingerprint of that CA ("pin"). The app downloads the CA, accepts it only
 * if the fingerprint matches, and from then on trusts exactly the certificates that CA signed
 * (normal checks: signature, dates, and the computer's IP address). Internet addresses keep using
 * the phone's normal certificate checks.
 */
object Tls {
    @Volatile private var ca: X509Certificate? = null

    fun load(prefs: Prefs) {
        ca = prefs.caDer.takeIf { it.isNotEmpty() }?.let {
            try { parse(Base64.decode(it, Base64.NO_WRAP)) } catch (e: Exception) { null }
        }
    }

    private fun parse(der: ByteArray): X509Certificate =
        CertificateFactory.getInstance("X.509").generateCertificate(ByteArrayInputStream(der)) as X509Certificate

    fun sha256(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

    /** Download the computer's CA and keep it if its fingerprint matches the QR code. */
    fun fetchAndPin(server: String, pin: String, prefs: Prefs) {
        val trustAll = object : X509TrustManager {   // safe here: what we receive is checked against the pin below
            override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) {}
            override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {}
            override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
        }
        val conn = URL("$server/mobile/attendance-ca.crt").openConnection() as HttpURLConnection
        if (conn is HttpsURLConnection) {
            conn.sslSocketFactory = SSLContext.getInstance("TLS").apply { init(null, arrayOf<TrustManager>(trustAll), null) }.socketFactory
            conn.setHostnameVerifier { _, _ -> true }
        }
        conn.connectTimeout = 10000
        conn.readTimeout = 10000
        val der = try {
            if (conn.responseCode != 200) throw IllegalStateException("the computer did not send its certificate (error ${conn.responseCode})")
            conn.inputStream.use { it.readBytes() }
        } finally {
            conn.disconnect()
        }
        if (!sha256(der).equals(pin, ignoreCase = true)) {
            throw SecurityException("the certificate does not match the QR code - scan the QR code again on the computer")
        }
        parse(der)                                      // must be a valid certificate
        prefs.caDer = Base64.encodeToString(der, Base64.NO_WRAP)
        load(prefs)
    }

    private fun factory(): javax.net.ssl.SSLSocketFactory? {
        val pinned = ca ?: return null
        val system = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm())
            .apply { init(null as KeyStore?) }.trustManagers.filterIsInstance<X509TrustManager>().first()
        val store = KeyStore.getInstance(KeyStore.getDefaultType()).apply { load(null, null); setCertificateEntry("faceattend", pinned) }
        val local = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm())
            .apply { init(store) }.trustManagers.filterIsInstance<X509TrustManager>().first()
        val both = object : X509TrustManager {
            override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) = system.checkClientTrusted(chain, authType)
            override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
                try { system.checkServerTrusted(chain, authType) } catch (e: Exception) { local.checkServerTrusted(chain, authType) }
            }
            override fun getAcceptedIssuers(): Array<X509Certificate> = system.acceptedIssuers + pinned
        }
        return SSLContext.getInstance("TLS").apply { init(null, arrayOf<TrustManager>(both), null) }.socketFactory
    }

    /** An HTTP(S) connection that also trusts the pinned school computer. */
    fun open(url: String): HttpURLConnection {
        val conn = URL(url).openConnection() as HttpURLConnection
        if (conn is HttpsURLConnection) factory()?.let { conn.sslSocketFactory = it }
        return conn
    }

    /** WebView: is this certificate (that Android does not know) one signed by the pinned school computer? */
    fun trusts(cert: SslCertificate?): Boolean {
        val pinned = ca ?: return false
        val x509 = try {
            if (Build.VERSION.SDK_INT >= 29) cert?.x509Certificate
            else SslCertificate.saveState(cert)?.getByteArray("x509-certificate")?.let { parse(it) }
        } catch (e: Exception) { null } ?: return false
        return try {
            x509.verify(pinned.publicKey)
            x509.checkValidity()
            true
        } catch (e: Exception) {
            false
        }
    }
}
