package com.robonix.client.domain

import com.robonix.client.data.local.SettingsStore
import com.robonix.client.data.model.ClientSettings
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class SettingsRepository @Inject constructor(
    private val settingsStore: SettingsStore,
) {
    fun observeSettings(): Flow<ClientSettings> = settingsStore.settings.map { map ->
        ClientSettings(
            robotHost = map["robotHost"] as? String ?: "",
            atlasPort = map["atlasPort"] as? Int ?: 50051,
            liaisonEndpoint = map["liaisonEndpoint"] as? String ?: "",
            userId = map["userId"] as? String ?: "",
            sessionId = map["sessionId"] as? String ?: "",
            recordSeconds = map["recordSeconds"] as? Int ?: 30,
            language = map["language"] as? String ?: "",
            micNodeId = map["micNodeId"] as? String ?: "",
            micDeviceId = map["micDeviceId"] as? String ?: "",
            speakerNodeId = map["speakerNodeId"] as? String ?: "",
            speakerDeviceId = map["speakerDeviceId"] as? String ?: "",
            ttsNodeId = map["ttsNodeId"] as? String ?: "",
            asrNodeId = map["asrNodeId"] as? String ?: "",
            voiceprintNodeId = map["voiceprintNodeId"] as? String ?: "",
        )
    }

    suspend fun saveSettings(settings: ClientSettings) {
        settingsStore.saveString("robot_host", settings.robotHost)
        settingsStore.saveInt("atlas_port", settings.atlasPort)
        settingsStore.saveString("liaison_endpoint", settings.liaisonEndpoint)
        settingsStore.saveString("user_id", settings.userId)
        settingsStore.saveString("session_id", settings.sessionId)
        settingsStore.saveInt("record_seconds", settings.recordSeconds)
        settingsStore.saveString("language", settings.language)
        settingsStore.saveString("mic_node_id", settings.micNodeId)
        settingsStore.saveString("mic_device_id", settings.micDeviceId)
        settingsStore.saveString("speaker_node_id", settings.speakerNodeId)
        settingsStore.saveString("speaker_device_id", settings.speakerDeviceId)
        settingsStore.saveString("tts_node_id", settings.ttsNodeId)
        settingsStore.saveString("asr_node_id", settings.asrNodeId)
        settingsStore.saveString("voiceprint_node_id", settings.voiceprintNodeId)
    }
}
