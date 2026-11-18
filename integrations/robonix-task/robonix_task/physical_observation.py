"""Project physical reports into World facts without promoting Node or Control authority."""

from __future__ import annotations

import math
import re
from typing import Any, TypeGuard, cast

SCHEMA = "roboguide.world.robot-physical-observation/v0.1"
ENTITY_SCHEMA = "roboguide.world.physical-entity-reference/v0.1"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


def entity_reference(entity_id: str) -> dict[str, str]:
    """Return only the deployment's explicit entity reference, never infer it from a hostname."""
    if _ID.fullmatch(entity_id) is None:
        raise ValueError("explicit_bounded_physical_entity_required")
    return {"entity_id": entity_id}


def _finite(value: object) -> TypeGuard[int | float]:
    """Admit finite physical scalars while rejecting booleans and overflowing integers."""
    try:
        return (
            isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
        )
    except OverflowError:
        return False


def physical_observation(snapshot: dict[str, Any], entity_id: str) -> dict[str, Any]:
    """Select only cached physical facts and preserve nested stale/unknown distinctions.

    The original chassis observer already computes aggregate age at each local read.
    Node reception does not renew it. Mapping HTTP response age is explicitly unrelated
    to pose measurement time or calibrated localization quality, which remain unknown.
    """
    entity_reference(entity_id)
    posture: dict[str, Any] = {
        "availability": "unknown",
        "reported_posture": None,
        "aggregate_age_ms": None,
        "source_stale_ms": None,
        "sample_received_at_ns": None,
        "individual_measurement_time": None,
        "remaining_at_publisher_ms": 0,
    }
    battery: dict[str, Any] = {
        "availability": "unknown",
        "percent": None,
        "remaining_at_publisher_ms": 0,
    }
    chassis = snapshot.get("chassis")
    if (
        isinstance(chassis, dict)
        and chassis.get("contract_id") == "robonix/primitive/chassis/state"
    ):
        value, age = chassis.get("value"), chassis.get("age_ms")
        value = value if isinstance(value, dict) else {}
        ttl = value.get("stale_ms")
        received = chassis.get("received_at_ns")
        qualified = (
            chassis.get("availability") == "current"
            and value.get("stale") is False
            and _finite(age)
            and age >= 0
            and type(ttl) is int
            and 0 < ttl <= 60000
            and age < min(5000, ttl)
            and type(received) is int
            and received > 0
        )
        posture.update(
            availability="reported_current" if qualified else "unavailable",
            aggregate_age_ms=age if _finite(age) and age >= 0 else None,
            source_stale_ms=ttl if type(ttl) is int else None,
            sample_received_at_ns=received if type(received) is int else None,
            remaining_at_publisher_ms=math.floor(min(5000, cast(int, ttl)) - cast(float, age))
            if qualified
            else 0,
        )
        if qualified and value.get("posture") in {"standing", "down"}:
            posture["reported_posture"] = value["posture"]
        else:
            posture["availability"] = "unavailable"
            posture["remaining_at_publisher_ms"] = 0
        original_battery = value.get("battery")
        percent = original_battery.get("percent") if isinstance(original_battery, dict) else None
        if qualified and _finite(percent) and 0 <= percent <= 100:
            battery = {
                "availability": "reported_current",
                "percent": percent,
                "remaining_at_publisher_ms": math.floor(
                    min(5000, cast(int, ttl)) - cast(float, age)
                ),
            }
    mapping: dict[str, Any] = {
        "response_availability": "unavailable",
        "map_id": None,
        "mode": None,
        "reported_pose": None,
        "response_received_at_ns": None,
        "response_age_ms": None,
        "pose_measurement_time": None,
        "localization_quality": None,
        "map_revision_id": None,
        "localization_verified": False,
    }
    original_mapping = snapshot.get("mapping")
    if isinstance(original_mapping, dict):
        age = original_mapping.get("age_ms")
        value = original_mapping.get("value")
        if (
            original_mapping.get("availability") == "current"
            and _finite(age)
            and 0 <= age <= 10000
            and isinstance(value, dict)
            and value.get("has_map") is True
            and type(original_mapping.get("received_at_ns")) is int
            and original_mapping["received_at_ns"] > 0
        ):
            pose = value.get("pose")
            mapping.update(
                response_availability="reported_current_response",
                map_id=value.get("map_id") if isinstance(value.get("map_id"), str) else None,
                mode=value.get("mode") if isinstance(value.get("mode"), str) else None,
                response_received_at_ns=original_mapping.get("received_at_ns"),
                response_age_ms=age,
            )
            if isinstance(pose, dict) and all(_finite(pose.get(k)) for k in ("x", "y", "theta")):
                mapping["reported_pose"] = {k: pose[k] for k in ("x", "y", "theta")}
    return {
        "schema_version": SCHEMA,
        "physical_entity_id": entity_id,
        "posture": posture,
        "battery": battery,
        "mapping": mapping,
        "damping_state": "not_provided",
        "remote_connection": "not_provided",
        "arrival_verified": False,
        "device_stop_verified": False,
        "shared_frame_verified": False,
        "motion_authorized": False,
        "freshness_basis": (
            "reported physical facts; deduct Controller retention and planning delay before use"
        ),
    }
