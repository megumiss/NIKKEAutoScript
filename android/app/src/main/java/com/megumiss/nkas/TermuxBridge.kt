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
        val command = "mkdir -p \$HOME/.nkas; echo $encodedService | base64 -d > \$HOME/.nkas/nkas-service.sh; chmod 700 \$HOME/.nkas/nkas-service.sh; echo $encoded | base64 -d > \$HOME/.nkas/bootstrap.sh; chmod 700 \$HOME/.nkas/bootstrap.sh; \$HOME/.nkas/bootstrap.sh"
        runCommand(command, onResult)
    }

    fun readBootstrapLog(onResult: (CommandResult) -> Unit) {
        runCommand("printf '%s\\n' '---STATE---'; cat \$HOME/.nkas/state 2>/dev/null || true; printf '%s\\n' '---LOG---'; tail -n 80 \$HOME/.nkas/bootstrap.log 2>/dev/null || true; printf '%s\\n' '---SERVICE---'; tail -n 40 \$HOME/.nkas/nkas-service.log 2>/dev/null || true", onResult)
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
