"""Independent archive reader for bounded reset/optics geometry ordering sources.

This consumer deliberately uses only frozen JSON and no Habitat adapter import.
The shared synthetic fixture fences Python/Rust projection drift; no source
hash establishes physical truth or grants a Node eligibility.
"""

from __future__ import annotations

import math
from typing import Any

COMPOUND_OBSERVATION_FORMAT = "detected(entity-ref) [AND at(same-entity-ref)]"
OPTICS_FIELDS = frozenset(
    {
        "camera-model",
        "camera-height-above-base-m",
        "camera-forward-offset-m",
        "camera-forward-elevation-degrees",
        "camera-horizontal-fov-degrees",
        "camera-vertical-fov-degrees",
        "camera-pan-tilt-supported",
    }
)


def _digest(document: dict[str, Any]) -> str:
    """Use the archive's existing exact-float identity without loading a simulator."""
    from .b1_deployment_feasibility import _content_digest

    return _content_digest(document)


def validate_optics(attributes: dict[str, Any]) -> dict[str, Any]:
    """Independently require the complete closed fixed-primary camera model."""
    if (
        set(attributes) != OPTICS_FIELDS
        or attributes["camera-model"] != "fixed-primary-pinhole/v0.1"
        or attributes["camera-pan-tilt-supported"] is not False
    ):
        raise ValueError("observation preference camera declaration is invalid")
    numbers = OPTICS_FIELDS - {"camera-model", "camera-pan-tilt-supported"}
    if any(
        isinstance(attributes[key], bool)
        or not isinstance(attributes[key], (int, float))
        or not math.isfinite(attributes[key])
        for key in numbers
    ):
        raise ValueError("observation preference calibration must be finite numeric data")
    if (
        not 0 < attributes["camera-horizontal-fov-degrees"] < 180
        or not 0 < attributes["camera-vertical-fov-degrees"] < 180
        or not -90 <= attributes["camera-forward-elevation-degrees"] <= 90
        or not 0 <= attributes["camera-height-above-base-m"] <= 100
        or abs(attributes["camera-forward-offset-m"]) > 100
    ):
        raise ValueError("observation preference calibration exceeds supported bounds")
    return attributes


SOURCE_SCHEMA = "roboguide.initial-observation-geometry/v0.1"
FEASIBILITY_SCHEMA = "roboguide.deployment-intent-feasibility/v0.7"
SCOPE = "hypothetical-upright-yaw-on-reset-base-plane"


def validate_preference_source(
    matrix: dict[str, Any], registry: dict[str, Any], readiness: dict[str, Any]
) -> None:
    """Check closed source coverage and loaded optics without making an eligibility claim.

    The reference offset is a deployment observation, not an independently
    authenticated physical fact. Hashes bind its exact original reset and every
    derived count; they cannot prove a simulator or device observation truthful.
    """
    source = matrix.get("observation_preferences")
    fields = {
        "schema_version",
        "scope",
        "authority",
        "parent_feasibility_digest",
        "identity",
        "frame_id",
        "position_unit",
        "up_axis",
        "target_entities",
        "candidate_count_per_record",
        "route_visibility_and_physical_success",
        "records",
        "digest",
    }
    parent = {
        key: value
        for key, value in matrix.items()
        if key not in {"digest", "observation_preferences"}
    }
    parent["schema_version"] = "roboguide.deployment-intent-feasibility/v0.6"
    if (
        matrix.get("schema_version") != FEASIBILITY_SCHEMA
        or not isinstance(source, dict)
        or set(source) != fields
        or source.get("schema_version") != SOURCE_SCHEMA
        or source.get("scope") != SCOPE
        or source.get("authority") != "deployment-observed-reset-state"
        or source.get("identity") != matrix["identity"]
        or source.get("frame_id") != "scene:" + matrix["identity"]["scene_id"]
        or source.get("position_unit") != "metres"
        or source.get("up_axis") != "y"
        or type(source.get("candidate_count_per_record")) is not int
        or source["candidate_count_per_record"] != 32
        or source.get("route_visibility_and_physical_success") != "unknown"
        or source.get("parent_feasibility_digest") != _digest(parent)
        or source.get("digest")
        != _digest({key: value for key, value in source.items() if key != "digest"})
    ):
        raise ValueError("observation preferences differ from original reset or declared scope")
    targets, records = source["target_entities"], source["records"]
    if (
        not isinstance(targets, dict)
        or len(targets) > 64
        or not isinstance(records, list)
        or len(records) > 512
    ):
        raise ValueError("observation preference coverage exceeds its bound")
    endpoints = {record["node_id"]: record for record in registry["endpoints"]}
    formats = {
        record["node_id"]: record.get("condition_format")
        for record in matrix["execution_profile"]["endpoints"]
    }
    expected = set()
    for entity, position in targets.items():
        if (
            not isinstance(entity, str)
            or not entity.strip()
            or any(char in entity for char in "()\n\r")
        ):
            raise ValueError("observation preference target identity is invalid")
        related = [record for record in matrix["records"] if record["destination"] == entity]
        if not related or any(
            record["destination_entity"]["position"] != position for record in related
        ):
            raise ValueError("observation preference target differs from reset source")
        for node, endpoint in endpoints.items():
            if "observation.acquire@v1" in endpoint["operations"]:
                expected.add((node, f"detected({entity})"))
                if formats[node] == COMPOUND_OBSERVATION_FORMAT:
                    expected.add((node, f"detected({entity}) AND at({entity})"))
    observed = set()
    record_fields = {
        "node_id",
        "agent_id",
        "node_config_digest",
        "operation",
        "expected",
        "entity_id",
        "base_position",
        "target_position",
        "reference_height_above_base_m",
        "camera",
        "arrival_tolerance_m",
        "geometric_candidate_count",
    }
    for record in records:
        if not isinstance(record, dict) or set(record) != record_fields:
            raise ValueError("observation preference record fields are invalid")
        key = (record["node_id"], record["expected"])
        endpoint = endpoints.get(record["node_id"])
        if key not in expected or key in observed or endpoint is None:
            raise ValueError("observation preference endpoint/condition coverage is invalid")
        agent, entity = str(endpoint["agent_id"]), record["entity_id"]
        camera_source = readiness.get("fixed_camera_optics", {}).get(agent, {})
        actual_camera = (
            camera_source.get("attributes") if camera_source.get("status") == "observed" else None
        )
        if (
            type(record["agent_id"]) is not int
            or record["agent_id"] != endpoint["agent_id"]
            or record["node_config_digest"] != endpoint["node_config_digest"]
            or record["operation"] != "observation.acquire@v1"
            or record["base_position"] != matrix["initial_agent_positions"][agent]
            or entity not in targets
            or record["target_position"] != targets[entity]
            or record["expected"]
            not in {f"detected({entity})", f"detected({entity}) AND at({entity})"}
            or record["camera"] != actual_camera
        ):
            raise ValueError("observation preference differs from exact loaded sources")
        for name in ("base_position", "target_position"):
            point = record[name]
            if (
                not isinstance(point, list)
                or len(point) != 3
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or abs(value) > 1_000_000
                    for value in point
                )
            ):
                raise ValueError("observation preference position is invalid")
        reference, radius = record["reference_height_above_base_m"], record["arrival_tolerance_m"]
        if reference is not None and (
            isinstance(reference, bool)
            or not isinstance(reference, (int, float))
            or not math.isfinite(reference)
            or abs(reference) > 100
        ):
            raise ValueError("observation reference model is invalid")
        compound = " AND " in record["expected"]
        if radius is not None and (
            not compound
            or isinstance(radius, bool)
            or not isinstance(radius, (int, float))
            or not math.isfinite(radius)
            or not 0 < radius <= 100
        ):
            raise ValueError("observation arrival tolerance is invalid")
        if record["camera"] is not None and (
            not isinstance(record["camera"], dict) or set(record["camera"]) != OPTICS_FIELDS
        ):
            raise ValueError("observation calibration fields are invalid")
        count = record["geometric_candidate_count"]
        if (
            count is not None and (type(count) is not int or not 0 <= count <= 32)
        ) or count != geometric_candidates(record):
            raise ValueError("observation preference count differs from bounded geometry")
        observed.add(key)
    if observed != expected:
        raise ValueError("observation preferences lack complete declared coverage")


def preference_declaration() -> dict[str, Any]:
    """Disclose a bounded ordering hint independently from actual eligibility and success."""
    return {
        "profile": SOURCE_SCHEMA,
        "scope": SCOPE,
        "candidates_per_record": 32,
        "influence": "first-mission-current-eligible-candidate-order",
        "missing_candidate": "unknown",
        "node_exclusion": False,
        "route_visibility_and_physical_success": "unknown",
    }


def geometric_candidates(record: dict[str, Any]) -> int | None:
    """Count a fixed 32-stance stencil; a miss or missing model remains unknown.

    No map, visibility, detector or physical motion is queried. The witness is
    only a geometric possibility in this explicitly limited hypothetical model.
    """
    camera = record["camera"]
    reference = record["reference_height_above_base_m"]
    radius = record["arrival_tolerance_m"]
    compound = " AND " in record["expected"]
    if camera is None or reference is None or (compound and radius is None):
        return None
    camera = validate_optics(camera)
    if not camera:
        return None
    base, target = record["base_position"], record["target_position"]
    extent = radius if compound else 2.0
    pitch = math.radians(camera["camera-forward-elevation-degrees"])
    tangent = math.tan(math.radians(camera["camera-horizontal-fov-degrees"]) / 2)
    vertical_tangent = math.tan(math.radians(camera["camera-vertical-fov-degrees"]) / 2)
    count = 0
    for fraction in (0.25, 0.5, 0.75, 1.0):
        for direction in range(8):
            angle = direction * math.pi / 4
            point = (
                target[0] + extent * fraction * math.cos(angle),
                base[1],
                target[2] + extent * fraction * math.sin(angle),
            )
            if (
                compound
                and math.dist((point[0], point[1] + reference, point[2]), target) > radius + 1e-7
            ):
                continue
            heading = math.atan2(-(target[2] - point[2]), target[0] - point[0])
            dx, dz = target[0] - point[0], target[2] - point[2]
            forward = (
                dx * math.cos(heading) - dz * math.sin(heading) - camera["camera-forward-offset-m"]
            )
            side = dx * math.sin(heading) + dz * math.cos(heading)
            height = target[1] - point[1] - camera["camera-height-above-base-m"]
            depth = forward * math.cos(pitch) + height * math.sin(pitch)
            vertical = height * math.cos(pitch) - forward * math.sin(pitch)
            if (
                depth > 0
                and abs(side) <= depth * tangent + 1e-7
                and abs(vertical) <= depth * vertical_tangent + 1e-7
            ):
                count += 1
    return count
