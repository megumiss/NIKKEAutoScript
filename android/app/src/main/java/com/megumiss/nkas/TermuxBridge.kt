package com.megumiss.nkas

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.util.Base64
import java.io.IOException

class TermuxBridge(private val context: Context) {
    companion object {
        private const val TERMUX_PACKAGE = "com.termux"
        const val RUN_COMMAND_PERMISSION = "com.termux.permission.RUN_COMMAND"
        private const val RUN_COMMAND = "com.termux.RUN_COMMAND"
        private const val EXTRA_PATH = "com.termux.RUN_COMMAND_PATH"
        private const val EXTRA_ARGUMENTS = "com.termux.RUN_COMMAND_ARGUMENTS"
        private const val EXTRA_BACKGROUND = "com.termux.RUN_COMMAND_BACKGROUND"
        private const val EXTRA_RESULT_BROADCAST = "com.termux.RUN_COMMAND_RESULT_BROADCAST"
    }

    fun isInstalled(): Boolean = try {
        context.packageManager.getApplicationInfo(TERMUX_PACKAGE, 0)
        true
    } catch (_: PackageManager.NameNotFoundException) {
        false
    }

    @Throws(IOException::class)
    fun startBootstrap() {
        val script = context.assets.open("bootstrap.sh").use { it.readBytes() }
        val service = context.assets.open("nkas-service.sh").use { it.readBytes() }
        val encoded = Base64.encodeToString(script, Base64.NO_WRAP)
        val encodedService = Base64.encodeToString(service, Base64.NO_WRAP)
        val command = "mkdir -p \$HOME/.nkas; echo $encodedService | base64 -d > \$HOME/.nkas/nkas-service.sh; chmod 700 \$HOME/.nkas/nkas-service.sh; echo $encoded | base64 -d > \$HOME/.nkas/bootstrap.sh; chmod 700 \$HOME/.nkas/bootstrap.sh; \$HOME/.nkas/bootstrap.sh"
        val intent = Intent(RUN_COMMAND).apply {
            setClassName(TERMUX_PACKAGE, "com.termux.app.RunCommandService")
            putExtra(EXTRA_PATH, "/data/data/com.termux/files/usr/bin/bash")
            putExtra(EXTRA_ARGUMENTS, arrayOf("-lc", command))
            putExtra(EXTRA_BACKGROUND, true)
            putExtra(EXTRA_RESULT_BROADCAST, true)
            addFlags(Intent.FLAG_INCLUDE_STOPPED_PACKAGES)
        }
        context.startForegroundService(intent)
    }
}
