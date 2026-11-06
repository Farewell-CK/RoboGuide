"""Validate historical reported-origin content without admitting localization or motion."""

from __future__ import annotations

import hashlib
import json
import math

from mission.models import JSONObject

ORIGIN_SCHEMA = "roboguide.reported-origin/v0.1"
MAX_ORIGIN_BYTES = 64 * 1024


def canonical_origin(content: JSONObject) -> bytes:
    """Encode the provider's immutable JSON representation for exact digest revalidation."""
    return json.dumps(
        content, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode()


def validate_origin(
    content: JSONObject,
    *,
    memory_id: str,
    owner: JSONObject,
    artifact: JSONObject | None,
    source_mission_id: str | None,
    source_execution_id: str | None,
    source_task_ref: JSONObject | None,
) -> None:
    """Recheck origin schema, original Task ownership, finite pose and immutable content hash."""
    fields = {
        "schema_version",
        "node_id",
        "memory_id",
        "captured_at_ns",
        "attempt_id",
        "source_mission_id",
        "source_task_id",
        "source_execution_id",
        "position",
        "mapping_observation",
        "pose_measurement_time",
        "localization_verified",
        "motion_authorized",
    }
    if set(content) != fields or content.get("schema_version") != ORIGIN_SCHEMA:
        raise ValueError("unsupported reported origin fields or generation")
    for key in (
        "node_id",
        "memory_id",
        "attempt_id",
        "source_mission_id",
        "source_task_id",
        "source_execution_id",
    ):
        if not isinstance(content[key], str) or not content[key]:
            raise ValueError("origin identity is missing")
    if (
        content["memory_id"] != memory_id
        or owner
        != {"owner": "node", "node_id": content["node_id"], "local_system_id": "robot-observer"}
        or content["source_mission_id"] != source_mission_id
        or content["source_execution_id"] != source_execution_id
        or source_task_ref
        != {"mission_id": source_mission_id, "task_id": content["source_task_id"]}
    ):
        raise ValueError("origin content and manifest provenance differ")
    if (
        content["motion_authorized"] is not False
        or content["localization_verified"] is not False
        or content["pose_measurement_time"] is not None
        or type(content["captured_at_ns"]) is not int
        or content["captured_at_ns"] <= 0
    ):
        raise ValueError("reported origin cannot grant measurement or motion authority")
    position = content["position"]
    if not isinstance(position, dict) or set(position) != {
        "map_id",
        "x_m",
        "y_m",
        "yaw_rad",
        "map_revision",
        "frame_basis",
    }:
        raise ValueError("origin position shape is unsupported")
    if (
        not isinstance(position["map_id"], str)
        or not position["map_id"]
        or position["map_revision"] is not None
        or position["frame_basis"] != "local Mapping map; shared physical frame unverified"
    ):
        raise ValueError("origin map or frame limitations are missing")
    for key in ("x_m", "y_m", "yaw_rad"):
        value = position[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            raise ValueError("origin pose must contain finite coordinates")
    mapping = content["mapping_observation"]
    if not isinstance(mapping, dict) or mapping.get("availability") != "current":
        raise ValueError("origin lacks original receive-age qualification")
    age = mapping.get("age_ms")
    if isinstance(age, bool) or not isinstance(age, int | float) or not 0 <= age <= 10000:
        raise ValueError("origin was not fresh at capture time")
    value = mapping.get("value")
    if not isinstance(value, dict) or value.get("map_id") != position["map_id"]:
        raise ValueError("origin map differs from the original observation")
    pose = value.get("pose")
    if (
        value.get("has_map") is not True
        or value.get("mode") != "localization"
        or not isinstance(pose, dict)
        or any(
            pose.get(old) != position[new]
            for old, new in (("x", "x_m"), ("y", "y_m"), ("theta", "yaw_rad"))
        )
    ):
        raise ValueError("origin pose differs from the original Mapping observation")
    raw = canonical_origin(content)
    if len(raw) > MAX_ORIGIN_BYTES or artifact != {
        "content_digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "byte_size": len(raw),
    }:
        raise ValueError("origin content digest or byte size differs")
