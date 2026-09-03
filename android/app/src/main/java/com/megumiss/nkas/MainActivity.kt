package com.megumiss.nkas

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient

class MainActivity : android.app.Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
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
        setContentView(webView)
    }
}
