"""Diagnostic route archive binding never grants execution or benchmark success."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
from pathlib import Path
from typing import Any

import pytest
from b1_helpers import make_run, write_json
from roboguide_eval.b1_deployment_feasibility import _content_digest
from roboguide_eval.b1_reset_route_support import (
    _PROBE,
    _REGION_PROFILE,
    preflight_reset_route_support,
)
from roboguide_eval.b1_run import assess_b1_directory
from roboguide_eval.b1_workload import extract_b1_workload


def _seal(document: dict[str, Any]) -> dict[str, Any]:
    """Recompute content identity so mutation tests exercise more than checksum rejection."""
    body = {key: value for key, value in document.items() if key != "digest"}
    return {**body, "digest": _content_digest(body)}


def _archive(run: Path) -> dict[str, Any]:
    """Write a complete neutral offline snapshot using the same producer contract."""
    workload = extract_b1_workload(json.loads((run / "b1-input-used.json").read_text()))
    semantic = json.loads((run / "evidence/authoritative-semantic-evidence.json").read_text())
    profile = {"digest": "sha256:" + "a" * 64}
    write_json(run / "spatial-profile.json", profile)
    goals = ["any_targets|0", "TARGET_any_targets|0"]
    sources = []
    for destination in goals:
        for agent_id in (0, 1):
            sources.append(
                {
                    "agent_id": agent_id,
                    "node_id": f"node-{agent_id}",
                    "destination": destination,
                    "goal_occupancy": "any_at",
                    "goal_tolerance_m": 2.0,
                    "status": "unknown",
                    "start": {"position": [0.0, 0.0, 0.0]},
                    "destination_entity": {"position": [1.0, 0.0, 0.0]},
                }
            )
    matrix = _seal(
        {
            "schema_version": "roboguide.deployment-intent-feasibility/v0.3",
            "authority": "deployment-observed-reset-state",
            "identity": {
                "run_id": run.name,
                "episode_id": workload.episode_id,
                "scene_id": workload.scene_id,
                "dataset_revision": workload.dataset_revision,
                "dataset_sha256": workload.dataset_sha256,
                "semantic_evidence_digest": semantic["digest"],
                "spatial_profile_digest": profile["digest"],
                "habitat_seed": workload.seed,
                "episode_reset_count": 1,
            },
            "initial_agent_positions": {"0": [0.0, 0.0, 0.0], "1": [0.0, 0.0, 0.0]},
            "records": sources,
        }
    )
    write_json(run / "evidence/preassignment-feasibility.json", matrix)
    local_how = {
        "schema_version": "roboguide.habitat-local-how-profile/v0.2",
        "navigation_point_resolver": "official-any-at-agent-navmesh/v0.1",
        "official_success_authority": "habitat-pddl",
        "reset_route_support_enabled": True,
    }
    write_json(run / "evidence/local-how-profile.json", local_how)
    modules: dict[str, Any] = {}
    for index, name in enumerate(
        (
            "habitat.tasks.rearrange.actions.habitat_mas_actions",
            "habitat.tasks.rearrange.actions.oracle_nav_action",
            "habitat_sim",
            "habitat_sim._ext.habitat_sim_bindings",
            "habitat_local_eaios.goal_region_action",
            "habitat_local_eaios.goal_region_navigation",
            "habitat_local_eaios.reset_route_support",
        )
    ):
        path = run / f"offline-module-{index}.py"
        path.write_text("# deterministic source fixture\n", encoding="utf-8")
        modules[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    runtime = {"schema_version": "roboguide.local-eaios-source-provenance/v0.1", "modules": modules}
    write_json(run / "evidence/runtime-source-manifest.json", runtime)
    records = [
        {
            "agent_id": source["agent_id"],
            "node_id": source["node_id"],
            "destination": source["destination"],
            "status": "supported",
            "reason_code": "static_path_witness",
            "start_base_position": [0.0, 0.0, 0.0],
            "start_reference_position": [0.0, 0.0, 0.0],
            "start_rotation_yaw_rad": 0.4,
            "goal_center": [1.0, 0.0, 0.0],
            "official_robot_at_threshold_m": 2.0,
            "oracle_stop_radius_m": 0.5,
            "original_point": [1.0, 0.0, 0.0],
            "navmesh_settings": {
                "agent_height": 1.5,
                "agent_max_climb": 0.2,
                "agent_max_slope": 45.0,
                "agent_radius": 0.45,
                "cell_height": 0.2,
                "cell_size": 0.05,
                "detail_sample_dist": 6.0,
                "detail_sample_max_error": 1.0,
                "edge_max_error": 1.3,
                "edge_max_len": 12.0,
                "filter_ledge_spans": True,
                "filter_low_hanging_obstacles": True,
                "filter_walkable_low_height_spans": True,
                "include_static_objects": True,
                "region_merge_size": 20.0,
                "region_min_size": 20.0,
                "verts_per_poly": 6.0,
            },
            "habitat_sim_version": "offline-test",
            "selection": {
                "point": [1.0, 0.0, 0.0],
                "source": "original",
                "original_status": "reachable",
                "estimated_pddl_reference_distance_m": 0.0,
                "estimated_stop_envelope_distance_m": 0.5,
                "path_length_m": 3.0,
                "vertices_seen": 0,
                "candidates_in_region": 0,
                "path_queries": 1,
                "search_truncated": False,
            },
            "search": None,
            "error_type": None,
            "error": None,
            "elapsed_ms": 1.0,
        }
        for source in sources
    ]
    document = _seal(
        {
            "schema_version": "roboguide.deployment-reset-route-support/v0.1",
            "authority": "deployment-observed-reset-state",
            "purpose": "diagnostic_only",
            "identity": {
                **matrix["identity"],
                "preassignment_digest": matrix["digest"],
                "local_how_digest": _content_digest(local_how),
                "runtime_sources_digest": _content_digest(runtime),
            },
            "initial_agent_positions": matrix["initial_agent_positions"],
            "probe": copy.deepcopy(_PROBE),
            "scope_status": "available",
            "scope_reason": "observed",
            "records": records,
            "elapsed_ms": 4.0,
        }
    )
    write_json(run / "evidence/reset-route-support.json", document)
    return document


def _save(run: Path, document: dict[str, Any]) -> None:
    """Persist a resealed mutated archive for deterministic preflight rejection."""
    write_json(run / "evidence/reset-route-support.json", _seal(document))


def _geometry_archive(run: Path, *, disjoint: bool = False) -> dict[str, Any]:
    """Freeze a complete v0.2 snapshot with geometrically consistent source points."""
    document = _archive(run)
    matrix = json.loads((run / "evidence/preassignment-feasibility.json").read_text())
    if disjoint:
        for source in matrix["records"]:
            source["destination_entity"]["position"] = [20.0, 0.0, 0.0]
        matrix = _seal(matrix)
        write_json(run / "evidence/preassignment-feasibility.json", matrix)
    local_how = json.loads((run / "evidence/local-how-profile.json").read_text())
    local_how.update(
        schema_version="roboguide.habitat-local-how-profile/v0.3", reset_route_geometry_enabled=True
    )
    write_json(run / "evidence/local-how-profile.json", local_how)
    runtime = json.loads((run / "evidence/runtime-source-manifest.json").read_text())
    module = run / "offline-region-module.py"
    module.write_text("# immutable geometry producer fixture\n")
    runtime["modules"]["habitat_local_eaios.navmesh_region"] = {
        "path": str(module),
        "sha256": hashlib.sha256(module.read_bytes()).hexdigest(),
    }
    write_json(run / "evidence/runtime-source-manifest.json", runtime)
    document["schema_version"] = "roboguide.deployment-reset-route-support/v0.2"
    document["probe"]["region_analysis"] = copy.deepcopy(_REGION_PROFILE)
    document["identity"].update(
        preassignment_digest=matrix["digest"],
        local_how_digest=_content_digest(local_how),
        runtime_sources_digest=_content_digest(runtime),
    )
    for record in document["records"]:
        if disjoint:
            record.update(
                status="not_found",
                reason_code="initial_candidates_exhausted",
                selection=None,
                goal_center=[20.0, 0.0, 0.0],
                search={
                    "reason_code": "candidates_exhausted",
                    "vertices_seen": 0,
                    "candidates_in_region": 0,
                    "path_queries": 1,
                    "search_truncated": False,
                },
            )
        record["region_analysis"] = {
            "schema_version": "roboguide.static-navigation-region/v0.1",
            "scope": "reset_static_start_component",
            "status": "disjoint" if disjoint else "intersects",
            "reason_code": "static_component_disjoint"
            if disjoint
            else "static_triangle_intersection",
            "component_id": 0,
            "mesh_digest": "sha256:" + "c" * 64,
            "start_snap_position": [0.0, 0.0, 0.0],
            "start_snap_distance_m": 0.0,
            "vertices": 3,
            "triangles": 1,
            "triangles_examined": 1,
            "complete": True,
            "minimum_reference_distance_m": 19.0 if disjoint else 0.0,
            "minimum_base_distance_m": 19.0 if disjoint else 0.0,
            "orientation_safe_distance_lower_bound_m": 19.0 if disjoint else 0.0,
            "closest_base_position": [1.0, 0.0, 0.0],
            "closest_triangle": [[-1.0, 0.0, -1.0], [1.0, 0.0, 0.0], [-1.0, 0.0, 1.0]],
            "closest_base_center_position": [1.0, 0.0, 0.0],
            "closest_base_center_triangle": [[-1.0, 0.0, -1.0], [1.0, 0.0, 0.0], [-1.0, 0.0, 1.0]],
            "elapsed_ms": 1.0,
        }
    _save(run, document)
    return _seal(document)


@pytest.mark.parametrize("disjoint", [False, True])
def test_scoped_geometry_archives_without_changing_formal_admission(
    tmp_path: Path,
    disjoint: bool,
) -> None:
    """A static intersection or negative remains separate from benchmark results."""
    run = make_run(tmp_path)
    baseline = assess_b1_directory(run)
    document = _geometry_archive(run, disjoint=disjoint)
    assert preflight_reset_route_support(run, require_geometry=True) == document
    assert assess_b1_directory(run) == baseline


def test_requested_geometry_cannot_silently_fall_back_to_old_schema(tmp_path: Path) -> None:
    """An explicitly requested new observation requires the new identity-bound artifact."""
    run = make_run(tmp_path)
    _archive(run)
    with pytest.raises(ValueError, match="requested but its archive is missing"):
        preflight_reset_route_support(run, require_geometry=True)


def _step_aware_archive(run: Path, *, geometry: bool) -> dict[str, Any]:
    """Bind the opt-in resolution profile to actual settings and its own source."""
    document = _geometry_archive(run) if geometry else _archive(run)
    local_how = json.loads((run / "evidence/local-how-profile.json").read_text())
    local_how.update(
        schema_version="roboguide.habitat-local-how-profile/v0.4",
        navmesh_resolution_profile="step-preserving-cell-height/v0.1",
    )
    write_json(run / "evidence/local-how-profile.json", local_how)
    runtime = json.loads((run / "evidence/runtime-source-manifest.json").read_text())
    path = run / "offline-mesh-profile.py"
    path.write_text("# immutable resolution producer fixture\n")
    runtime["modules"]["habitat_local_eaios.navmesh_profile"] = {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    write_json(run / "evidence/runtime-source-manifest.json", runtime)
    document["identity"].update(
        local_how_digest=_content_digest(local_how), runtime_sources_digest=_content_digest(runtime)
    )
    for record in document["records"]:
        record["navmesh_settings"].update(agent_max_climb=0.02, cell_height=0.01)
    _save(run, document)
    return _seal(document)


@pytest.mark.parametrize("geometry", [False, True])
def test_resolution_profile_is_bound_independently_of_geometry(
    tmp_path: Path, geometry: bool
) -> None:
    """The explicit Local How difference neither invents success nor changes admission."""
    run = make_run(tmp_path)
    baseline = assess_b1_directory(run)
    document = _step_aware_archive(run, geometry=geometry)
    assert preflight_reset_route_support(run, require_geometry=geometry) == document
    assert assess_b1_directory(run) == baseline


@pytest.mark.parametrize("fault", ["climb_resolution", "tiny_voxel", "source", "profile"])
def test_resealed_resolution_inconsistency_is_rejected(tmp_path: Path, fault: str) -> None:
    """A checksum cannot hide coarse settings or an unidentified Local How change."""
    run = make_run(tmp_path)
    document = _step_aware_archive(run, geometry=True)
    if fault in {"climb_resolution", "tiny_voxel"}:
        document["records"][0]["navmesh_settings"]["cell_height"] = (
            0.2 if fault == "climb_resolution" else 0.001
        )
    elif fault == "source":
        (run / "offline-mesh-profile.py").write_text("# changed resolution source\n")
    else:
        local_how = json.loads((run / "evidence/local-how-profile.json").read_text())
        local_how["navmesh_resolution_profile"] = "unidentified-policy"
        write_json(run / "evidence/local-how-profile.json", local_how)
        document["identity"]["local_how_digest"] = _content_digest(local_how)
    _save(run, document)
    with pytest.raises(ValueError):
        preflight_reset_route_support(run, require_geometry=True)


@pytest.mark.parametrize(
    "fault",
    [
        "partial",
        "margin",
        "distance",
        "triangle",
        "scope",
        "budget",
        "source",
        "missing",
        "witness",
        "projection",
        "local_profile",
    ],
)
def test_resealed_geometry_faults_do_not_become_valid_evidence(
    tmp_path: Path,
    fault: str,
) -> None:
    """A fresh checksum never excuses invented completeness, distance or scope."""
    run = make_run(tmp_path)
    document = _geometry_archive(run, disjoint=True)
    record = document["records"][0]
    region = record["region_analysis"]
    if fault == "partial":
        region["complete"] = False
    elif fault == "margin":
        record["official_robot_at_threshold_m"] = 19.0
    elif fault == "distance":
        region["minimum_reference_distance_m"] = 20.0
    elif fault == "triangle":
        region["closest_base_position"] = [1.0, 1.0, 0.0]
    elif fault == "scope":
        region["scope"] = "all_physical_behavior"
    elif fault == "budget":
        region["triangles_examined"] = 50_001
    elif fault == "source":
        (run / "offline-region-module.py").write_text("# source changed\n")
    elif fault == "missing":
        record.pop("region_analysis")
    elif fault == "projection":
        region["start_snap_position"][0] = 1.0
    elif fault == "local_profile":
        profile = json.loads((run / "evidence/local-how-profile.json").read_text())
        profile["reset_route_geometry_enabled"] = False
        write_json(run / "evidence/local-how-profile.json", profile)
        document["identity"]["local_how_digest"] = _content_digest(profile)
    else:
        record.update(
            status="supported",
            reason_code="static_path_witness",
            search=None,
            selection={
                "point": [20.0, 0.0, 0.0],
                "source": "agent-navmesh",
                "original_status": "outside_goal_region",
                "estimated_pddl_reference_distance_m": 0.0,
                "estimated_stop_envelope_distance_m": 0.5,
                "path_length_m": 3.0,
                "vertices_seen": 0,
                "candidates_in_region": 1,
                "path_queries": 1,
                "search_truncated": False,
            },
        )
    _save(run, document)
    with pytest.raises(ValueError):
        preflight_reset_route_support(run, require_geometry=True)


def test_supported_miss_and_unavailable_are_archival_states_only(tmp_path: Path) -> None:
    """All honest statuses archive successfully and leave B1 admission unchanged."""
    run = make_run(tmp_path)
    document = _archive(run)
    baseline = assess_b1_directory(run)
    assert len(preflight_reset_route_support(run)["records"]) == 4
    miss = document["records"][1]
    miss.update(
        status="not_found",
        reason_code="initial_candidates_exhausted",
        selection=None,
        search={
            "reason_code": "candidates_exhausted",
            "vertices_seen": 0,
            "candidates_in_region": 0,
            "path_queries": 1,
            "search_truncated": False,
        },
    )
    unavailable = document["records"][2]
    unavailable.update(
        status="unavailable",
        reason_code="observation_error",
        selection=None,
        search=None,
        error_type="ReadError",
        error="observation unavailable",
    )
    _save(run, document)
    assert [record["status"] for record in preflight_reset_route_support(run)["records"]] == [
        "supported",
        "not_found",
        "unavailable",
        "supported",
    ]
    assert assess_b1_directory(run) == baseline


def test_actual_producer_envelope_round_trips_through_b1_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The adapter's real envelope/digest writer agrees with the independent reader."""
    adapter_root = Path(__file__).parents[2] / "integrations/habitat-local-eaios"
    monkeypatch.syspath_prepend(str(adapter_root))
    producer = importlib.import_module("habitat_local_eaios.reset_route_support")
    run = make_run(tmp_path)
    archived = _archive(run)

    class OfflineProbe:
        """Supply frozen route observations without importing a simulator."""

        def __init__(self, environment: Any, agent_id: int) -> None:
            """Retain the endpoint while rejecting any need for a live environment."""
            assert environment is None
            self.agent_id = agent_id

        def observe(self, destination: str) -> dict[str, Any]:
            """Return the schema-valid observation for this exact endpoint/goal."""
            record = next(
                item
                for item in archived["records"]
                if item["agent_id"] == self.agent_id and item["destination"] == destination
            )
            return {
                key: copy.deepcopy(value)
                for key, value in record.items()
                if key not in {"agent_id", "node_id", "destination"}
            }

    monkeypatch.setattr(producer, "ResetRouteProbe", OfflineProbe)
    document = producer.build_reset_route_support(
        None,
        json.loads((run / "evidence/authoritative-semantic-evidence.json").read_text()),
        json.loads((run / "evidence/preassignment-feasibility.json").read_text()),
        json.loads((run / "evidence/local-how-profile.json").read_text()),
        json.loads((run / "evidence/runtime-source-manifest.json").read_text()),
    )
    write_json(run / "evidence/reset-route-support.json", document)
    assert preflight_reset_route_support(run) == document


@pytest.mark.parametrize(
    "field",
    [
        "run_id",
        "episode_id",
        "scene_id",
        "dataset_revision",
        "dataset_sha256",
        "semantic_evidence_digest",
        "spatial_profile_digest",
        "habitat_seed",
        "preassignment_digest",
        "local_how_digest",
        "runtime_sources_digest",
    ],
)
def test_resealed_cross_snapshot_identity_is_rejected(tmp_path: Path, field: str) -> None:
    """A freshly computed checksum cannot substitute another reset or Local How."""
    run = make_run(tmp_path)
    document = _archive(run)
    document["identity"][field] = "other"
    _save(run, document)
    with pytest.raises(ValueError, match="frozen reset identity"):
        preflight_reset_route_support(run)


@pytest.mark.parametrize(
    "fault",
    [
        "start",
        "goal",
        "threshold",
        "geometry",
        "query_budget",
        "missing",
        "duplicate",
        "invented",
        "status",
        "probe",
        "source",
        "settings",
    ],
)
def test_resealed_observation_faults_fail_closed(tmp_path: Path, fault: str) -> None:
    """Reject geometry substitution, dishonest search metadata, and partial archives."""
    run = make_run(tmp_path)
    document = _archive(run)
    record = document["records"][0]
    if fault == "start":
        record["start_base_position"][0] = 9.0
    elif fault == "goal":
        record["goal_center"][1] = 3.0
    elif fault == "threshold":
        record["official_robot_at_threshold_m"] = 99.0
    elif fault == "geometry":
        record["original_point"] = record["selection"]["point"] = [1.0, 3.0, 0.0]
    elif fault == "query_budget":
        record["selection"]["path_queries"] = 3
    elif fault == "missing":
        document["records"].pop()
    elif fault == "duplicate":
        document["records"].append(copy.deepcopy(record))
    elif fault == "invented":
        record["destination"] = "invented-destination"
    elif fault == "status":
        record["status"] = "physically_impossible"
    elif fault == "probe":
        document["probe"]["is_node_exclusion"] = 0
    elif fault == "settings":
        record["navmesh_settings"].pop("agent_height")
    else:
        source = run / "offline-module-0.py"
        source.write_text("# source changed\n", encoding="utf-8")
    _save(run, document)
    with pytest.raises(ValueError):
        preflight_reset_route_support(run)


def test_missing_and_oversized_archives_are_explicit_errors(tmp_path: Path) -> None:
    """A requested diagnostic source cannot silently disappear or exceed its byte budget."""
    run = make_run(tmp_path)
    path = run / "evidence/reset-route-support.json"
    with pytest.raises(ValueError, match="missing"):
        preflight_reset_route_support(run)
    path.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="exceeds"):
        preflight_reset_route_support(run)


def test_unavailable_scope_cannot_hide_existing_supported_goal(tmp_path: Path) -> None:
    """Dropping records does not justify an unsupported-goal claim under resealing."""
    run = make_run(tmp_path)
    document = _archive(run)
    document.update(scope_status="unavailable", scope_reason="unsupported_goal", records=[])
    _save(run, document)
    with pytest.raises(ValueError, match="scope unavailable"):
        preflight_reset_route_support(run)
