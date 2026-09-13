package com.robonix.client.data.model

/**
 * Robot description + hardware snapshot models for the Vitals "核心可视化"
 * (3D URDF model, topology tree, hardware signals/bodies/power).
 *
 * These mirror the web client's normalized shapes in
 * `src/robonix_client/vitals_transport.py` (normalize_robot_description /
 * vitals_snapshot_to_dict / aggregate_component_health).
 */

data class RobotComponent(
    val id: String = "",
    val localId: String = "",
    val parentId: String = "",
    val label: String = "",
    val type: String = "component",
    val model: String = "",
    val urdfLink: String = "",
    val urdfJoint: String = "",
    val providers: List<String> = emptyList(),
)

data class RobotDimensions(
    val lengthM: Float = 0.7f,
    val widthM: Float = 0.6f,
    val heightM: Float = 1.0f,
)

data class RobotDescription(
    val id: String = "robot",
    val displayName: String = "Robot",
    val family: String = "generic",
    val dimensions: RobotDimensions = RobotDimensions(),
    val components: List<RobotComponent> = emptyList(),
    val renderMode: String = "procedural",
    val urdfXml: String = "",
    val urdfAssetBaseUrl: String = "",
    val summary: String = "",
)

/** One component's aggregated health (signals + bodies, propagated to parents). */
data class ComponentHealthRow(
    val componentId: String = "",
    val health: String = "unknown",
    val visualState: String = "unknown",
    val signalCount: Int = 0,
    val detail: String = "",
)

data class SignalState(
    val key: String = "",
    val health: String = "unknown",
    val status: Int = 0,
    val detail: String = "",
    val observedValue: Float = 0f,
    val referenceValue: Float = 0f,
    val visualState: String = "unknown",
)

data class BodyComponentState(
    val id: String = "",
    val parentId: String = "",
    val name: String = "",
    val kind: String = "",
    val model: String = "",
    val temperature: Float = 0f,
    val errorCode: Int = 0,
    val enabled: Boolean = false,
)

data class BodyState(
    val key: String = "body",
    val model: String = "",
    val health: String = "unknown",
    val status: Int = 0,
    val message: String = "",
    val components: List<BodyComponentState> = emptyList(),
)

data class PowerSummary(
    val socPercent: Float = -1f,
    val voltage: Float = -1f,
    val charging: Boolean = false,
    val remainingSeconds: Long = 0L,
)

data class HardwareSnapshot(
    val timestampNs: Long = 0L,
    val power: PowerSummary? = null,
    val signals: List<SignalState> = emptyList(),
    val bodies: List<BodyState> = emptyList(),
    val componentHealth: List<ComponentHealthRow> = emptyList(),
)
