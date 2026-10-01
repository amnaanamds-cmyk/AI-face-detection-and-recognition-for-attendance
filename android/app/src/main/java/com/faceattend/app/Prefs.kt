package com.faceattend.app

import android.content.Context
import android.net.Uri
import org.json.JSONObject

/** Saved settings: the server address and the SMS-sender pairing. */
class Prefs(context: Context) {
    private val p = context.getSharedPreferences("faceattend", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = p.getString("server_url", "") ?: ""
        set(v) = p.edit().putString("server_url", v.trimEnd('/')).apply()

    var gatewayServer: String
        get() = p.getString("gw_server", "") ?: ""
        set(v) = p.edit().putString("gw_server", v.trimEnd('/')).apply()

    var gatewayToken: String
        get() = p.getString("gw_token", "") ?: ""
        set(v) = p.edit().putString("gw_token", v).apply()

    var gatewayOrg: String
        get() = p.getString("gw_org", "") ?: ""
        set(v) = p.edit().putString("gw_org", v).apply()

    var gatewayEnabled: Boolean
        get() = p.getBoolean("gw_enabled", false)
        set(v) = p.edit().putBoolean("gw_enabled", v).apply()

    var sentCount: Int
        get() = p.getInt("gw_sent", 0)
        set(v) = p.edit().putInt("gw_sent", v).apply()

    var lastStatus: String
        get() = p.getString("gw_last", "") ?: ""
        set(v) = p.edit().putString("gw_last", v).apply()

    val paired: Boolean get() = gatewayServer.isNotEmpty() && gatewayToken.isNotEmpty()

    companion object {
        /** A scanned QR code: either a plain server address (Connect phones page) or a
         *  pairing code {"faceattend":1,"server":"…","token":"…"} (Parent messages page). */
        fun parseScan(text: String): Scan? {
            val t = text.trim()
            if (t.startsWith("{")) {
                return try {
                    val o = JSONObject(t)
                    if (!o.has("server") || !o.has("token")) null
                    else Scan(normalizeUrl(o.getString("server")), o.getString("token"))
                } catch (e: Exception) {
                    null
                }
            }
            val url = normalizeUrl(t)
            return if (url.isEmpty()) null else Scan(url, null)
        }

        fun normalizeUrl(raw: String): String {
            var s = raw.trim()
            if (s.isEmpty()) return ""
            if (!s.startsWith("http://") && !s.startsWith("https://")) s = "https://$s"
            val u = Uri.parse(s)
            if (u.host.isNullOrEmpty()) return ""
            return "${u.scheme}://${u.authority}".trimEnd('/')
        }
    }

    data class Scan(val server: String, val token: String?)
}
