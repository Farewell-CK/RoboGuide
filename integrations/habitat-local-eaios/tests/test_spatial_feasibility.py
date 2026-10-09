"""Deterministic checks for deployment capability and reset-state floor admission."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

import habitat_local_eaios.shared_world as shared_world_module  # noqa: E402
from habitat_local_eaios.crabagent_backend import CrabAgentBackendConfig  # noqa: E402
from habitat_local_eaios.emos_stage2 import EmosStage2Runtime  # noqa: E402
from habitat_local_eaios.model import CanonicalMobilityInvocation, IntegrationError  # noqa: E402
from habitat_local_eaios.preassignment_feasibility import (  # noqa: E402
    PREASSIGNMENT_FEASIBILITY_SCHEMA,
    build_preassignment_feasibility,
)
from habitat_local_eaios.shared_world import SharedEmosStage2Runtime  # noqa: E402
from habitat_local_eaios.spatial_feasibility import (  # noqa: E402
    FloorTransitionProfile,
    _digest,
    assess_spatial_feasibility,
    build_spatial_profile_snapshot,
    load_spatial_profile_snapshot,
    verify_spatial_profile_sources,
)

_ROOT = Path(__file__).parents[3]
_DIGEST = "sha256:" + "0" * 64
_OPERATIONS = ("mobility.move@v1", "mobility.navigate@v1")


def _profile(agent_id: int, support: bool | None) -> FloorTransitionProfile:
    """Create one startup-frozen profile fact independent of robot names."""
    return FloorTransitionProfile(
        agent_id,
        f"node-{agent_id}",
        tuple((operation, support) for operation in _OPERATIONS),
        _DIGEST,
    )


def _invocation(destination: str, task_id: str = "task") -> CanonicalMobilityInvocation:
    """Create one canonical move whose destination is an exact entity id."""
    return CanonicalMobilityInvocation(
        mission_id="mission",
        task_id=task_id,
        group_id="group",
        role_id="role",
        operation="mobility.move@v1",
        objective=f"Navigate to {destination}",
        parameters={"destination": destination},
        resource_ids=("space-1",),
    )


def _region(name: str, floor: str, y: float) -> SimpleNamespace:
    """Build one unique loaded semantic-region AABB for a whole floor."""
    return SimpleNamespace(
        id=name,
        level=SimpleNamespace(id=floor),
        aabb=SimpleNamespace(center=[0.0, y, 0.0], sizes=[20.0, 2.0, 20.0]),
    )


def _environment(
    starts: dict[int, tuple[float, float, float]],
    destinations: dict[str, tuple[float, float, float]],
    *,
    regions: list[SimpleNamespace] | None = None,
    goal_destinations: tuple[str, ...] = (),
    robot_at_thresh: float = 2.0,
) -> SimpleNamespace:
    """Expose only read-only Habitat/PDDL accessors and count no simulator calls."""

    def get_agent_data(agent_id: int) -> SimpleNamespace:
        """Read one actual-reset base position from the fake simulator."""
        return SimpleNamespace(articulated_agent=SimpleNamespace(base_pos=starts[agent_id]))

    def get_entity(name: str) -> str | None:
        """Resolve an exact PDDL entity name without string rewriting."""
        return name if name in destinations else None

    def get_entity_pos(name: str) -> tuple[float, float, float]:
        """Read one official PDDL entity position without mutation."""
        return destinations[name]

    goal_predicates = [
        SimpleNamespace(name="any_at", _arg_values=[SimpleNamespace(name=destination)])
        for destination in goal_destinations
    ]
    goal = SimpleNamespace(
        sub_exprs=goal_predicates
        or [SimpleNamespace(name="any_at", _arg_values=[SimpleNamespace(name="other")])],
        expr_type=SimpleNamespace(value="and"),
        quantifier=None,
        inputs=(),
    )
    problem = SimpleNamespace(
        get_entity=get_entity,
        sim_info=SimpleNamespace(get_entity_pos=get_entity_pos, robot_at_thresh=robot_at_thresh),
        goal=goal,
    )
    sim = SimpleNamespace(
        get_agent_data=get_agent_data,
        semantic_scene=SimpleNamespace(
            regions=regions
            if regions is not None
            else [_region("lower", "floor-lower", 0.0), _region("upper", "floor-upper", 5.0)]
        ),
    )
    return SimpleNamespace(
        sim=sim,
        task=SimpleNamespace(
            pddl_problem=problem,
            get_task_text_context=lambda: {"scene_description": "fake scene"},
        ),
        current_episode=SimpleNamespace(episode_id="episode", scene_id="scene"),
        episodes=[],
        episode_over=False,
    )


def test_exact_node_registration_snapshot_round_trip_and_source_change(tmp_path: Path) -> None:
    """The child consumes typed facts only while original Node config bytes match."""
    source = _ROOT / "scenarios/e1-shared-world-episode-51"
    a = tmp_path / "a.toml"
    b = tmp_path / "b.toml"
    a.write_bytes((source / "node-a.toml").read_bytes())
    b.write_bytes((source / "node-b.toml").read_bytes())
    snapshot = tmp_path / "profile.json"
    snapshot.write_text(
        json.dumps(build_spatial_profile_snapshot(((0, a), (1, b)))),
        encoding="utf-8",
    )
    profiles = load_spatial_profile_snapshot(snapshot)
    assert [profile.node_id for profile in profiles] == ["e1-shared-node-a", "e1-shared-node-b"]
    assert [profile.support_for("mobility.move@v1") for profile in profiles] == [True, False]
    verify_spatial_profile_sources(snapshot, ((0, a), (1, b)))
    tampered = json.loads(snapshot.read_text(encoding="utf-8"))
    tampered["agents"][1]["operation_support"]["mobility.move@v1"] = True
    snapshot.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(IntegrationError, match="digest does not match"):
        load_spatial_profile_snapshot(snapshot)
    snapshot.write_text(
        json.dumps(build_spatial_profile_snapshot(((0, a), (1, b)))),
        encoding="utf-8",
    )
    b.write_bytes(b.read_bytes() + b"\n# changed after snapshot\n")
    with pytest.raises(IntegrationError, match="Node config changed"):
        load_spatial_profile_snapshot(snapshot)


@pytest.mark.parametrize("field", ["operation_support", "node_id", "agent_id"])
def test_resealed_spatial_profile_cannot_replace_node_source_facts(
    tmp_path: Path, field: str
) -> None:
    """A fresh digest cannot replace exact Node capability or endpoint declarations."""
    source = _ROOT / "scenarios/e1-shared-world-episode-51"
    a = tmp_path / "a.toml"
    b = tmp_path / "b.toml"
    a.write_bytes((source / "node-a.toml").read_bytes())
    b.write_bytes((source / "node-b.toml").read_bytes())
    snapshot = tmp_path / "profile.json"
    document = build_spatial_profile_snapshot(((0, a), (1, b)))
    second = cast(dict[str, Any], cast(list[Any], document["agents"])[1])
    if field == "operation_support":
        support = cast(dict[str, bool], second["operation_support"])
        support["mobility.move@v1"] = True
    elif field == "node_id":
        second["node_id"] = "substituted-node"
    else:
        second["agent_id"] = 2
    body = {key: value for key, value in document.items() if key != "digest"}
    document["digest"] = _digest(body)
    snapshot.write_text(json.dumps(document), encoding="utf-8")
    load_spatial_profile_snapshot(snapshot)
    with pytest.raises(IntegrationError, match="differs from configured Node sources"):
        verify_spatial_profile_sources(snapshot, ((0, a), (1, b)))


@pytest.mark.parametrize(
    ("support", "expected"),
    [(False, "incompatible"), (True, "compatible"), (None, "unknown")],
)
def test_cross_floor_decision_uses_actual_reset_start_and_registered_fact(
    support: bool | None, expected: str
) -> None:
    """Only an explicit cross-floor conflict with a false capability rejects."""
    env = _environment({4: (1.0, 0.0, 1.0)}, {"goal": (2.0, 5.0, 2.0)})
    record = assess_spatial_feasibility(env, 4, _invocation("goal"), _profile(4, support))
    assert record["status"] == expected
    assert record["start"] == {
        "position": [1.0, 0.0, 1.0],
        "region_id": "lower",
        "floor_id": "floor-lower",
    }
    assert record["destination_entity"] == {
        "position": [2.0, 5.0, 2.0],
        "region_id": "upper",
        "floor_id": "floor-upper",
    }
    assert record["route_reachability_proven"] is False


def test_distance_goal_on_another_floor_is_not_rejected_as_impossible() -> None:
    """A floor boundary cannot rule out a 3D occupancy goal within its radius."""
    env = _environment(
        {0: (-1.748696, -2.474246, 3.506123), 1: (-7.893826, 0.125754, 4.319351)},
        {"TARGET_any_targets|0": (-3.972340, -1.498180, 3.479910)},
        regions=[
            _region("ground", "floor-0", -1.498180),
            _region("upper", "floor-1", 0.125754),
        ],
        goal_destinations=("TARGET_any_targets|0",),
    )
    record = assess_spatial_feasibility(
        env, 1, _invocation("TARGET_any_targets|0"), _profile(1, False)
    )
    assert record["status"] == "unknown"
    assert record["goal_occupancy"] == "any_at"
    assert record["goal_tolerance_m"] == 2.0
    assert cast(dict[str, Any], record["start"])["floor_id"] == "floor-1"
    assert cast(dict[str, Any], record["destination_entity"])["floor_id"] == "floor-0"
    assert record["route_reachability_proven"] is False


def test_unreadable_goal_does_not_authorize_floor_rejection() -> None:
    """Missing official goal metadata leaves cross-floor evidence unresolved."""
    env = _environment({1: (0.0, 0.0, 0.0)}, {"goal": (1.0, 5.0, 0.0)})
    del env.task.pddl_problem.goal
    record = assess_spatial_feasibility(env, 1, _invocation("goal"), _profile(1, False))
    assert record["status"] == "unknown"
    assert record["goal_occupancy"] == "unavailable"


def test_preassignment_matrix_uses_one_observed_reset_and_exact_intents() -> None:
    """Preassignment evidence records negative facts without creating a Task or acting."""
    env = _environment(
        {0: (0.0, 0.0, 0.0), 1: (0.0, 5.0, 0.0)},
        {"goal": (1.0, 0.0, 0.0)},
    )
    semantic: dict[str, Any] = {
        "identity": {
            "run_id": "run",
            "episode_id": "episode",
            "dataset_revision": "dataset-v1",
            "dataset_sha256": "0" * 64,
        },
        "world_context": {"scene_id": "scene", "entity_catalog": ["goal"]},
        "digest": _DIGEST,
    }
    evidence = build_preassignment_feasibility(
        env, semantic, (_profile(0, True), _profile(1, False)), _DIGEST, 40, (0, 1)
    )
    assert evidence["schema_version"] == PREASSIGNMENT_FEASIBILITY_SCHEMA
    assert evidence["identity"]["habitat_seed"] == 40
    assert evidence["initial_agent_positions"] == {
        "0": [0.0, 0.0, 0.0],
        "1": [0.0, 5.0, 0.0],
    }
    assert [record["status"] for record in evidence["records"]] == [
        "compatible",
        "incompatible",
        "compatible",
        "incompatible",
    ]
    assert all(record["destination"] == "goal" for record in evidence["records"])
    assert all(record["route_reachability_proven"] is False for record in evidence["records"])
    assert evidence["digest"].startswith("sha256:")
    semantic["world_context"]["scene_id"] = "other"
    with pytest.raises(IntegrationError, match="differs from semantic evidence"):
        build_preassignment_feasibility(
            env, semantic, (_profile(0, True), _profile(1, False)), _DIGEST, 40, (0, 1)
        )


def test_preassignment_keeps_a_cross_floor_distance_goal_unresolved() -> None:
    """The reset matrix keeps an official occupancy goal available to Control."""
    env = _environment(
        {0: (0.0, 0.0, 0.0), 1: (0.0, 5.0, 0.0)},
        {"goal": (1.0, 0.0, 0.0)},
        goal_destinations=("goal",),
    )
    semantic: dict[str, Any] = {
        "identity": {
            "run_id": "run",
            "episode_id": "episode",
            "dataset_revision": "dataset-v1",
            "dataset_sha256": "0" * 64,
        },
        "world_context": {"scene_id": "scene", "entity_catalog": ["goal"]},
        "digest": _DIGEST,
    }
    evidence = build_preassignment_feasibility(
        env, semantic, (_profile(0, True), _profile(1, False)), _DIGEST, 40, (0, 1)
    )
    assert [record["status"] for record in evidence["records"]] == [
        "compatible",
        "unknown",
        "compatible",
        "unknown",
    ]
    assert all(record["goal_occupancy"] == "any_at" for record in evidence["records"])


@pytest.mark.parametrize("route_support", [False, True])
def test_shared_world_initialization_freezes_actual_reset_before_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route_support: bool
) -> None:
    """The advertised artifact and later execution share exactly one reset world."""
    source = _ROOT / "scenarios/e1-shared-world-episode-51"
    node_paths = (tmp_path / "a.toml", tmp_path / "b.toml")
    for name, path in zip(("node-a.toml", "node-b.toml"), node_paths, strict=True):
        path.write_bytes((source / name).read_bytes())
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(build_spatial_profile_snapshot(((0, node_paths[0]), (1, node_paths[1])))),
        encoding="utf-8",
    )
    profiles = load_spatial_profile_snapshot(profile_path)
    env = _environment(
        {0: (0.0, 0.0, 0.0), 1: (0.0, 5.0, 0.0)},
        {"goal": (1.0, 0.0, 0.0)},
    )
    resets: list[int] = []
    route_observations: list[dict[str, Any]] = []

    def reset() -> dict[str, int]:
        """Record the one real lifecycle reset in the fake simulator."""
        resets.append(1)
        return {"reset": len(resets)}

    def initialize_vendor(self: EmosStage2Runtime) -> None:
        """Install a vendor-shaped world without importing Habitat or Stage2."""
        self._gym_env = SimpleNamespace(reset=reset)
        self._habitat_env = env
        self._episode = env.current_episode
        self._actor = object()
        self._agent_access = object()
        if route_support:
            self._write_json("local-how-profile.json", {"reset_route_support_enabled": True})
            self._write_json("runtime-source-manifest.json", {"modules": {}})

    def semantic(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Supply an already frozen authoritative semantic identity."""
        del args, kwargs
        assert resets == [1]
        body: dict[str, object] = {
            "schema_version": "roboguide.authoritative-semantic-evidence/v0.2",
            "authority": "environment-authoritative",
            "identity": {
                "run_id": "run",
                "episode_id": "episode",
                "revision": "goal-1",
                "dataset_revision": "dataset-v1",
                "dataset_sha256": "0" * 64,
            },
            "objective_scope": "joint_terminal_state",
            "goal": {"kind": "predicate", "name": "any_at", "arguments": ["goal"]},
            "world_context": {"scene_id": "scene", "agent_ids": [0, 1], "entity_catalog": ["goal"]},
        }
        return {**body, "digest": _digest(body)}

    def planning(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Keep unrelated planning evidence out of this lifecycle check."""
        del args, kwargs
        assert resets == [1]
        return {"schema_version": "offline-planning"}

    def route_probe(
        world: Any,
        semantic: dict[str, Any],
        matrix: dict[str, Any],
        local_how: dict[str, Any],
        sources: dict[str, Any],
    ) -> dict[str, Any]:
        """Observe the same prepared reset exactly once when explicitly enabled."""
        assert world is env and resets == [1]
        assert matrix["identity"]["semantic_evidence_digest"] == semantic["digest"]
        assert local_how["reset_route_support_enabled"] is True
        assert "habitat_sim._ext.habitat_sim_bindings" in sources["modules"]
        route_observations.append(matrix)
        return {"reset_count": 1, "diagnostic_only": True}

    monkeypatch.setattr(EmosStage2Runtime, "initialize", initialize_vendor)
    monkeypatch.setattr(shared_world_module, "build_authoritative_semantic_evidence", semantic)
    monkeypatch.setattr(
        shared_world_module, "build_authoritative_planning_world_evidence", planning
    )
    monkeypatch.setattr(shared_world_module, "build_reset_route_support", route_probe)
    monkeypatch.setattr(
        shared_world_module,
        "build_runtime_source_manifest",
        lambda modules: {
            "modules": {
                "habitat_sim._ext.habitat_sim_bindings": {
                    "path": "/offline/bindings.so",
                    "sha256": "a" * 64,
                }
            },
        },
    )
    runtime = SharedEmosStage2Runtime(
        CrabAgentBackendConfig(
            config_path=tmp_path / "unused.yaml",
            episode_id="episode",
            agent_id=0,
            max_steps=10,
            step_period_ms=0,
            seed=40,
            evidence_dir=tmp_path,
            run_id="run",
            spatial_capabilities=profiles,
            spatial_profile_path=profile_path,
            goal_region_navigation=route_support,
            reset_route_support=route_support,
        ),
        (0, 1),
    )
    runtime.initialize()
    evidence = json.loads((tmp_path / "preassignment-feasibility.json").read_text())
    assert resets == [1]
    assert len(route_observations) == int(route_support)
    assert (tmp_path / "reset-route-support.json").exists() is route_support
    assert runtime._prepared_observations == {"reset": 1}
    assert evidence["identity"]["episode_reset_count"] == 1
    assert evidence["initial_agent_positions"] == {
        "0": [0.0, 0.0, 0.0],
        "1": [0.0, 5.0, 0.0],
    }
    with pytest.raises(IntegrationError, match="already been reset"):
        runtime._prepare_reset()
    assert resets == [1]
    runtime.initialize()
    assert resets == [1] and len(route_observations) == int(route_support)


def test_habitat_vector_object_is_read_as_three_coordinates() -> None:
    """Magnum-style x/y/z vectors must not silently disable the live guard."""
    env = _environment({1: (0.0, 5.0, 0.0)}, {"goal": (1.0, 0.0, 1.0)})
    vector = SimpleNamespace(x=1.0, y=0.0, z=1.0)
    env.task.pddl_problem.sim_info.get_entity_pos = lambda _entity: vector
    env.sim.get_agent_data = lambda _agent_id: SimpleNamespace(
        articulated_agent=SimpleNamespace(base_pos=SimpleNamespace(x=0.0, y=5.0, z=0.0))
    )
    record = assess_spatial_feasibility(env, 1, _invocation("goal"), _profile(1, False))
    assert record["status"] == "incompatible"
    start = cast(dict[str, object], record["start"])
    destination = cast(dict[str, object], record["destination_entity"])
    assert start["position"] == [0.0, 5.0, 0.0]
    assert destination["position"] == [1.0, 0.0, 1.0]


def test_same_floor_and_unresolved_floor_do_not_invent_reachability() -> None:
    """A known same-floor target admits while an unlocated target stays unknown."""
    env = _environment({2: (1.0, 0.0, 1.0)}, {"goal": (2.0, 0.0, 2.0)})
    same = assess_spatial_feasibility(env, 2, _invocation("goal"), _profile(2, False))
    assert same["status"] == "compatible"
    assert same["route_reachability_proven"] is False
    env.sim.semantic_scene.regions = []
    unknown = assess_spatial_feasibility(env, 2, _invocation("goal"), _profile(2, False))
    assert unknown["status"] == "unknown"
    assert "no unique semantic floor" in str(unknown["reason"])


def test_overlapping_regions_on_one_floor_still_prove_cross_floor_conflict() -> None:
    """Multiple regions on one floor must not hide a registered floor mismatch."""
    regions = [
        _region("start", "floor-upper", 5.0),
        _region("hallway", "floor-lower", 0.0),
        _region("stairs", "floor-lower", 0.0),
    ]
    env = _environment(
        {1: (0.0, 5.0, 0.0)},
        {"goal": (1.0, 0.0, 1.0)},
        regions=regions,
    )
    record = assess_spatial_feasibility(env, 1, _invocation("goal"), _profile(1, False))
    assert record["status"] == "incompatible"
    assert record["start"] == {
        "position": [0.0, 5.0, 0.0],
        "region_id": "start",
        "floor_id": "floor-upper",
    }
    assert record["destination_entity"] == {
        "position": [1.0, 0.0, 1.0],
        "region_id": None,
        "floor_id": "floor-lower",
    }


def test_overlapping_regions_across_floors_remain_unknown() -> None:
    """Conflicting loaded floor memberships cannot authorize a floor claim."""
    regions = [
        _region("start", "floor-upper", 5.0),
        _region("goal-lower", "floor-lower", 0.0),
        _region("goal-other", "floor-other", 0.0),
    ]
    env = _environment(
        {1: (0.0, 5.0, 0.0)},
        {"goal": (1.0, 0.0, 1.0)},
        regions=regions,
    )
    record = assess_spatial_feasibility(env, 1, _invocation("goal"), _profile(1, False))
    assert record["status"] == "unknown"
    assert record["destination_entity"] == {
        "position": [1.0, 0.0, 1.0],
        "region_id": None,
        "floor_id": None,
    }


def test_missing_profile_or_pddl_entity_retains_explicit_unknown() -> None:
    """Missing authoritative facts never become a guessed incompatibility."""
    env = _environment({0: (1.0, 0.0, 1.0)}, {"actual": (2.0, 5.0, 2.0)})
    assert assess_spatial_feasibility(env, 0, _invocation("actual"), None)["status"] == "unknown"
    missing = assess_spatial_feasibility(env, 0, _invocation("wrong"), _profile(0, False))
    assert missing["status"] == "unknown"
    assert "unresolved" in str(missing["reason"])


def test_pair_guard_rejects_before_actor_and_step_and_preserves_evidence(tmp_path: Path) -> None:
    """Same-floor region overlap cannot hide a pair's incompatible assignment."""
    env = _environment(
        {0: (0.0, 0.0, 0.0), 1: (0.0, 5.0, 0.0)},
        {"left": (1.0, 0.0, 0.0), "right": (1.0, 0.0, 0.0)},
        regions=[
            _region("lower-hallway", "floor-lower", 0.0),
            _region("lower-stairs", "floor-lower", 0.0),
            _region("upper", "floor-upper", 5.0),
        ],
    )
    gym = SimpleNamespace(reset_calls=0, step_calls=0)

    def reset() -> dict[str, Any]:
        """Record one real reset boundary without stepping the fake world."""
        gym.reset_calls += 1
        return {}

    gym.reset = reset
    actor = SimpleNamespace(act_calls=0)
    runtime = SharedEmosStage2Runtime.__new__(SharedEmosStage2Runtime)
    runtime._agent_ids = (0, 1)
    runtime._config = CrabAgentBackendConfig(
        config_path=tmp_path / "unused.yaml",
        episode_id="episode",
        agent_id=0,
        max_steps=30,
        step_period_ms=0,
        evidence_dir=tmp_path,
        spatial_capabilities=(_profile(0, True), _profile(1, False)),
    )
    runtime._episode = object()
    runtime._video = cast(Any, None)
    runtime._diagnostics = cast(
        Any,
        SimpleNamespace(
            record_reset=lambda *_args: None,
            record_terminal=lambda *_args: None,
            install_nav_probes=lambda *_args: None,
        ),
    )
    runtime._require_initialized = lambda: (gym, env, actor, object())  # type: ignore[method-assign]
    runtime._prepared_observations = None
    runtime._prepare_reset()
    running: list[int] = []
    with pytest.raises(IntegrationError, match="spatial feasibility rejected"):
        runtime.execute_pair(
            {0: _invocation("left", "task-left"), 1: _invocation("right", "task-right")},
            lambda: False,
            lambda agent_id, _detail: running.append(agent_id),
        )
    assert gym.reset_calls == 1
    assert gym.step_calls == 0
    assert actor.act_calls == 0
    assert running == []
    archived = json.loads((tmp_path / "spatial-feasibility.json").read_text(encoding="utf-8"))
    assert archived["all_admitted"] is False
    assert archived["execution_allowed"] is False
    assert [item["status"] for item in archived["agent_records"]] == ["compatible", "incompatible"]
    assert all(
        item["destination_entity"]["floor_id"] == "floor-lower"
        for item in archived["agent_records"]
    )
    assert all(
        item["destination_entity"]["region_id"] is None for item in archived["agent_records"]
    )
    assert "pddl_success" not in archived


def test_unknown_floor_does_not_claim_admission_or_block_execution(tmp_path: Path) -> None:
    """Ambiguous geometry stays visibly unresolved without guessing a conflict."""
    env = _environment(
        {0: (0.0, 0.0, 0.0), 1: (0.0, 5.0, 0.0)},
        {"left": (1.0, 0.0, 0.0), "right": (1.0, 0.0, 0.0)},
        regions=[],
    )
    runtime = SharedEmosStage2Runtime.__new__(SharedEmosStage2Runtime)
    runtime._config = CrabAgentBackendConfig(
        config_path=tmp_path / "unused.yaml",
        episode_id="episode",
        agent_id=0,
        max_steps=30,
        step_period_ms=0,
        evidence_dir=tmp_path,
        spatial_capabilities=(_profile(0, True), _profile(1, False)),
    )
    runtime._admit_pair_spatial_feasibility({0: _invocation("left"), 1: _invocation("right")}, env)
    archived = json.loads((tmp_path / "spatial-feasibility.json").read_text(encoding="utf-8"))
    assert [record["status"] for record in archived["agent_records"]] == ["unknown", "unknown"]
    assert archived["all_admitted"] is False
    assert archived["execution_allowed"] is True


def test_profile_used_evidence_failure_closes_initialized_world(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Startup evidence failure cannot leave a ready simulator behind."""
    monkeypatch.setattr(EmosStage2Runtime, "initialize", lambda _self: None)
    runtime = SharedEmosStage2Runtime.__new__(SharedEmosStage2Runtime)
    runtime._agent_ids = (0, 1)
    runtime._config = CrabAgentBackendConfig(
        config_path=tmp_path / "unused.yaml",
        episode_id="episode",
        agent_id=0,
        max_steps=30,
        step_period_ms=0,
        evidence_dir=tmp_path,
        spatial_capabilities=(_profile(0, True), _profile(1, False)),
        spatial_profile_path=tmp_path / "missing-profile.json",
    )
    runtime._require_initialized = lambda: (object(), object(), object(), object())  # type: ignore[method-assign]
    closed: list[bool] = []
    runtime.close = lambda: closed.append(True)  # type: ignore[method-assign]
    with pytest.raises(IntegrationError, match="shared-world initialization failed"):
        runtime.initialize()
    assert closed == [True]
