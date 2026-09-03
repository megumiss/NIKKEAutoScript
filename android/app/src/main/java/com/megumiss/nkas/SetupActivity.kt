package com.megumiss.nkas

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

class SetupActivity : android.app.Activity() {
    private val handler = Handler(Looper.getMainLooper())
    private val executor = Executors.newSingleThreadExecutor()
    private lateinit var status: TextView
    private lateinit var action: Button
    private var checking = false
    private var waitingForPermission = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(buildView())
        if (Build.VERSION.SDK_INT < 30 || !isArm64()) {
            status.text = "当前设备不满足要求：需要 Android 11 及以上的 ARM64 设备。"
            action.isEnabled = false
            return
        }
        refreshState()
    }

    override fun onResume() {
        super.onResume()
        if (::status.isInitialized && Build.VERSION.SDK_INT >= 30 && isArm64()) {
            refreshState()
        }
    }

    private fun buildView(): ScrollView {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 64, 48, 48)
        }
        val title = TextView(this).apply { text = "NKAS Android"; textSize = 28f }
        status = TextView(this).apply { textSize = 16f; setPadding(0, 32, 0, 32) }
        action = Button(this).apply { setOnClickListener { onAction() } }
        box.addView(title)
        box.addView(status)
        box.addView(action)
        return ScrollView(this).apply { addView(box) }
    }

    private fun refreshState() {
        val bridge = TermuxBridge(this)
        if (!bridge.isInstalled()) {
            status.text = "未检测到 Termux。请先安装官方 GitHub 版本，安装后返回此应用继续。"
            action.text = "下载 Termux"
            return
        }
        status.text = "正在检查 NKAS 服务……"
        action.text = "开始初始化"
        pollBackend()
    }

    private fun onAction() {
        if (!TermuxBridge(this).isInstalled()) {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(TERMUX_URL)))
            return
        }
        if (checkSelfPermission(TermuxBridge.RUN_COMMAND_PERMISSION) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            waitingForPermission = true
            status.text = "请允许 NKAS 调用 Termux 执行初始化命令。"
            requestPermissions(arrayOf(TermuxBridge.RUN_COMMAND_PERMISSION), RUN_COMMAND_REQUEST)
            return
        }
        status.text = "正在请求 Termux 执行初始化。请确认 Termux 已允许外部应用执行命令。"
        action.isEnabled = false
        val result = BootstrapService(this).start()
        if (result.isFailure) {
            status.text = "无法启动 Termux：${result.exceptionOrNull()?.message ?: "未知错误"}"
            action.isEnabled = true
            return
        }
        handler.postDelayed({ pollBackend() }, 1500)
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != RUN_COMMAND_REQUEST || !waitingForPermission) return
        waitingForPermission = false
        if (grantResults.firstOrNull() == android.content.pm.PackageManager.PERMISSION_GRANTED) {
            status.text = "Termux 执行权限已获得，请点击开始初始化。"
            action.text = "开始初始化"
            action.isEnabled = true
        } else {
            status.text = "未获得 Termux 执行权限，无法自动初始化。"
            action.isEnabled = true
        }
    }

    private fun pollBackend() {
        if (checking) return
        checking = true
        executor.execute {
            val ready = try {
                val connection = URL("http://127.0.0.1:12271/api/system/status").openConnection() as HttpURLConnection
                connection.connectTimeout = 1500
                connection.readTimeout = 1500
                connection.requestMethod = "GET"
                connection.responseCode == 200
            } catch (_: Exception) {
                false
            }
            handler.post {
                checking = false
                if (ready) {
                    startActivity(Intent(this, MainActivity::class.java))
                    finish()
                } else {
                    if (action.text != "下载 Termux") {
                        status.text = "NKAS 尚未就绪，初始化仍在进行中。"
                        action.text = "重试初始化"
                        action.isEnabled = true
                        handler.postDelayed({ pollBackend() }, 3000)
                    }
                }
            }
        }
    }

    private fun isArm64(): Boolean = Build.SUPPORTED_ABIS.any { it == "arm64-v8a" }

    override fun onDestroy() {
        executor.shutdownNow()
        super.onDestroy()
    }

    companion object {
        private const val TERMUX_URL = "https://github.com/termux/termux-app/releases/latest"
        private const val RUN_COMMAND_REQUEST = 1001
    }
}
