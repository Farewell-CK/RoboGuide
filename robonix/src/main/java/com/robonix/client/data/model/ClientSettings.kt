package com.robonix.client.data.model

data class ClientSettings(
    val robotHost: String = "100.87.172.93",
    val atlasPort: Int = 50051,
    val liaisonEndpoint: String = "",
    val userId: String = "voice:wheatfox",
    val sessionId: String = "",
    val recordSeconds: Int = 30,
    val language: String = "system",
    val micNodeId: String = "",
    val micDeviceId: String = "",
    val speakerNodeId: String = "",
    val speakerDeviceId: String = "",
    val ttsNodeId: String = "",
    val asrNodeId: String = "",
    val voiceprintNodeId: String = "",
) {
    val atlasEndpoint: String
        get() = if (robotHost.isNotBlank()) "$robotHost:$atlasPort" else ""

    val isConfigured: Boolean
        get() = robotHost.isNotBlank() && atlasPort > 0
}
