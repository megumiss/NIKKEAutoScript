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
import android.provider.Settings
import android.os.Handler
import android.os.Looper
import android.view.View
import android.view.Gravity
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import android.app.AlertDialog
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
    private lateinit var floatingHost: android.widget.FrameLayout
    private var checking = false
    private var destroyed = false
    private var currentPage = "setup"
    private var bootstrapActive = false
    private var artifactChecking = false
    private var expandedLogKey: String? = null
    private var bootstrapStageIndex = -1
    private var initialNoticeShowing = false
    private val artifactState = mutableMapOf<String, Boolean>()
    private val steps = linkedMapOf<String, Step>()

    private val bg = Color.rgb(248, 250, 252)
    private val card = Color.rgb(255, 255, 255)
    private val card2 = Color.rgb(243, 246, 249)
    private val border = Color.rgb(220, 226, 232)
    private val text = Color.rgb(25, 35, 48)
    private val text2 = Color.rgb(92, 105, 120)
    private val accent = Color.rgb(26, 112, 170)
    private val green = Color.rgb(24, 145, 95)
    private val disabled = Color.rgb(210, 216, 223)

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
                "settings" -> { startActivity(Intent(this, SettingsActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)); finish() }
                "about" -> renderAbout()
            }
        }
        val scroll = ScrollView(this).apply { isFillViewport = true }
        content = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(24), dp(24), dp(24), dp(170)) }
        scroll.addView(content)
        built.content.addView(scroll, android.widget.FrameLayout.LayoutParams(-1, -1))
        floatingHost = android.widget.FrameLayout(this).apply { setBackgroundColor(Color.TRANSPARENT); visibility = View.GONE }
        built.content.addView(floatingHost, android.widget.FrameLayout.LayoutParams(-1, dp(78), Gravity.BOTTOM))
        return built.root
    }

    private fun renderSetup() {
        currentPage = "setup"
        floatingHost.visibility = View.VISIBLE
        content.removeAllViews()
        heading("初始化", "准备 Termux、NKAS 服务和本地 Web UI")
        status = TextView(this).apply { textSize = 14f; setTextColor(text2); setPadding(0, 0, 0, dp(16)) }
        content.addView(status)
        section("环境准备")
        step("Termux", "安装官方 Termux v0.118.3", "termux")
        step("Android 外部命令权限", "系统授权：允许 NKAS 调用 Termux", "permission")
        val termuxSettingStep = step("Termux 外部应用开关", "Termux 配置 allow-external-apps=true", "termux_setting")
        manualCommand(termuxSettingStep.wrapper)
        step("无线调试", "开启并检查 Android 无线调试", "wireless")
        section("项目安装")
        step("Termux 工具", "安装 bash、git、python 等依赖", "tools")
        step("NKAS 源码", "下载并更新项目文件", "source")
        step("项目配置", "写入本地设备和 Web UI 配置", "config")
        step("容器", "安装或检查 NKAS 运行容器", "container")
        step("容器服务", "启动本地服务和 Web UI", "service")
        setProjectBlocked()
        action = Button(this).apply { text = "开始初始化"; textSize = 14f; isAllCaps = false; setOnClickListener { onAction() }; elevation = dp(6).toFloat() }
        setActionEnabled(false)
        floatingHost.removeAllViews()
        floatingHost.addView(action, android.widget.FrameLayout.LayoutParams(-1, dp(52)).apply { leftMargin = dp(24); rightMargin = dp(24); topMargin = dp(10) })
    }

    private fun manualCommand(parent: LinearLayout) {
        parent.addView(TextView(this).apply { text = "在 Termux 中执行以下命令，然后完全退出并重新打开 Termux。页面会根据实际配置自动更新状态。"; textSize = 12f; setTextColor(text2); setPadding(0, dp(8), 0, dp(6)) })
        val command = "mkdir -p ~/.termux\necho 'allow-external-apps=true' > ~/.termux/termux.properties"
        parent.addView(TextView(this).apply { text = command; textSize = 12f; setTextColor(accent); setPadding(dp(10), dp(10), dp(10), dp(10)); background = rounded(card2, 6) })
        val actions = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        val open = Button(this).apply { text = "打开 Termux"; isAllCaps = false; textSize = 12f; setOnClickListener { packageManager.getLaunchIntentForPackage("com.termux")?.let { startActivity(it) } } }
        val copy = Button(this).apply { text = "复制命令"; isAllCaps = false; textSize = 12f; setOnClickListener { (getSystemService(CLIPBOARD_SERVICE) as ClipboardManager).setPrimaryClip(ClipData.newPlainText("Termux 命令", command)); text = "已复制" } }
        actions.addView(open, LinearLayout.LayoutParams(0, dp(42), 1f)); actions.addView(copy, LinearLayout.LayoutParams(0, dp(42), 1f).apply { leftMargin = dp(8) })
        parent.addView(actions, LinearLayout.LayoutParams(-1, dp(42)).apply { topMargin = dp(8) })
    }

    private fun heading(main: String, sub: String) {
        content.addView(TextView(this).apply { text = main; textSize = 26f; setTextColor(this@SetupActivity.text); setTypeface(Typeface.DEFAULT, Typeface.BOLD) })
        content.addView(TextView(this).apply { text = sub; textSize = 14f; setTextColor(text2); setPadding(0, dp(6), 0, dp(20) ) })
    }

    private fun section(label: String) { content.addView(TextView(this).apply { text = label.uppercase(); textSize = 11f; setTextColor(text2); setTypeface(Typeface.DEFAULT, Typeface.BOLD); setPadding(0, dp(8), 0, dp(8)) }) }

    private data class Step(val dot: TextView, val state: TextView, val progress: ProgressBar, val key: String, val wrapper: LinearLayout, val log: TextView)
    private fun step(name: String, detail: String, key: String): Step {
        val wrapper = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(14), dp(10), dp(14), dp(10)); background = rounded(card, 10) }
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        val dot = TextView(this).apply { text = "○"; textSize = 22f; setTextColor(text2); gravity = Gravity.CENTER }
        val labels = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        labels.addView(TextView(this).apply { text = name; textSize = 15f; setTextColor(this@SetupActivity.text) })
        labels.addView(TextView(this).apply { text = detail; textSize = 12f; setTextColor(text2); setPadding(0, dp(3), 0, 0) })
        val state = TextView(this).apply { textSize = 12f; setTextColor(text2); gravity = Gravity.CENTER }
        val progress = ProgressBar(this).apply { isIndeterminate = true; visibility = View.GONE }
        val log = TextView(this).apply { textSize = 11f; setTextColor(text2); setPadding(dp(10), dp(8), dp(10), dp(8)); background = rounded(card2, 6); visibility = View.GONE; typeface = Typeface.MONOSPACE; maxLines = 12 }
        row.addView(dot, LinearLayout.LayoutParams(dp(30), dp(30))); row.addView(labels, LinearLayout.LayoutParams(0, -2, 1f)); row.addView(progress, LinearLayout.LayoutParams(dp(28), dp(28))); row.addView(state, LinearLayout.LayoutParams(dp(64), -2))
        wrapper.addView(row); wrapper.addView(log, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(8) })
        val item = Step(dot, state, progress, key, wrapper, log)
        steps[key] = item
        content.addView(wrapper, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(8) })
        return item
    }

    private fun setStep(key: String, done: Boolean, label: String) {
        val info = steps[key] ?: return
        info.dot.text = if (done) "✓" else "○"
        info.dot.setTextColor(if (done) green else text2)
        val running = !done && label == "执行中"
        info.progress.visibility = if (running) View.VISIBLE else View.GONE
        info.state.text = if (running) "" else label
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
        if (currentIndex >= 0 && bootstrapStageIndex > currentIndex) return
        if (currentIndex >= 0) bootstrapStageIndex = currentIndex
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
            setActionEnabled(true)
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
        setActionEnabled(true)
        action.setOnClickListener { onAction() }
        val bridge = TermuxBridge(this)
        val installed = bridge.isInstalled()
        val permission = checkSelfPermission(TermuxBridge.RUN_COMMAND_PERMISSION) == PackageManager.PERMISSION_GRANTED
        val wireless = isWirelessDebugEnabled()
        setStep("termux", installed, if (installed) "已安装" else "待安装")
        setStep("permission", permission, if (permission) "已授权" else "待授权")
        setStep("wireless", wireless, if (wireless) "已开启" else "待开启")
        if (!installed) { setProjectBlocked(); status.text = "未检测到 Termux，请先下载并安装官方版本。"; action.text = "下载 Termux"; setActionEnabled(false); return }
        if (!permission) { setProjectBlocked(); status.text = "Termux 已安装，还需要允许外部命令权限。"; action.text = "授权并继续"; setActionEnabled(false); return }
        if (!wireless) { setProjectBlocked(); status.text = "请先开启 Android 无线调试，完成后再继续项目安装。"; action.text = "打开无线调试设置"; action.setOnClickListener { openWirelessSettings() }; setActionEnabled(false); return }
        if (artifactChecking) return
        artifactChecking = true
        status.text = "正在检查实际产物，不读取上次保存的状态……"
        setActionEnabled(false)
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
        if (bootstrapActive || artifactChecking || checking || initialNoticeShowing) return
        val bridge = TermuxBridge(this)
        if (!bridge.isInstalled()) { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(TERMUX_URL))); return }
        if (checkSelfPermission(TermuxBridge.RUN_COMMAND_PERMISSION) != PackageManager.PERMISSION_GRANTED) { requestPermissions(arrayOf(TermuxBridge.RUN_COMMAND_PERMISSION), RUN_COMMAND_REQUEST); return }
        if (!isWirelessDebugEnabled()) { openWirelessSettings(); return }
        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        if (!prefs.getBoolean(KEY_INITIAL_NOTICE_SHOWN, false)) {
            showInitialNotice()
            return
        }
        beginInitializationCheck()
    }

    private fun showInitialNotice() {
        initialNoticeShowing = true
        setActionEnabled(false)
        AlertDialog.Builder(this)
            .setTitle("初始化前提醒")
            .setMessage("初始化可能需要较长时间。执行期间请保持 NKAS Mobile 始终在前台，并确保网络连接稳定；切换到其他应用或断网可能导致下载失败。")
            .setNegativeButton("取消") { _, _ ->
                initialNoticeShowing = false
                setActionEnabled(true)
            }
            .setPositiveButton("继续初始化") { _, _ ->
                getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit().putBoolean(KEY_INITIAL_NOTICE_SHOWN, true).apply()
                initialNoticeShowing = false
                beginInitializationCheck()
            }
            .setOnCancelListener {
                initialNoticeShowing = false
                setActionEnabled(true)
            }
            .show()
    }

    private fun beginInitializationCheck() {
        val bridge = TermuxBridge(this)
        status.text = "正在重新检查实际产物……"
        setActionEnabled(false)
        artifactChecking = true
        bridge.checkArtifacts { check ->
            handler.post {
                artifactChecking = false
                val output = check.stdout + if (check.stderr.isNotBlank()) "\n[stderr]\n${check.stderr}" else ""
                applyArtifactResults(output, check.exitCode)
                if (artifactState["termux_setting"] == true && (artifactState["service"] != true || SettingsStore.settingsChanged(this@SetupActivity))) startBootstrap()
            }
        }
    }

    private fun isWirelessDebugEnabled(): Boolean = try {
        android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.R && Settings.Global.getInt(contentResolver, "adb_wifi_enabled", 0) == 1
    } catch (_: Settings.SettingNotFoundException) {
        false
    }

    private fun openWirelessSettings() {
        val intent = Intent(Settings.ACTION_APPLICATION_DEVELOPMENT_SETTINGS)
        runCatching { startActivity(intent) }.onFailure { startActivity(Intent(Settings.ACTION_SETTINGS)) }
    }

    private fun setProjectBlocked() {
        listOf("tools", "source", "config", "container", "service").forEach { key ->
            setStep(key, false, "等待环境")
        }
    }

    private fun setActionEnabled(enabled: Boolean) {
        action.isEnabled = enabled
        action.setTextColor(if (enabled) Color.WHITE else text2)
        action.background = rounded(if (enabled) accent else disabled, 12)
    }

    private fun startBootstrap() {
        status.text = "正在恢复初始化，日志会显示在当前步骤中……"; setActionEnabled(false); bootstrapActive = true; bootstrapStageIndex = -1; setStepLog("tools", "正在请求 Termux 恢复执行脚本……", true)
        val result = BootstrapService(this).start { result ->
            handler.post {
                if (result.exitCode != 0) {
                    val output = result.stdout + "\n" + result.stderr
                    if (output.contains("bootstrap already running")) {
                        status.text = "已有初始化任务正在执行，继续等待其完成……"
                        setActionEnabled(false)
                        pollLog()
                        return@post
                    }
                    bootstrapActive = false
                    status.text = "Termux 命令返回失败，详见日志。"
                    setActionEnabled(true)
                    action.text = "重试初始化"
                    pollLogOnce()
                }
            }
        }
        if (result.isFailure) {
            status.text = "Termux 拒绝了外部命令。请确认已设置 allow-external-apps=true，并重启 Termux。"
            setActionEnabled(true)
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
                    setActionEnabled(true)
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
        val wirelessReady = isWirelessDebugEnabled()
        setStep("termux_setting", setting, if (setting) "已检测" else "待设置")
        setStep("wireless", wirelessReady, if (wirelessReady) "已开启" else "待开启")
        setStep("tools", toolsReady, if (toolsReady) "已检测" else "待安装")
        setStep("source", sourceReady, if (sourceReady) "已检测" else "待下载")
        setStep("config", configReady, if (configReady) "已检测" else "待配置")
        setStep("container", containerReady, if (containerReady) "已检测" else "待安装")
        setStep("service", serviceReady, if (serviceReady) "运行中" else "未运行")
        if (exitCode != 0 && raw.isBlank()) {
            status.text = "无法读取 Termux 实际产物：请确认 allow-external-apps=true。"
            action.text = "打开 Termux 设置"
            setActionEnabled(false)
            return
        }
        when {
            !wirelessReady -> { status.text = "请先开启 Android 无线调试，环境准备完成后才能安装项目。"; action.text = "打开无线调试设置"; action.setOnClickListener { openWirelessSettings() }; setActionEnabled(false) }
            !setting -> { status.text = "未检测到 Termux 的 allow-external-apps=true，请执行上方步骤中的命令并重启 Termux。"; action.text = "等待 Termux 设置"; setActionEnabled(false) }
            serviceReady && SettingsStore.settingsChanged(this) -> { status.text = "设置已变更，需要重新应用后才能启动服务。"; action.text = "应用设置并重启"; action.setOnClickListener { onAction() } }
            serviceReady -> { SettingsStore.markApplied(this); status.text = "已检测到 NKAS Web UI 服务，可以打开 UI。"; action.text = "打开 NKAS UI"; action.setOnClickListener { startActivity(Intent(this, MainActivity::class.java)); finish() } }
            else -> { status.text = "实际产物检查完成，可以开始初始化缺失步骤。"; action.text = "开始初始化"; action.setOnClickListener { onAction() } }
        }
        setActionEnabled(wirelessReady && setting)
    }

    private fun renderAbout() { currentPage = "about"; floatingHost.visibility = View.GONE; content.removeAllViews(); heading("关于 NKAS Mobile", "NIKKEAutoScript 的 Android 控制端"); content.addView(TextView(this).apply { text = "应用负责初始化 Termux 环境，并通过本地 Web UI 管理 NKAS。\n\n包名：com.megumiss.nkas.mobile\n版本：0.2.0\n\n不会自动启动 NIKKE 游戏。"; textSize = 14f; setTextColor(text2); setPadding(dp(16), dp(16), dp(16), dp(16)); background = rounded(card, 10) }) }
    override fun onBackPressed() { if (currentPage == "about") { renderSetup(); refreshState() } else super.onBackPressed() }
    private fun rounded(color: Int, radius: Int) = android.graphics.drawable.GradientDrawable().apply { setColor(color); cornerRadius = dp(radius).toFloat(); setStroke(dp(1), border) }
    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()
    override fun onDestroy() { destroyed = true; handler.removeCallbacksAndMessages(null); executor.shutdownNow(); super.onDestroy() }
    companion object {
        private const val TERMUX_URL = "https://github.com/termux/termux-app/releases/latest"
        private const val RUN_COMMAND_REQUEST = 1001
        private const val PREFS_NAME = "nkas_state"
        private const val KEY_INITIAL_NOTICE_SHOWN = "initial_notice_shown"
    }
}
