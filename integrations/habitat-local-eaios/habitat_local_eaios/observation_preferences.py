"""Publish bounded hypothetical observation geometry, never eligibility or world truth."""

from __future__ import annotations

import math
from typing import Any

from .model import COMPOUND_OBSERVATION_FORMAT, IntegrationError
from .observation_optics import OPTICS_FIELDS, validate_optics
from .preassignment_feasibility import preassignment_digest

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
        or source.get("parent_feasibility_digest") != preassignment_digest(parent)
        or source.get("digest")
        != preassignment_digest({key: value for key, value in source.items() if key != "digest"})
    ):
        raise IntegrationError(
            "observation preferences differ from original reset or declared scope"
        )
    targets, records = source["target_entities"], source["records"]
    if (
        not isinstance(targets, dict)
        or len(targets) > 64
        or not isinstance(records, list)
        or len(records) > 512
    ):
        raise IntegrationError("observation preference coverage exceeds its bound")
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
            raise IntegrationError("observation preference target identity is invalid")
        related = [record for record in matrix["records"] if record["destination"] == entity]
        if not related or any(
            record["destination_entity"]["position"] != position for record in related
        ):
            raise IntegrationError("observation preference target differs from reset source")
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
            raise IntegrationError("observation preference record fields are invalid")
        key = (record["node_id"], record["expected"])
        endpoint = endpoints.get(record["node_id"])
        if key not in expected or key in observed or endpoint is None:
            raise IntegrationError("observation preference endpoint/condition coverage is invalid")
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
            raise IntegrationError("observation preference differs from exact loaded sources")
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
                raise IntegrationError("observation preference position is invalid")
        reference, radius = record["reference_height_above_base_m"], record["arrival_tolerance_m"]
        if reference is not None and (
            isinstance(reference, bool)
            or not isinstance(reference, (int, float))
            or not math.isfinite(reference)
            or abs(reference) > 100
        ):
            raise IntegrationError("observation reference model is invalid")
        compound = " AND " in record["expected"]
        if radius is not None and (
            not compound
            or isinstance(radius, bool)
            or not isinstance(radius, (int, float))
            or not math.isfinite(radius)
            or not 0 < radius <= 100
        ):
            raise IntegrationError("observation arrival tolerance is invalid")
        if record["camera"] is not None and (
            not isinstance(record["camera"], dict) or set(record["camera"]) != OPTICS_FIELDS
        ):
            raise IntegrationError("observation calibration fields are invalid")
        count = record["geometric_candidate_count"]
        if (
            count is not None and (type(count) is not int or not 0 <= count <= 32)
        ) or count != geometric_candidates(record):
            raise IntegrationError("observation preference count differs from bounded geometry")
        observed.add(key)
    if observed != expected:
        raise IntegrationError("observation preferences lack complete declared coverage")


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


def attach_observation_preferences(
    matrix: dict[str, Any],
    environment: Any,
    registry: dict[str, Any],
    optics: dict[str, Any],
    formats: dict[int, str | None],
) -> dict[str, Any]:
    """Read only the existing reset and calibrated declarations before any Task dispatch.

    Reference models other than a vertical offset stay unknown. The stencil
    does not assert that the reset base plane exists at a goal or is reachable.
    No negative geometry result is an exclusion or impossibility certificate.
    """
    if matrix.get("schema_version") != "roboguide.deployment-intent-feasibility/v0.6" or matrix.get(
        "digest"
    ) != preassignment_digest({key: value for key, value in matrix.items() if key != "digest"}):
        raise IntegrationError("observation preferences require the exact admitted reset source")
    targets: dict[str, list[float]] = {}
    problem = environment.task.pddl_problem
    for record in matrix["records"]:
        entity = record["destination"]
        position = record["destination_entity"]["position"]
        resolved = problem.get_entity(entity)
        if resolved is None or not problem.sim_info.check_type_matches(
            resolved, "movable_entity_type"
        ):
            continue
        if position is not None:
            if entity in targets and targets[entity] != position:
                raise IntegrationError(
                    "observation target geometry differs across endpoint sources"
                )
            targets[entity] = position
    if len(targets) > 64:
        raise IntegrationError("observation preference target coverage exceeds its bound")
    records = []
    for endpoint in registry["endpoints"]:
        agent = endpoint["agent_id"]
        if "observation.acquire@v1" not in endpoint["operations"]:
            continue
        base = matrix["initial_agent_positions"][str(agent)]
        reference = None
        try:
            robot = environment.sim.get_agent_data(agent).articulated_agent
            actual_base = [float(value) for value in robot.base_pos]
            actual_reference = [float(value) for value in robot.base_transformation.translation]
            if actual_base != base:
                raise IntegrationError(
                    "observation preferences differ from original reset position"
                )
            if all(math.isfinite(value) for value in actual_reference) and all(
                abs(actual_reference[i] - base[i]) < 1e-4 for i in (0, 2)
            ):
                reference = actual_reference[1] - base[1]
        except IntegrationError:
            raise
        except Exception:  # noqa: BLE001 - unsupported geometry stays unknown, never guessed
            pass
        observed = optics.get(str(agent), {})
        camera = observed.get("attributes") if observed.get("status") == "observed" else None
        radius = getattr(problem.sim_info, "robot_at_thresh", None)
        if (
            isinstance(radius, bool)
            or not isinstance(radius, (float, int))
            or not math.isfinite(radius)
            or not 0 < radius <= 100
        ):
            radius = None
        for entity, target in sorted(targets.items()):
            conditions = [f"detected({entity})"]
            if formats[agent] == COMPOUND_OBSERVATION_FORMAT:
                conditions.append(f"detected({entity}) AND at({entity})")
            for expected in conditions:
                record = {
                    "node_id": endpoint["node_id"],
                    "agent_id": agent,
                    "node_config_digest": endpoint["node_config_digest"],
                    "operation": "observation.acquire@v1",
                    "expected": expected,
                    "entity_id": entity,
                    "base_position": base,
                    "target_position": target,
                    "reference_height_above_base_m": reference,
                    "camera": camera,
                    "arrival_tolerance_m": radius if " AND " in expected else None,
                }
                record["geometric_candidate_count"] = geometric_candidates(record)
                records.append(record)
    body = {
        "schema_version": SOURCE_SCHEMA,
        "scope": SCOPE,
        "authority": "deployment-observed-reset-state",
        "parent_feasibility_digest": matrix["digest"],
        "identity": matrix["identity"],
        "frame_id": "scene:" + matrix["identity"]["scene_id"],
        "position_unit": "metres",
        "up_axis": "y",
        "target_entities": targets,
        "candidate_count_per_record": 32,
        "route_visibility_and_physical_success": "unknown",
        "records": records,
    }
    source = {**body, "digest": preassignment_digest(body)}
    result = {key: value for key, value in matrix.items() if key != "digest"}
    result.update(schema_version=FEASIBILITY_SCHEMA, observation_preferences=source)
    return {**result, "digest": preassignment_digest(result)}
