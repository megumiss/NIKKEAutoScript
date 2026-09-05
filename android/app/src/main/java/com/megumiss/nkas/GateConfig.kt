package com.megumiss.nkas

import android.net.Uri

object GateConfig {
    // Replace this with the deployed Worker URL before publishing the APK.
    const val WORKER_BASE_URL = "https://nkas-license.example.workers.dev"
    const val REPOSITORY_URL = "https://github.com/megumiss/NIKKEAutoScript"
    const val CALLBACK_URI = "nkas://auth/callback"

    // Public half of the RSA key used by the Worker to sign one-year licenses.
    val LICENSE_PUBLIC_KEY_PEM = """
-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAqt+mvxSSyA4rsWro38Q1
NGB3MABuZBL8dkdJDwW3Bd18yaXC8h1EO/JnxKLIa0T1kubuasSECtclYMuP/3sM
QkwdUqLy0YY0LXBW+Tt0kwcpsWaaIU27iUKk6jkQUyaw0FFFE5VxUf/TORMHnxzr
jt8MlKKJYhTT5ODI5WjrXaUQQ7T1z+YSAvGgMqiCpso1GOeb1eosbjOsiAYJOwoW
DYxZ+XdlxVMMJuxPqkuHjZY7+HtBV/P4562mCqmPDivo7h9gd/EQeGFmwWid7jI6
/SkfgvhL+u3j68h7olOVefEX5M8aTxc5eR0AQ5G6VYB/DWboXZ8GnVYJUzM4akR6
mwIDAQAB
-----END PUBLIC KEY-----
""".trimIndent()

    fun authorizationUrl(state: String): Uri = Uri.parse(WORKER_BASE_URL)
        .buildUpon()
        .appendPath("oauth")
        .appendPath("start")
        .appendQueryParameter("state", state)
        .build()
}
