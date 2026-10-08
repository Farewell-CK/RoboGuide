"""Deterministic registration/reset-source tests without a simulator or Provider."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from mission.grounding_context import GroundingContextSnapshot
from mission.planning_world_evidence import AuthoritativePlanningWorldEvidence

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.model import CanonicalRelocationInvocation, IntegrationError  # noqa: E402
from habitat_local_eaios.relocation_deployment import (  # noqa: E402
    _digest as profile_digest,
)
from habitat_local_eaios.relocation_deployment import (  # noqa: E402
    build_relocation_profile,
    load_relocation_profile,
    verify_loaded_robots,
    verify_relocation_profile_sources,
)
from habitat_local_eaios.relocation_start import (  # noqa: E402
    MAX_ARTIFACT_BYTES,
    _digest,
    admit_relocation_source,
    build_relocation_start,
    planning_object_sources,
    source_location_id,
    validate_relocation_start,
)
from habitat_local_eaios.semantic_evidence import (  # noqa: E402
    build_authoritative_semantic_evidence,  # noqa: E402
)

SCENARIO = INTEGRATION_ROOT.parents[1] / "scenarios/e1-shared-world-relocation"
DATASET_BYTES = b"offline dataset bytes, not a Habitat fixture"


class ObservedPddlInfo:
    """Expose the existing exact entity-position reads, without a predicate or action method."""

    def __init__(self) -> None:
        """Keep distinct initial object/destination positions and a read counter."""
        self.positions = {
            "object:0": [0.0, 1.0, 0.0],
            "object:1": [1.0, 1.0, 0.0],
            "destination:0": [4.0, 1.0, 0.0],
            "destination:1": [5.0, 1.0, 0.0],
        }
        self.reads = 0

    def check_type_matches(self, entity: Any, kind: str) -> bool:
        """Mirror exact loaded entity typing rather than infer physical types from goals."""
        return bool(entity.kind == kind)

    def get_entity_pos(self, entity: Any) -> list[float]:
        """Return copied observed coordinates without changing the fake world's state."""
        self.reads += 1
        return list(self.positions[entity.name])


def relocation_world(tmp_path: Path) -> tuple[Any, dict[str, Any]]:
    """Build original-shaped reset surfaces and real identity-checked semantic evidence."""
    info = ObservedPddlInfo()
    entities = {
        name: SimpleNamespace(
            name=name,
            kind=("movable_entity_type" if name.startswith("object:") else "goal_entity_type"),
        )
        for name in info.positions
    }
    predicates = [
        SimpleNamespace(
            name="at", _arg_values=[entities[f"object:{i}"], entities[f"destination:{i}"]]
        )
        for i in (0, 1)
    ]
    problem = SimpleNamespace(
        sim_info=info,
        get_entity=entities.get,
        get_ordered_entities_list=lambda: list(entities.values()),
        goal=SimpleNamespace(
            sub_exprs=predicates, expr_type=SimpleNamespace(value="and"), inputs=[], quantifier=None
        ),
    )
    robots = []
    for index, name in enumerate(("FetchRobot", "StretchRobot")):
        robot = type(name, (), {})()
        robot.base_pos = (float(index), 0.0, 0.0)
        robot.base_rot = 0.0
        robots.append(
            SimpleNamespace(articulated_agent=robot, grasp_mgrs=[SimpleNamespace(is_grasped=False)])
        )
    dataset = tmp_path / "offline-dataset.json.gz"
    dataset.write_bytes(DATASET_BYTES)
    environment = SimpleNamespace(
        _dataset=SimpleNamespace(
            config=SimpleNamespace(data_path=str(dataset), split="offline"),
            content_scenes_path=str(tmp_path / "absent/{scene}.json.gz"),
        ),
        sim=SimpleNamespace(get_agent_data=robots.__getitem__),
        task=SimpleNamespace(
            pddl_problem=problem,
            actions={},
            get_task_text_context=lambda: {"scene_description": "offline world"},
        ),
        current_episode=SimpleNamespace(episode_id="offline", scene_id="scene"),
        episode_over=False,
        get_metrics=lambda: {"pddl_success": False},
        episodes=[],
    )
    semantic = build_authoritative_semantic_evidence(
        environment, run_id="run-offline", episode_id="offline", agent_ids=(0, 1)
    )
    return environment, semantic


def relocation_snapshot(environment: Any, semantic: dict[str, Any]) -> dict[str, Any]:
    """Capture actual fake-reset sources through the production read-only builder."""
    return build_relocation_start(
        environment, semantic, seed=40, registration_digest="sha256:" + "a" * 64, agent_ids=(0, 1)
    )


def source_for(index: int) -> str:
    """Name the fixed observed fixture location without shared temporary files."""
    return source_location_id(
        {
            "run_id": "run-offline",
            "episode_id": "offline",
            "scene_id": "scene",
            "dataset_revision": "offline-dataset",
            "dataset_sha256": hashlib.sha256(DATASET_BYTES).hexdigest(),
            "habitat_seed": 40,
        },
        f"object:{index}",
        [float(index), 1.0, 0.0],
    )


def invocation(snapshot: dict[str, Any], index: int = 0) -> CanonicalRelocationInvocation:
    """Construct the exact canonical operation without modifying any incoming argument."""
    return CanonicalRelocationInvocation.from_request(
        {
            "invocation": {
                "mission_id": "mission",
                "group_id": "group",
                "task_id": "task",
                "role_id": "role",
                "attempt_id": "attempt",
                "operation": "object.relocate@v1",
                "objective": "Relocate this object.",
                "parameters": {
                    "object": f"object:{index}",
                    "source": snapshot["objects"][index]["source_entity_id"],
                    "destination": f"destination:{index}",
                },
                "resource_ids": ["space-slot"],
            }
        }
    )


def test_actual_reset_sources_are_neutral_and_bound(tmp_path: Path) -> None:
    """Reset reads retain real positions, empty-gripper evidence and object source references."""
    environment, semantic = relocation_world(tmp_path)
    before = copy.deepcopy(environment.task.pddl_problem.sim_info.positions)
    snapshot = relocation_snapshot(environment, semantic)
    validate_relocation_start(snapshot, semantic)
    assert snapshot["identity"]["habitat_seed"] == 40
    assert snapshot["simulator_steps"] == 0 and snapshot["reset_count"] == 1
    assert snapshot["agents"][0]["rotation_yaw_rad"] == 0.0
    assert snapshot["objects"][0]["position"] == [0.0, 1.0, 0.0]
    assert snapshot["objects"][0]["source_entity_id"].startswith("initial-location:")
    assert environment.task.pddl_problem.sim_info.positions == before
    assert environment.task.pddl_problem.sim_info.reads == 4
    sources = planning_object_sources(snapshot, semantic)
    assert sources[0]["evidence_digest"] == snapshot["digest"]
    assert "node_id" not in json.dumps(sources) and "position" not in json.dumps(sources)
    admit_relocation_source(environment, invocation(snapshot), 0, snapshot, semantic)


@pytest.mark.parametrize(
    "mutation",
    [
        "source_alias",
        "source_other_object",
        "digest",
        "run",
        "episode",
        "scene",
        "dataset_revision",
        "dataset_sha256",
        "registration",
        "over_budget",
    ],
)
def test_invalid_source_evidence_fails_closed(tmp_path: Path, mutation: str) -> None:
    """Recomputed digests cannot launder object aliases, wrong identity or invalid declarations."""
    environment, semantic = relocation_world(tmp_path)
    snapshot = relocation_snapshot(environment, semantic)
    if mutation.startswith("source_"):
        snapshot["objects"][0]["source_entity_id"] = (
            "object:0" if mutation == "source_alias" else snapshot["objects"][1]["source_entity_id"]
        )
    elif mutation == "registration":
        snapshot["registration_profile_digest"] = "missing"
    elif mutation == "over_budget":
        snapshot["gaps"] = [{"reason": "x" * MAX_ARTIFACT_BYTES}]
    elif mutation == "digest":
        snapshot["objects"][0]["position"][0] = 9.0
    else:
        field = {"run": "run_id", "episode": "episode_id", "scene": "scene_id"}.get(
            mutation, mutation
        )
        snapshot["identity"][field] = "different"
    with pytest.raises(IntegrationError):
        if mutation != "digest":
            snapshot["digest"] = _digest(
                {key: value for key, value in snapshot.items() if key != "digest"}
            )
        validate_relocation_start(snapshot, semantic)


@pytest.mark.parametrize("missing", ["object_position", "grasp", "held"])
def test_unavailable_or_held_reset_is_archivable_but_not_ready(
    tmp_path: Path, missing: str
) -> None:
    """Incomplete raw evidence is retained and never becomes a guessed valid source."""
    environment, semantic = relocation_world(tmp_path)
    if missing == "object_position":
        del environment.task.pddl_problem.sim_info.positions["object:0"]
    elif missing == "grasp":
        environment.sim.get_agent_data(0).grasp_mgrs = []
    else:
        environment.sim.get_agent_data(0).grasp_mgrs[0].is_grasped = True
    snapshot = relocation_snapshot(environment, semantic)
    assert snapshot["complete"] is False
    assert snapshot["objects"]
    with pytest.raises(IntegrationError, match="incomplete"):
        validate_relocation_start(snapshot, semantic)


@pytest.mark.parametrize("changed", ["source", "object", "grasp", "episode", "destination"])
def test_assignment_source_checked_again_before_policy(tmp_path: Path, changed: str) -> None:
    """Changed inputs and actual world state cannot execute under an old valid source snapshot."""
    environment, semantic = relocation_world(tmp_path)
    snapshot = relocation_snapshot(environment, semantic)
    request = invocation(snapshot).as_dict()
    if changed == "source":
        cast(dict[str, Any], request["parameters"])["source"] = "object:0"
    elif changed == "object":
        environment.task.pddl_problem.sim_info.positions["object:0"][0] += 1.0
    elif changed == "destination":
        environment.task.pddl_problem.sim_info.positions["destination:0"][0] += 1.0
    elif changed == "grasp":
        environment.sim.get_agent_data(0).grasp_mgrs[0].is_grasped = True
    else:
        environment.current_episode.episode_id = "different"
    with pytest.raises(IntegrationError):
        admit_relocation_source(
            environment,
            CanonicalRelocationInvocation.from_request({"invocation": request}),
            0,
            snapshot,
            semantic,
        )


def test_optional_rotation_unavailable_does_not_fabricate_yaw(tmp_path: Path) -> None:
    """An unavailable optional rotation remains null while source and grasp facts stay valid."""
    environment, semantic = relocation_world(tmp_path)
    environment.sim.get_agent_data(0).articulated_agent.base_rot = object()
    snapshot = relocation_snapshot(environment, semantic)
    validate_relocation_start(snapshot, semantic)
    assert snapshot["agents"][0]["rotation_yaw_rad"] is None


def test_registration_profile_cli_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deployment-side command emits and verifies the same source-bound profile artifact."""
    from habitat_local_eaios import relocation_deployment

    output = tmp_path / "registration.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "relocation_deployment",
            "--node",
            f"0={SCENARIO / 'node-a.toml'}",
            "--node",
            f"1={SCENARIO / 'node-b.toml'}",
            "--output",
            str(output),
        ],
    )
    relocation_deployment.main()
    assert json.loads(output.read_text())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "relocation_deployment",
            "--node",
            f"0={SCENARIO / 'node-a.toml'}",
            "--node",
            f"1={SCENARIO / 'node-b.toml'}",
            "--output",
            str(output),
            "--verify-sources",
        ],
    )
    relocation_deployment.main()


def test_registration_profile_cli_rejects_unbound_verify_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verification must compare caller-supplied Node sources with the frozen profile."""
    from habitat_local_eaios import relocation_deployment

    output = tmp_path / "registration.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "relocation_deployment",
            "--node",
            f"0={SCENARIO / 'node-a.toml'}",
            "--node",
            f"1={SCENARIO / 'node-b.toml'}",
            "--output",
            str(output),
        ],
    )
    relocation_deployment.main()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "relocation_deployment",
            "--node",
            f"0={SCENARIO / 'node-a.toml'}",
            "--node",
            f"1={SCENARIO / 'node-a.toml'}",
            "--output",
            str(output),
            "--verify-sources",
        ],
    )
    with pytest.raises(SystemExit, match="do not match"):
        relocation_deployment.main()


def test_registered_templates_freeze_real_capabilities_and_capacity(tmp_path: Path) -> None:
    """Fetch/Stretch register relocation and exclusive space without invented payload limits."""
    profile = build_relocation_profile(
        tuple((i, SCENARIO / f"node-{suffix}.toml") for i, suffix in enumerate(("a", "b")))
    )
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile))
    assert load_relocation_profile(path) == profile
    verify_relocation_profile_sources(path)
    environment, _ = relocation_world(tmp_path)
    verify_loaded_robots(profile, environment, (0, 1))
    assert [record["robot_type"] for record in profile["agents"]] == ["FetchRobot", "StretchRobot"]
    assert all(
        record["capability_attributes"] == {} and record["resource"]["capacity"] == 1
        for record in profile["agents"]
    )
    with pytest.raises(IntegrationError, match="cover"):
        verify_loaded_robots(profile, environment, (0,))
    environment.sim.get_agent_data(0).articulated_agent = SimpleNamespace()
    with pytest.raises(IntegrationError, match="robot"):
        verify_loaded_robots(profile, environment, (0, 1))


@pytest.mark.parametrize(
    "change",
    [
        "missing_operation",
        "wrong_readiness",
        "capacity",
        "wrong_owner",
        "missing_type",
        "missing_lock",
    ],
)
def test_bad_registration_cannot_produce_profile(tmp_path: Path, change: str) -> None:
    """Admission checks declarations without assuming all manipulators are equivalent."""
    source = (SCENARIO / "node-a.toml").read_text()
    if change == "missing_operation":
        source = source[: source.index('[[operations]]\noperation = "object.relocate@v1"')]
    elif change == "wrong_readiness":
        source = source.replace("/v1/capabilities/object.relocate@v1", "/v1/health")
    elif change == "capacity":
        source = source.replace("capacity = 1", "capacity = 2")
    elif change == "wrong_owner":
        source = source.replace(
            'operation = "object.relocate@v1"\nowner = "habitat-local-eaios"',
            'operation = "object.relocate@v1"\nowner = "other"',
        )
    elif change == "missing_type":
        source = source.replace('"roboguide.habitat-robot-type" = "FetchRobot"', "")
    else:
        source = source.replace('local_locks = ["habitat-simulator-a"]', "local_locks = []")
    node = tmp_path / "node.toml"
    node.write_text(source)
    with pytest.raises(IntegrationError):
        build_relocation_profile(((0, node),))


def test_changed_source_and_rehashed_profile_are_rejected(tmp_path: Path) -> None:
    """Node source changes and forged snapshot attributes are distinct fail-closed cases."""
    node = tmp_path / "node.toml"
    node.write_text((SCENARIO / "node-a.toml").read_text())
    profile = build_relocation_profile(((0, node),))
    path = tmp_path / "profile.json"
    profile["agents"][0]["robot_type"] = "SpotRobot"
    profile["digest"] = profile_digest(
        {key: value for key, value in profile.items() if key != "digest"}
    )
    path.write_text(json.dumps(profile))
    with pytest.raises(IntegrationError, match="differs"):
        verify_relocation_profile_sources(path)
    node.write_text(node.read_text() + "\n# source changed\n")
    with pytest.raises(IntegrationError, match="source changed"):
        load_relocation_profile(path)


def test_neutral_sources_reach_frozen_grounding(tmp_path: Path) -> None:
    """Adapter sources round-trip through MI while positions and Node IDs stay local."""
    from habitat_local_eaios.planning_world_evidence import (
        build_authoritative_planning_world_evidence,
    )
    from mission.semantic_evidence import AuthoritativeSemanticEvidence

    environment, semantic = relocation_world(tmp_path)
    start = relocation_snapshot(environment, semantic)
    planning = build_authoritative_planning_world_evidence(
        environment,
        run_id="run-offline",
        episode_id="offline",
        reset_goal_geometry=True,
        object_sources=planning_object_sources(start, semantic),
    )
    restored = AuthoritativePlanningWorldEvidence.from_json(planning)
    grounding = GroundingContextSnapshot.create(
        "request",
        "sha256:" + "b" * 64,
        1,
        semantic_evidence=AuthoritativeSemanticEvidence.from_json(semantic),
        planning_world_evidence=restored,
    )
    assert GroundingContextSnapshot.from_json(grounding.to_json()) == grounding
    assert restored.object_sources[0].source_entity_id == start["objects"][0]["source_entity_id"]


@pytest.mark.parametrize("incomplete", [False, True])
def test_shared_initialization_records_one_reset_before_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, incomplete: bool
) -> None:
    """Startup binds one actual reset and archives unavailable sources before refusal."""
    from habitat_local_eaios.crabagent_backend import CrabAgentBackendConfig
    from habitat_local_eaios.emos_stage2 import EmosStage2Runtime
    from habitat_local_eaios.shared_world import SharedEmosStage2Runtime
    from test_shared_world import RecordingDiagnostics

    profile = build_relocation_profile(
        tuple((i, SCENARIO / f"node-{suffix}.toml") for i, suffix in enumerate(("a", "b")))
    )
    path = tmp_path / "registration.json"
    path.write_text(json.dumps(profile))
    environment, _ = relocation_world(tmp_path)
    if incomplete:
        environment.sim.get_agent_data(1).grasp_mgrs = []
    resets: list[str] = []

    def reset() -> dict[str, object]:
        """Expose the one existing simulator reset without a physical step or model."""
        resets.append("reset")
        return {"reset-observation": True}

    def install(runtime: EmosStage2Runtime) -> None:
        """Inject the normally constructed surfaces; run the production shared initialize next."""
        runtime._gym_env = SimpleNamespace(reset=reset, close=lambda: None)
        runtime._habitat_env = environment
        runtime._actor = SimpleNamespace()
        runtime._agent_access = SimpleNamespace()
        runtime._episode = environment.current_episode
        runtime._relocation_capability = {"ready": True}

    monkeypatch.setattr(EmosStage2Runtime, "initialize", install)
    runtime = SharedEmosStage2Runtime(
        CrabAgentBackendConfig(
            config_path=tmp_path / "unused.yaml",
            episode_id="offline",
            agent_id=0,
            run_id="run-offline",
            seed=40,
            max_steps=20,
            step_period_ms=0,
            evidence_dir=tmp_path / "evidence",
            enable_relocation=True,
            relocation_profile_path=path,
        ),
        (0, 1),
    )
    runtime._diagnostics = cast(Any, RecordingDiagnostics())
    if incomplete:
        with pytest.raises(IntegrationError, match="incomplete"):
            runtime.initialize()
        saved = json.loads((runtime._evidence_dir() / "relocation-episode-start.json").read_text())
        assert saved["complete"] is False
        with pytest.raises(IntegrationError, match="readiness is unavailable"):
            runtime.supported_operations()
    else:
        runtime.initialize()
        runtime.initialize()
        assert "object.relocate@v1" in runtime.supported_operations()
        start = json.loads((runtime._evidence_dir() / "relocation-episode-start.json").read_text())
        assert start["registration_profile_digest"] == profile["digest"]
        planning = AuthoritativePlanningWorldEvidence.load(
            runtime._evidence_dir() / "authoritative-planning-world-evidence.json"
        )
        assert planning.object_sources[0].evidence_digest == start["digest"]
        runtime.close()
    assert resets == ["reset"]


def test_missing_registration_fails_before_runtime_construction(tmp_path: Path) -> None:
    """A flag cannot advertise manipulation readiness without the configured Node evidence."""
    from habitat_local_eaios.crabagent_backend import CrabAgentBackendConfig
    from habitat_local_eaios.shared_world import SharedEmosStage2Runtime

    runtime = SharedEmosStage2Runtime(
        CrabAgentBackendConfig(
            config_path=tmp_path / "absent.yaml",
            episode_id="offline",
            agent_id=0,
            evidence_dir=tmp_path,
            max_steps=20,
            step_period_ms=0,
            enable_relocation=True,
        ),
        (0, 1),
    )
    with pytest.raises(IntegrationError, match="registration profile"):
        runtime.initialize()
    assert runtime._gym_env is None
