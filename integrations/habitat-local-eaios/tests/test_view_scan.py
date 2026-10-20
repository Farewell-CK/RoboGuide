"""Explicit exact-target scanning is bounded, capability-backed and separate from detection."""

from __future__ import annotations

import math
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from habitat_local_eaios import view_scan  # noqa: E402
from habitat_local_eaios.model import IntegrationError  # noqa: E402
from habitat_local_eaios.stage2_contract import (  # noqa: E402
    Stage2ContractViolation,
    Stage2ExecutionContract,
    install_stage2_contract_guard,
)
from habitat_local_eaios.stage2_feedback import Stage2ExecutionFeedback  # noqa: E402
from habitat_local_eaios.view_scan import (  # noqa: E402
    MAX_SCAN_STEPS,
    ViewScanBinding,
    inspect_view_scan,
)
from test_observation_control import ObservationModel, _control, _sample  # noqa: E402
from test_stage2_feedback import VendorAgent, VendorSkill  # noqa: E402

BASE_TYPES = tuple(
    type(name, (), {})
    for name in ("BaseVelAction", "BaseVelNonCylinderAction", "DroneBaseVelAction")
)


def leaf(width: int) -> Any:
    """Represent one actual continuous action leaf without a Habitat or Gym dependency."""
    return SimpleNamespace(shape=(width,))


def controls(
    name: str = "BaseVelAction", *, agent: int = 0, lateral: bool = False
) -> tuple[Any, Any]:
    """Expose one independently owned base controller and its exact ordered local vector."""
    action = next(kind for kind in BASE_TYPES if kind.__name__ == name)()
    action._ang_speed, action._agent_index, action._multi_agent = 10.0, agent, True
    action._action_arg_prefix = f"agent_{agent}_"
    action._enable_lateral_move = lateral
    action.cur_grasp_mgr = SimpleNamespace(snap_idx=None)
    space = SimpleNamespace(
        spaces={
            "before": leaf(3),
            "base_velocity": SimpleNamespace(
                spaces={f"agent_{agent}_lin_vel": leaf(3), f"agent_{agent}_ang_vel": leaf(1)}
                if name == "DroneBaseVelAction"
                else {f"agent_{agent}_base_vel": leaf(3 if lateral else 2)}
            ),
            "wait": leaf(1),
            "rearrange_stop": leaf(1),
        }
    )
    names = {"wait": 0}
    policy = SimpleNamespace(
        _skills={0: VendorSkill()},
        _name_to_idx=names,
        _idx_to_name={0: "wait"},
        _high_level_policy=SimpleNamespace(_action_space=space, _skill_name_to_idx=names),
    )
    world = SimpleNamespace(
        task=SimpleNamespace(actions={f"agent_{agent}_base_velocity": action}),
        sim=SimpleNamespace(ctrl_freq=30.0, agents_mgr=[object(), object()]),
    )
    return world, policy


@pytest.fixture(autouse=True)
def original_interfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace vendor class resolution and allocation only; exercise production binding rules."""
    monkeypatch.setattr(view_scan, "_original_base_types", lambda: BASE_TYPES)
    monkeypatch.setattr(view_scan, "_skill_factory", lambda *args: VendorSkill())


@pytest.mark.parametrize(
    "name,lateral,yaw,width",
    [
        ("BaseVelAction", False, 4, 7),
        ("BaseVelNonCylinderAction", True, 5, 8),
        ("DroneBaseVelAction", False, 6, 9),
    ],
)
@pytest.mark.parametrize("agent", [0, 1])
def test_actual_control_layout_and_motion_bounds(
    name: str, lateral: bool, yaw: int, width: int, agent: int
) -> None:
    """Yaw indices follow the supplied layout; zero translation and original rates are retained."""
    world, policy = controls(name, agent=agent, lateral=lateral)
    spec = inspect_view_scan(world, policy, agent)
    assert spec.yaw_index == yaw and spec.width == width
    assert spec.normalized_yaw * spec.angular_scale <= 1.5
    assert MAX_SCAN_STEPS * spec.normalized_yaw * spec.angular_scale / spec.ctrl_freq <= math.pi / 2
    assert world.task.actions[f"agent_{agent}_base_velocity"] is spec.action


def test_one_agent_uses_the_original_unprefixed_interface() -> None:
    """A one-agent world does not fabricate a multi-agent action prefix."""
    world, policy = controls()
    action = world.task.actions.pop("agent_0_base_velocity")
    action._multi_agent, action._action_arg_prefix = False, ""
    world.task.actions["base_velocity"] = action
    world.sim.agents_mgr = [object()]
    policy._high_level_policy._action_space.spaces["base_velocity"].spaces = {"base_vel": leaf(2)}
    assert inspect_view_scan(world, policy, 0).action is action


@pytest.mark.parametrize(
    "failure",
    ["missing", "other-agent", "rate", "layout", "type", "unprefixed", "peer-prefix"],
)
def test_unavailable_control_cannot_be_advertised(failure: str) -> None:
    """Missing or mismatched real control facts refuse readiness instead of assuming capacity."""
    world, policy = controls()
    action = world.task.actions["agent_0_base_velocity"]
    if failure == "missing":
        world.task.actions.clear()
    elif failure == "other-agent":
        action._agent_index = 1
    elif failure == "rate":
        world.sim.ctrl_freq = float("nan")
    elif failure == "layout":
        policy._high_level_policy._action_space.spaces["base_velocity"].spaces[
            "agent_0_base_vel"
        ] = leaf(7)
    elif failure == "type":
        world.task.actions["agent_0_base_velocity"] = type("BaseVelAction", (), vars(action))()
    else:
        key = "base_vel" if failure == "unprefixed" else "agent_1_base_vel"
        policy._high_level_policy._action_space.spaces["base_velocity"].spaces = {key: leaf(2)}
    with pytest.raises(IntegrationError, match="no supported"):
        inspect_view_scan(world, policy, 0)


def test_scan_registry_is_scoped_and_rollback_preserves_original_wait() -> None:
    """Only one endpoint's fresh skill is installed and removed; no action or sensor runs."""
    world, policy = controls()
    original_skills = dict(policy._skills)
    original_names = dict(policy._name_to_idx)
    binding = ViewScanBinding(world, policy, 0, _control(), lambda row: None)
    assert binding.tool()["parameters"]["properties"]["target_obj"]["enum"] == ["marker:north"]
    assert binding.index != policy._name_to_idx["wait"]
    binding.close()
    binding.close()
    assert policy._skills == original_skills and policy._name_to_idx == original_names
    assert policy._idx_to_name == {0: "wait"}


def test_guard_requires_loaded_implementation_and_rejects_wrong_target() -> None:
    """A profile flag alone cannot authorize a tool; wrong-target scans never dispatch."""
    world, policy = controls()
    control = _control()
    control.update(_sample(control, 10, False))
    contract = Stage2ExecutionContract.for_invocation(control.invocation, allow_view_scan=True)
    agent = VendorAgent("agent_0")
    with pytest.raises(IntegrationError, match="no loaded"):
        install_stage2_contract_guard([agent], {agent.name: contract}, lambda row: None)
    binding = ViewScanBinding(world, policy, 0, control, lambda row: None)
    feedback = Stage2ExecutionFeedback(
        {agent.name: contract}, lambda row: None, observation_control=control, view_scan=binding
    )
    model = ObservationModel()
    agent.llm_model = model
    model.next_action = ("scan_for_object", {"target_obj": "marker:south"})
    restore = install_stage2_contract_guard(
        [agent], {agent.name: contract}, lambda row: None, feedback=feedback
    )
    try:
        with pytest.raises(Stage2ContractViolation, match="canonical destination"):
            agent.chat("choose actual local action")
        assert not agent.dispatches
        assert "scan_for_object" in {tool["name"] for tool in model.offered[-1]}
    finally:
        restore()
        feedback.close()


def test_navigation_contract_does_not_gain_scan_authority() -> None:
    """A broad local tool declaration cannot expand a mobility operation's contract."""
    control = _control()
    contract = Stage2ExecutionContract.for_invocation(control.invocation)
    scan = {"name": "scan_for_object", "arguments": {"target_obj": "marker:north"}}
    with pytest.raises(Stage2ContractViolation, match="not authorized"):
        contract.validate("agent_0", scan, frozenset())
    unrelated = replace(contract, operation="mobility.move@v1", allow_view_scan=True)
    with pytest.raises(Stage2ContractViolation, match="not authorized"):
        unrelated.validate("agent_0", scan, frozenset())


@pytest.mark.parametrize("failure", ["target", "held-object", "exhausted", "changed-control"])
def test_scan_dispatch_preserves_exact_identity_and_nonrenewable_budget(failure: str) -> None:
    """Reject target substitution, object transport, renewed budgets and endpoint replacement."""
    world, policy = controls()
    control = _control()
    control.update(_sample(control, 10, False))
    binding = ViewScanBinding(world, policy, 0, control, lambda row: None)
    raw = {"name": "scan_for_object", "arguments": {"target_obj": "marker:north"}}
    if failure == "target":
        raw["arguments"] = {"target_obj": "marker:south"}
    elif failure == "held-object":
        binding.spec.action.cur_grasp_mgr.snap_idx = 42
    elif failure == "exhausted":
        control.update(_sample(control, 266, False))
    else:
        binding.spec.action._ang_speed = 11
    try:
        with pytest.raises(IntegrationError, match="detached, exhausted"):
            binding.admit(raw, raw, "call", 1)
    finally:
        binding.close()


def test_finished_scan_receipt_is_not_detection_or_benchmark_success() -> None:
    """Scan completion retains current detection false and makes no benchmark assertion."""
    world, policy = controls()
    control = _control()
    control.update(_sample(control, 10, False))
    contract = Stage2ExecutionContract.for_invocation(control.invocation, allow_view_scan=True)
    rows: list[dict[str, Any]] = []
    binding = ViewScanBinding(world, policy, 0, control, rows.append)
    feedback = Stage2ExecutionFeedback(
        {"agent_0": contract}, rows.append, observation_control=control, view_scan=binding
    )
    model = ObservationModel()
    model.next_action = ("scan_for_object", {"target_obj": "marker:north"})
    model.chat("raw response")
    feedback.admit(
        "agent_0", model, {"name": "scan_for_object", "arguments": {"target_obj": "marker:north"}}
    )
    skill = policy._skills[binding.index]
    skill._cur_skill_step = [32]
    for _ in range(32):
        feedback.physical_step()
    feedback._termination(
        "agent_0",
        skill,
        {"skill_name": ["scan_for_object"], "batch_idx": [0], "hl_wants_skill_term": [False]},
        ([True], [False], object()),
        True,
        1,
    )
    row = rows[-1]
    assert row["status"] == "view-scan-finished" and row["local_skill_completed"] is True
    assert row["benchmark_goal_satisfied"] is None
    assert feedback.operation_completed("agent_0") is False
    assert control.snapshot()["sample"]["condition_holds"] is False
    feedback.close()


def test_real_raw_tool_choice_passes_through_without_action_rewriting() -> None:
    """The production guard offers the tool and retains its exact selected arguments."""

    class OriginalMappedAgent(VendorAgent):
        """Use CrabAgent's original passthrough for a newly registered local skill name."""

        def chat(self, observation: str) -> dict[str, Any]:
            """Map the returned raw tool verbatim; no simulator side effect exists here."""
            name, parameters = self.llm_model.chat(observation)
            result = {"name": name, "arguments": parameters}
            self.dispatches.append(result)
            return result

    world, policy = controls()
    ctrl = _control()
    ctrl.update(_sample(ctrl, 10, False))
    rows: list[dict[str, Any]] = []
    scan = ViewScanBinding(world, policy, 0, ctrl, rows.append)
    contract = Stage2ExecutionContract.for_invocation(ctrl.invocation, allow_view_scan=True)
    feedback = Stage2ExecutionFeedback(
        {"agent_0": contract}, rows.append, observation_control=ctrl, view_scan=scan
    )
    agent = OriginalMappedAgent("agent_0")
    agent.llm_model = ObservationModel()
    agent.llm_model.next_action = ("scan_for_object", {"target_obj": "marker:north"})
    restore = install_stage2_contract_guard(
        [agent], {agent.name: contract}, rows.append, feedback=feedback
    )
    try:
        raw = {"name": "scan_for_object", "arguments": {"target_obj": "marker:north"}}
        result = agent.chat("choose a tool from the actual schema")
        assert result == raw
        dispatched = next(row for row in rows if row.get("event") == "view-scan-dispatch-bound")
        assert dispatched["raw_selected_action"] == dispatched["local_dispatch"] == result
        assert dispatched["condition_holds"] is False
        assert len(agent.llm_model.requests) == 1
        with pytest.raises(IntegrationError, match="detached"):
            scan.admit(raw, raw, "reused", dispatched["action_sequence"])
        assert ctrl.snapshot()["remaining_simulator_steps"] == 256
    finally:
        restore()
        feedback.close()
