package com.seaway.guideassistant.naviindoor.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Android Keystore protects login tokens. Connection fields can migrate from Flutter. */
class NaviPreferences(context: Context): SessionStore {
    private val prefs = context.getSharedPreferences("navi_native", Context.MODE_PRIVATE)
    private val old = context.getSharedPreferences("FlutterSharedPreferences", Context.MODE_PRIVATE)
    var connection: Connection
        get() = Connection(prefs.getString("vision", null) ?: old.getString("flutter.VISION_SERVER_URL", "")!!,
            prefs.getString("database", null) ?: old.getString("flutter.SUPABASE_URL", "")!!,
            prefs.getString("key", null) ?: old.getString("flutter.SUPABASE_ANON_KEY", "")!!)
        set(value) {
            if (value.database.trimEnd('/') != connection.database.trimEnd('/') || value.publicKey != connection.publicKey) session = null
            prefs.edit().putString("vision", value.vision.trimEnd('/')).putString("database", value.database.trimEnd('/'))
                .putString("key", value.publicKey).apply()
        }
    var voice: Boolean
        get() = prefs.getBoolean("voice", true)
        set(value) { prefs.edit().putBoolean("voice", value).apply() }
    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey("navi_native_session", null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder("navi_native_session", KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        }.generateKey()
    }
    override var session: AuthSession?
        @Synchronized get() {
            val text = prefs.getString("session", null) ?: return null
            return runCatching {
                val parts = text.split('.')
                val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply {
                    init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, Base64.decode(parts[0], Base64.NO_WRAP)))
                }
                NaviJson.gson.fromJson(cipher.doFinal(Base64.decode(parts[1], Base64.NO_WRAP)).toString(Charsets.UTF_8), AuthSession::class.java)
            }.getOrElse { prefs.edit().remove("session").apply(); null }
        }
        @Synchronized set(value) {
            if (value == null) { prefs.edit().remove("session").apply(); return }
            val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key()) }
            val encoded = Base64.encodeToString(cipher.iv, Base64.NO_WRAP) + "." +
                Base64.encodeToString(cipher.doFinal(NaviJson.gson.toJson(value).toByteArray(Charsets.UTF_8)), Base64.NO_WRAP)
            prefs.edit().putString("session", encoded).apply()
        }
}
