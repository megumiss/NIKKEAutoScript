package com.megumiss.nkas

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.os.Bundle
import android.view.View
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.TextView

class SettingsActivity : Activity() {
    private val bg = Color.rgb(248, 250, 252)
    private val card = Color.rgb(255, 255, 255)
    private val border = Color.rgb(220, 226, 232)
    private val text = Color.rgb(25, 35, 48)
    private val text2 = Color.rgb(92, 105, 120)
    private val accent = Color.rgb(26, 112, 170)
    private lateinit var content: LinearLayout
    private lateinit var aptSpinner: Spinner
    private lateinit var dockerInput: EditText
    private lateinit var status: TextView

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        if (!AccessGate.isAuthorized(this)) {
            startActivity(Intent(this, GateActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP))
            finish()
            return
        }
        window.statusBarColor = bg
        window.navigationBarColor = bg
        setContentView(buildShell())
        render()
    }

    private fun buildShell(): View {
        val built = DrawerShell.build(this, "settings") { key ->
            when (key) {
                "setup" -> { startActivity(Intent(this, SetupActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)); finish() }
                "ui" -> { startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)); finish() }
                "about" -> { startActivity(Intent(this, SetupActivity::class.java).putExtra("page", "about").addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)); finish() }
            }
        }
        val scroll = ScrollView(this).apply { isFillViewport = true }
        content = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(24), dp(24), dp(24), dp(28)) }
        scroll.addView(content)
        built.content.addView(scroll, android.widget.FrameLayout.LayoutParams(-1, -1))
        return built.root
    }

    private fun render() {
        content.removeAllViews()
        heading("设置", "选择初始化时使用的下载源")
        sourceLabel("Termux apt 源", "用于安装 Termux 工具，默认使用国内清华源")
        aptSpinner = spinner(SettingsStore.aptSources)
        aptSpinner.setSelection(SettingsStore.aptSources.indexOfFirst { it.value == SettingsStore.aptSource(this) }.coerceAtLeast(0))
        content.addView(aptSpinner, LinearLayout.LayoutParams(-1, dp(50)).apply { bottomMargin = dp(18) })
        sourceLabel("Docker 镜像", "用于安装 NKAS 容器，默认使用毫秒镜像 docker.1ms.run")
        dockerInput = EditText(this).apply {
            setText(SettingsStore.dockerImage(this@SettingsActivity))
            setTextColor(this@SettingsActivity.text)
            setHintTextColor(text2)
            textSize = 14f
            setSingleLine(true)
            inputType = android.text.InputType.TYPE_CLASS_TEXT
            setPadding(dp(12), 0, dp(12), 0)
            background = rounded(card, 8)
        }
        content.addView(dockerInput, LinearLayout.LayoutParams(-1, dp(50)).apply { bottomMargin = dp(22) })
        val save = Button(this).apply {
            text = "保存设置"
            textSize = 14f
            isAllCaps = false
            setTextColor(accent)
            background = rounded(Color.rgb(229, 241, 250), 10)
            setOnClickListener { saveSettings() }
        }
        content.addView(save, LinearLayout.LayoutParams(-1, dp(48)))
        status = TextView(this).apply { textSize = 13f; setTextColor(text2); setPadding(0, dp(14), 0, 0) }
        content.addView(status)
    }

    private fun sourceLabel(title: String, detail: String) {
        content.addView(TextView(this).apply {
            text = title
            textSize = 15f
            setTextColor(this@SettingsActivity.text)
            setTypeface(Typeface.DEFAULT, Typeface.BOLD)
        })
        content.addView(TextView(this).apply {
            text = detail
            textSize = 12f
            setTextColor(text2)
            setPadding(0, dp(4), 0, dp(8))
        })
    }

    private fun spinner(options: List<SourceChoice>): Spinner {
        val spinner = Spinner(this)
        val adapter = ArrayAdapter(this, android.R.layout.simple_spinner_item, options.map { it.label }).apply {
            setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
        }
        spinner.adapter = adapter
        return spinner
    }

    private fun saveSettings() {
        val apt = SettingsStore.aptSources[aptSpinner.selectedItemPosition].value
        val docker = dockerInput.text.toString().trim()
        if (!docker.matches(Regex("[A-Za-z0-9._/-]+:[A-Za-z0-9._-]+"))) {
            status.text = "Docker 镜像格式不正确，例如：docker.1ms.run/megumiss/nkas:latest"
            return
        }
        getSharedPreferences(SettingsStore.PREFS_NAME, MODE_PRIVATE).edit()
            .putString("apt_source", apt)
            .putString("docker_image", docker)
            .putBoolean("settings_changed", true)
            .apply()
        status.text = "已保存。下次安装或重试时将应用新的源。"
    }

    private fun heading(main: String, sub: String) {
        content.addView(TextView(this).apply { text = main; textSize = 26f; setTextColor(this@SettingsActivity.text); setTypeface(Typeface.DEFAULT, Typeface.BOLD) })
        content.addView(TextView(this).apply { text = sub; textSize = 14f; setTextColor(text2); setPadding(0, dp(6), 0, dp(22)) })
    }

    private fun rounded(color: Int, radius: Int) = android.graphics.drawable.GradientDrawable().apply { setColor(color); cornerRadius = dp(radius).toFloat(); setStroke(dp(1), border) }
    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
}
