package com.megumiss.nkas

import android.content.Context
import android.util.Base64
import org.json.JSONObject
import java.nio.charset.StandardCharsets
import java.security.KeyFactory
import java.security.Signature
import java.security.spec.X509EncodedKeySpec
import java.time.Instant

object AccessGate {
    data class License(val username: String, val repository: String, val expiresAt: Long)

    private const val PREFS = "nkas_access"
    private const val KEY_LICENSE = "license"
    private const val KEY_STATE = "oauth_state"

    fun storedLicense(context: Context): License? = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        .getString(KEY_LICENSE, null)
        ?.let(::parseAndVerify)

    fun isAuthorized(context: Context): Boolean = storedLicense(context) != null

    fun saveOAuthState(context: Context, state: String) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY_STATE, state).apply()
    }

    fun consumeOAuthState(context: Context, state: String?): Boolean {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val expected = prefs.getString(KEY_STATE, null)
        prefs.edit().remove(KEY_STATE).apply()
        return !state.isNullOrBlank() && state == expected
    }

    fun saveLicense(context: Context, token: String): License? {
        val license = parseAndVerify(token) ?: return null
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY_LICENSE, token).apply()
        return license
    }

    fun clear(context: Context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().clear().apply()
    }

    private fun parseAndVerify(token: String): License? = runCatching {
        val parts = token.split('.')
        require(parts.size == 3)
        val signed = "${parts[0]}.${parts[1]}"
        val signature = Signature.getInstance("SHA256withRSA")
        signature.initVerify(publicKey())
        signature.update(signed.toByteArray(StandardCharsets.UTF_8))
        require(signature.verify(decode(parts[2])))
        val payload = JSONObject(String(decode(parts[1]), StandardCharsets.UTF_8))
        require(payload.optBoolean("starred"))
        require(payload.optString("repo") == "megumiss/NIKKEAutoScript")
        val expiresAt = payload.optLong("exp")
        require(expiresAt > Instant.now().epochSecond)
        License(payload.optString("sub"), payload.optString("repo"), expiresAt)
    }.getOrNull()

    private fun publicKey() = KeyFactory.getInstance("RSA").generatePublic(
        X509EncodedKeySpec(decode(GateConfig.LICENSE_PUBLIC_KEY_PEM
            .replace("-----BEGIN PUBLIC KEY-----", "")
            .replace("-----END PUBLIC KEY-----", "")
            .replace("\\s".toRegex(), ""))),
    )

    private fun decode(value: String): ByteArray = Base64.decode(value, Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)
}
