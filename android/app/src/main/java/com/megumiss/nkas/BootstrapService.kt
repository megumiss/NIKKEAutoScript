package com.megumiss.nkas

import android.content.Context

class BootstrapService(context: Context) {
    private val bridge = TermuxBridge(context)

    fun start(onResult: (TermuxBridge.CommandResult) -> Unit = {}): Result<Unit> = runCatching { bridge.startBootstrap(onResult) }

    fun readLog(onResult: (TermuxBridge.CommandResult) -> Unit) = bridge.readBootstrapLog(onResult)
}
