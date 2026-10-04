package com.faceattend.app

import android.Manifest
import android.annotation.SuppressLint
import android.app.DownloadManager
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import android.view.Gravity
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.PermissionRequest
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.Toolbar
import androidx.core.content.ContextCompat
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout

/**
 * The FaceAttend web app in a full-screen WebView, with what a browser tab cannot do well:
 * camera permission, file upload, report downloads, WhatsApp / SMS links, and the SMS sender.
 */
class MainActivity : AppCompatActivity() {
    private lateinit var prefs: Prefs
    private lateinit var web: WebView
    private lateinit var refresh: SwipeRefreshLayout
    private lateinit var offline: View
    private var pendingPermission: PermissionRequest? = null
    private var fileCallback: ValueCallback<Array<Uri>>? = null

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

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        buildViews()
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (web.canGoBack()) web.goBack() else finish()
            }
        })
        if (prefs.serverUrl.isEmpty()) setup.launch(Intent(this, SetupActivity::class.java))
        else if (savedInstanceState != null) web.restoreState(savedInstanceState) else loadHome()
        if (prefs.gatewayEnabled) SmsGatewayService.start(this)
        Updater.check(this)   // at most once a day: offers a newer release from GitHub
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        web.saveState(outState)
    }

    private fun loadHome() {
        offline.visibility = View.GONE
        web.loadUrl(prefs.serverUrl + "/")
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun buildViews() {
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        val toolbar = Toolbar(this).apply {
            setBackgroundColor(ContextCompat.getColor(this@MainActivity, R.color.brand))
            setTitleTextColor(Color.WHITE)
            title = getString(R.string.app_name)
        }
        setSupportActionBar(toolbar)
        root.addView(toolbar, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(48)))

        val frame = FrameLayout(this)
        web = WebView(this)
        refresh = SwipeRefreshLayout(this).apply {
            addView(web)
            setOnRefreshListener { web.reload() }
            setOnChildScrollUpCallback { _, _ -> web.scrollY > 0 }
        }
        frame.addView(refresh, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        offline = offlineView()
        frame.addView(offline, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        root.addView(frame, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        setContentView(root)

        CookieManager.getInstance().setAcceptCookie(true)
        with(web.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false   // live camera preview starts by itself
            loadWithOverviewMode = true
            useWideViewPort = true
            builtInZoomControls = false
            userAgentString = "$userAgentString FaceAttendAndroid/1.0"
        }
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val url = request.url
                val server = Uri.parse(prefs.serverUrl)
                val inApp = (url.scheme == "http" || url.scheme == "https") && url.host == server.host
                if (inApp) return false
                openExternally(url)   // WhatsApp, SMS, phone, e-mail, payment pages ...
                return true
            }

            override fun onPageFinished(view: WebView, url: String) {
                refresh.isRefreshing = false
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (request.isForMainFrame) {
                    refresh.isRefreshing = false
                    offline.visibility = View.VISIBLE
                }
            }
        }
        web.webChromeClient = object : WebChromeClient() {
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

    private fun offlineView(): View = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        gravity = Gravity.CENTER
        setBackgroundColor(Color.WHITE)
        setPadding(dp(32), dp(32), dp(32), dp(32))
        visibility = View.GONE
        addView(TextView(context).apply {
            text = getString(R.string.offline_title); textSize = 20f; gravity = Gravity.CENTER
        })
        addView(TextView(context).apply {
            text = getString(R.string.offline_text); gravity = Gravity.CENTER; setPadding(0, dp(12), 0, dp(20))
        })
        addView(Button(context).apply { text = getString(R.string.retry); setOnClickListener { loadHome() } })
        addView(Button(context).apply {
            text = getString(R.string.menu_server)
            setOnClickListener { setup.launch(Intent(this@MainActivity, SetupActivity::class.java)) }
        })
    }

    private fun openExternally(url: Uri) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, url))
        } catch (e: ActivityNotFoundException) {
            Toast.makeText(this, "No app can open $url", Toast.LENGTH_SHORT).show()
        }
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menu.add(0, 1, 0, R.string.menu_reload)
        menu.add(0, 2, 1, R.string.menu_server)
        menu.add(0, 3, 2, R.string.menu_gateway)
        menu.add(0, 4, 3, "Check for updates (v${BuildConfig.VERSION_NAME})")
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        when (item.itemId) {
            1 -> web.reload()
            2 -> setup.launch(Intent(this, SetupActivity::class.java))
            3 -> startActivity(Intent(this, GatewayActivity::class.java))
            4 -> Updater.check(this, force = true)
            else -> return super.onOptionsItemSelected(item)
        }
        return true
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()
}
