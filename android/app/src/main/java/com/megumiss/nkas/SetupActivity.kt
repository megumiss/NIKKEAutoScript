package com.megumiss.nkas

import android.app.Activity
import android.content.Intent
import android.content.ClipData
import android.content.ClipboardManager
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.view.Gravity
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors
import java.util.Locale

class SetupActivity : Activity() {
    private val handler = Handler(Looper.getMainLooper())
    private val executor = Executors.newSingleThreadExecutor()
    private lateinit var content: LinearLayout
    private lateinit var status: TextView
    private lateinit var action: Button
    private var checking = false
    private var destroyed = false
    private var currentPage = "setup"
    private var bootstrapActive = false
    private var artifactChecking = false
    private var expandedLogKey: String? = null
    private val artifactState = mutableMapOf<String, Boolean>()
    private val steps = linkedMapOf<String, Step>()

    private val bg = Color.rgb(13, 17, 23)
    private val card = Color.rgb(21, 26, 34)
    private val card2 = Color.rgb(26, 32, 41)
    private val border = Color.rgb(38, 47, 61)
    private val text = Color.rgb(232, 235, 240)
    private val text2 = Color.rgb(151, 160, 175)
    private val accent = Color.rgb(102, 184, 234)
    private val green = Color.rgb(85, 217, 162)

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        window.statusBarColor = bg
        window.navigationBarColor = bg
        setContentView(buildShell())
        if (intent.getStringExtra("page") == "about") renderAbout() else {
            renderSetup()
            refreshState()
        }
    }

    override fun onResume() {
        super.onResume()
        if (::content.isInitialized && currentPage == "setup") refreshState()
    }

    private fun buildShell(): View {
        val built = DrawerShell.build(this, "setup") { key ->
            when (key) {
                "setup" -> { renderSetup(); refreshState() }
                "ui" -> { startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)); finish() }
                "about" -> renderAbout()
            }
        }
        val scroll = ScrollView(this).apply { isFillViewport = true }
        content = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(24), dp(24), dp(24), dp(28)) }
        scroll.addView(content)
        built.content.addView(scroll, android.widget.FrameLayout.LayoutParams(-1, -1))
        return built.root
    }

    private fun renderSetup() {
        currentPage = "setup"
        content.removeAllViews()
        heading("初始化", "准备 Termux、NKAS 服务和本地 Web UI")
        status = TextView(this).apply { textSize = 14f; setTextColor(text2); setPadding(0, 0, 0, dp(16)) }
        content.addView(status)
        section("执行步骤")
        step("Termux", "安装官方 Termux v0.118.3", "termux")
        step("Android 外部命令权限", "系统授权：允许 NKAS 调用 Termux", "permission")
        step("Termux 外部应用开关", "Termux 配置 allow-external-apps=true", "termux_setting")
        manualCommand()
        step("Termux 工具", "安装 bash、git、python 等依赖", "tools")
        step("NKAS 源码", "下载并更新项目文件", "source")
        step("项目配置", "写入本地设备和 Web UI 配置", "config")
        step("容器", "安装或检查 NKAS 运行容器", "container")
        step("容器服务", "启动本地服务和 Web UI", "service")
        action = Button(this).apply { text = "开始初始化"; textSize = 14f; isAllCaps = false; setTextColor(accent); background = rounded(Color.rgb(28, 54, 72), 10); setOnClickListener { onAction() } }
        content.addView(action, LinearLayout.LayoutParams(-1, dp(48)))
    }

    private fun manualCommand() {
        val box = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(14), dp(12), dp(14), dp(12)); background = rounded(card2, 10) }
        box.addView(TextView(this).apply { text = "Termux 外部命令配置"; textSize = 14f; setTextColor(this@SetupActivity.text); setTypeface(Typeface.DEFAULT, Typeface.BOLD) })
        box.addView(TextView(this).apply { text = "在 Termux 中执行以下命令，然后完全退出并重新打开 Termux："; textSize = 12f; setTextColor(text2); setPadding(0, dp(6), 0, dp(6)) })
        val command = "mkdir -p ~/.termux\necho 'allow-external-apps=true' > ~/.termux/termux.properties"
        box.addView(TextView(this).apply { text = command; textSize = 12f; setTextColor(accent); setPadding(dp(10), dp(10), dp(10), dp(10)); background = rounded(card, 6) })
        val actions = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        val open = Button(this).apply { text = "打开 Termux"; isAllCaps = false; textSize = 12f; setOnClickListener { packageManager.getLaunchIntentForPackage("com.termux")?.let { startActivity(it) } } }
        val copy = Button(this).apply { text = "复制命令"; isAllCaps = false; textSize = 12f; setOnClickListener { (getSystemService(CLIPBOARD_SERVICE) as ClipboardManager).setPrimaryClip(ClipData.newPlainText("Termux 命令", command)); text = "已复制" } }
        val verify = Button(this).apply { text = "我已执行并重启"; isAllCaps = false; textSize = 12f; setOnClickListener {
            refreshState()
        } }
        actions.addView(open, LinearLayout.LayoutParams(0, dp(42), 1f)); actions.addView(copy, LinearLayout.LayoutParams(0, dp(42), 1f).apply { leftMargin = dp(8) }); actions.addView(verify, LinearLayout.LayoutParams(0, dp(42), 1f).apply { leftMargin = dp(8) })
        box.addView(actions, LinearLayout.LayoutParams(-1, dp(42)).apply { topMargin = dp(8) })
        content.addView(box, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(12) })
    }

    private fun heading(main: String, sub: String) {
        content.addView(TextView(this).apply { text = main; textSize = 26f; setTextColor(this@SetupActivity.text); setTypeface(Typeface.DEFAULT, Typeface.BOLD) })
        content.addView(TextView(this).apply { text = sub; textSize = 14f; setTextColor(text2); setPadding(0, dp(6), 0, dp(20) ) })
    }

    private fun section(label: String) { content.addView(TextView(this).apply { text = label.uppercase(); textSize = 11f; setTextColor(text2); setTypeface(Typeface.DEFAULT, Typeface.BOLD); setPadding(0, dp(8), 0, dp(8)) }) }

    private data class Step(val dot: TextView, val state: TextView, val key: String, val wrapper: LinearLayout, val log: TextView)
    private fun step(name: String, detail: String, key: String) {
        val wrapper = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(14), dp(10), dp(14), dp(10)); background = rounded(card, 10) }
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        val dot = TextView(this).apply { text = "○"; textSize = 22f; setTextColor(text2); gravity = Gravity.CENTER }
        val labels = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        labels.addView(TextView(this).apply { text = name; textSize = 15f; setTextColor(this@SetupActivity.text) })
        labels.addView(TextView(this).apply { text = detail; textSize = 12f; setTextColor(text2); setPadding(0, dp(3), 0, 0) })
        val state = TextView(this).apply { textSize = 12f; setTextColor(text2); gravity = Gravity.CENTER }
        val log = TextView(this).apply { textSize = 11f; setTextColor(text2); setPadding(dp(10), dp(8), dp(10), dp(8)); background = rounded(card2, 6); visibility = View.GONE; typeface = Typeface.MONOSPACE; maxLines = 12 }
        row.addView(dot, LinearLayout.LayoutParams(dp(30), dp(30))); row.addView(labels, LinearLayout.LayoutParams(0, -2, 1f)); row.addView(state, LinearLayout.LayoutParams(dp(64), -2))
        wrapper.addView(row); wrapper.addView(log, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(8) })
        val item = Step(dot, state, key, wrapper, log)
        steps[key] = item
        content.addView(wrapper, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(8) })
    }

    private fun setStep(key: String, done: Boolean, label: String) {
        val info = steps[key] ?: return
        info.dot.text = if (done) "✓" else "○"
        info.dot.setTextColor(if (done) green else text2)
        info.state.text = label
        info.state.setTextColor(if (done) green else text2)
        if (done) {
            info.log.visibility = View.GONE
            if (expandedLogKey == key) expandedLogKey = null
        }
    }

    private fun setStepLog(key: String, value: String, expanded: Boolean = true) {
        val info = steps[key] ?: return
        info.log.text = value.takeLast(5000)
        if (expanded && value.isNotBlank()) {
            steps.values.forEach { other ->
                if (other.key != key) other.log.visibility = View.GONE
            }
            expandedLogKey = key
            info.log.visibility = View.VISIBLE
        } else {
            info.log.visibility = View.GONE
            if (expandedLogKey == key) expandedLogKey = null
        }
    }

    private fun applyBootstrapLog(raw: String) {
        if (raw.isBlank()) return
        val state = raw.substringAfter("---STATE---", "").substringBefore("---LOG---").trim().lowercase(Locale.ROOT)
        val log = raw.substringAfter("---LOG---", "").substringBefore("---SERVICE---").trim()
        val service = raw.substringAfter("---SERVICE---", "").trim()
        val mapping = listOf("installing-termux-tools" to "tools", "cloning-nkas" to "source", "creating-config" to "config", "installing-container" to "container", "starting-nkas" to "service")
        var active: String? = null
        val currentIndex = mapping.indexOfFirst { it.first == state }
        mapping.forEach { (stage, key) ->
            if (state == stage) active = key
            val stageIndex = mapping.indexOfFirst { it.first == stage }
            val done = state == "ready" || (currentIndex >= 0 && currentIndex > stageIndex)
            setStep(key, done, if (done) "完成" else if (state == stage) "执行中" else "等待")
        }
        active?.let { setStepLog(it, log.ifBlank { "正在执行……" }, true) }
        if (service.isNotBlank()) setStepLog("service", service, active == "service")
        if (state == "failed") {
            val failed = active ?: mapping.firstOrNull { (stage, _) -> log.contains("stage $stage") }?.second ?: "tools"
            setStep(failed, false, "失败")
            setStepLog(failed, log.ifBlank { raw }, true)
            status.text = "初始化失败，当前步骤日志已展开。"
            action.isEnabled = true
            action.text = "重试当前初始化"
        }
        if (state == "ready") {
            bootstrapActive = false
            status.text = "初始化脚本已结束，正在重新检查实际产物……"
            refreshState()
        }
    }

    private fun refreshState() {
        if (!::status.isInitialized) return
        if (bootstrapActive) return
        val bridge = TermuxBridge(this)
        val installed = bridge.isInstalled()
        val permission = checkSelfPermission(TermuxBridge.RUN_COMMAND_PERMISSION) == PackageManager.PERMISSION_GRANTED
        setStep("termux", installed, if (installed) "已安装" else "待安装")
        setStep("permission", permission, if (permission) "已授权" else "待授权")
        if (!installed) { status.text = "未检测到 Termux，请先下载并安装官方版本。"; action.text = "下载 Termux"; return }
        if (!permission) { status.text = "Termux 已安装，还需要允许外部命令权限。"; action.text = "授权并继续"; return }
        if (artifactChecking) return
        artifactChecking = true
        status.text = "正在检查实际产物，不读取上次保存的状态……"
        action.isEnabled = false
        BootstrapService(this).checkArtifacts { result ->
            handler.post {
                artifactChecking = false
                if (bootstrapActive) return@post
                val output = result.stdout + if (result.stderr.isNotBlank()) "\n[stderr]\n${result.stderr}" else ""
                applyArtifactResults(output, result.exitCode)
            }
        }
    }

    private fun onAction() {
        val bridge = TermuxBridge(this)
        if (!bridge.isInstalled()) { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(TERMUX_URL))); return }
        if (checkSelfPermission(TermuxBridge.RUN_COMMAND_PERMISSION) != PackageManager.PERMISSION_GRANTED) { requestPermissions(arrayOf(TermuxBridge.RUN_COMMAND_PERMISSION), RUN_COMMAND_REQUEST); return }
        status.text = "正在重新检查实际产物……"
        action.isEnabled = false
        artifactChecking = true
        bridge.checkArtifacts { check ->
            handler.post {
                artifactChecking = false
                val output = check.stdout + if (check.stderr.isNotBlank()) "\n[stderr]\n${check.stderr}" else ""
                applyArtifactResults(output, check.exitCode)
                if (artifactState["termux_setting"] == true && artifactState["service"] != true) startBootstrap()
            }
        }
    }

    private fun startBootstrap() {
        status.text = "正在启动初始化脚本，日志会显示在当前步骤中……"; action.isEnabled = false; bootstrapActive = true; setStep("tools", false, "执行中"); setStepLog("tools", "正在请求 Termux 执行脚本……", true)
        val result = BootstrapService(this).start { result ->
            handler.post {
                val output = buildString { append(result.stdout); if (result.stderr.isNotBlank()) append("\n[stderr]\n").append(result.stderr); append("\n[exit=${result.exitCode}]") }
                setStepLog("service", output, result.exitCode != 0)
                if (result.exitCode != 0) {
                    bootstrapActive = false
                    status.text = "Termux 命令返回失败，详见日志。"
                    action.isEnabled = true
                    action.text = "重试初始化"
                }
            }
        }
        if (result.isFailure) {
            status.text = "Termux 拒绝了外部命令。请确认已设置 allow-external-apps=true，并重启 Termux。"
            action.isEnabled = true
            setStep("termux_setting", false, "待设置")
            setStep("tools", false, "未执行")
            return
        }
        handler.postDelayed({ pollBackend() }, 1800)
        handler.postDelayed({ pollLog() }, 700)
    }

    private fun pollBackend() {
        if (destroyed || checking) return
        checking = true
        executor.execute {
            val ready = try { (URL("http://127.0.0.1:12271/api/system/status").openConnection() as HttpURLConnection).apply { connectTimeout = 1500; readTimeout = 1500; requestMethod = "GET" }.responseCode == 200 } catch (_: Exception) { false }
            handler.post {
                if (destroyed) return@post
                checking = false
                if (ready) {
                    if (bootstrapActive) {
                        status.text = "Web UI 已响应，等待初始化脚本完成最后检查……"
                        handler.postDelayed({ pollBackend() }, 3500)
                    } else {
                        bootstrapActive = false
                        refreshState()
                    }
                } else if (bootstrapActive) {
                    status.text = "初始化仍在执行，当前步骤日志会持续更新……"
                    handler.postDelayed({ pollBackend() }, 3500)
                } else {
                    status.text = "服务尚未就绪，请展开失败步骤查看日志后重试。"
                    action.text = "重试初始化"
                    action.isEnabled = true
                    handler.postDelayed({ pollBackend() }, 3500)
                }
            }
        }
    }

    private fun pollLogOnce() {
        BootstrapService(this).readLog { result -> handler.post { applyBootstrapLog(result.stdout + if (result.stderr.isNotBlank()) "\n[stderr]\n${result.stderr}" else "") } }
    }

    private fun pollLog() {
        if (destroyed || !bootstrapActive) return
        BootstrapService(this).readLog { result ->
            handler.post {
                applyBootstrapLog(result.stdout + if (result.stderr.isNotBlank()) "\n[stderr]\n${result.stderr}" else "")
                if (bootstrapActive && !destroyed) handler.postDelayed({ pollLog() }, 1400)
            }
        }
    }

    private fun applyArtifactResults(raw: String, exitCode: Int) {
        val values = raw.lineSequence()
            .mapNotNull { line -> line.trim().split('=', limit = 2).takeIf { it.size == 2 } }
            .associate { it[0] to (it[1] == "yes") }
        artifactState.clear()
        artifactState.putAll(values)
        val setting = values["termux_setting"] == true
        val toolsReady = values["tools"] == true
        val sourceReady = values["source"] == true
        val configReady = values["config"] == true
        val containerReady = values["container"] == true
        val serviceReady = values["service"] == true
        setStep("termux_setting", setting, if (setting) "已检测" else "待设置")
        setStep("tools", toolsReady, if (toolsReady) "已检测" else "待安装")
        setStep("source", sourceReady, if (sourceReady) "已检测" else "待下载")
        setStep("config", configReady, if (configReady) "已检测" else "待配置")
        setStep("container", containerReady, if (containerReady) "已检测" else "待安装")
        setStep("service", serviceReady, if (serviceReady) "运行中" else "未运行")
        if (exitCode != 0 && raw.isBlank()) {
            status.text = "无法读取 Termux 实际产物：请确认 allow-external-apps=true。"
            action.text = "打开 Termux 设置"
            action.isEnabled = true
            return
        }
        when {
            !setting -> { status.text = "未检测到 Termux 的 allow-external-apps=true，请执行步骤中的命令并重启 Termux。"; action.text = "等待 Termux 设置" }
            serviceReady -> { status.text = "已检测到 NKAS Web UI 服务，可以打开 UI。"; action.text = "打开 NKAS UI"; action.setOnClickListener { startActivity(Intent(this, MainActivity::class.java)); finish() } }
            else -> { status.text = "实际产物检查完成，可以开始初始化缺失步骤。"; action.text = "开始初始化"; action.setOnClickListener { onAction() } }
        }
        action.isEnabled = true
    }

    private fun renderAbout() { currentPage = "about"; content.removeAllViews(); heading("关于 NKAS Mobile", "NIKKEAutoScript 的 Android 控制端"); content.addView(TextView(this).apply { text = "应用负责初始化 Termux 环境，并通过本地 Web UI 管理 NKAS。\n\n包名：com.megumiss.nkas.mobile\n版本：0.2.0\n\n不会自动启动 NIKKE 游戏。"; textSize = 14f; setTextColor(text2); setPadding(dp(16), dp(16), dp(16), dp(16)); background = rounded(card, 10) }) }
    override fun onBackPressed() { if (currentPage == "about") { renderSetup(); refreshState() } else super.onBackPressed() }
    private fun rounded(color: Int, radius: Int) = android.graphics.drawable.GradientDrawable().apply { setColor(color); cornerRadius = dp(radius).toFloat(); setStroke(dp(1), border) }
    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()
    override fun onDestroy() { destroyed = true; handler.removeCallbacksAndMessages(null); executor.shutdownNow(); super.onDestroy() }
    companion object { private const val TERMUX_URL = "https://github.com/termux/termux-app/releases/latest"; private const val RUN_COMMAND_REQUEST = 1001 }
}
