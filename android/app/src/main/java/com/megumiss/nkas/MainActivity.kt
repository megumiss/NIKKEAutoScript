package com.megumiss.nkas

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout

class MainActivity : Activity() {
    private val bg = Color.rgb(248, 250, 252)

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        if (!AccessGate.isAuthorized(this)) {
            startActivity(Intent(this, GateActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP))
            finish()
            return
        }
        window.statusBarColor = bg
        window.navigationBarColor = bg
        val built = DrawerShell.build(this, "ui") { key ->
            when (key) {
                "setup" -> { startActivity(Intent(this, SetupActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)); finish() }
                "about" -> { startActivity(Intent(this, SetupActivity::class.java).putExtra("page", "about").addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)); finish() }
                "settings" -> { startActivity(Intent(this, SettingsActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)); finish() }
            }
        }
        val webView = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                    val uri = request.url
                    if (uri.host == "127.0.0.1" || uri.host == "localhost") return false
                    startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(uri.toString())))
                    return true
                }
            }
            loadUrl("http://127.0.0.1:12271/app/")
        }
        built.content.addView(webView, FrameLayout.LayoutParams(-1, -1))
        setContentView(built.root)
    }
}
