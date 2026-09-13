package com.robonix.client.data.local

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import javax.inject.Inject
import javax.inject.Singleton

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore("robonix_settings")

@Singleton
class SettingsStore @Inject constructor(
    private val context: Context,
) {
    companion object {
        private val KEY_ROBOT_HOST = stringPreferencesKey("robot_host")
        private val KEY_ATLAS_PORT = intPreferencesKey("atlas_port")
        private val KEY_LIAISON_ENDPOINT = stringPreferencesKey("liaison_endpoint")
        private val KEY_USER_ID = stringPreferencesKey("user_id")
        private val KEY_SESSION_ID = stringPreferencesKey("session_id")
        private val KEY_RECORD_SECONDS = intPreferencesKey("record_seconds")
        private val KEY_LANGUAGE = stringPreferencesKey("language")
        private val KEY_MIC_NODE_ID = stringPreferencesKey("mic_node_id")
        private val KEY_MIC_DEVICE_ID = stringPreferencesKey("mic_device_id")
        private val KEY_SPEAKER_NODE_ID = stringPreferencesKey("speaker_node_id")
        private val KEY_SPEAKER_DEVICE_ID = stringPreferencesKey("speaker_device_id")
        private val KEY_TTS_NODE_ID = stringPreferencesKey("tts_node_id")
        private val KEY_ASR_NODE_ID = stringPreferencesKey("asr_node_id")
        private val KEY_VOICEPRINT_NODE_ID = stringPreferencesKey("voiceprint_node_id")
        private val KEY_HANDSFREE_ENABLED = booleanPreferencesKey("handsfree_enabled")
    }

    val settings: Flow<Map<String, Any>> = context.dataStore.data.map { prefs ->
        mapOf(
            "robotHost" to (prefs[KEY_ROBOT_HOST] ?: "100.87.172.93"),
            "atlasPort" to (prefs[KEY_ATLAS_PORT] ?: 50051),
            "liaisonEndpoint" to (prefs[KEY_LIAISON_ENDPOINT] ?: ""),
            "userId" to (prefs[KEY_USER_ID] ?: "voice:wheatfox"),
            "sessionId" to (prefs[KEY_SESSION_ID] ?: ""),
            "recordSeconds" to (prefs[KEY_RECORD_SECONDS] ?: 30),
            "language" to (prefs[KEY_LANGUAGE] ?: "system"),
            "micNodeId" to (prefs[KEY_MIC_NODE_ID] ?: ""),
            "micDeviceId" to (prefs[KEY_MIC_DEVICE_ID] ?: ""),
            "speakerNodeId" to (prefs[KEY_SPEAKER_NODE_ID] ?: ""),
            "speakerDeviceId" to (prefs[KEY_SPEAKER_DEVICE_ID] ?: ""),
            "ttsNodeId" to (prefs[KEY_TTS_NODE_ID] ?: ""),
            "asrNodeId" to (prefs[KEY_ASR_NODE_ID] ?: ""),
            "voiceprintNodeId" to (prefs[KEY_VOICEPRINT_NODE_ID] ?: ""),
            "handsfreeEnabled" to (prefs[KEY_HANDSFREE_ENABLED] ?: false),
        )
    }

    suspend fun saveString(key: String, value: String) {
        context.dataStore.edit { prefs ->
            prefs[stringPreferencesKey(key)] = value
        }
    }

    suspend fun saveInt(key: String, value: Int) {
        context.dataStore.edit { prefs ->
            prefs[intPreferencesKey(key)] = value
        }
    }

    suspend fun saveBoolean(key: String, value: Boolean) {
        context.dataStore.edit { prefs ->
            prefs[booleanPreferencesKey(key)] = value
        }
    }
}
