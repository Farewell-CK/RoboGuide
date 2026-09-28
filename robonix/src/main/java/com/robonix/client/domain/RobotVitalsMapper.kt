package com.robonix.client.domain

import com.robonix.client.data.model.BodyComponentState
import com.robonix.client.data.model.BodyState
import com.robonix.client.data.model.ComponentHealthRow
import com.robonix.client.data.model.HardwareSnapshot
import com.robonix.client.data.model.PowerSummary
import com.robonix.client.data.model.RobotComponent
import com.robonix.client.data.model.RobotDescription
import com.robonix.client.data.model.RobotDimensions
import com.robonix.client.data.model.SignalState
import com.robonix.proto.vitalsstream.VitalsSnapshot
import org.yaml.snakeyaml.Yaml

/**
 * Pure port of the web client's vitals mapping/aggregation logic
 * (`src/robonix_client/vitals_transport.py`): normalize_robot_description,
 * vitals_snapshot_to_dict and aggregate_component_health. Kept side-effect
 * free so it can be unit-tested without a robot.
 */
object RobotVitalsMapper {
    private val HEALTH_SEVERITY = mapOf(
        "unknown" to 0, "ok" to 1, "stale" to 2, "warn" to 3, "error" to 4,
    )
    private val VISUAL_STATE_SEVERITY = mapOf(
        "unknown" to 0, "ok" to 1, "idle" to 2, "stale" to 3, "warn" to 4, "error" to 5,
    )
    private val SIGNAL_HEALTH_NAMES = mapOf(0 to "ok", 1 to "warn", 2 to "error", 3 to "stale")
    private val BODY_HEALTH_NAMES = mapOf(0 to "ok", 1 to "error", 2 to "error")

    private val yaml = Yaml()

    // ---- safe accessors over SnakeYAML output ----

    private fun asMap(value: Any?): Map<String, Any?> =
        (value as? Map<*, *>)?.entries?.associate { (k, v) -> k.toString() to v } ?: emptyMap()

    private fun asList(value: Any?): List<Any?> = value as? List<*> ?: emptyList()

    private fun str(value: Any?): String = value?.toString() ?: ""

    private fun number(value: Any?, default: Float): Float = when (value) {
        is Number -> value.toFloat()
        is String -> value.toFloatOrNull() ?: default
        else -> default
    }

    private fun label(value: String): String =
        value.replace('_', ' ').replace('-', ' ')
            .split(Regex("\\s+")).filter { it.isNotEmpty() }
            .joinToString(" ") { w -> w.lowercase().replaceFirstChar { it.titlecase() } }

    private fun componentPath(parentId: String, componentId: String): String {
        val value = componentId.trim().trim('/')
        if (value.isEmpty()) return parentId
        if (value == "body" || value.startsWith("body/")) return value
        return "$parentId/$value"
    }

    // ---- robot description ----

    fun normalizeRobotDescription(
        yamlText: String,
        urdfXml: String = "",
        robotIdHint: String = "",
        urdfAssetBaseUrl: String = "",
    ): RobotDescription {
        val raw = try {
            // SnakeYAML's load() is declared <T> T load(String). Without an
            // explicit type argument Kotlin infers T = Void and inserts a
            // `checkcast Void` on the result — which fails at runtime even
            // though the YAML parsed fine ("LinkedHashMap cannot be cast to
            // Void"). Pin the type argument so the map/scalar comes back as-is.
            yaml.load<Any?>(yamlText)
        } catch (e: Exception) {
            throw IllegalArgumentException("Soma YAML could not be parsed (${e.message})", e)
        }
        val document = asMap(raw)
        if (document.isEmpty()) throw IllegalArgumentException("Soma YAML must contain a mapping")

        val robot = asMap(document["robot"])
        val urdf = asMap(document["urdf"])
        val visual = asMap(robot["visual"])
        val robotId = str(robot["id"]).ifBlank { robotIdHint }.ifBlank { "robot" }.trim()
        val displayName = str(robot["display_name"]).ifBlank { label(robotId) }.ifBlank { "Robot" }
        val family = str(robot["family"]).ifBlank { "generic" }.trim().lowercase()
        val dimensions = asMap(robot["dimensions"])

        fun providerIds(exports: Any?): List<String> =
            asList(exports).mapNotNull { exp -> str(asMap(exp)["provider_id"]).ifBlank { null } }

        val components = mutableListOf<RobotComponent>()
        components.add(
            RobotComponent(
                id = "body",
                localId = "body",
                parentId = "",
                label = displayName,
                type = "robot",
                urdfLink = str(urdf["root_link"]),
                providers = providerIds(robot["exports"]),
            ),
        )

        fun appendComponents(items: Any?, parentId: String) {
            for (rawComponent in asList(items)) {
                val component = asMap(rawComponent)
                val localId = str(component["id"]).trim()
                if (localId.isEmpty()) continue
                val componentId = componentPath(parentId, localId)
                components.add(
                    RobotComponent(
                        id = componentId,
                        localId = localId,
                        parentId = parentId,
                        label = str(component["display_name"]).ifBlank { label(localId) },
                        type = str(component["type"]).ifBlank { "component" }.trim().lowercase(),
                        model = str(component["model"]),
                        urdfLink = str(component["urdf_link"]),
                        urdfJoint = str(component["urdf_joint"]),
                        providers = providerIds(component["exports"]),
                    ),
                )
                appendComponents(component["components"], componentId)
            }
        }
        appendComponents(robot["components"], "body")

        val modelUrl = str(visual["model_url"])
            .ifBlank { str(visual["modelUrl"]) }
            .ifBlank { str(robot["model_url"]) }
        val hasUrdfVisuals = urdfXml.isNotBlank() && "<visual" in urdfXml
        val renderMode = when {
            modelUrl.isNotBlank() -> "asset"
            hasUrdfVisuals -> "urdf"
            else -> "procedural"
        }

        return RobotDescription(
            id = robotId,
            displayName = displayName,
            family = family,
            dimensions = RobotDimensions(
                lengthM = number(dimensions["length_m"], 0.7f),
                widthM = number(dimensions["width_m"], 0.6f),
                heightM = number(dimensions["height_m"], 1.0f),
            ),
            components = components,
            renderMode = renderMode,
            urdfXml = urdfXml,
            urdfAssetBaseUrl = urdfAssetBaseUrl,
            summary = str(asMap(document["description"])["summary"]),
        )
    }

    fun fallbackRobotDescription(): RobotDescription = RobotDescription(
        components = listOf(
            RobotComponent(id = "body", localId = "body", label = "Robot", type = "robot"),
        ),
    )

    // ---- hardware snapshot + aggregation ----

    private fun signalVisualState(signal: SignalState): String {
        if (signal.health != "ok") return signal.health
        if (signal.key.endsWith("/torque_enabled") && signal.observedValue < 0.5f) return "idle"
        return signal.health
    }

    fun snapshotToHardware(snapshot: VitalsSnapshot, description: RobotDescription): HardwareSnapshot {
        val signals = snapshot.componentsList.map { sig ->
            SignalState(
                key = sig.name,
                health = SIGNAL_HEALTH_NAMES[sig.health] ?: "unknown",
                status = sig.health,
                detail = sig.detail,
                observedValue = sig.value,
                referenceValue = sig.threshold,
                visualState = "unknown",
            ).let { it.copy(visualState = signalVisualState(it)) }
        }

        val bodies = snapshot.bodiesList.map { body ->
            BodyState(
                key = componentPath("body", body.bodyType),
                model = body.model,
                health = BODY_HEALTH_NAMES[body.state] ?: "unknown",
                status = body.state,
                message = body.message,
                components = body.componentsList.map { c ->
                    BodyComponentState(
                        id = c.id,
                        parentId = c.parentId,
                        name = c.name,
                        kind = c.kind,
                        model = c.model,
                        temperature = c.temperature,
                        errorCode = c.errorCode,
                        enabled = c.enabled,
                    )
                },
            )
        }

        val power = snapshot.power
        val powerSummary = if (power.batteryPercent >= 0f || power.voltage >= 0f) {
            PowerSummary(
                socPercent = power.batteryPercent,
                voltage = power.voltage,
                charging = power.charging,
                remainingSeconds = power.remainingS,
            )
        } else null

        return HardwareSnapshot(
            timestampNs = snapshot.tsNs,
            power = powerSummary,
            signals = signals,
            bodies = bodies,
            componentHealth = aggregateComponentHealth(description, signals, bodies),
        )
    }

    private fun matchesComponent(signalKey: String, componentId: String): Boolean =
        signalKey == componentId ||
            signalKey.startsWith("$componentId/") ||
            signalKey.startsWith("$componentId.") ||
            signalKey.startsWith("$componentId:")

    private fun highestHealth(values: List<String>): String =
        values.maxByOrNull { HEALTH_SEVERITY[it] ?: 0 } ?: "unknown"

    private fun highestVisualState(values: List<String>): String =
        values.maxByOrNull { VISUAL_STATE_SEVERITY[it] ?: 0 } ?: "unknown"

    fun aggregateComponentHealth(
        description: RobotDescription,
        signals: List<SignalState>,
        bodies: List<BodyState>,
    ): List<ComponentHealthRow> {
        val components = description.components.filter { it.id.isNotBlank() }
        val componentIds = components.map { it.id }.sortedByDescending { it.length }

        val directSignals = componentIds.associateWith { mutableListOf<SignalState>() }
        val bodyHealth = mutableMapOf<String, String>()

        for (signal in signals) {
            val match = componentIds.firstOrNull { matchesComponent(signal.key, it) } ?: ""
            if (match.isNotEmpty()) directSignals[match]?.add(signal)
        }
        for (body in bodies) {
            val match = componentIds.firstOrNull { matchesComponent(body.key, it) }
                ?: if (directSignals.containsKey("body")) "body" else ""
            if (match.isNotEmpty()) bodyHealth[match] = body.health
        }

        val rows = LinkedHashMap<String, ComponentHealthRow>()
        for (component in components) {
            val cid = component.id
            val componentSignals = directSignals[cid].orEmpty()
            val directValues = componentSignals.map { it.health }.toMutableList()
            val directVisualValues = componentSignals.map { it.visualState }.toMutableList()
            bodyHealth[cid]?.let {
                directValues.add(it)
                directVisualValues.add(it)
            }
            val worstSignal = componentSignals.maxByOrNull { HEALTH_SEVERITY[it.health] ?: 0 }
            rows[cid] = ComponentHealthRow(
                componentId = cid,
                health = highestHealth(directValues),
                visualState = highestVisualState(directVisualValues),
                signalCount = componentSignals.size,
                detail = worstSignal?.detail ?: "",
            )
        }

        // Propagate the worst child health up to parents (deepest first).
        for (component in components.sortedByDescending { it.id.count { c -> c == '/' } }) {
            val parentId = component.parentId
            if (parentId.isBlank()) continue
            val child = rows[component.id] ?: continue
            val parent = rows[parentId] ?: continue
            if ((HEALTH_SEVERITY[child.health] ?: 0) > (HEALTH_SEVERITY[parent.health] ?: 0)) {
                rows[parentId] = parent.copy(
                    health = child.health,
                    detail = child.detail.ifBlank { parent.detail },
                )
            }
            if ((VISUAL_STATE_SEVERITY[child.visualState] ?: 0) >
                (VISUAL_STATE_SEVERITY[parent.visualState] ?: 0)
            ) {
                rows[parentId] = rows.getValue(parentId).copy(visualState = child.visualState)
            }
        }

        return components.map { rows.getValue(it.id) }
    }
}
