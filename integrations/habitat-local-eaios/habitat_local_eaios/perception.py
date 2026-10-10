"""Read already-returned visual evidence without activating sensors or moving agents."""

from __future__ import annotations

import math
from collections.abc import Mapping
from numbers import Integral
from typing import Any

from .model import CanonicalObservationInvocation, IntegrationError

OBSERVATION_SCHEMA = "roboguide.local-condition-observation/v0.1"
MAX_DETECTED_IDS = 4096


def cached_detection_observations(gym: Any, observations: Any, sensor_uuid: str) -> Any:
    """Read the original Gym wrapper's pre-filter cache, never activate a sensor.

    HabGymWrapper stores each reset/step result in _last_obs before applying its
    policy-only key filter. Inspect at most eight existing wrapper dictionaries;
    avoid forwarded properties and active observation APIs. An absent cache is
    unavailable, not an empty detection or a reason to render a fresh frame.
    """
    current = gym
    seen: set[int] = set()
    for _ in range(8):
        if id(current) in seen:
            break
        seen.add(id(current))
        attributes = vars(current) if hasattr(current, "__dict__") else {}
        cached = attributes.get("_last_obs")
        if isinstance(cached, Mapping) and sensor_uuid in cached:
            return cached
        current = attributes.get("env")
        if current is None:
            break
    return observations


def inspect_perception(environment: Any, agent_ids: tuple[int, ...]) -> dict[int, str]:
    """Verify actual original sensor instances and semantic cameras before advertising readiness.

    This only inspects existing configuration and objects. It does not create a
    sensor, render, call get_observation or ask the simulator for observations.
    A global detector is explicitly global; it is not agent-local evidence.
    """
    sensors = environment.task.sensor_suite.sensors
    result: dict[int, str] = {}
    for agent_id in agent_ids:
        key = f"agent_{agent_id}_detected_objects"
        if key not in sensors:
            key = "detected_objects"
        sensor = sensors.get(key)
        if (
            type(sensor).__name__ != "DetectedObjectsSensor"
            or not isinstance(getattr(sensor, "pixel_threshold", None), (int, float))
            or isinstance(sensor.pixel_threshold, bool)
            or not math.isfinite(sensor.pixel_threshold)
            or sensor.pixel_threshold < 0
        ):
            raise IntegrationError("original visual detection sensor is unavailable")
        simulator_sensors = environment.sim._sensors
        if not any(
            name.startswith(f"agent_{agent_id}_") and "semantic" in name
            for name in simulator_sensors
        ):
            raise IntegrationError("registered agent has no actual semantic camera")
        result[agent_id] = key
    return result


def observe_detection(
    invocation: CanonicalObservationInvocation,
    environment: Any,
    observations: Any,
    agent_id: int,
    sensor_uuid: str,
    steps: int,
    semantic_digest: str,
) -> dict[str, Any]:
    """Bind a true/false/unknown observation to the exact invocation and cached Gym result.

    The original sensor uses simulator object ID plus object_ids_start. Missing
    cached observations remain unknown, including at reset when the Gym facade
    filters non-policy sensors. A negative read still completes an observation;
    it never means the requested condition or benchmark goal was achieved.
    """
    document: dict[str, Any] = {
        "schema_version": OBSERVATION_SCHEMA,
        "invocation": invocation.as_dict(),
        "invocation_digest": "sha256:" + invocation.request_key(),
        "authoritative_semantic_evidence_digest": semantic_digest,
        "agent_id": agent_id,
        "sensor_uuid": sensor_uuid,
        "sensor_scope": "world" if sensor_uuid == "detected_objects" else "agent",
        "simulator_steps": steps,
        "condition": invocation.parameters["expected"],
        "condition_holds": None,
        "status": "unavailable",
        "source": "existing-gym-observation",
    }
    try:
        problem = environment.task.pddl_problem
        entities = [
            entity
            for entity in problem.get_ordered_entities_list()
            if entity.name == invocation.object_ref
        ]
        if len(entities) != 1:
            raise ValueError("entity is not in the admitted world")
        sim_info = problem.sim_info
        local_id = sim_info.search_for_entity(entities[0])
        offset = sim_info.sim.habitat_config.object_ids_start
        if (
            any(
                isinstance(value, bool) or not isinstance(value, Integral)
                for value in (local_id, offset)
            )
            or local_id < 0
            or offset < 0
        ):
            raise ValueError("semantic object index or offset is not a nonnegative integer")
        # Habitat's PDDL lookup returns a scene index, not the simulator's
        # object ID. Read the original mapping without refreshing any sensor.
        mapped_id = sim_info.sim.scene_obj_ids[int(local_id)]
        if isinstance(mapped_id, bool) or not isinstance(mapped_id, Integral) or mapped_id < 0:
            raise ValueError("semantic object identity is not a nonnegative integer")
        object_id = int(mapped_id) + int(offset)
        if not isinstance(observations, Mapping) or sensor_uuid not in observations:
            raise ValueError("cached detection observation is missing")
        value = observations[sensor_uuid]
        if hasattr(value, "tolist"):
            value = value.tolist()
        if (
            not isinstance(value, list)
            or len(value) > MAX_DETECTED_IDS
            or any(type(item) is not int or item < 0 for item in value)
        ):
            raise ValueError("cached detection observation is malformed or oversized")
        document.update(
            status="observed",
            condition_holds=int(object_id) in value,
            simulator_semantic_object_id=int(object_id),
        )
    except Exception as error:  # noqa: BLE001 - a failed read is not invented world truth
        document["unavailable_reason"] = str(error)[:256]
    return document
