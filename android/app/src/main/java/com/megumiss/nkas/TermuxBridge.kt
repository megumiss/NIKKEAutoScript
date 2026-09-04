package com.megumiss.nkas

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.util.Base64
import java.io.IOException
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import android.os.Handler
import android.os.Looper
import android.util.Log

class TermuxBridge(private val context: Context) {
    companion object {
        private const val TAG = "NkasTermuxBridge"
        private const val TERMUX_PACKAGE = "com.termux"
        const val RUN_COMMAND_PERMISSION = "com.termux.permission.RUN_COMMAND"
        private const val RUN_COMMAND = "com.termux.RUN_COMMAND"
        private const val EXTRA_PATH = "com.termux.RUN_COMMAND_PATH"
        private const val EXTRA_ARGUMENTS = "com.termux.RUN_COMMAND_ARGUMENTS"
        private const val EXTRA_BACKGROUND = "com.termux.RUN_COMMAND_BACKGROUND"
        private const val EXTRA_PENDING_INTENT = "com.termux.RUN_COMMAND_PENDING_INTENT"
        private const val TOKEN = "nkas_result_token"
        private const val COMMAND_TIMEOUT_MS = 12_000L
        private val callbacks = ConcurrentHashMap<String, (CommandResult) -> Unit>()
        private val timeoutHandler = Handler(Looper.getMainLooper())

        internal fun deliver(intent: Intent) {
            val token = intent.getStringExtra(TOKEN) ?: return
            val callback = callbacks.remove(token) ?: return
            val bundle = intent.getBundleExtra("result")
            val stdout = bundle?.getString("stdout") ?: intent.getStringExtra("stdout") ?: ""
            val stderr = bundle?.getString("stderr") ?: intent.getStringExtra("stderr") ?: ""
            val code = bundle?.getInt("exitCode", -1) ?: intent.getIntExtra("exitCode", -1)
            val error = bundle?.getString("errmsg") ?: ""
            Log.i(TAG, "result token=$token exitCode=$code stdout=${stdout.take(200)} stderr=${stderr.take(200)} errmsg=${error.take(200)}")
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
        val settings = "NKAS_APT_SOURCE=${SettingsStore.aptSource(context)}\nNKAS_DOCKER_IMAGE=${SettingsStore.dockerImage(context)}\n"
        val encoded = Base64.encodeToString(script, Base64.NO_WRAP)
        val encodedService = Base64.encodeToString(service, Base64.NO_WRAP)
        val encodedSettings = Base64.encodeToString(settings.toByteArray(), Base64.NO_WRAP)
        val command = "mkdir -p \$HOME/.nkas; echo $encodedSettings | base64 -d > \$HOME/.nkas/settings.env; echo $encodedService | base64 -d > \$HOME/.nkas/nkas-service.sh; chmod 700 \$HOME/.nkas/nkas-service.sh; echo $encoded | base64 -d > \$HOME/.nkas/bootstrap.sh; chmod 700 \$HOME/.nkas/bootstrap.sh; \$HOME/.nkas/bootstrap.sh"
        runCommand(command, onResult)
    }

    fun readBootstrapLog(onResult: (CommandResult) -> Unit) {
        runCommand("printf '%s\\n' '---STATE---'; cat \$HOME/.nkas/state 2>/dev/null || true; printf '%s\\n' '---LOG---'; tail -n 80 \$HOME/.nkas/bootstrap.log 2>/dev/null || true; printf '%s\\n' '---SERVICE---'; tail -n 40 \$HOME/.nkas/nkas-service.log 2>/dev/null || true", onResult)
    }

    fun checkArtifacts(onResult: (CommandResult) -> Unit) {
        val expectedImage = SettingsStore.dockerImage(context).replace("'", "")
        val stableCommand = "printf 'termux_setting='; if [ -f \"${'$'}HOME/.termux/termux.properties\" ] && grep -Eq '^[[:space:]]*allow-external-apps[[:space:]]*=[[:space:]]*true[[:space:]]*${'$'}' \"${'$'}HOME/.termux/termux.properties\"; then printf 'yes'; else printf 'no'; fi; printf '\\n'; printf 'tools='; if command -v git >/dev/null 2>&1 && command -v proot-distro >/dev/null 2>&1 && command -v curl >/dev/null 2>&1; then printf 'yes'; else printf 'no'; fi; printf '\\n'; printf 'source='; if [ -d \"${'$'}HOME/NIKKEAutoScript/.git\" ] && git -C \"${'$'}HOME/NIKKEAutoScript\" rev-parse --is-inside-work-tree >/dev/null 2>&1; then printf 'yes'; else printf 'no'; fi; printf '\\n'; printf 'config='; if [ -f \"${'$'}HOME/NIKKEAutoScript/config/nkas.json\" ]; then printf 'yes'; else printf 'no'; fi; printf '\\n'; printf 'container='; if [ -d \"${'$'}PREFIX/var/lib/proot-distro/containers/nkas/rootfs\" ] && [ -x \"${'$'}PREFIX/var/lib/proot-distro/containers/nkas/rootfs/usr/local/bin/python\" ] && [ \"${'$'}(sed -n 's/^NKAS_DOCKER_IMAGE=//p' \"${'$'}HOME/.nkas/settings.env\" 2>/dev/null)\" = '$expectedImage' ]; then printf 'yes'; else printf 'no'; fi; printf '\\n'; printf 'service='; if curl -fsS --max-time 3 http://127.0.0.1:12271/api/system/status >/dev/null 2>&1; then printf 'yes'; else printf 'no'; fi; printf '\\n'"
        runCommand(stableCommand, onResult)
        return
        val command = """
            check_file() { [ -f "${'$'}1" ] && printf 'yes' || printf 'no'; }
            check_dir() { [ -d "${'$'}1" ] && printf 'yes' || printf 'no'; }
            check_cmds() { command -v git >/dev/null 2>&1 && command -v proot-distro >/dev/null 2>&1 && command -v curl >/dev/null 2>&1 && printf 'yes' || printf 'no'; }
            check_source() { [ -d "${'$'}HOME/NIKKEAutoScript/.git" ] && git -C "${'$'}HOME/NIKKEAutoScript" rev-parse --is-inside-work-tree >/dev/null 2>&1 && printf 'yes' || printf 'no'; }
            check_config() { [ -f "${'$'}HOME/NIKKEAutoScript/config/deploy.yaml" ] && grep -Eq '^[[:space:]]+WebuiHost:[[:space:]]*127\.0\.0\.1([[:space:]]*#.*)?$' "${'$'}HOME/NIKKEAutoScript/config/deploy.yaml" && grep -Eq '^[[:space:]]+WebuiPort:[[:space:]]*12271([[:space:]]*#.*)?$' "${'$'}HOME/NIKKEAutoScript/config/deploy.yaml" && printf 'yes' || printf 'no'; }
            check_container() {
                local rootfs="${'$'}PREFIX/var/lib/proot-distro/containers/nkas/rootfs"
                [ -d "${'$'}rootfs" ] &&
                [ -x "${'$'}rootfs/usr/local/bin/python" ] &&
                [ "${'$'}(sed -n 's/^NKAS_DOCKER_IMAGE=//p' "${'$'}HOME/.nkas/settings.env" 2>/dev/null)" = '$expectedImage' ] &&
                printf 'yes' || printf 'no'
            }
            check_service() { curl -fsS --max-time 3 http://127.0.0.1:12271/api/system/status >/dev/null 2>&1 && printf 'yes' || printf 'no'; }
            printf 'termux_setting=yes\n'
            printf 'tools=%s\n' "${'$'}(check_cmds)"
            printf 'source=%s\n' "${'$'}(check_source)"
            printf 'config=%s\n' "${'$'}(check_config)"
            printf 'container=%s\n' "${'$'}(check_container)"
            printf 'service=%s\n' "${'$'}(check_service)"
        """.trimIndent().replace("\n", ";")
        runCommand(command, onResult)
    }

    private fun runCommand(
        command: String,
        onResult: (CommandResult) -> Unit,
    ) {
        val token = UUID.randomUUID().toString()
        Log.i(TAG, "send token=$token command=${command.take(240)}")
        callbacks[token] = onResult
        timeoutHandler.postDelayed({
            val callback = callbacks.remove(token) ?: return@postDelayed
            Log.w(TAG, "timeout token=$token")
            callback(CommandResult("", "Termux 外部命令等待超时，请确认 Termux 已完全重启且 allow-external-apps=true。", -2))
        }, COMMAND_TIMEOUT_MS)
        val callbackIntent = Intent(context, TermuxResultReceiver::class.java).putExtra(TOKEN, token)
        val pendingIntent = PendingIntent.getBroadcast(context, token.hashCode(), callbackIntent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_ONE_SHOT or PendingIntent.FLAG_MUTABLE)
        val intent = Intent(RUN_COMMAND).apply {
            setClassName(TERMUX_PACKAGE, "com.termux.app.RunCommandService")
            putExtra(EXTRA_PATH, "/data/data/com.termux/files/usr/bin/bash")
            putExtra(EXTRA_ARGUMENTS, arrayOf("-lc", command))
            putExtra(EXTRA_BACKGROUND, true)
            putExtra(EXTRA_PENDING_INTENT, pendingIntent)
            putExtra("com.termux.RUN_COMMAND_COMMAND_LABEL", "NKAS Mobile")
            addFlags(Intent.FLAG_INCLUDE_STOPPED_PACKAGES)
        }
        try {
            context.startForegroundService(intent)
            Log.i(TAG, "started token=$token")
        } catch (error: Exception) {
            callbacks.remove(token)
            Log.e(TAG, "start failed token=$token", error)
            onResult(CommandResult("", error.message ?: "无法启动 Termux 命令", -1))
        }
    }

    data class CommandResult(val stdout: String, val stderr: String, val exitCode: Int)
}
