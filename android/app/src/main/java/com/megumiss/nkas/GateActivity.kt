package com.megumiss.nkas

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.net.Uri
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.UUID

class GateActivity : Activity() {
    private val bg = Color.rgb(248, 250, 252)
    private val card = Color.WHITE
    private val border = Color.rgb(220, 226, 232)
    private val text = Color.rgb(25, 35, 48)
    private val text2 = Color.rgb(92, 105, 120)
    private val accent = Color.rgb(26, 112, 170)
    private val success = Color.rgb(24, 145, 95)
    private val error = Color.rgb(195, 63, 73)
    private lateinit var content: LinearLayout
    private lateinit var status: TextView
    private lateinit var action: Button
    private lateinit var openRepository: Button
    private var authorized: AccessGate.License? = null

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        window.statusBarColor = bg
        window.navigationBarColor = bg
        setContentView(buildView())
        handleIntent(intent)
    }

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIntent(intent)
    }

    private fun buildView(): View {
        val scroll = ScrollView(this).apply { isFillViewport = true; setBackgroundColor(bg) }
        content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(dp(24), dp(42), dp(24), dp(32))
        }
        scroll.addView(content)
        renderWaiting()
        return scroll
    }

    private fun renderWaiting() {
        content.removeAllViews()
        val panel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(24), dp(26), dp(24), dp(24))
            background = rounded(card, 12)
        }
        panel.addView(ImageView(this).apply {
            setImageResource(R.mipmap.ic_launcher)
            contentDescription = getString(R.string.app_name)
            scaleType = ImageView.ScaleType.CENTER_INSIDE
        }, LinearLayout.LayoutParams(-1, dp(72)))
        panel.addView(TextView(this).apply {
            text = "项目授权"
            textSize = 24f
            setTextColor(this@GateActivity.text)
            setTypeface(Typeface.DEFAULT, Typeface.BOLD)
            gravity = Gravity.CENTER
            setPadding(0, dp(16), 0, dp(8))
        })
        panel.addView(TextView(this).apply {
            text = "使用 NKAS 前，请先使用 GitHub 完成项目授权。授权后即可进入初始化页面。"
            textSize = 14f
            setTextColor(text2)
            gravity = Gravity.CENTER
        })
        panel.addView(TextView(this).apply {
            text = "megumiss/NIKKEAutoScript"
            textSize = 15f
            setTextColor(accent)
            gravity = Gravity.CENTER
            setPadding(0, dp(18), 0, dp(18))
        })
        status = TextView(this).apply { textSize = 13f; setTextColor(text2); gravity = Gravity.CENTER; setPadding(0, dp(8), 0, dp(12)) }
        panel.addView(status)
        action = Button(this).apply {
            text = "前往 GitHub 授权"
            textSize = 14f
            isAllCaps = false
            setTextColor(Color.WHITE)
            background = rounded(accent, 10)
            setOnClickListener { beginAuthorization() }
        }
        panel.addView(action, LinearLayout.LayoutParams(-1, dp(50)))
        openRepository = Button(this).apply {
            text = "打开项目仓库"
            textSize = 13f
            isAllCaps = false
            setTextColor(accent)
            background = rounded(Color.rgb(229, 241, 250), 10)
            setOnClickListener { openExternal(GateConfig.REPOSITORY_URL) }
        }
        panel.addView(openRepository, LinearLayout.LayoutParams(-1, dp(48)).apply { topMargin = dp(10) })
        content.addView(panel, LinearLayout.LayoutParams(-1, -2))
        content.addView(TextView(this).apply {
            text = "授权密钥有效期为一年。App 只验证签名，不保存 GitHub 密码。"
            textSize = 12f
            setTextColor(text2)
            gravity = Gravity.CENTER
            setPadding(0, dp(18), 0, 0)
        }, LinearLayout.LayoutParams(-1, -2))
        status.text = "尚未完成授权"
    }

    private fun renderAuthorized(license: AccessGate.License) {
        authorized = license
        content.removeAllViews()
        val panel = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(24), dp(26), dp(24), dp(24)); background = rounded(card, 12) }
        panel.addView(ImageView(this).apply { setImageResource(R.mipmap.ic_launcher); contentDescription = getString(R.string.app_name); scaleType = ImageView.ScaleType.CENTER_INSIDE }, LinearLayout.LayoutParams(-1, dp(72)))
        panel.addView(TextView(this).apply { text = "项目授权已完成"; textSize = 24f; setTextColor(this@GateActivity.text); setTypeface(Typeface.DEFAULT, Typeface.BOLD); gravity = Gravity.CENTER; setPadding(0, dp(16), 0, dp(8)) })
        panel.addView(TextView(this).apply { text = "GitHub 账号：${license.username}\n授权有效期至：${formatDate(license.expiresAt)}"; textSize = 14f; setTextColor(text2); gravity = Gravity.CENTER; setPadding(0, dp(8), 0, dp(20)) })
        panel.addView(TextView(this).apply { text = "✓ 已确认 Star megumiss/NIKKEAutoScript"; textSize = 14f; setTextColor(success); gravity = Gravity.CENTER; setPadding(0, 0, 0, dp(18)) })
        val openSetup = Button(this).apply { text = "打开初始化"; textSize = 14f; isAllCaps = false; setTextColor(Color.WHITE); background = rounded(accent, 10); setOnClickListener { startActivity(Intent(this@GateActivity, SetupActivity::class.java)); finish() } }
        panel.addView(openSetup, LinearLayout.LayoutParams(-1, dp(50)))
        val refresh = Button(this).apply { text = "重新检查授权"; textSize = 13f; isAllCaps = false; setTextColor(accent); background = rounded(Color.rgb(229, 241, 250), 10); setOnClickListener { beginAuthorization() } }
        panel.addView(refresh, LinearLayout.LayoutParams(-1, dp(48)).apply { topMargin = dp(10) })
        content.addView(panel, LinearLayout.LayoutParams(-1, -2))
    }

    private fun handleIntent(intent: Intent?) {
        val data = intent?.data ?: run {
            AccessGate.storedLicense(this)?.let(::renderAuthorized) ?: renderWaiting()
            return
        }
        if (data.scheme != "nkas" || data.host != "auth") return
        val state = data.getQueryParameter("state")
        if (!AccessGate.consumeOAuthState(this, state)) {
            showError("授权回调无效，请重新开始授权")
            return
        }
        val token = data.getQueryParameter("key")
        data.getQueryParameter("error")?.let { errorCode ->
            showError(
                when (errorCode) {
                    "repository_not_starred" -> "当前 GitHub 账号尚未 Star 项目，请完成 Star 后重试"
                    "oauth_cancelled" -> "GitHub 授权已取消"
                    "oauth_not_configured" -> "授权服务尚未配置，请联系项目维护者"
                    else -> "GitHub 授权失败，请稍后重试"
                },
            )
            return
        }
        val license = token?.let { AccessGate.saveLicense(this, it) }
        if (license != null) renderAuthorized(license) else showError("授权密钥无效或已过期，请重新授权")
    }

    private fun beginAuthorization() {
        val state = UUID.randomUUID().toString()
        AccessGate.saveOAuthState(this, state)
        action.isEnabled = false
        status.text = "正在打开 GitHub 授权页面…"
        runCatching { openExternal(GateConfig.authorizationUrl(state).toString()) }
            .onFailure { action.isEnabled = true; showError("无法打开浏览器，请检查系统浏览器") }
    }

    private fun showError(message: String) {
        if (!::status.isInitialized) return
        status.text = message
        status.setTextColor(error)
        if (::action.isInitialized) action.isEnabled = true
    }

    private fun openExternal(url: String) = startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
    private fun formatDate(epoch: Long) = DateTimeFormatter.ofPattern("yyyy-MM-dd").withZone(ZoneId.systemDefault()).format(Instant.ofEpochSecond(epoch))
    private fun rounded(color: Int, radius: Int) = android.graphics.drawable.GradientDrawable().apply { setColor(color); cornerRadius = dp(radius).toFloat(); setStroke(dp(1), border) }
    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
}
