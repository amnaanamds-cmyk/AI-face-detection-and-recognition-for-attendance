package com.faceattend.app

import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** Minimal client for the server's SMS-gateway API (see app/routers/messages.py). */
class Api(private val server: String, private val token: String) {

    class ApiException(val code: Int, message: String) : Exception(message)

    data class Message(val id: Long, val to: String, val body: String)

    private fun request(method: String, path: String, body: JSONObject? = null): JSONObject {
        val conn = URL(server + path).openConnection() as HttpURLConnection
        conn.requestMethod = method
        conn.connectTimeout = 15000
        conn.readTimeout = 20000
        conn.setRequestProperty("Authorization", "Bearer $token")
        conn.setRequestProperty("Accept", "application/json")
        if (body != null) {
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            conn.outputStream.use { it.write(body.toString().toByteArray()) }
        }
        try {
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val text = stream?.bufferedReader()?.use { it.readText() } ?: ""
            if (code !in 200..299) {
                val detail = try { JSONObject(text).optString("detail", text) } catch (e: Exception) { text }
                throw ApiException(code, detail.ifEmpty { "HTTP $code" })
            }
            return if (text.isEmpty()) JSONObject() else JSONObject(text)
        } finally {
            conn.disconnect()
        }
    }

    /** Organization name if the pairing code is valid. */
    fun ping(): String = request("GET", "/api/gateway/ping").optString("organization", "")

    fun pull(): List<Message> {
        val arr: JSONArray = request("GET", "/api/gateway/messages").optJSONArray("messages") ?: JSONArray()
        return (0 until arr.length()).map {
            val o = arr.getJSONObject(it)
            Message(o.getLong("id"), o.getString("to"), o.getString("body"))
        }
    }

    fun report(id: Long, ok: Boolean, error: String? = null) {
        val body = JSONObject().put("ok", ok)
        if (error != null) body.put("error", error)
        request("POST", "/api/gateway/messages/$id", body)
    }
}
