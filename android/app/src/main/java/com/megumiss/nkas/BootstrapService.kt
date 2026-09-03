package com.megumiss.nkas

import android.content.Context

class BootstrapService(context: Context) {
    private val bridge = TermuxBridge(context)

    fun start(): Result<Unit> = runCatching { bridge.startBootstrap() }
}
