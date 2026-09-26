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

from habitat_local_eaios.crabagent_backend import CrabAgentBackendConfig  # noqa: E402
from habitat_local_eaios.emos_stage2 import EmosStage2Runtime  # noqa: E402
from habitat_local_eaios.model import CanonicalMobilityInvocation, IntegrationError  # noqa: E402
from habitat_local_eaios.shared_world import SharedEmosStage2Runtime  # noqa: E402
from habitat_local_eaios.spatial_feasibility import (  # noqa: E402
    FloorTransitionProfile,
    assess_spatial_feasibility,
    build_spatial_profile_snapshot,
    load_spatial_profile_snapshot,
)

_ROOT = Path(__file__).parents[3]
_DIGEST = "sha256:" + "0" * 64
_OPERATIONS = ("mobility.move@v1", "mobility.navigate@v1")


def _profile(agent_id: int, support: bool | None) -> FloorTransitionProfile:
    """Create one startup-frozen profile fact independent of robot names."""
    return FloorTransitionProfile(
        agent_id,
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

    problem = SimpleNamespace(
        get_entity=get_entity,
        sim_info=SimpleNamespace(get_entity_pos=get_entity_pos),
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
    assert [profile.support_for("mobility.move@v1") for profile in profiles] == [True, False]
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
        ),
    )
    runtime._require_initialized = lambda: (gym, env, actor, object())  # type: ignore[method-assign]
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
    with pytest.raises(IntegrationError, match="spatial capability evidence initialization failed"):
        runtime.initialize()
    assert closed == [True]
