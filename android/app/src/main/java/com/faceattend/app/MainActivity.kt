package com.faceattend.app

import android.Manifest
import android.annotation.SuppressLint
import android.app.DownloadManager
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.net.ConnectivityManager
import android.net.Network
import android.net.Uri
import android.net.http.SslError
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.PermissionRequest
import android.webkit.SslErrorHandler
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.Toolbar
import androidx.core.content.ContextCompat
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import com.google.android.material.bottomnavigation.BottomNavigationView
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.google.android.material.navigation.NavigationBarView
import org.json.JSONObject

/**
 * The FaceAttend app: the school's FaceAttend pages in a WebView, with native parts a browser tab
 * cannot offer - a bottom menu that follows the person's role, a "More" sheet, sensible Back,
 * camera permission, uploads and downloads, automatic reconnection and the SMS sender.
 */
class MainActivity : AppCompatActivity() {
    private lateinit var prefs: Prefs
    private lateinit var web: WebView
    private lateinit var refresh: SwipeRefreshLayout
    private lateinit var progress: ProgressBar
    private lateinit var bottom: BottomNavigationView
    private lateinit var offline: LinearLayout
    private lateinit var offlineTitle: TextView
    private lateinit var offlineText: TextView
    private lateinit var offlineRetry: TextView
    private var pendingPermission: PermissionRequest? = null
    private var fileCallback: ValueCallback<Array<Uri>>? = null

    private var nav: JSONObject? = null
    private var mainPaths: List<String> = emptyList()
    private var selectingTab = false
    private var lastBack = 0L
    private val handler = Handler(Looper.getMainLooper())
    private var retryIn = 0
    private var retryStep = 0
    private var failedUrl: String? = null

    private val cameraPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        val req = pendingPermission ?: return@registerForActivityResult
        pendingPermission = null
        if (granted) req.grant(arrayOf(PermissionRequest.RESOURCE_VIDEO_CAPTURE)) else req.deny()
    }

    private val pickFile = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val cb = fileCallback ?: return@registerForActivityResult
        fileCallback = null
        cb.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(result.resultCode, result.data))
    }

    private val setup = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        if (prefs.serverUrl.isEmpty()) finish() else loadHome()
    }

    private val network = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(n: Network) {        // Wi-Fi / data back: reconnect at once
            handler.post { if (offline.visibility == View.VISIBLE) retryNow() }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        buildViews()
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() = goBack()
        })
        if (prefs.serverUrl.isEmpty() && BuildConfig.CLOUD_URL.isNotEmpty()) prefs.serverUrl = BuildConfig.CLOUD_URL
        when {
            linkServer(intent) -> {}
            prefs.serverUrl.isEmpty() -> setup.launch(Intent(this, SetupActivity::class.java))
            savedInstanceState != null -> web.restoreState(savedInstanceState)
            else -> loadHome()
        }
        if (prefs.gatewayEnabled) SmsGatewayService.start(this)
        try {
            (getSystemService(CONNECTIVITY_SERVICE) as ConnectivityManager).registerDefaultNetworkCallback(network)
        } catch (e: Exception) { /* not essential */ }
        Updater.check(this)   // at most once a day: offers a newer release
    }

    override fun onDestroy() {
        try { (getSystemService(CONNECTIVITY_SERVICE) as ConnectivityManager).unregisterNetworkCallback(network) } catch (e: Exception) {}
        handler.removeCallbacksAndMessages(null)
        super.onDestroy()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        web.saveState(outState)
    }

    private fun server() = prefs.serverUrl
    private fun loadHome() = open(homePath())
    private fun homePath() = mainPaths.firstOrNull() ?: "/"

    private fun open(path: String) {
        hideOffline()
        web.loadUrl(if (path.startsWith("http")) path else server() + path)
    }

    // ------------------------------------------------------------------ views
    @SuppressLint("SetJavaScriptEnabled")
    private fun buildViews() {
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        val toolbar = Toolbar(this).apply {
            setBackgroundColor(ContextCompat.getColor(this@MainActivity, R.color.brand))
            setTitleTextColor(Color.WHITE)
            setSubtitleTextColor(Color.parseColor("#C9D7E6"))
            title = getString(R.string.app_name)
        }
        setSupportActionBar(toolbar)
        root.addView(toolbar, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        progress = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply { max = 100; visibility = View.GONE }
        root.addView(progress, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(3)))

        val frame = FrameLayout(this)
        web = WebView(this)
        refresh = SwipeRefreshLayout(this).apply {
            addView(web)
            setOnRefreshListener { if (offline.visibility == View.VISIBLE) retryNow() else web.reload() }
            setOnChildScrollUpCallback { _, _ -> web.scrollY > 0 }
        }
        frame.addView(refresh, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        offline = offlineView()
        frame.addView(offline, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        root.addView(frame, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))

        bottom = BottomNavigationView(this).apply {
            visibility = View.GONE
            labelVisibilityMode = NavigationBarView.LABEL_VISIBILITY_LABELED
            setOnItemSelectedListener { item ->
                if (selectingTab) return@setOnItemSelectedListener true
                if (item.itemId == MORE) { showMore(); false }
                else { mainPaths.getOrNull(item.itemId)?.let { open(it) }; true }
            }
            setOnItemReselectedListener { item -> if (item.itemId == MORE) showMore() else mainPaths.getOrNull(item.itemId)?.let { open(it) } }
        }
        root.addView(bottom, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        setContentView(root)

        CookieManager.getInstance().setAcceptCookie(true)
        with(web.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false   // live camera preview starts by itself
            loadWithOverviewMode = true
            useWideViewPort = true
            builtInZoomControls = false
            userAgentString = "$userAgentString FaceAttendAndroid/2.0"
        }
        web.addJavascriptInterface(Bridge(), "FaceAttendApp")
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val url = request.url
                val inApp = (url.scheme == "http" || url.scheme == "https") && url.host == Uri.parse(server()).host
                if (inApp) return false
                if (url.scheme == "faceattend") { linkServer(Intent(Intent.ACTION_VIEW, url)); return true }
                openExternally(url)   // WhatsApp, SMS, phone, e-mail, payment pages ...
                return true
            }

            override fun onPageFinished(view: WebView, url: String) {
                refresh.isRefreshing = false
                if (offline.visibility != View.VISIBLE) retryStep = 0   // connected again: next outage retries quickly
                highlightTab(url)
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (!request.isForMainFrame) return
                failedUrl = request.url.toString()
                val kind = when (error.errorCode) {
                    WebViewClient.ERROR_HOST_LOOKUP -> if (online()) Offline.NOT_FOUND else Offline.NO_INTERNET
                    else -> if (online()) Offline.SERVER_OFF else Offline.NO_INTERNET
                }
                showOffline(kind)
            }

            override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, response: WebResourceResponse) {
                // an online link whose computer is off answers through Cloudflare with 502 / 530
                if (request.isForMainFrame && request.url.host?.endsWith("trycloudflare.com") == true &&
                    response.statusCode in listOf(502, 503, 504, 530)) {
                    failedUrl = request.url.toString()
                    showOffline(Offline.LINK_DOWN)
                }
            }

            override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
                handler.cancel()                    // never accept an untrusted certificate
                failedUrl = error.url
                showOffline(Offline.CERTIFICATE)
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView, newProgress: Int) {
                progress.progress = newProgress
                progress.visibility = if (newProgress in 1..99) View.VISIBLE else View.GONE
            }

            override fun onReceivedTitle(view: WebView, title: String?) {
                val clean = title?.substringBefore(" - ")?.trim().orEmpty()
                supportActionBar?.title = if (clean.isEmpty() || clean.startsWith("http")) getString(R.string.app_name) else clean
                supportActionBar?.subtitle = Uri.parse(server()).host
            }

            override fun onPermissionRequest(request: PermissionRequest) {
                if (!request.resources.contains(PermissionRequest.RESOURCE_VIDEO_CAPTURE)) {
                    request.deny()
                    return
                }
                runOnUiThread {
                    if (ContextCompat.checkSelfPermission(this@MainActivity, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
                        request.grant(arrayOf(PermissionRequest.RESOURCE_VIDEO_CAPTURE))
                    } else {
                        pendingPermission = request
                        cameraPermission.launch(Manifest.permission.CAMERA)
                    }
                }
            }

            override fun onShowFileChooser(view: WebView, callback: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean {
                fileCallback?.onReceiveValue(null)
                fileCallback = callback
                return try {
                    pickFile.launch(params.createIntent())
                    true
                } catch (e: ActivityNotFoundException) {
                    fileCallback = null
                    false
                }
            }
        }
        web.setDownloadListener { url, userAgent, contentDisposition, mimeType, _ ->
            val name = URLUtil.guessFileName(url, contentDisposition, mimeType)
            val req = DownloadManager.Request(Uri.parse(url))
                .addRequestHeader("Cookie", CookieManager.getInstance().getCookie(url) ?: "")
                .addRequestHeader("User-Agent", userAgent)
                .setMimeType(mimeType)
                .setTitle(name)
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name)
            try {
                (getSystemService(DOWNLOAD_SERVICE) as DownloadManager).enqueue(req)
                Toast.makeText(this, "Downloading $name", Toast.LENGTH_SHORT).show()
            } catch (e: Exception) {   // e.g. no storage permission on Android 7-9: let the browser download it
                openExternally(Uri.parse(url))
            }
        }
    }

    // ------------------------------------------------------------------ bottom menu (from the page)
    inner class Bridge {
        @JavascriptInterface
        fun setNav(json: String?) {
            runOnUiThread { applyNav(json) }
        }
    }

    private fun applyNav(json: String?) {
        val data = try { if (json.isNullOrEmpty() || json == "null") null else JSONObject(json) } catch (e: Exception) { null }
        nav = data
        if (data == null) {                         // logged out: no menu
            bottom.visibility = View.GONE
            mainPaths = emptyList()
            return
        }
        val main = data.optJSONArray("main") ?: return
        val paths = (0 until minOf(main.length(), 4)).map { main.getJSONObject(it).getString("p") }
        if (paths != mainPaths || bottom.menu.size() == 0) {
            selectingTab = true
            bottom.menu.clear()
            for (i in paths.indices) {
                val it = main.getJSONObject(i)
                bottom.menu.add(Menu.NONE, i, i, it.getString("t")).setIcon(icon(it.optString("i")))
            }
            bottom.menu.add(Menu.NONE, MORE, 9, "More").setIcon(R.drawable.ic_more)
            mainPaths = paths
            selectingTab = false
        }
        bottom.visibility = View.VISIBLE
        highlightTab(web.url ?: "")
    }

    private fun highlightTab(url: String) {
        if (mainPaths.isEmpty()) return
        val path = Uri.parse(url).path ?: "/"
        val best = mainPaths.withIndex().filter { (_, p) -> if (p == "/") path == "/" else path.startsWith(p) }
            .maxByOrNull { it.value.length }?.index ?: return
        selectingTab = true
        bottom.selectedItemId = best
        selectingTab = false
    }

    private fun icon(name: String) = when (name) {
        "home" -> R.drawable.ic_home
        "camera", "door" -> R.drawable.ic_camera
        "people", "badge" -> R.drawable.ic_people
        "dashboard", "book" -> R.drawable.ic_dashboard
        "calendar" -> R.drawable.ic_calendar
        "chart" -> R.drawable.ic_chart
        "bell", "alert" -> R.drawable.ic_bell
        "settings" -> R.drawable.ic_settings
        else -> R.drawable.ic_dot
    }

    /** The "More" sheet: every other page this person may open, then the app's own settings. */
    private fun showMore() {
        val sheet = BottomSheetDialog(this)
        val list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(0, dp(8), 0, dp(16)) }
        fun header(text: String) = list.addView(TextView(this).apply {
            this.text = text.uppercase(); textSize = 12f; setTypeface(typeface, Typeface.BOLD)
            setTextColor(ContextCompat.getColor(this@MainActivity, R.color.brand)); setPadding(dp(20), dp(14), dp(20), dp(4))
        })
        fun row(title: String, iconName: String, action: () -> Unit) = list.addView(LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(20), dp(12), dp(20), dp(12))
            isClickable = true
            background = ContextCompat.getDrawable(this@MainActivity, android.R.drawable.list_selector_background)
            addView(ImageView(context).apply {
                setImageResource(icon(iconName)); setColorFilter(Color.parseColor("#55606E"))
            }, LinearLayout.LayoutParams(dp(22), dp(22)))
            addView(TextView(context).apply { text = title; textSize = 16f; setTextColor(Color.parseColor("#212529")); setPadding(dp(16), 0, 0, 0) })
            setOnClickListener { sheet.dismiss(); action() }
        })
        nav?.optJSONArray("more")?.let { groups ->
            for (g in 0 until groups.length()) {
                val group = groups.getJSONObject(g)
                header(group.optString("h"))
                val items = group.optJSONArray("items") ?: continue
                for (i in 0 until items.length()) {
                    val it = items.getJSONObject(i)
                    row(it.getString("t"), it.optString("i")) { open(it.getString("p")) }
                }
            }
        }
        header("App")
        row("Change school / server", "settings") { setup.launch(Intent(this, SetupActivity::class.java)) }
        row(getString(R.string.menu_gateway), "bell") { startActivity(Intent(this, GatewayActivity::class.java)) }
        row("Check for updates (v${BuildConfig.VERSION_NAME})", "dot") { Updater.check(this, force = true) }
        if (nav != null) row("Log out", "dot") { open("/logout") }
        sheet.setContentView(ScrollView(this).apply { addView(list) })
        sheet.show()
    }

    // ------------------------------------------------------------------ back button
    private fun goBack() {
        when {
            offline.visibility == View.VISIBLE && web.canGoBack() -> { hideOffline(); web.goBack() }
            web.canGoBack() -> web.goBack()
            mainPaths.isNotEmpty() && (Uri.parse(web.url ?: "").path ?: "/") != homePath() -> loadHome()
            System.currentTimeMillis() - lastBack < 2000 -> finish()
            else -> {
                lastBack = System.currentTimeMillis()
                Toast.makeText(this, "Press Back again to close FaceAttend", Toast.LENGTH_SHORT).show()
            }
        }
    }

    // ------------------------------------------------------------------ offline screen with automatic retry
    private enum class Offline { NO_INTERNET, SERVER_OFF, NOT_FOUND, LINK_DOWN, CERTIFICATE }

    private fun offlineView(): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        gravity = Gravity.CENTER
        setBackgroundColor(Color.WHITE)
        setPadding(dp(28), dp(28), dp(28), dp(28))
        visibility = View.GONE
        addView(ImageView(context).apply { setImageResource(R.drawable.ic_wifi_off); setColorFilter(Color.parseColor("#9AA5B1")) },
            LinearLayout.LayoutParams(dp(64), dp(64)))
        offlineTitle = TextView(context).apply { textSize = 20f; gravity = Gravity.CENTER; setTypeface(typeface, Typeface.BOLD); setPadding(0, dp(12), 0, 0) }
        addView(offlineTitle)
        offlineText = TextView(context).apply { gravity = Gravity.CENTER; textSize = 15f; setPadding(0, dp(10), 0, dp(10)) }
        addView(offlineText)
        offlineRetry = TextView(context).apply { gravity = Gravity.CENTER; setTextColor(Color.GRAY); setPadding(0, 0, 0, dp(16)) }
        addView(offlineRetry)
        addView(Button(context).apply { text = getString(R.string.retry); setOnClickListener { retryNow() } })
        addView(Button(context).apply {
            text = "Change school / server"
            setOnClickListener { setup.launch(Intent(this@MainActivity, SetupActivity::class.java)) }
        })
    }

    private fun showOffline(kind: Offline) {
        val host = Uri.parse(server()).host ?: ""
        offlineTitle.text = when (kind) {
            Offline.NO_INTERNET -> getString(R.string.offline_no_internet)
            Offline.CERTIFICATE -> getString(R.string.offline_certificate)
            Offline.LINK_DOWN -> "The online link is not active"
            else -> getString(R.string.offline_server_off)
        }
        offlineText.text = when (kind) {
            Offline.NO_INTERNET -> "Turn on Wi-Fi or mobile data. FaceAttend reconnects by itself."
            Offline.CERTIFICATE -> "The secure connection to $host failed. Tap \"Change school / server\" and scan the QR code on the school computer (Connect phones > Share online)."
            Offline.LINK_DOWN -> "The school computer is off, or Share online was stopped. When it is back on, FaceAttend reconnects by itself. " +
                "If sharing was restarted, scan the new QR code."
            Offline.NOT_FOUND -> "The address $host does not exist any more. Tap \"Change school / server\"."
            Offline.SERVER_OFF -> "The FaceAttend server ($host) is not answering: the school computer may be off, or FaceAttend " +
                "is not running on it. FaceAttend reconnects by itself as soon as it is back."
        }
        offline.visibility = View.VISIBLE
        refresh.isRefreshing = false
        progress.visibility = View.GONE
        if (kind != Offline.CERTIFICATE && kind != Offline.NOT_FOUND) scheduleRetry() else offlineRetry.text = ""
    }

    private fun hideOffline() {
        offline.visibility = View.GONE
        handler.removeCallbacks(tick)
        retryStep = 0
    }

    private val tick = object : Runnable {
        override fun run() {
            if (offline.visibility != View.VISIBLE) return
            if (retryIn <= 0) { retryNow(auto = true); return }
            offlineRetry.text = "Trying again in $retryIn s …"
            retryIn -= 1
            handler.postDelayed(this, 1000)
        }
    }

    private fun scheduleRetry() {
        handler.removeCallbacks(tick)
        retryIn = listOf(5, 10, 20, 30).getOrElse(retryStep) { 30 }
        retryStep += 1
        handler.post(tick)
    }

    private fun retryNow(auto: Boolean = false) {
        handler.removeCallbacks(tick)
        offlineRetry.text = "Connecting …"
        val step = retryStep
        offline.visibility = View.GONE
        web.loadUrl(failedUrl ?: (server() + homePath()))
        if (auto) retryStep = step else retryStep = 0
    }

    private fun online(): Boolean = try {
        val cm = getSystemService(CONNECTIVITY_SERVICE) as ConnectivityManager
        cm.activeNetwork != null
    } catch (e: Exception) { true }

    // ------------------------------------------------------------------ misc
    private fun openExternally(url: Uri) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, url))
        } catch (e: ActivityNotFoundException) {
            Toast.makeText(this, "No app can open $url", Toast.LENGTH_SHORT).show()
        }
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menu.add(0, 1, 0, R.string.menu_reload).setShowAsAction(MenuItem.SHOW_AS_ACTION_NEVER)
        menu.add(0, 2, 1, "Change school / server")
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        when (item.itemId) {
            1 -> if (offline.visibility == View.VISIBLE) retryNow() else web.reload()
            2 -> setup.launch(Intent(this, SetupActivity::class.java))
            else -> return super.onOptionsItemSelected(item)
        }
        return true
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        linkServer(intent)
    }

    /** faceattend://connect?url=https://... (the "Open in the FaceAttend app" button): no QR code needed. */
    private fun linkServer(intent: Intent?): Boolean {
        val data = intent?.data ?: return false
        if (data.scheme != "faceattend" || data.host != "connect") return false
        val url = Prefs.normalizeUrl(data.getQueryParameter("url") ?: return false)
        if (url.isEmpty()) return false
        prefs.serverUrl = url
        Toast.makeText(this, "Connected to $url", Toast.LENGTH_LONG).show()
        loadHome()
        return true
    }

    companion object {
        private const val MORE = 99
    }
}
