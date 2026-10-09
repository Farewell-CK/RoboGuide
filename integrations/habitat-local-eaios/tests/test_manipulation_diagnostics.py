"""Deterministic proof of read-only arm taps, bounded evidence and exact restoration."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.diagnostics import PhysicalDiagnostics  # noqa: E402
from habitat_local_eaios.manipulation_diagnostics import (  # noqa: E402
    MAX_CALLS_PER_STEP,
    ManipulationDiagnostics,
)
from habitat_local_eaios.model import CanonicalRelocationInvocation  # noqa: E402


class FakeIK:
    """Count actual stateful solver calls; extra observation calls would be visible."""

    def __init__(self) -> None:
        """Create fixed original results with observable call counters."""
        self.calls = {"calc_fk": 0, "calc_ik": 0, "set_arm_state": 0}
        self.result = [0.4, 0.5, 0.6]

    def set_arm_state(self, joint_pos: Any, joint_vel: Any = None) -> None:
        """Count the original side effect without replacing its input objects."""
        self.calls["set_arm_state"] += 1

    def calc_fk(self, js: Any) -> list[float]:
        """Model original FK updating its private solver state exactly once."""
        self.calls["calc_fk"] += 1
        self.set_arm_state(js)
        return [0.1, 0.2, 0.3]

    def calc_ik(self, targ_ee: Any, **kwargs: Any) -> list[float]:
        """Return one actual mutable solver result without caller-independent evaluation."""
        self.calls["calc_ik"] += 1
        return self.result


class FakeArm:
    """Represent existing action order and provide only attribute-based world reads."""

    def __init__(self, helper: FakeIK) -> None:
        """Expose geometry, actual joint/motor state and exact target identity."""
        self._ik_helper = helper
        self.ee_index = 0
        self.ee_target = [0.0, 0.0, 0.0]
        self._entities = [SimpleNamespace(name="object")]
        self.cur_grasp_mgr = SimpleNamespace(is_grasped=False, snap_idx=None)
        self.cur_articulated_agent = SimpleNamespace(
            base_pos=[0.0, 0.0, 0.0],
            base_transformation=[
                [1.0, 0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            arm_joint_pos=[0.1, 0.2, 0.3],
            arm_motor_pos=[0.1, 0.2, 0.3],
            params=SimpleNamespace(ee_constraint=[[[-2.0, 2.0], [-2.0, 2.0], [-2.0, 2.0]]]),
            ee_transform=lambda: SimpleNamespace(translation=[0.5, 0.5, 0.5]),
        )
        self.physics_steps = 0

    def _get_coord_for_pddl_idx(self, index: Any) -> list[float]:
        """Return the original target position, with no synthetic re-resolution."""
        return [3.0, 0.8, 0.9]

    def apply_ee_constraints(self) -> None:
        """Apply the original deterministic coordinate clipping."""
        self.ee_target = [max(-2.0, min(2.0, component)) for component in self.ee_target]

    def set_desired_ee_pos(self, ee_pos: Any) -> None:
        """Run one original solver and one physical update, as in the vendor action."""
        self.ee_target = list(ee_pos)
        self.apply_ee_constraints()
        self._ik_helper.set_arm_state(self.cur_articulated_agent.arm_joint_pos)
        result = self._ik_helper.calc_ik(self.ee_target, _original="passthrough")
        self.cur_articulated_agent.arm_motor_pos = list(result)
        result[0] = 9.0  # Observer must copy the real return before caller mutation.
        self.physics_steps += 1

    def step(self, selected: Any) -> list[float]:
        """Use the decoded index and run the original arm calls only when selected."""
        if selected[1] != 0:
            target = self._get_coord_for_pddl_idx(selected[0])
            self.ee_target = self._ik_helper.calc_fk(self.cur_articulated_agent.arm_joint_pos)
            self.set_desired_ee_pos(target)
        return self.ee_target


class FakeGripper:
    """Expose original grasp admission and a configured distance threshold."""

    def __init__(self, arm: FakeArm) -> None:
        """Retain the exact original controller and threshold."""
        self.arm = arm
        self.calls = 0
        self._config = SimpleNamespace(grasp_thresh_dist=0.15)

    def step(self, grip_action: Any) -> None:
        """Count the original gripper call without inventing grasp success."""
        self.calls += 1


class FakeParent:
    """Represent the outer Habitat action that runs arm then gripper."""

    def __init__(self, arm: FakeArm, action_key: str = "agent_0_arm_pick_action") -> None:
        """Bind one arm and original gripper without changing their ownership."""
        self.arm_ctrlr = arm
        self.grip_ctrlr = FakeGripper(arm)
        self.calls = 0
        self.action_key = action_key

    def step(self, *args: Any, **kwargs: Any) -> Any:
        """Pass the exact decoded arm selection through the existing local pipeline."""
        self.calls += 1
        result = self.arm_ctrlr.step(kwargs[self.action_key])
        self.grip_ctrlr.step(1)
        return result


def make_invocation(attempt: str = "attempt-1") -> CanonicalRelocationInvocation:
    """Build canonical intent solely for observer attribution, never dispatch."""
    return CanonicalRelocationInvocation(
        "mission",
        "task",
        "group",
        "role",
        "object.relocate@v1",
        "Relocate",
        {"object": "object", "source": "source", "destination": "destination"},
        (),
        attempt_id=attempt,
    )


def make_actions() -> tuple[dict[str, Any], FakeParent, FakeParent, FakeIK]:
    """Build pick/place actions sharing one original helper, as the deployed agent does."""
    helper = FakeIK()
    pick, place = (
        FakeParent(FakeArm(helper)),
        FakeParent(FakeArm(helper), "agent_0_arm_place_action"),
    )
    return {"agent_0_arm_pick_action": pick, "agent_0_arm_place_action": place}, pick, place, helper


def test_arm_taps_preserve_original_counts_results_and_capture_clipping() -> None:
    """Shared helper calls execute once and immutable evidence precedes later mutation."""
    actions, pick, _, helper = make_actions()
    _, baseline, _, baseline_helper = make_actions()
    observer = ManipulationDiagnostics()
    observer.bind({0: make_invocation()})
    restores = observer.install(actions, 0)
    selected = [0, 1]
    result = pick.step(agent_0_arm_pick_action=selected)
    baseline_result = baseline.step(agent_0_arm_pick_action=selected)
    assert result is pick.arm_ctrlr.ee_target
    assert result == baseline_result
    assert helper.calls == baseline_helper.calls == {"calc_fk": 1, "calc_ik": 1, "set_arm_state": 2}
    assert pick.arm_ctrlr.physics_steps == baseline.arm_ctrlr.physics_steps == 1
    assert pick.grip_ctrlr.calls == baseline.grip_ctrlr.calls == 1
    record = observer.sample(0, 1)
    assert record["call_records_complete"] is True
    assert record["invocation"]["canonical_object"] == "object"
    calls = {call["method"]: call for call in record["calls"]}
    assert calls["_get_coord_for_pddl_idx"]["selected_entity"] == "object"
    assert calls["apply_ee_constraints"]["target_before"] == [3.0, 0.8, 0.9]
    assert calls["apply_ee_constraints"]["target_after"] == [2.0, 0.8, 0.9]
    assert calls["calc_ik"]["input"] == [2.0, 0.8, 0.9]
    assert calls["calc_ik"]["returned_value"] == [0.4, 0.5, 0.6]
    assert calls["arm_pick_action.step"]["state_after"]["is_grasped"] is False
    assert calls["arm_pick_action.step"]["gripper_configuration"]["grasp_threshold_m"] == 0.15
    for restore in reversed(restores):
        restore()
    assert not {"step", "apply_ee_constraints"}.intersection(vars(pick.arm_ctrlr))
    assert "calc_ik" not in vars(helper)


@pytest.mark.parametrize("active", [(True, False), (False, True), (True, True)])
@pytest.mark.parametrize("reverse", [False, True])
def test_joint_action_kwargs_are_bound_to_exact_agent_not_first_matching_suffix(
    active: tuple[bool, bool], reverse: bool
) -> None:
    """Habitat's all-agent keyword dispatch preserves separate arm attribution in any order."""
    helpers = [FakeIK(), FakeIK()]
    parents = [
        FakeParent(FakeArm(helpers[agent]), f"agent_{agent}_arm_pick_action") for agent in range(2)
    ]
    actions = {parent.action_key: parent for parent in parents}
    observer = ManipulationDiagnostics()
    for agent in range(2):
        observer.install(actions, agent)
    keys = list(reversed(list(actions))) if reverse else list(actions)
    kwargs = {key: [0, int(active[int(key[6])])] for key in keys}
    for parent in parents:
        parent.step(**kwargs)
    for agent, parent in enumerate(parents):
        sample = observer.sample(agent, 1)
        assert (
            helpers[agent].calls["calc_ik"] == parent.arm_ctrlr.physics_steps == int(active[agent])
        )
        if active[agent]:
            assert sample["call_records_complete"] is True
            assert sample["calls"][0]["action_argument_key"] == parent.action_key
            assert sample["calls"][0]["selected_action"] == [0.0, 1.0]
            assert len([call for call in sample["calls"] if call["method"] == "calc_ik"]) == 1
        else:
            assert sample["_status"] == "unavailable"


def test_pick_and_place_kwargs_keep_their_own_scope_with_one_shared_helper() -> None:
    """A place selection cannot open an inactive pick scope or double-tap the helper."""
    actions, pick, place, helper = make_actions()
    observer = ManipulationDiagnostics()
    observer.install(actions, 0)
    kwargs = {"agent_0_arm_pick_action": [0, 0], "agent_0_arm_place_action": [0, 2]}
    pick.step(**kwargs)
    place.step(**kwargs)
    record = observer.sample(0, 1)
    assert record["calls"][0]["method"] == "arm_place_action.step"
    assert record["calls"][0]["selected_action"] == [0.0, 2.0]
    assert helper.calls["calc_ik"] == 1


@pytest.mark.parametrize("error", [RuntimeError("original"), KeyboardInterrupt()])
def test_original_solver_failure_is_rethrown_and_retained_pending(error: BaseException) -> None:
    """A failed Gym action keeps pending call evidence without a fictitious step number."""
    actions, pick, _, helper = make_actions()
    calls = []

    def fail(*args: Any, **kwargs: Any) -> Any:
        """Raise the original exception with an observable exact invocation count."""
        calls.append((args, kwargs))
        raise error

    helper.calc_ik = fail  # type: ignore[method-assign]
    observer = ManipulationDiagnostics()
    restores = observer.install(actions, 0)
    with pytest.raises(type(error)) as caught:
        pick.step(agent_0_arm_pick_action=[0, 1])
    assert caught.value is error and len(calls) == 1
    record = observer.boundary(0)["pending_since_last_post_step"]
    assert "simulator_step" not in record
    assert (
        next(call for call in record["calls"] if call["method"] == "calc_ik")["exception_type"]
        == type(error).__name__
    )
    for restore in reversed(restores):
        restore()
    assert helper.calc_ik is fail


def test_unreadable_geometry_does_not_change_original_action() -> None:
    """Missing optional reads do not block the original solver or physics calls."""
    actions, pick, _, helper = make_actions()
    del pick.arm_ctrlr.cur_articulated_agent.ee_transform
    observer = ManipulationDiagnostics()
    observer.install(actions, 0)
    pick.step(agent_0_arm_pick_action=[0, 1])
    record = observer.sample(0, 1)["calls"][0]
    assert record["state_before"]["end_effector_position_world"]["_status"] == "unavailable"
    assert helper.calls["calc_ik"] == 1 and pick.arm_ctrlr.physics_steps == 1


def test_lazy_solver_return_is_not_consumed_by_observer() -> None:
    """Unsupported lazy returns reach the caller intact, without hidden iteration."""
    actions, pick, _, helper = make_actions()
    consumed = []

    def original(targ_ee: Any, **kwargs: Any) -> Any:
        """Provide one lazy solver result whose consumption can be counted."""
        assert kwargs == {"_original": "passthrough"}

        def values() -> Any:
            """Yield only when the original caller asks for the solver values."""
            consumed.append(True)
            yield from [0.4, 0.5, 0.6]

        return values()

    helper.calc_ik = original  # type: ignore[method-assign]
    observer = ManipulationDiagnostics()
    observer.install(actions, 0)
    # FakeArm deliberately treats the result as mutable; the same original
    # TypeError must remain visible rather than being masked by diagnostics.
    with pytest.raises(TypeError):
        pick.step(agent_0_arm_pick_action=[0, 1])
    assert consumed == [True]
    pending = observer.boundary(0)["pending_since_last_post_step"]
    result = next(call for call in pending["calls"] if call["method"] == "calc_ik")
    assert result["returned_value"]["_status"] == "unavailable"


def test_joint_vector_bound_marks_fields_unavailable_without_losing_action() -> None:
    """Large joint arrays remain unrecorded while the physical solver keeps its inputs."""
    actions, pick, _, helper = make_actions()
    pick.arm_ctrlr.cur_articulated_agent.arm_joint_pos = [0.0] * 65
    observer = ManipulationDiagnostics()
    observer.install(actions, 0)
    pick.step(agent_0_arm_pick_action=[0, 1])
    record = observer.sample(0, 1)["calls"][0]
    assert record["state_before"]["arm_joint_positions"]["_status"] == "unavailable"
    assert helper.calls["calc_ik"] == 1


@pytest.mark.parametrize("selection", [[0, 0], [0]])
def test_inactive_or_unsupported_selection_preserves_one_original_exception(
    selection: list[int],
) -> None:
    """Observer selection fallback cannot retry an inactive original action that raises."""
    actions, pick, _, _ = make_actions()
    calls = []
    error = RuntimeError("original inactive action")

    def fail(*args: Any, **kwargs: Any) -> Any:
        """Record the exact received selection before raising once."""
        calls.append(kwargs["agent_0_arm_pick_action"])
        raise error

    pick.step = fail  # type: ignore[method-assign]
    observer = ManipulationDiagnostics()
    observer.install(actions, 0)
    with pytest.raises(RuntimeError) as caught:
        pick.step(agent_0_arm_pick_action=selection)
    assert caught.value is error and calls == [selection]
    assert observer.stats()["calls_observed"] == 0


def test_observer_callback_failures_do_not_repeat_or_hide_original_action(monkeypatch: Any) -> None:
    """Both pre/post observation faults leave the single physical dispatch intact."""
    actions, pick, _, helper = make_actions()
    observer = ManipulationDiagnostics()
    observer.install(actions, 0)

    def fail(*args: Any, **kwargs: Any) -> Any:
        """Simulate observer defects without touching the actual solver."""
        raise ValueError("diagnostic-only")

    monkeypatch.setattr(observer, "_begin", fail)
    monkeypatch.setattr(observer, "_finish", fail)
    pick.step(agent_0_arm_pick_action=[0, 1])
    assert pick.calls == pick.arm_ctrlr.physics_steps == helper.calls["calc_ik"] == 1
    assert observer.stats()["observation_failures"] > 0


def test_call_budget_and_new_attempt_fence_do_not_limit_execution() -> None:
    """Extra actions still execute while loss is exposed and stale identity is cleared."""
    actions, pick, _, helper = make_actions()
    observer = ManipulationDiagnostics()
    observer.bind({0: make_invocation()})
    observer.install(actions, 0)
    for _ in range(10):
        pick.step(agent_0_arm_pick_action=[0, 1])
    pending = observer.boundary(0)["pending_since_last_post_step"]
    assert len(pending["calls"]) == MAX_CALLS_PER_STEP
    assert pending["call_records_complete"] is False and pending["dropped_calls"] > 0
    assert helper.calls["calc_ik"] == pick.arm_ctrlr.physics_steps == 10
    observer.bind({0: make_invocation("attempt-2")})
    assert observer.boundary(0) == {"last_post_step": None, "pending_since_last_post_step": None}
    pick.step(agent_0_arm_pick_action=[0, 1])
    assert observer.sample(0, 2)["invocation"]["attempt_id"] == "attempt-2"


def test_disabled_diagnostics_install_nothing_and_terminal_flushes_pending_arm_failure(
    tmp_path: Path,
) -> None:
    """Only explicit diagnostics install taps; unreadable final state still retains failed calls."""
    actions, pick, _, helper = make_actions()
    environment = SimpleNamespace(task=SimpleNamespace(actions=actions))
    disabled = PhysicalDiagnostics(tmp_path / "disabled", (0,), False, 10)
    disabled.install_nav_probes(environment)
    assert "step" not in vars(pick) and "calc_ik" not in vars(helper)
    observer = PhysicalDiagnostics(tmp_path / "enabled", (0,), True, 10)
    observer.install_nav_probes(environment)
    observer.bind_manipulation_invocations({0: make_invocation()})
    pick.step(agent_0_arm_pick_action=[0, 1])
    observer.record_terminal(environment, 0, "original_failure")
    terminal = json.loads((tmp_path / "enabled/diagnostics-terminal.json").read_text())
    assert terminal["_status"] == "unavailable"
    assert terminal["termination_reason"] == "original_failure"
    assert (
        terminal["agents"]["0"]["local_manipulation"]["pending_since_last_post_step"]["invocation"][
            "attempt_id"
        ]
        == "attempt-1"
    )
    assert "step" not in vars(pick) and "calc_ik" not in vars(helper)
