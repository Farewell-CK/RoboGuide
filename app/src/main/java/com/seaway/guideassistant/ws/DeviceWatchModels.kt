package com.seaway.guideassistant.ws

import com.google.gson.annotations.SerializedName

/**
 * 设备监控 watch 服务支持的设备类型取值。
 */
object DeviceType {
    const val GLASSES = "glasses"
    const val CANE = "cane"
    const val ROBOT_DOG = "robot_dog"
    const val PHONE = "phone"
    const val DEPTH_CAMERA = "depth_camera"
}

internal object MessageType {
    const val DEVICE_STATUS = "device_status"
    const val DEVICE_FRAME = "device_frame"
    const val NAVIGATION = "navigation"
}

data class DeviceWatchEnvelope<T>(
    val type: String,
    val timestamp: Long,
    val deviceType: String? = null,
    val data: T
)

data class DeviceStatusData(
    val deviceName: String,
    val status: String, // online / offline / connecting / error
    val battery: Int? = null,
    val signal: Int? = null,
    val reason: String? = null,
    val lastOnlineTime: Long? = null
)

data class DeviceFrameData(
    val mediaType: String = "image",
    val base64: String
)

data class NavigationPhaseData(
    val phase: Int,
    val mode: String, // outdoor / indoor
    val description: String,
    val floor: Int? = null,
    @SerializedName("estimated_time") val estimatedTime: String? = null,
    val distance: String? = null
)

data class NavigationData(
    val intent: String = "navigation",
    @SerializedName("navigation_instruction") val navigationInstruction: String,
    val destination: String,
    @SerializedName("navigation_phases") val navigationPhases: List<NavigationPhaseData>,
    @SerializedName("current_phase") val currentPhase: Int,
    @SerializedName("total_phases") val totalPhases: Int,
    @SerializedName("need_clarification") val needClarification: Boolean,
    @SerializedName("clarification_question") val clarificationQuestion: String? = null,
    val confidence: Float
)
