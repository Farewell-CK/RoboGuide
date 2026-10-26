"""Bounded calibrated geometry hints do not claim routes, visibility or Node exclusion."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from habitat_local_eaios.endpoint_registry import FINALITY_PROFILE  # noqa: E402
from habitat_local_eaios.model import COMPOUND_OBSERVATION_FORMAT  # noqa: E402
from habitat_local_eaios.observation_preferences import (  # noqa: E402
    attach_observation_preferences,
    geometric_candidates,
    validate_preference_source,
)
from habitat_local_eaios.preassignment_feasibility import preassignment_digest  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def geometry_sources() -> tuple[
    dict[str, Any], Any, dict[str, Any], dict[str, Any], dict[int, str | None]
]:
    """Construct generic original-shaped declarations without any simulator or provider."""
    matrix = json.loads((FIXTURES / "deployment-operation-admission.json").read_bytes())
    matrix.pop("operation_admission")
    matrix.pop("digest")
    matrix["schema_version"] = "roboguide.deployment-intent-feasibility/v0.6"
    matrix["finality_profile"] = dict(FINALITY_PROFILE)
    endpoints = [
        {
            "node_id": f"node-{letter}",
            "agent_id": i,
            "node_config_digest": "sha256:" + "0" * 64,
            "operations": ["mobility.move@v1", "mobility.navigate@v1", "observation.acquire@v1"],
            "condition_format": COMPOUND_OBSERVATION_FORMAT,
        }
        for i, letter in enumerate(("a", "b"))
    ]
    matrix["execution_profile"] = {
        "mode": "independent-live/v0.2",
        "registry_digest": "sha256:" + "b" * 64,
        "endpoints": [
            {key: value for key, value in endpoint.items() if key != "node_config_digest"}
            for endpoint in endpoints
        ],
    }
    targets = {"object:0": [4.0, 1.4, 0.0], "object:1": [5.0, 0.4, 0.0]}
    for record in matrix["records"]:
        if record["destination"] in targets:
            record["destination_entity"]["position"] = targets[record["destination"]]
    matrix["digest"] = preassignment_digest(matrix)
    robots = {
        i: SimpleNamespace(
            base_pos=matrix["initial_agent_positions"][str(i)],
            base_transformation=SimpleNamespace(translation=[float(i), height, 0.0]),
        )
        for i, height in enumerate((0.5, 1.5))
    }
    environment = SimpleNamespace(
        sim=SimpleNamespace(get_agent_data=lambda i: SimpleNamespace(articulated_agent=robots[i])),
        task=SimpleNamespace(
            pddl_problem=SimpleNamespace(
                get_entity=lambda name: SimpleNamespace(name=name),
                sim_info=SimpleNamespace(
                    check_type_matches=lambda entity, kind: entity.name.startswith("object:"),
                    robot_at_thresh=1.0,
                ),
            )
        ),
    )
    optics = {
        str(i): {
            "status": "observed",
            "attributes": {
                "camera-model": "fixed-primary-pinhole/v0.1",
                "camera-height-above-base-m": height,
                "camera-forward-offset-m": offset,
                "camera-forward-elevation-degrees": pitch,
                "camera-horizontal-fov-degrees": 90.0,
                "camera-vertical-fov-degrees": 90.0,
                "camera-pan-tilt-supported": False,
            },
        }
        for i, (height, offset, pitch) in enumerate(((0.5, 0.4, 0.0), (1.5, 0.0, -45.0)))
    }
    return (
        matrix,
        environment,
        {"endpoints": endpoints},
        optics,
        {i: COMPOUND_OBSERVATION_FORMAT for i in range(2)},
    )


def test_shared_projection_is_produced_without_actions_or_mutation() -> None:
    """The Rust fixture uses the real producer; missing routes remain expressly unknown."""
    sources = geometry_sources()
    before = deepcopy(sources[0])
    actual = attach_observation_preferences(*sources)
    assert actual == json.loads((FIXTURES / "initial-observation-geometry.json").read_bytes())
    assert sources[0] == before
    assert actual["records"] == before["records"]
    assert actual["observation_preferences"]["route_visibility_and_physical_success"] == "unknown"
    counts = {
        (record["node_id"], record["entity_id"]): record["geometric_candidate_count"]
        for record in actual["observation_preferences"]["records"]
        if " AND " in record["expected"]
    }
    assert counts[("node-a", "object:0")] == 0
    assert counts[("node-a", "object:1")] > 0
    assert counts[("node-b", "object:0")] > 0
    assert counts[("node-b", "object:1")] == 0
    assert "eligibility" not in json.dumps(actual["observation_preferences"])


def test_world_translation_does_not_change_calibrated_preference() -> None:
    """A coordinate origin cannot act as a benchmark-specific target-height rule."""
    data = json.loads((FIXTURES / "initial-observation-geometry.json").read_bytes())
    for record in data["observation_preferences"]["records"]:
        moved = deepcopy(record)
        for field in ["base_position", "target_position"]:
            moved[field] = [
                value + offset
                for value, offset in zip(moved[field], [10.0, 37.0, -20.0], strict=True)
            ]
        assert geometric_candidates(moved) == record["geometric_candidate_count"]


@pytest.mark.parametrize(
    "missing", ["camera", "reference_height_above_base_m", "arrival_tolerance_m"]
)
def test_missing_geometry_is_unknown_without_a_negative_decision(missing: str) -> None:
    """An unavailable observation or empty stencil cannot remove an otherwise eligible Node."""
    data = json.loads((FIXTURES / "initial-observation-geometry.json").read_bytes())
    record = next(
        record
        for record in data["observation_preferences"]["records"]
        if " AND " in record["expected"]
    )
    record[missing] = None
    assert geometric_candidates(record) is None


@pytest.mark.parametrize("endpoint_count", [1, 2, 3, 4])
def test_complete_endpoint_coverage_and_independent_archive_reader(endpoint_count: int) -> None:
    """One to four real-shaped sources agree across the producer and independent reader."""
    from roboguide_eval.b1_observation_preferences import (  # type: ignore[import-untyped]
        validate_preference_source as archive_validate,
    )

    matrix, environment, registry, optics, formats = geometry_sources()
    template_records = deepcopy(matrix["records"])
    endpoints = []
    records = []
    positions = {}
    original_data = environment.sim.get_agent_data
    for agent in range(endpoint_count):
        prototype = agent % 2
        node = f"node-{chr(97 + agent)}"
        endpoint = deepcopy(registry["endpoints"][prototype])
        endpoint.update(node_id=node, agent_id=agent)
        endpoints.append(endpoint)
        positions[str(agent)] = matrix["initial_agent_positions"][str(prototype)]
        optics[str(agent)] = deepcopy(optics[str(prototype)])
        formats[agent] = COMPOUND_OBSERVATION_FORMAT
        for record in template_records:
            if record["node_id"] == f"node-{chr(97 + prototype)}":
                record.update(node_id=node, agent_id=agent)
                records.append(deepcopy(record))
    environment.sim.get_agent_data = lambda agent: original_data(agent % 2)
    matrix["records"], matrix["initial_agent_positions"] = records, positions
    matrix["execution_profile"]["endpoints"] = [
        {key: value for key, value in record.items() if key != "node_config_digest"}
        for record in endpoints
    ]
    matrix.pop("digest")
    matrix["digest"] = preassignment_digest(matrix)
    actual = attach_observation_preferences(
        matrix, environment, {"endpoints": endpoints}, optics, formats
    )
    readiness = {"fixed_camera_optics": optics}
    validate_preference_source(actual, {"endpoints": endpoints}, readiness)
    archive_validate(actual, {"endpoints": endpoints}, readiness)
    assert len(actual["observation_preferences"]["records"]) == 4 * endpoint_count


@pytest.mark.parametrize(
    "fault",
    [
        "count",
        "camera",
        "base",
        "target",
        "identity",
        "coverage",
        "extra",
        "bool-count",
        "old-profile",
    ],
)
def test_resealed_geometry_cannot_launder_source_drift(fault: str) -> None:
    """Changed identity, optics, derived counts and incomplete coverage fail both readers."""
    from habitat_local_eaios.model import IntegrationError
    from roboguide_eval.b1_observation_preferences import (
        validate_preference_source as archive_validate,
    )

    sources = geometry_sources()
    matrix = attach_observation_preferences(*sources)
    source = matrix["observation_preferences"]
    record = source["records"][0]
    if fault == "count":
        record["geometric_candidate_count"] = 31
    elif fault == "camera":
        record["camera"] = {**record["camera"], "camera-height-above-base-m": 42.0}
        record["geometric_candidate_count"] = geometric_candidates(record)
    elif fault == "base":
        record["base_position"] = [42.0, 0.0, 0.0]
    elif fault == "target":
        record["target_position"] = [42.0, 0.0, 0.0]
    elif fault == "identity":
        source["identity"] = {**source["identity"], "episode_id": "other"}
    elif fault == "coverage":
        source["records"].pop()
    elif fault == "extra":
        record["eligibility"] = True
    elif fault == "bool-count":
        record["geometric_candidate_count"] = True
    elif fault == "old-profile":
        matrix["schema_version"] = "roboguide.deployment-intent-feasibility/v0.6"
    source["digest"] = preassignment_digest(
        {key: value for key, value in source.items() if key != "digest"}
    )
    matrix["digest"] = preassignment_digest(
        {key: value for key, value in matrix.items() if key != "digest"}
    )
    for validate in (validate_preference_source, archive_validate):
        with pytest.raises((ValueError, IntegrationError)):
            validate(matrix, sources[2], {"fixed_camera_optics": sources[3]})
