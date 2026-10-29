"""Exact fresh relocation commands re-evaluate original navigation without fake success."""

from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from habitat_local_eaios import navigation_dispatch  # noqa: E402
from habitat_local_eaios.model import IntegrationError  # noqa: E402
from habitat_local_eaios.navigation_dispatch import (
    RELOCATION_NAVIGATION_SCHEMA,  # noqa: E402
    NavigationDispatchLifecycle,
    relocation_navigation_declaration,
)
from habitat_local_eaios.stage2_feedback import Stage2ExecutionFeedback  # noqa: E402
from test_navigation_dispatch import OracleCommand  # noqa: E402
from test_relocation_contract import _contract  # noqa: E402


def world() -> Any:
    """Expose only current entity positions and two independent original-shaped actions."""
    names = ("object:sample", "receptacle:destination", "unrelated")
    actions: dict[str, Any] = {
        f"agent_{i}_oracle_nav_action": OracleCommand(names) for i in range(2)
    }
    positions = {name: [float(i), 0.0, 0.0] for i, name in enumerate(names)}
    for action in actions.values():
        action._targets = {
            i: ([float(i), 0.0, 1.0], list(positions[name])) for i, name in enumerate(names)
        }
    return SimpleNamespace(
        positions=positions,
        task=SimpleNamespace(
            actions=actions,
            pddl_problem=SimpleNamespace(
                sim_info=SimpleNamespace(get_entity_pos=lambda entity: positions[entity.name])
            ),
        ),
    )


@pytest.fixture(autouse=True)
def original_action(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the real lifecycle over a stateful facade with no simulator or RNG interface."""
    monkeypatch.setattr(navigation_dispatch, "_original_action_type", lambda: OracleCommand)


def dispatch(
    binding: NavigationDispatchLifecycle, target: str, identity: str, sequence: int
) -> None:
    """Pass an exact original raw-to-local mapping without invoking a skill or world step."""
    binding.dispatch(
        {"name": "nav_to_obj", "arguments": {"target_obj": target, "robot": "agent_0"}},
        {"name": "nav_to_obj", "arguments": {"target_obj": target}},
        identity,
        sequence,
    )


@pytest.mark.parametrize("target_index", [0, 1])
@pytest.mark.parametrize("at_goal", [False, True])
def test_fresh_relocation_recomputes_each_model_selected_target(
    target_index: int, at_goal: bool
) -> None:
    """Object and destination calls rearm once; completion still comes from original evaluation."""
    env = world()
    action, peer = env.task.actions.values()
    action.prev_match_target_id = target_index
    action.at_goal = at_goal
    before_peer = dict(vars(peer))
    cache, pathfinder = action._targets, action.pathfinder
    action.step(target_index)
    assert action.queries == 0 and not action.skill_done
    records: list[dict[str, Any]] = []
    binding = NavigationDispatchLifecycle(env, 0, _contract(), records.append)
    name = action._poss_entities[target_index].name
    for sequence in (1, 2):
        dispatch(binding, name, f"call-{sequence}", sequence)
        assert action.skill_done is False
        assert action.queries == sequence - 1
        action.step(target_index)
        assert action.queries == sequence and action.skill_done is at_goal
    assert action._targets is cache and action.pathfinder is pathfinder
    assert vars(peer) == before_peer
    assert all(row["benchmark_goal_satisfied"] is None for row in records)
    assert all(row["selected_target_cache_invalidated"] is False for row in records)


def test_changed_entity_invalidates_only_its_original_cached_point() -> None:
    """A moved object cannot reuse its old point; all peer, destination and path state stays."""
    env = world()
    action, peer = env.task.actions.values()
    cache = action._targets
    before_peer = dict(vars(peer))
    untouched = deepcopy({i: cache[i] for i in (1, 2)})
    env.positions["object:sample"][0] = 4.0
    records: list[dict[str, Any]] = []
    binding = NavigationDispatchLifecycle(env, 0, _contract(), records.append)
    dispatch(binding, "object:sample", "fresh", 1)
    assert action._targets is cache and 0 not in cache
    assert cache == untouched and vars(peer) == before_peer
    assert action.queries == 0  # Only the original following skill can sample a new point.
    assert records[-1]["selected_target_cache_invalidated"] is True


@pytest.mark.parametrize("failure", ["duplicate-id", "stale-sequence", "peer", "closed", "unknown"])
def test_invalid_delivery_does_not_rearm_or_invalidate(failure: str) -> None:
    """Replays, stale calls, detached owners and unknown targets retain exact original state."""
    env = world()
    binding = NavigationDispatchLifecycle(env, 0, _contract(), lambda row: None)
    dispatch(binding, "object:sample", "old", 1)
    action = env.task.actions["agent_0_oracle_nav_action"]
    action.step(0)
    env.positions["object:sample"][0] = 7.0
    raw: dict[str, Any] = {"name": "nav_to_obj", "arguments": {"target_obj": "object:sample"}}
    mapped: dict[str, Any] = {
        "name": "nav_to_obj",
        "arguments": {**raw["arguments"], "robot": "agent_0"},
    }
    if failure == "peer":
        mapped["arguments"]["robot"] = "agent_1"
    if failure == "closed":
        binding.close()
    if failure == "unknown":
        raw["arguments"]["target_obj"] = "unrelated"
        mapped["arguments"]["target_obj"] = "unrelated"
    before = {**vars(action), "_targets": deepcopy(action._targets)}
    with pytest.raises(IntegrationError, match="malformed or detached"):
        binding.dispatch(
            mapped,
            raw,
            "old" if failure == "duplicate-id" else "new",
            1 if failure == "stale-sequence" else 2,
        )
    assert vars(action) == before


@pytest.mark.parametrize("failure", ["read", "nonfinite", "malformed-cache"])
def test_unavailable_current_target_fails_before_command_mutation(failure: str) -> None:
    """Missing current geometry cannot silently grant use of a known stale cached target."""
    env = world()
    action = env.task.actions["agent_0_oracle_nav_action"]
    if failure == "read":
        env.task.pddl_problem.sim_info.get_entity_pos = lambda entity: [][0]
    elif failure == "nonfinite":
        env.positions["object:sample"] = [float("inf"), 0.0, 0.0]
    else:
        action._targets[0] = None
    before = {**vars(action), "_targets": deepcopy(action._targets)}
    binding = NavigationDispatchLifecycle(env, 0, _contract(), lambda row: None)
    with pytest.raises(IntegrationError, match="target (position|cache)"):
        dispatch(binding, "object:sample", "fresh", 1)
    assert vars(action) == before


def test_feedback_refuses_cross_attempt_or_cross_endpoint_lifecycle() -> None:
    """A valid lifecycle cannot be attached to another attempt or a peer's feedback."""
    env = world()
    contract = _contract()
    binding = NavigationDispatchLifecycle(env, 0, contract, lambda row: None)
    for name, other in [
        ("agent_1", contract),
        (
            "agent_0",
            type(contract)(
                contract.operation,
                contract.expected_destination,
                "different",
                contract.expected_object,
            ),
        ),
    ]:
        with pytest.raises(IntegrationError, match="exact attempt"):
            Stage2ExecutionFeedback({name: other}, lambda row: None, navigation_dispatch=binding)


def test_attempt_call_identity_budget_refuses_before_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Call history cannot grow without bound or renew capacity after original completion."""
    monkeypatch.setattr(navigation_dispatch, "_MAX_CALLS", 2)
    env = world()
    action = env.task.actions["agent_0_oracle_nav_action"]
    binding = NavigationDispatchLifecycle(env, 0, _contract(), lambda row: None)
    for sequence in (1, 2):
        dispatch(binding, "object:sample", f"call-{sequence}", sequence)
        action.step(0)
    before = {**vars(action), "_targets": deepcopy(action._targets)}
    with pytest.raises(IntegrationError, match="malformed or detached"):
        dispatch(binding, "object:sample", "call-3", 3)
    assert vars(action) == before


@pytest.mark.parametrize("recoveries", [0, 2])
@pytest.mark.parametrize(
    "mutation", [None, "scope", "base", "extra", "disabled", "type", "missing", "nested"]
)
def test_independent_actual_profile_readers_bind_new_scope_without_weakening_base(
    tmp_path: Path, recoveries: int, mutation: str | None
) -> None:
    """Both complete source readers check the new envelope and unchanged original deployment."""
    from habitat_local_eaios.relocation_preflight import _check_execution_profile
    from roboguide_eval.b1_deployment_feasibility import (  # type: ignore[import-untyped]
        preflight_deployment_feasibility,
    )
    from test_live_relocation_sources import live_relocation_run

    run = live_relocation_run(tmp_path, version=6, recoveries=recoveries)
    path = run / "evidence/local-how-profile.json"
    base = json.loads(path.read_bytes())
    envelope = {
        "schema_version": RELOCATION_NAVIGATION_SCHEMA,
        "base_profile": base,
        "relocation_navigation_dispatch": relocation_navigation_declaration(),
    }
    if mutation == "scope":
        envelope["relocation_navigation_dispatch"]["operation"] = "mobility.move@v1"
    elif mutation == "type":
        envelope["relocation_navigation_dispatch"]["max_calls_per_attempt"] = 65536.0
    elif mutation == "base":
        base["official_success_authority"] = "local-skill"
    elif mutation == "extra":
        envelope["invented"] = True
    elif mutation == "missing":
        del envelope["relocation_navigation_dispatch"]
    elif mutation == "nested":
        base["schema_version"] = RELOCATION_NAVIGATION_SCHEMA
    elif mutation == "disabled":
        p = run / "b1-deployment-used.json"
        used = json.loads(p.read_bytes())
        used["declaration"]["relocation_completion_binding"] = False
        used["relocation_completion_binding"] = False
        p.write_text(json.dumps(used))
        q = run / "b1-local-execution-profile-required.json"
        req = json.loads(q.read_bytes())
        req["relocation_completion_binding"] = False
        req["deployment_sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
        q.write_text(json.dumps(req))
    path.write_text(json.dumps(envelope))
    frozen = hashlib.sha256((run / "b1-input-used.json").read_bytes()).hexdigest()
    if mutation is None:
        preflight_deployment_feasibility(run)
        assert (
            _check_execution_profile(run, frozen)["local_how_schema"]
            == RELOCATION_NAVIGATION_SCHEMA
        )
    else:
        with pytest.raises(ValueError):
            preflight_deployment_feasibility(run)
        with pytest.raises(IntegrationError):
            _check_execution_profile(run, frozen)
