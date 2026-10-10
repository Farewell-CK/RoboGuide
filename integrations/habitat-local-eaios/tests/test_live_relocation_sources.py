"""Three-endpoint reset sources retain navigation-only peers and exact relocator admission."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from habitat_local_eaios.endpoint_registry import prepare_live_deployment  # noqa: E402
from habitat_local_eaios.model import IntegrationError  # noqa: E402
from habitat_local_eaios.operation_admission import attach_relocation_admission  # noqa: E402
from habitat_local_eaios.planning_world_evidence import (
    build_authoritative_planning_world_evidence,  # noqa: E402
)
from habitat_local_eaios.preassignment_feasibility import (  # noqa: E402
    build_preassignment_feasibility,
    preassignment_digest,
)
from habitat_local_eaios.relocation_preflight import preflight_relocation  # noqa: E402
from habitat_local_eaios.relocation_start import (  # noqa: E402
    build_relocation_start,
    planning_object_sources,
)
from habitat_local_eaios.semantic_evidence import (
    build_authoritative_semantic_evidence,  # noqa: E402
)
from habitat_local_eaios.spatial_feasibility import load_spatial_profile_snapshot  # noqa: E402
from test_endpoint_registry import node_sources  # noqa: E402
from test_relocation_preflight import bound_run  # noqa: E402
from test_relocation_start import relocation_world  # noqa: E402


def live_relocation_run(tmp_path: Path) -> Path:
    """Use real builders over one synthetic reset and actual three-Node config sources."""
    run = bound_run(tmp_path)
    for name in ("relocation-registration-profile.json", "spatial-profile.json"):
        (run / name).unlink()
    node_sources(run, 3)
    prepare_live_deployment(run, 3)
    registry = json.loads((run / "endpoint-registry.json").read_bytes())
    profile = json.loads((run / "relocation-registration-profile.json").read_bytes())
    environment, _ = relocation_world(tmp_path)
    original_data = environment.sim.get_agent_data
    drone = type("DJIDrone", (), {"base_pos": (2.0, 0.0, 0.0), "base_rot": 0.0})()
    environment.sim.get_agent_data = lambda agent: (
        original_data(agent)
        if agent < 2
        else SimpleNamespace(articulated_agent=drone, grasp_mgrs=[])
    )
    semantic = build_authoritative_semantic_evidence(
        environment, run_id=run.name, episode_id="offline", agent_ids=(0, 1, 2)
    )
    start = build_relocation_start(
        environment,
        semantic,
        seed=40,
        registration_digest=profile["digest"],
        agent_ids=(0, 1),
        allow_agent_subset=True,
    )
    spatial = json.loads((run / "spatial-profile.json").read_bytes())
    navigation = build_preassignment_feasibility(
        environment,
        semantic,
        load_spatial_profile_snapshot(run / "spatial-profile.json"),
        spatial["digest"],
        40,
        (0, 1, 2),
    )
    matrix = attach_relocation_admission(navigation, semantic, start, profile)
    matrix.update(
        schema_version="roboguide.deployment-intent-feasibility/v0.5",
        execution_profile={
            "mode": registry["profile"],
            "registry_digest": registry["digest"],
            "endpoints": [
                {key: record[key] for key in ("agent_id", "node_id", "operations")}
                for record in registry["endpoints"]
            ],
        },
    )
    matrix["digest"] = preassignment_digest(
        {key: value for key, value in matrix.items() if key != "digest"}
    )
    planning = build_authoritative_planning_world_evidence(
        environment,
        run_id=run.name,
        episode_id="offline",
        reset_goal_geometry=True,
        object_sources=planning_object_sources(start, semantic),
    )
    evidence = run / "evidence"
    base_how = json.loads((evidence / "local-how-profile.json").read_bytes())
    for name, document in (
        ("authoritative-semantic-evidence.json", semantic),
        ("relocation-episode-start.json", start),
        ("relocation-registration-profile-used.json", profile),
        ("authoritative-planning-world-evidence.json", planning),
        ("preassignment-feasibility.json", matrix),
        ("endpoint-registry-used.json", registry),
        (
            "local-how-profile.json",
            {
                "schema_version": "roboguide.habitat-local-how-profile/v0.9",
                "execution_profile": registry["profile"],
                "endpoint_registry_digest": registry["digest"],
                "observation_enabled": False,
                "base_profile": base_how,
                "unassigned_policy": "original-wait-model-free",
                "official_success_authority": "habitat-pddl",
            },
        ),
    ):
        (evidence / name).write_text(json.dumps(document))
    used_path = run / "b1-deployment-used.json"
    used = json.loads(used_path.read_bytes())
    used["schema_version"] = "roboguide.e1.b1-deployment-used/v0.3"
    used["declaration"].update(
        schema_version="roboguide.e1.b1-deployment/v0.3",
        enable_observation=False,
        endpoints=[f"node-{chr(97 + agent)}.toml" for agent in range(3)],
    )
    used["node_ids"] = [record["node_id"] for record in registry["endpoints"]]
    used_path.write_text(json.dumps(used))
    required_path = run / "b1-local-execution-profile-required.json"
    required = json.loads(required_path.read_bytes())
    required["deployment_sha256"] = hashlib.sha256(used_path.read_bytes()).hexdigest()
    required_path.write_text(json.dumps(required))
    return run


def test_three_endpoint_preflight_accepts_only_actual_relocators(tmp_path: Path) -> None:
    """The nav-only Drone remains in world evidence without invented manipulation readiness."""
    run = live_relocation_run(tmp_path)
    result = preflight_relocation(run)
    assert result["valid"] is True and result["simulator_steps"] == 0
    start = json.loads((run / "evidence/relocation-episode-start.json").read_bytes())
    matrix = json.loads((run / "evidence/preassignment-feasibility.json").read_bytes())
    assert start["schema_version"] == "roboguide.habitat-relocation-start/v0.2"
    assert [record["agent_id"] for record in start["agents"]] == [0, 1]
    assert set(matrix["initial_agent_positions"]) == {"0", "1", "2"}
    assert len(matrix["execution_profile"]["endpoints"]) == 3
    assert len(matrix["operation_admission"]["endpoint_profiles"]) == 2
    # Cross-package runtime conformance; evaluation has its own strict mypy scope.
    from roboguide_eval.b1_deployment_feasibility import (  # type: ignore[import-untyped]
        preflight_deployment_feasibility,
    )

    assert preflight_deployment_feasibility(run) == matrix


@pytest.mark.parametrize(
    "mutation", ["extra-relocator", "source-pose", "registry-support", "base-profile"]
)
def test_three_endpoint_sources_reject_rehashed_forgery(tmp_path: Path, mutation: str) -> None:
    """A valid JSON digest cannot invent Drone manipulation or alter a relocator reset pose."""
    run = live_relocation_run(tmp_path)
    path = run / "evidence/preassignment-feasibility.json"
    matrix: dict[str, Any] = json.loads(path.read_bytes())
    if mutation == "extra-relocator":
        record = dict(matrix["operation_admission"]["endpoint_profiles"][0])
        record.update(agent_id=2, node_id=matrix["execution_profile"]["endpoints"][2]["node_id"])
        matrix["operation_admission"]["endpoint_profiles"].append(record)
    elif mutation == "source-pose":
        matrix["initial_agent_positions"]["0"][0] = 99
    elif mutation == "registry-support":
        registry_path = run / "endpoint-registry.json"
        registry = json.loads(registry_path.read_bytes())
        registry["endpoints"][2]["operations"].append("object.relocate@v1")
        from habitat_local_eaios.endpoint_registry import registry_digest

        registry["digest"] = registry_digest(
            {key: value for key, value in registry.items() if key != "digest"}
        )
        registry_path.write_text(json.dumps(registry))
    else:
        how_path = run / "evidence/local-how-profile.json"
        how = json.loads(how_path.read_bytes())
        how["base_profile"]["navigation_point_resolver"] = "invented-controller"
        how_path.write_text(json.dumps(how))
    matrix["digest"] = preassignment_digest(
        {key: value for key, value in matrix.items() if key != "digest"}
    )
    path.write_text(json.dumps(matrix))
    with pytest.raises(IntegrationError):
        preflight_relocation(run)
