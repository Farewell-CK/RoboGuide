"""Source-bound optics, conservative frusta and bounded observation search regressions."""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from habitat_local_eaios import observation_optics  # noqa: E402
from habitat_local_eaios.endpoint_registry import build_endpoint_registry  # noqa: E402
from habitat_local_eaios.model import IntegrationError  # noqa: E402
from habitat_local_eaios.observation_optics import (  # noqa: E402
    build_optics_declaration,
    check_loaded_optics,
    fixed_camera_mount,
    read_optics_declaration,
    validate_optics,
    validate_optics_readiness,
)
from habitat_local_eaios.view_recovery import (  # noqa: E402
    MAX_ROUTE_QUERIES,
    MAX_SCAN_CANDIDATES,
    local_grid_candidates,
    select_viewpoint,
    sphere_may_intersect_camera_frustum,
)

ROOT = Path(__file__).parents[3]


def configured_sources(run: Path) -> tuple[tuple[int, Path], ...]:
    """Copy the actual deployment declarations into a fresh isolated source directory."""
    source = ROOT / "scenarios/e1-shared-world-perception"
    records = []
    for agent in range(2):
        name = f"node-{chr(97 + agent)}.toml"
        path = run / name
        path.write_bytes((source / name).read_bytes())
        records.append((agent, path))
    return tuple(records)


@pytest.mark.parametrize(
    "camera_point,visible",
    [
        ((0.0, 0.0, -1.0), True),
        ((0.0, 1.5, -0.2), False),
        ((0.0, 0.0, 1.0), False),
        ((2.0, 0.0, -1.0), False),
        ((0.0, -2.0, -1.0), False),
    ],
)
def test_frustum_is_only_a_camera_geometric_necessary_condition(
    camera_point: tuple[float, float, float], visible: bool
) -> None:
    """Off-axis or behind-camera targets fail even when a navigation route exists."""
    assert sphere_may_intersect_camera_frustum(camera_point, 1.0, 1.0, 0.02) is visible


@pytest.mark.parametrize("tangent", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_optics_cannot_admit_a_viewpoint(tangent: float) -> None:
    """Unknown or malformed field of view cannot be treated as a wide camera."""
    with pytest.raises(IntegrationError, match="malformed"):
        sphere_may_intersect_camera_frustum((0.0, 0.0, -1.0), tangent, 1.0, 0.01)


def test_object_volume_is_conservative_but_never_claims_pixels() -> None:
    """A sphere may intersect an edge even when its center is out of the view."""
    assert not sphere_may_intersect_camera_frustum((1.02, 0.0, -1.0), 1.0, 1.0, 0.0)
    assert sphere_may_intersect_camera_frustum((1.02, 0.0, -1.0), 1.0, 1.0, 0.02)


def test_local_grid_is_finite_deterministic_and_rejects_oversize_before_allocation() -> None:
    """Fixed bounds prevent tiny spacing from causing a huge candidate allocation."""
    first = local_grid_candidates((2.0, 3.0, 4.0))
    assert first == local_grid_candidates((2.0, 3.0, 4.0))
    assert len(first) == 288 and all(point[1] == 3 for point in first)
    with pytest.raises(IntegrationError, match="bound"):
        local_grid_candidates((0.0, 0.0, 0.0), spacing_m=1e-12)


def test_grid_finds_a_narrow_region_between_the_old_radial_rays() -> None:
    """A valid supplied map corridor need not intersect one of eight radial directions."""
    start, target = (0.0, 0.0, 0.0), (0.0, 1.0, -2.0)
    point = (-0.3, 0.0, 0.6)
    result = select_viewpoint(
        start,
        target,
        lambda p: p,
        lambda p: math.dist(start, p),
        lambda p, heading: math.dist(p, point) < 1e-6,
        candidate_points=local_grid_candidates,
        permits_viewpoint=lambda p, heading: math.dist(p, point) < 1e-6,
        viewpoint_evidence=lambda p, heading: {"pixel_detection": "unavailable"},
    )
    assert result.point == point and result.route_queries == result.scan_candidates == 1
    assert result.viewpoint_evidence == {"pixel_detection": "unavailable"}


@pytest.mark.parametrize("budget", ["route", "scan"])
def test_finite_native_query_budgets_are_enforced_independently(budget: str) -> None:
    """A dense grid cannot issue unbounded paths or full rotation replays."""
    calls = {"route": 0, "scan": 0}

    def route(point: Any) -> float | None:
        """Count actual supplied route queries independently of rejected candidates."""
        calls["route"] += 1
        return None if budget == "route" else math.dist((0, 0, 0), point)

    def scan(point: Any, heading: float) -> bool:
        """Reject original rotation checks without relaxing them to obtain success."""
        calls["scan"] += 1
        return False

    with pytest.raises(IntegrationError, match=f"{budget} query budget exhausted"):
        select_viewpoint(
            (0.0, 0.0, 0.0),
            (0.0, 0.5, 2.0),
            lambda p: p,
            route,
            scan,
            candidate_points=local_grid_candidates,
            permits_viewpoint=lambda p, heading: True,
        )
    assert calls[budget] == (MAX_ROUTE_QUERIES if budget == "route" else MAX_SCAN_CANDIDATES)


def test_no_optical_candidate_never_queries_a_route_or_actuator() -> None:
    """Geometry rejection is independent of motion and does not fabricate a sensor result."""
    with pytest.raises(IntegrationError, match="no supported candidate"):
        select_viewpoint(
            (0.0, 0.0, 0.0),
            (0.0, 4.0, 0.0),
            lambda p: p,
            lambda p: pytest.fail("optically rejected candidate queried route"),
            lambda p, heading: pytest.fail("optically rejected candidate queried actuator"),
            candidate_points=local_grid_candidates,
            permits_viewpoint=lambda p, heading: False,
        )


def test_source_bound_optics_do_not_depend_on_episode_or_target(tmp_path: Path) -> None:
    """The deployment freezes typed Node facts, without workload-specific identifiers."""
    sources = configured_sources(tmp_path)
    registry = build_endpoint_registry(sources, tmp_path)
    document = build_optics_declaration(sources)
    path = tmp_path / "observation-optics-declaration.json"
    path.write_text(json.dumps(document))
    facts = read_optics_declaration(path, registry)
    assert facts[0]["camera-height-above-base-m"] == 0.48
    assert facts[1]["camera-forward-elevation-degrees"] == -45
    assert all(value["camera-pan-tilt-supported"] is False for value in facts.values())
    document["agents"][0]["node_config_digest"] = document["agents"][1]["node_config_digest"]
    path.write_text(json.dumps(document))
    with pytest.raises(IntegrationError, match="Node source"):
        read_optics_declaration(path, registry)


@pytest.mark.parametrize("fault", ["height", "pan-tilt", "partial", "nan"])
def test_inaccurate_or_incomplete_declared_optics_refuse_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Configured facts must match the actual loaded camera before advertising readiness."""
    facts = build_optics_declaration(configured_sources(tmp_path))["agents"][0]["attributes"]
    actual = dict(facts)
    if fault == "height":
        actual["camera-height-above-base-m"] = 1.5
    elif fault == "pan-tilt":
        facts["camera-pan-tilt-supported"] = True
    elif fault == "partial":
        del facts["camera-vertical-fov-degrees"]
    else:
        facts["camera-horizontal-fov-degrees"] = float("nan")
    monkeypatch.setattr(
        observation_optics,
        "loaded_fixed_camera_optics",
        lambda env, agent: {"sensor_uuid": "agent_0_camera_semantic", "attributes": actual},
    )
    with pytest.raises(IntegrationError):
        check_loaded_optics(object(), 0, facts)


def test_undeclared_optics_remain_unavailable_without_reading_a_camera(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy declarations acquire no invented mount, FOV or pan/tilt guarantee."""
    monkeypatch.setattr(
        observation_optics,
        "loaded_fixed_camera_optics",
        lambda env, agent: pytest.fail("undeclared optics should stay unknown"),
    )
    assert validate_optics({"sensing-modality": "visual"}) == {}
    assert check_loaded_optics(object(), 0, {})["status"] == "unavailable"


@pytest.mark.parametrize("prefixed", [False, True])
def test_native_mount_prefixes_remain_bound_to_the_own_endpoint(prefixed: bool) -> None:
    """Original multi-agent construction prefixes mounts as well as sensor UUIDs."""
    own = SimpleNamespace(attached_link_id=-1)
    articulated = SimpleNamespace(attached_link_id=6)
    prefix = "agent_0_" if prefixed else ""
    mounts = {prefix + "head": own, prefix + "jaw": articulated}
    assert fixed_camera_mount(mounts, "agent_0_head_semantic", 0) is own
    assert fixed_camera_mount(mounts, "agent_0_jaw_semantic", 0) is None
    with pytest.raises(IntegrationError, match="another endpoint"):
        fixed_camera_mount(mounts, "agent_1_head_semantic", 0)


@pytest.mark.parametrize(
    "fault", [None, "coverage", "height", "peer", "unknown", "extra", "schema", "source"]
)
def test_independent_consumers_fence_archived_camera_readiness(
    tmp_path: Path, fault: str | None
) -> None:
    """Eval and adapter consumers reject drift rather than blessing a rehashed declaration."""
    from roboguide_eval.b1_deployment_feasibility import (  # type: ignore[import-untyped]
        _check_optics_readiness,
    )

    sources = configured_sources(tmp_path)
    registry = build_endpoint_registry(sources, tmp_path)
    document = build_optics_declaration(sources)
    path = tmp_path / "observation-optics-declaration.json"
    path.write_text(json.dumps(document))
    declared = read_optics_declaration(path, registry)
    readiness: dict[str, Any] = {
        "schema_version": "roboguide.perception-readiness/v0.3",
        "sensors": {str(i): f"agent_{i}_detected_objects" for i in range(2)},
        "fixed_camera_optics": {
            str(i): {
                "status": "observed",
                "source": "loaded-original-camera-matrices",
                "sensor_uuid": f"agent_{i}_head_semantic",
                "attributes": dict(facts),
            }
            for i, facts in declared.items()
        },
    }
    if fault == "coverage":
        del readiness["fixed_camera_optics"]["0"]
    elif fault == "height":
        readiness["fixed_camera_optics"]["0"]["attributes"]["camera-height-above-base-m"] = 2.0
    elif fault == "peer":
        readiness["fixed_camera_optics"]["0"]["sensor_uuid"] = "agent_1_head_semantic"
    elif fault == "unknown":
        readiness["fixed_camera_optics"]["0"] = {"status": "unavailable"}
    elif fault == "extra":
        readiness["fixed_camera_optics"]["0"]["attributes"]["visibility-guaranteed"] = True
    elif fault == "schema":
        readiness["schema_version"] = "roboguide.perception-readiness/v0.2"
    elif fault == "source":
        readiness["fixed_camera_optics"]["0"]["source"] = "assumed-from-robot-name"
    if fault is None:
        validate_optics_readiness(readiness, declared)
        _check_optics_readiness(tmp_path, registry["endpoints"], readiness)
    else:
        with pytest.raises(IntegrationError):
            validate_optics_readiness(readiness, declared)
        with pytest.raises(ValueError):
            _check_optics_readiness(tmp_path, registry["endpoints"], readiness)


@pytest.mark.parametrize("height", ["unknown", True, -1, 101])
def test_rehashing_invalid_source_facts_cannot_validate_camera_readiness(
    tmp_path: Path, height: Any
) -> None:
    """Matching hashes and matching observations cannot legitimize invalid typed source facts."""
    from roboguide_eval.b1_deployment_feasibility import (
        _check_optics_readiness,
    )

    sources = configured_sources(tmp_path)
    registry = build_endpoint_registry(sources, tmp_path)
    declaration = build_optics_declaration(sources)
    config_path = sources[0][1]
    config_path.write_text(
        config_path.read_text().replace(
            "camera-height-above-base-m = 0.48",
            "camera-height-above-base-m = " + json.dumps(height),
        )
    )
    digest = "sha256:" + hashlib.sha256(config_path.read_bytes()).hexdigest()
    registry["endpoints"][0]["node_config_digest"] = digest
    declaration["agents"][0]["node_config_digest"] = digest
    attributes = declaration["agents"][0]["attributes"]
    attributes["camera-height-above-base-m"] = height
    path = tmp_path / "observation-optics-declaration.json"
    path.write_text(json.dumps(declaration))
    readiness = {
        "schema_version": "roboguide.perception-readiness/v0.3",
        "sensors": {"0": "agent_0_detected_objects"},
        "fixed_camera_optics": {
            "0": {
                "status": "observed",
                "source": "loaded-original-camera-matrices",
                "sensor_uuid": "agent_0_head_semantic",
                "attributes": attributes,
            }
        },
    }
    with pytest.raises(IntegrationError):
        read_optics_declaration(path, registry)
    with pytest.raises(ValueError, match="malformed"):
        _check_optics_readiness(tmp_path, registry["endpoints"], readiness)
