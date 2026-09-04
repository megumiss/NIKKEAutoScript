package com.megumiss.nkas

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.util.Base64
import java.io.IOException
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

class TermuxBridge(private val context: Context) {
    companion object {
        private const val TERMUX_PACKAGE = "com.termux"
        const val RUN_COMMAND_PERMISSION = "com.termux.permission.RUN_COMMAND"
        private const val RUN_COMMAND = "com.termux.RUN_COMMAND"
        private const val EXTRA_PATH = "com.termux.RUN_COMMAND_PATH"
        private const val EXTRA_ARGUMENTS = "com.termux.RUN_COMMAND_ARGUMENTS"
        private const val EXTRA_BACKGROUND = "com.termux.RUN_COMMAND_BACKGROUND"
        private const val EXTRA_PENDING_INTENT = "com.termux.RUN_COMMAND_PENDING_INTENT"
        private const val TOKEN = "nkas_result_token"
        private val callbacks = ConcurrentHashMap<String, (CommandResult) -> Unit>()

        internal fun deliver(intent: Intent) {
            val token = intent.getStringExtra(TOKEN) ?: return
            val callback = callbacks.remove(token) ?: return
            val bundle = intent.getBundleExtra("result")
            val stdout = bundle?.getString("stdout") ?: intent.getStringExtra("stdout") ?: ""
            val stderr = bundle?.getString("stderr") ?: intent.getStringExtra("stderr") ?: ""
            val code = bundle?.getInt("exitCode", -1) ?: intent.getIntExtra("exitCode", -1)
            val error = bundle?.getString("errmsg") ?: ""
            callback(CommandResult(stdout, if (error.isBlank()) stderr else "$stderr\n$error", code))
        }
    }

    fun isInstalled(): Boolean = try {
        context.packageManager.getApplicationInfo(TERMUX_PACKAGE, 0)
        true
    } catch (_: PackageManager.NameNotFoundException) {
        false
    }

    @Throws(IOException::class)
    fun startBootstrap(onResult: (CommandResult) -> Unit = {}) {
        val script = context.assets.open("bootstrap.sh").use { it.readBytes() }
        val service = context.assets.open("nkas-service.sh").use { it.readBytes() }
        val encoded = Base64.encodeToString(script, Base64.NO_WRAP)
        val encodedService = Base64.encodeToString(service, Base64.NO_WRAP)
        val command = "mkdir -p \$HOME/.nkas; printf '%s\\n' starting > \$HOME/.nkas/state; echo $encodedService | base64 -d > \$HOME/.nkas/nkas-service.sh; chmod 700 \$HOME/.nkas/nkas-service.sh; echo $encoded | base64 -d > \$HOME/.nkas/bootstrap.sh; chmod 700 \$HOME/.nkas/bootstrap.sh; \$HOME/.nkas/bootstrap.sh"
        runCommand(command, onResult)
    }

    fun readBootstrapLog(onResult: (CommandResult) -> Unit) {
        runCommand("printf '%s\\n' '---STATE---'; cat \$HOME/.nkas/state 2>/dev/null || true; printf '%s\\n' '---LOG---'; tail -n 80 \$HOME/.nkas/bootstrap.log 2>/dev/null || true; printf '%s\\n' '---SERVICE---'; tail -n 40 \$HOME/.nkas/nkas-service.log 2>/dev/null || true", onResult)
    }

    fun checkArtifacts(onResult: (CommandResult) -> Unit) {
        val command = """
            check_file() { [ -f "${'$'}1" ] && printf 'yes' || printf 'no'; }
            check_dir() { [ -d "${'$'}1" ] && printf 'yes' || printf 'no'; }
            check_cmds() { command -v git >/dev/null 2>&1 && command -v proot-distro >/dev/null 2>&1 && command -v curl >/dev/null 2>&1 && printf 'yes' || printf 'no'; }
            check_setting() { grep -Eq 'allow-external-apps[[:space:]]*=[[:space:]]*true' "${'$'}HOME/.termux/termux.properties" 2>/dev/null && printf 'yes' || printf 'no'; }
            check_source() { [ -d "${'$'}HOME/NIKKEAutoScript/.git" ] && git -C "${'$'}HOME/NIKKEAutoScript" rev-parse --is-inside-work-tree >/dev/null 2>&1 && printf 'yes' || printf 'no'; }
            check_config() { [ -f "${'$'}HOME/NIKKEAutoScript/config/deploy.yaml" ] && grep -Eq '^[[:space:]]+WebuiHost:[[:space:]]*127\.0\.0\.1([[:space:]]*#.*)?$' "${'$'}HOME/NIKKEAutoScript/config/deploy.yaml" && grep -Eq '^[[:space:]]+WebuiPort:[[:space:]]*12271([[:space:]]*#.*)?$' "${'$'}HOME/NIKKEAutoScript/config/deploy.yaml" && printf 'yes' || printf 'no'; }
            check_container() { [ -d "${'$'}PREFIX/var/lib/proot-distro/containers/nkas/rootfs" ] && proot-distro run -b "${'$'}HOME/NIKKEAutoScript:/app/NIKKEAutoScript" nkas -- /usr/local/bin/python -c 'import uvicorn' >/dev/null 2>&1 && printf 'yes' || printf 'no'; }
            check_service() { curl -fsS --max-time 3 http://127.0.0.1:12271/api/system/status >/dev/null 2>&1 && printf 'yes' || printf 'no'; }
            printf 'termux_setting=%s\n' "${'$'}(check_setting)"
            printf 'tools=%s\n' "${'$'}(check_cmds)"
            printf 'source=%s\n' "${'$'}(check_source)"
            printf 'config=%s\n' "${'$'}(check_config)"
            printf 'container=%s\n' "${'$'}(check_container)"
            printf 'service=%s\n' "${'$'}(check_service)"
        """.trimIndent().replace("\n", ";")
        runCommand(command, onResult)
    }

    private fun runCommand(command: String, onResult: (CommandResult) -> Unit) {
        val token = UUID.randomUUID().toString()
        callbacks[token] = onResult
        val callbackIntent = Intent(context, TermuxResultReceiver::class.java).putExtra(TOKEN, token)
        val pendingIntent = PendingIntent.getBroadcast(context, token.hashCode(), callbackIntent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE)
        val intent = Intent(RUN_COMMAND).apply {
            setClassName(TERMUX_PACKAGE, "com.termux.app.RunCommandService")
            putExtra(EXTRA_PATH, "/data/data/com.termux/files/usr/bin/bash")
            putExtra(EXTRA_ARGUMENTS, arrayOf("-lc", command))
            putExtra(EXTRA_BACKGROUND, true)
            putExtra(EXTRA_PENDING_INTENT, pendingIntent)
            addFlags(Intent.FLAG_INCLUDE_STOPPED_PACKAGES)
        }
        try {
            context.startForegroundService(intent)
        } catch (error: Exception) {
            callbacks.remove(token)
            onResult(CommandResult("", error.message ?: "无法启动 Termux 命令", -1))
        }
    }

    data class CommandResult(val stdout: String, val stderr: String, val exitCode: Int)
}
