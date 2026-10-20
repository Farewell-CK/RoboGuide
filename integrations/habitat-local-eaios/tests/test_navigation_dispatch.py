"""Fresh exact-target commands must recompute arrival without resetting the world."""

from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from habitat_local_eaios import navigation_dispatch  # noqa: E402
from habitat_local_eaios.model import IntegrationError  # noqa: E402
from habitat_local_eaios.navigation_dispatch import NavigationDispatchLifecycle  # noqa: E402
from habitat_local_eaios.stage2_contract import (  # noqa: E402
    Stage2ContractViolation,
    Stage2ExecutionContract,
    install_stage2_contract_guard,
)
from habitat_local_eaios.stage2_feedback import Stage2ExecutionFeedback  # noqa: E402
from test_observation_control import ObservationModel, _control, _sample  # noqa: E402
from test_stage2_feedback import VendorAgent  # noqa: E402


class OracleCommand:
    """Expose the original completed-target suppression independently of world arrival."""

    def __init__(self, targets: tuple[str, ...] = ("marker:north", "marker:south")) -> None:
        """Retain a sampled target cache and original pathfinder across new commands."""
        self._poss_entities = [SimpleNamespace(name=name) for name in targets]
        self._targets = {0: ("original-navigation-point", "original-object-point")}
        self.pathfinder = object()
        self.prev_nav_done = True
        self.prev_match_target_id = 0
        self.skill_done = True
        self.queries = 0
        self.at_goal = True

    def step(self, target: int) -> None:
        """Reproduce the vendor latch branch; only actual evaluation can set completion."""
        self.skill_done = False
        if self.prev_nav_done and target == self.prev_match_target_id:
            return
        self.queries += 1
        self.skill_done = self.at_goal
        self.prev_nav_done = self.at_goal
        if self.at_goal:
            self.prev_match_target_id = target


def environment() -> Any:
    """Return two independently owned action objects, with no step or sensor interface."""
    return SimpleNamespace(
        task=SimpleNamespace(
            actions={f"agent_{agent}_oracle_nav_action": OracleCommand() for agent in range(2)}
        )
    )


@pytest.fixture(autouse=True)
def action_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bind the vendor type boundary to this deterministic, stateful original-shaped action."""
    monkeypatch.setattr(navigation_dispatch, "_original_action_type", lambda: OracleCommand)


@pytest.mark.parametrize("still_at_goal", [False, True])
def test_repeated_completed_target_recomputes_actual_arrival(still_at_goal: bool) -> None:
    """The old branch stalls; a fresh command rechecks instead of reusing a true receipt."""
    world = environment()
    action, peer = world.task.actions.values()
    action.at_goal = still_at_goal
    action.step(0)
    action.step(0)
    assert action.queries == 0 and action.skill_done is False
    cache, pathfinder, peer_state = action._targets, action.pathfinder, dict(vars(peer))
    contract = Stage2ExecutionContract.for_invocation(_control().invocation)
    rows: list[dict[str, Any]] = []
    binding = NavigationDispatchLifecycle(world, 0, contract, rows.append)
    raw: dict[str, Any] = {"name": "nav_to_obj", "arguments": {"target_obj": "marker:north"}}
    mapped: dict[str, Any] = {
        "name": "nav_to_obj",
        "arguments": {**raw["arguments"], "robot": "agent_0"},
    }
    original = deepcopy(mapped)
    binding.dispatch(mapped, raw, "fresh-call", 1)
    assert action.skill_done is False and action.prev_nav_done is False
    assert action.queries == 0  # Binding never calls the original action or sensor itself.
    action.step(0)
    assert action.queries == 1 and action.skill_done is still_at_goal
    assert action._targets is cache and action.pathfinder is pathfinder
    assert vars(peer) == peer_state and mapped == original
    assert rows[0]["local_skill_completed"] is None
    assert rows[0]["benchmark_goal_satisfied"] is None
    action.step(0)
    assert action.queries == (1 if still_at_goal else 2)  # No automatic per-step rearming.


def test_guarded_model_call_rearms_each_real_navigation_selection_once() -> None:
    """Two exact admitted calls rearm; a wrong target and wait do not touch the action."""
    world = environment()
    control = _control()
    control.update(_sample(control, 10, False))
    contract = Stage2ExecutionContract.for_invocation(control.invocation)
    agent = VendorAgent("agent_0")
    model = ObservationModel()
    agent.llm_model = model
    model.next_action = ("nav_to_obj", {"target_obj": "marker:north"})
    rows: list[dict[str, Any]] = []
    feedback = Stage2ExecutionFeedback(
        {agent.name: contract},
        rows.append,
        observation_control=control,
        navigation_dispatch=NavigationDispatchLifecycle(world, 0, contract, rows.append),
    )
    restore = install_stage2_contract_guard(
        [agent], {agent.name: contract}, rows.append, feedback=feedback
    )
    try:
        action = world.task.actions["agent_0_oracle_nav_action"]
        for expected_queries in (1, 2):
            result = agent.chat("real model facade input")
            assert result["arguments"] == {"robot": "agent_0", "target_obj": "marker:north"}
            assert action.prev_nav_done is False
            action.step(0)
            assert action.skill_done is True and action.queries == expected_queries
        model.next_action = ("nav_to_obj", {"target_obj": "marker:south"})
        with pytest.raises(Stage2ContractViolation, match="canonical destination"):
            agent.chat("unauthorized target")
        assert action.prev_nav_done is True
        model.next_action = ("wait", {})
        assert agent.chat("wait instead")["arguments"] == ["16"]
        assert action.prev_nav_done is True
        assert len([row for row in rows if row.get("event") == "fresh-navigation-command"]) == 2
        assert control.snapshot()["remaining_simulator_steps"] == 256
    finally:
        restore()


@pytest.mark.parametrize("failure", ["mapping", "endpoint", "entity", "sequence"])
def test_detached_or_reused_dispatch_fails_before_mutation(failure: str) -> None:
    """Malformed mappings, substituted actions, unknown entities and reused calls fail closed."""
    world = environment()
    action = world.task.actions["agent_0_oracle_nav_action"]
    contract = Stage2ExecutionContract.for_invocation(_control().invocation)
    binding = NavigationDispatchLifecycle(world, 0, contract, lambda row: None)
    raw: dict[str, Any] = {"name": "nav_to_obj", "arguments": {"target_obj": "marker:north"}}
    mapped: dict[str, Any] = {
        "name": "nav_to_obj",
        "arguments": {**raw["arguments"], "robot": "agent_0"},
    }
    if failure == "mapping":
        mapped["arguments"]["robot"] = "agent_1"
    elif failure == "endpoint":
        world.task.actions["agent_0_oracle_nav_action"] = OracleCommand()
    elif failure == "entity":
        action._poss_entities.clear()
    else:
        binding.dispatch(mapped, raw, "original", 1)
        action.step(0)
    before = dict(vars(action))
    with pytest.raises(IntegrationError, match="malformed or detached"):
        binding.dispatch(mapped, raw, "duplicate", 1)
    assert vars(action) == before


def test_evidence_write_failure_does_not_override_the_original_action() -> None:
    """An unavailable audit leaves a rearmed command awaiting its actual physical result."""

    def fail(document: dict[str, Any]) -> None:
        """Inject storage failure after the command boundary, without changing physics."""
        raise OSError("audit full")

    world = environment()
    binding = NavigationDispatchLifecycle(
        world, 0, Stage2ExecutionContract.for_invocation(_control().invocation), fail
    )
    binding.dispatch(
        {"name": "nav_to_obj", "arguments": {"target_obj": "marker:north", "robot": "agent_0"}},
        {"name": "nav_to_obj", "arguments": {"target_obj": "marker:north"}},
        "call",
        1,
    )
    action = world.task.actions["agent_0_oracle_nav_action"]
    assert action.skill_done is False and action.queries == 0
    action.step(0)
    assert action.skill_done is True and action.queries == 1


@pytest.mark.parametrize("failure", ["missing", "type", "flag", "cache"])
def test_unavailable_original_interface_is_rejected_before_dispatch(failure: str) -> None:
    """No guessed lifecycle is installed for missing, incompatible or malformed actions."""
    world = environment()
    action = world.task.actions["agent_0_oracle_nav_action"]
    if failure == "missing":
        world.task.actions.clear()
    elif failure == "type":
        world.task.actions["agent_0_oracle_nav_action"] = object()
    elif failure == "flag":
        action.prev_nav_done = 1
    else:
        action._targets = None
    before = dict(vars(action))
    with pytest.raises(IntegrationError, match="lifecycle is unavailable"):
        NavigationDispatchLifecycle(
            world,
            0,
            Stage2ExecutionContract.for_invocation(_control().invocation),
            lambda row: None,
        )
    assert vars(action) == before
