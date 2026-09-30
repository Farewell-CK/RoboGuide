"""Validate diagnostic reset-route evidence without changing B1 admission.

This module reads archived JSON only. It never loads Habitat, queries a route,
filters a Node, or interprets a bounded miss as physical impossibility.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

from roboguide_eval.b1_deployment_feasibility import (
    _content_digest,
    preflight_deployment_feasibility,
)
from roboguide_eval.b1_provenance import load_document

_SCHEMA = "roboguide.deployment-reset-route-support/v0.1"
_PROBE = {
    "revision": "goal-region-initial-candidates/v0.1",
    "scope": "reset_state_only",
    "navmesh_vertex_search_performed": False,
    "max_records": 128,
    "max_path_queries_per_record": 2,
    "max_total_path_queries": 256,
    "is_node_exclusion": False,
}
_RECORD_KEYS = {
    "agent_id",
    "node_id",
    "destination",
    "status",
    "reason_code",
    "start_base_position",
    "start_reference_position",
    "start_rotation_yaw_rad",
    "goal_center",
    "official_robot_at_threshold_m",
    "oracle_stop_radius_m",
    "original_point",
    "navmesh_settings",
    "habitat_sim_version",
    "selection",
    "search",
    "error_type",
    "error",
    "elapsed_ms",
}
_NAVMESH_FIELDS = {
    "agent_height",
    "agent_max_climb",
    "agent_max_slope",
    "agent_radius",
    "cell_height",
    "cell_size",
    "detail_sample_dist",
    "detail_sample_max_error",
    "edge_max_error",
    "edge_max_len",
    "filter_ledge_spans",
    "filter_low_hanging_obstacles",
    "filter_walkable_low_height_spans",
    "include_static_objects",
    "region_merge_size",
    "region_min_size",
    "verts_per_poly",
}


def _object(value: Any, label: str) -> dict[str, Any]:
    """Reject a missing or non-object identity source without type coercion."""
    if not isinstance(value, dict):
        raise ValueError(f"reset route support {label} must be an object")
    return value


def _number(value: Any, minimum: float = 0.0) -> bool:
    """Recognize finite numerical evidence; booleans never count as numbers."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= minimum
    )


def _position(value: Any) -> bool:
    """Require a real three-dimensional point without accepting placeholders."""
    return (
        isinstance(value, list)
        and len(value) == 3
        and all(_number(part, -math.inf) for part in value)
    )


def _check_navmesh_settings(settings: dict[str, Any]) -> None:
    """Require the copied supported scalar layout, not a partial mesh identity."""
    if set(settings) != _NAVMESH_FIELDS or settings["include_static_objects"] is not True:
        raise ValueError("reset route support navmesh settings layout is invalid")
    for key, value in settings.items():
        if key.startswith("filter_") or key == "include_static_objects":
            if not isinstance(value, bool):
                raise ValueError("reset route support navmesh flag is invalid")
        elif not _number(value):
            raise ValueError("reset route support navmesh scalar is invalid")


def _goal_names(expression: Any) -> set[str]:
    """Follow only direct mandatory any_at conjuncts, matching the probe scope."""
    if not isinstance(expression, dict):
        return set()
    if expression.get("kind") == "predicate":
        arguments = expression.get("arguments")
        if (
            expression.get("name") == "any_at"
            and isinstance(arguments, list)
            and len(arguments) == 1
            and isinstance(arguments[0], str)
            and arguments[0]
        ):
            return {arguments[0]}
        return set()
    if (
        expression.get("kind") != "logical"
        or expression.get("operator") != "and"
        or expression.get("quantifier") is not None
    ):
        return set()
    operands = expression.get("operands")
    if not isinstance(operands, list) or any(not isinstance(item, dict) for item in operands):
        return set()
    return set().union(*(_goal_names(item) for item in operands))


def _check_selection(record: dict[str, Any]) -> None:
    """Check static witness geometry and query limits, never assert route truth."""
    selection = _object(record["selection"], "selection")
    if set(selection) != {
        "point",
        "source",
        "original_status",
        "estimated_pddl_reference_distance_m",
        "estimated_stop_envelope_distance_m",
        "path_length_m",
        "vertices_seen",
        "candidates_in_region",
        "path_queries",
        "search_truncated",
    } or not _position(selection["point"]):
        raise ValueError("reset route support selection fields are invalid")
    if (
        not isinstance(selection["source"], str)
        or not isinstance(selection["original_status"], str)
        or selection["source"] not in {"original", "agent-navmesh"}
        or selection["original_status"]
        not in {
            "reachable",
            "outside_goal_region",
            "stop_envelope_exceeds_goal",
            "no_agent_path",
        }
    ):
        raise ValueError("reset route support selection source is invalid")
    if selection["source"] == "original" and (
        selection["point"] != record["original_point"]
        or selection["original_status"] != "reachable"
    ):
        raise ValueError("reset route support original selection differs from observation")
    if selection["vertices_seen"] != 0 or selection["search_truncated"] is not False:
        raise ValueError("reset route support claims an unperformed vertex search")
    for key, minimum, maximum in (("path_queries", 1, 2), ("candidates_in_region", 0, 1)):
        value = selection[key]
        if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
            raise ValueError("reset route support selection exceeds query bounds")
    if not _number(selection["path_length_m"]):
        raise ValueError("reset route support static path length is invalid")
    point, start, reference, center = (
        selection["point"],
        record["start_base_position"],
        record["start_reference_position"],
        record["goal_center"],
    )
    offset = [reference[index] - start[index] for index in range(3)]
    distance = math.dist([point[index] + offset[index] for index in range(3)], center)
    horizontal = math.hypot(point[0] - center[0], point[2] - center[2])
    envelope = math.hypot(
        point[1] + offset[1] - center[1],
        horizontal + math.hypot(offset[0], offset[2]) + record["oracle_stop_radius_m"],
    )
    radius = record["official_robot_at_threshold_m"]
    bound = radius - min(0.02, radius * 0.01)
    for key, computed in (
        ("estimated_pddl_reference_distance_m", distance),
        ("estimated_stop_envelope_distance_m", envelope),
    ):
        if (
            not _number(selection[key])
            or not math.isclose(selection[key], computed, rel_tol=1e-9, abs_tol=1e-9)
            or computed >= bound
        ):
            raise ValueError("reset route support witness exceeds official goal geometry")


def _check_record(record: dict[str, Any], source: dict[str, Any]) -> None:
    """Keep observed witnesses, bounded misses, and unavailable evidence distinct."""
    if set(record) != _RECORD_KEYS or not _number(record["elapsed_ms"]):
        raise ValueError("reset route support record fields are invalid")
    status = record["status"]
    reasons = {
        "supported": {"static_path_witness"},
        "not_found": {"initial_candidates_exhausted"},
        "unavailable": {"probe_initialization_error", "observation_error", "reset_state_changed"},
    }
    if (
        not isinstance(status, str)
        or status not in reasons
        or not isinstance(record["reason_code"], str)
        or record["reason_code"] not in reasons[status]
    ):
        raise ValueError("reset route support status or reason is invalid")
    for key in ("start_base_position", "start_reference_position", "goal_center", "original_point"):
        if record[key] is not None and not _position(record[key]):
            raise ValueError("reset route support position is invalid")
    for key in ("start_rotation_yaw_rad", "official_robot_at_threshold_m", "oracle_stop_radius_m"):
        if record[key] is not None and not _number(
            record[key], -math.inf if key == "start_rotation_yaw_rad" else 0.0
        ):
            raise ValueError("reset route support radius or rotation is invalid")
    if record["navmesh_settings"] is not None:
        _check_navmesh_settings(_object(record["navmesh_settings"], "navmesh settings"))
    if record["habitat_sim_version"] is not None and (
        not isinstance(record["habitat_sim_version"], str)
        or not record["habitat_sim_version"].strip()
    ):
        raise ValueError("reset route support native version is invalid")
    if (
        record["reason_code"] != "reset_state_changed"
        and record["start_base_position"] is not None
        and record["start_base_position"] != source["start"]["position"]
    ):
        raise ValueError("reset route support differs from frozen agent start")
    if (
        record["goal_center"] is not None
        and record["goal_center"] != source["destination_entity"]["position"]
    ):
        raise ValueError("reset route support differs from reset goal entity")
    if (
        record["official_robot_at_threshold_m"] is not None
        and record["official_robot_at_threshold_m"] != source["goal_tolerance_m"]
    ):
        raise ValueError("reset route support differs from official goal tolerance")
    if status == "unavailable":
        if record["selection"] is not None or record["search"] is not None:
            raise ValueError("reset route support unavailable record claims a search result")
        if (
            not isinstance(record["error_type"], str)
            or not record["error_type"]
            or not isinstance(record["error"], str)
            or len(record["error"]) > 512
        ):
            raise ValueError("reset route support unavailable record lacks an error")
        return
    if (
        any(
            record[key] is None
            for key in (
                "start_base_position",
                "start_reference_position",
                "start_rotation_yaw_rad",
                "goal_center",
                "official_robot_at_threshold_m",
                "oracle_stop_radius_m",
                "original_point",
            )
        )
        or record["official_robot_at_threshold_m"] <= 0
    ):
        raise ValueError("reset route support search lacks observed geometry")
    _check_navmesh_settings(_object(record["navmesh_settings"], "navmesh settings"))
    if (
        not isinstance(record["habitat_sim_version"], str)
        or not record["habitat_sim_version"].strip()
        or record["error_type"] is not None
        or record["error"] is not None
    ):
        raise ValueError("reset route support search metadata is invalid")
    if status == "supported":
        if record["search"] is not None:
            raise ValueError("reset route support witness also claims a miss")
        _check_selection(record)
    else:
        search = _object(record["search"], "search")
        if record["selection"] is not None or set(search) != {
            "reason_code",
            "vertices_seen",
            "candidates_in_region",
            "path_queries",
            "search_truncated",
        }:
            raise ValueError("reset route support miss also claims a witness")
        if (
            not isinstance(search["reason_code"], str)
            or search["reason_code"] not in {"candidates_exhausted", "path_query_budget"}
            or search["vertices_seen"] != 0
            or not isinstance(search["search_truncated"], bool)
        ):
            raise ValueError("reset route support miss search scope is invalid")
        for key, maximum in (("path_queries", 2), ("candidates_in_region", 1)):
            value = search[key]
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= maximum:
                raise ValueError("reset route support miss exceeds search bounds")


def preflight_reset_route_support(run: Path) -> dict[str, Any]:
    """Bind an opted-in diagnostic archive to exact reset and Local How sources.

    Valid not_found/unavailable records pass this archival check. Passing it
    never proves Mission feasibility, completion, or benchmark admission.
    """
    path = run / "evidence/reset-route-support.json"
    if not path.is_file() or not 0 < path.stat().st_size <= 1024 * 1024:
        raise ValueError("reset route support archive is missing or exceeds 1 MiB")
    document = _object(load_document(path), "archive")
    preassignment = preflight_deployment_feasibility(run)
    semantic = _object(
        load_document(run / "evidence/authoritative-semantic-evidence.json"), "semantic source"
    )
    local_how = _object(load_document(run / "evidence/local-how-profile.json"), "Local How source")
    runtime = _object(
        load_document(run / "evidence/runtime-source-manifest.json"), "runtime sources"
    )
    if (
        set(document)
        != {
            "schema_version",
            "authority",
            "purpose",
            "identity",
            "initial_agent_positions",
            "probe",
            "scope_status",
            "scope_reason",
            "records",
            "digest",
            "elapsed_ms",
        }
        or document["schema_version"] != _SCHEMA
        or document["authority"] != "deployment-observed-reset-state"
        or document["purpose"] != "diagnostic_only"
        or not _number(document["elapsed_ms"])
    ):
        raise ValueError("reset route support schema or authority is invalid")
    if document["digest"] != _content_digest(
        {key: value for key, value in document.items() if key != "digest"}
    ):
        raise ValueError("reset route support digest differs from content")
    if _content_digest(local_how) != _content_digest(
        {
            "schema_version": "roboguide.habitat-local-how-profile/v0.2",
            "navigation_point_resolver": "official-any-at-agent-navmesh/v0.1",
            "official_success_authority": "habitat-pddl",
            "reset_route_support_enabled": True,
        }
    ):
        raise ValueError("reset route support differs from active Local How profile")
    expected_identity = {
        **preassignment["identity"],
        "preassignment_digest": preassignment["digest"],
        "local_how_digest": _content_digest(local_how),
        "runtime_sources_digest": _content_digest(runtime),
    }
    if _content_digest(_object(document["identity"], "identity")) != _content_digest(
        expected_identity
    ) or _content_digest(
        _object(document["initial_agent_positions"], "initial positions")
    ) != _content_digest(preassignment["initial_agent_positions"]):
        raise ValueError("reset route support differs from frozen reset identity")
    if _content_digest(_object(document["probe"], "probe")) != _content_digest(_PROBE):
        raise ValueError("reset route support probe profile or bounds are invalid")
    modules = _object(runtime.get("modules"), "runtime modules")
    for name in (
        "habitat.tasks.rearrange.actions.habitat_mas_actions",
        "habitat.tasks.rearrange.actions.oracle_nav_action",
        "habitat_sim",
        "habitat_sim._ext.habitat_sim_bindings",
        "habitat_local_eaios.goal_region_action",
        "habitat_local_eaios.goal_region_navigation",
        "habitat_local_eaios.reset_route_support",
    ):
        module = _object(modules.get(name), "runtime module")
        if (
            set(module) != {"path", "sha256"}
            or not isinstance(module["path"], str)
            or not Path(module["path"]).is_absolute()
        ):
            raise ValueError("reset route support runtime source path is invalid")
        if hashlib.sha256(Path(module["path"]).read_bytes()).hexdigest() != module["sha256"]:
            raise ValueError("reset route support runtime source changed since observation")
    goals = _goal_names(semantic.get("goal"))
    sources: dict[tuple[int, str, str], dict[str, Any]] = {}
    for source in preassignment["records"]:
        key = (source["agent_id"], source["node_id"], source["destination"])
        if key[2] in goals:
            if key in sources and any(
                source[field] != sources[key][field]
                for field in ("start", "destination_entity", "goal_tolerance_m")
            ):
                raise ValueError("reset route support reset sources disagree on geometry")
            sources[key] = source
    endpoints = {(source["agent_id"], source["node_id"]) for source in preassignment["records"]}
    if set(sources) != {(agent, node, goal) for agent, node in endpoints for goal in goals}:
        raise ValueError("reset route support reset source lacks full endpoint/goal coverage")
    records = document["records"]
    if not isinstance(records, list) or len(records) > 128:
        raise ValueError("reset route support record budget is invalid")
    if document["scope_status"] == "unavailable":
        expected_reason = (
            "unsupported_goal" if not goals else "record_budget" if len(sources) > 128 else None
        )
        if records or document["scope_reason"] != expected_reason or expected_reason is None:
            raise ValueError("reset route support scope unavailable claim is invalid")
        return document
    if (
        document["scope_status"] != "available"
        or document["scope_reason"] != "observed"
        or not goals
    ):
        raise ValueError("reset route support scope is invalid")
    seen: set[tuple[int, str, str]] = set()
    for value in records:
        record = _object(value, "record")
        if set(record) != _RECORD_KEYS:
            raise ValueError("reset route support record fields are invalid")
        key = (record["agent_id"], record["node_id"], record["destination"])
        if (
            not isinstance(key[0], int)
            or isinstance(key[0], bool)
            or not isinstance(key[1], str)
            or not isinstance(key[2], str)
            or key not in sources
            or key in seen
        ):
            raise ValueError("reset route support repeats or invents an endpoint/goal")
        _check_record(record, sources[key])
        seen.add(key)
    if seen != set(sources):
        raise ValueError("reset route support lacks full endpoint/goal coverage")
    return document
