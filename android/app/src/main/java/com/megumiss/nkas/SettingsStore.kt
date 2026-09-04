package com.megumiss.nkas

import android.content.Context

data class SourceChoice(val label: String, val value: String)

object SettingsStore {
    const val PREFS_NAME = "nkas_settings"
    const val DEFAULT_APT_SOURCE = "https://mirrors.tuna.tsinghua.edu.cn/termux/termux-main"
    const val DEFAULT_DOCKER_IMAGE = "m.daocloud.io/docker.io/megumiss/nkas:latest"

    val aptSources = listOf(
        SourceChoice("清华 Termux 源（国内）", DEFAULT_APT_SOURCE),
        SourceChoice("阿里云 Termux 源（国内）", "https://mirrors.aliyun.com/termux/termux-main"),
        SourceChoice("官方 Termux 源", "https://packages.termux.dev/apt/termux-main")
    )

    fun aptSource(context: Context): String = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        .getString("apt_source", DEFAULT_APT_SOURCE) ?: DEFAULT_APT_SOURCE

    fun dockerImage(context: Context): String = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        .getString("docker_image", DEFAULT_DOCKER_IMAGE) ?: DEFAULT_DOCKER_IMAGE

    fun settingsChanged(context: Context): Boolean = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        .getBoolean("settings_changed", false)

    fun markApplied(context: Context) = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE).edit()
        .putBoolean("settings_changed", false).apply()
}
