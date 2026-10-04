package com.faceattend.app

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.appcompat.app.AlertDialog
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

/**
 * Store-like updates without a store: once a day the app asks GitHub for the latest release of
 * this project. If it is newer than the installed version, it offers to download the new APK,
 * which installs over the old one (same signing key) and keeps all settings.
 */
object Updater {
    private const val PREFS = "updater"
    private const val DAY_MS = 24 * 60 * 60 * 1000L

    data class Release(val version: String, val apkUrl: String, val notes: String)

    /** "v1.2.10" -> [1, 2, 10]; anything that is not a number counts as 0. */
    fun parse(v: String): List<Int> = v.trim().removePrefix("v").split(".", "-").take(3)
        .map { it.takeWhile(Char::isDigit).toIntOrNull() ?: 0 }

    fun isNewer(latest: String, installed: String): Boolean {
        val a = parse(latest).let { it + List(3 - it.size) { 0 } }
        val b = parse(installed).let { it + List(3 - it.size) { 0 } }
        for (i in 0 until 3) if (a[i] != b[i]) return a[i] > b[i]
        return false
    }

    fun fetchLatest(repo: String): Release? {
        val conn = URL("https://api.github.com/repos/$repo/releases/latest").openConnection() as HttpURLConnection
        conn.connectTimeout = 10000
        conn.readTimeout = 10000
        conn.setRequestProperty("Accept", "application/vnd.github+json")
        return try {
            if (conn.responseCode != 200) return null
            val o = JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
            val assets = o.optJSONArray("assets") ?: return null
            val apk = (0 until assets.length()).map { assets.getJSONObject(it) }
                .firstOrNull { it.optString("name").endsWith(".apk") } ?: return null
            Release(o.optString("tag_name"), apk.getString("browser_download_url"), o.optString("body").take(600))
        } finally {
            conn.disconnect()
        }
    }

    /** Check in the background; show a dialog only when an update exists. `force` = menu "Check for updates". */
    fun check(activity: Activity, force: Boolean = false) {
        val prefs = activity.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val now = System.currentTimeMillis()
        if (!force && now - prefs.getLong("last_check", 0) < DAY_MS) return
        prefs.edit().putLong("last_check", now).apply()
        thread {
            val latest = try { fetchLatest(BuildConfig.UPDATE_REPO) } catch (e: Exception) { null }
            activity.runOnUiThread {
                if (activity.isFinishing) return@runOnUiThread
                when {
                    latest != null && isNewer(latest.version, BuildConfig.VERSION_NAME) &&
                        (force || prefs.getString("skipped", "") != latest.version) ->
                        AlertDialog.Builder(activity)
                            .setTitle("Update available: ${latest.version}")
                            .setMessage("You have ${BuildConfig.VERSION_NAME}.\n\n${latest.notes}".trim())
                            .setPositiveButton("Download") { _, _ ->
                                activity.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(latest.apkUrl)))
                            }
                            .setNeutralButton("Skip this version") { _, _ ->
                                prefs.edit().putString("skipped", latest.version).apply()
                            }
                            .setNegativeButton("Later", null)
                            .show()
                    force -> AlertDialog.Builder(activity)
                        .setMessage(if (latest == null) "Could not check for updates (no internet?)."
                                    else "You have the latest version (${BuildConfig.VERSION_NAME}).")
                        .setPositiveButton("OK", null).show()
                }
            }
        }
    }
}
