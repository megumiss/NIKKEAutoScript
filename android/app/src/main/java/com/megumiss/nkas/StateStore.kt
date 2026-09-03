package com.megumiss.nkas

import android.content.Context

class StateStore(context: Context) {
    private val prefs = context.getSharedPreferences("nkas_state", Context.MODE_PRIVATE)

    var lastError: String
        get() = prefs.getString("last_error", "") ?: ""
        set(value) = prefs.edit().putString("last_error", value).apply()

    var serial: String
        get() = prefs.getString("serial", "") ?: ""
        set(value) = prefs.edit().putString("serial", value).apply()
}
