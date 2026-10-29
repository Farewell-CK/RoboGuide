"""Check command-scoped reset references, unavailable targets and original failure retention."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_ROOT = Path(__file__).parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from habitat_local_eaios import reset_completion as module  # noqa: E402
from habitat_local_eaios.model import (  # noqa: E402
    CanonicalRelocationInvocation,
    IntegrationError,
)
from habitat_local_eaios.stage2_contract import Stage2ExecutionContract  # noqa: E402


class Matrix:
    """Represent the original skill's single-env reference without a numerical dependency."""

    def __init__(self, row: list[float]) -> None:
        """Copy one reference so temporary replacement cannot alias the original array."""
        self.row = list(row)
        self.shape = (1, len(row))

    def copy(self) -> Matrix:
        """Preserve the original copy-on-check numerical interface."""
        return Matrix(self.row)

    def __getitem__(self, index: int) -> list[float]:
        """Expose only the original single-env row."""
        assert index == 0
        return self.row

    def __setitem__(self, index: int, value: Any) -> None:
        """Emulate numerical row assignment without mutating the previous matrix."""
        assert index == 0
        self.row = list(value)


class Result(list[bool]):
    """Support the original tensor's unavailable-target mask."""

    def __and__(self, other: bool) -> Result:
        """Retain the result shape while withholding unsupported completion."""
        return Result([value and other for value in self])


class Vector(list[float]):
    """Keep the controller's numerical copy/reshape interface distinct from skill storage."""

    def copy(self) -> Vector:
        """Retain the controller reference without aliasing its original storage."""
        return Vector(self)

    def reshape(self, shape: tuple[int, int]) -> Matrix:
        """Expose one environment without coercing the target to a skill observation dtype."""
        assert shape == (1, len(self))
        return Matrix(list(self))


class Action:
    """Represent an original controller whose reference was actually sampled earlier."""

    def __init__(self) -> None:
        """Keep an actual home distinct from the failed skill-entry pose."""
        self.is_init = True
        self._init_joint_pos = Vector([0.0, 1.0])


class Parent:
    """Preserve Habitat's actual ResetArmAction-to-arm_ctrlr indirection."""

    def __init__(self) -> None:
        """Own a distinct original-shaped controller."""
        self.arm_ctrlr = Action()


class Skill:
    """Retain original maximum-joint-error completion and an unchanged skill budget."""

    def __init__(self) -> None:
        """Simulate first reset entry after a failed pick moved the arm."""
        self._init_joint_pos = Matrix([2.0, 3.0])
        self._max_skill_steps = 100
        self._cur_skill_step = [17]
        self.calls = 0
        self.error: Exception | None = None

    def _is_skill_done(self, joints: list[float]) -> Result:
        """Use the original strict tolerance rather than a new arrival predicate."""
        self.calls += 1
        if self.error is not None:
            raise self.error
        return Result(
            [max(abs(a - b) for a, b in zip(joints, self._init_joint_pos[0], strict=True)) < 0.01]
        )


def harness(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, Skill, module.ResetCompletionBinding, Any]:
    """Create an attributed relocation with independent controller and skill references."""
    monkeypatch.setattr(module, "_original_types", lambda: (Parent, Action, Skill))
    action, skill = Parent(), Skill()
    environment = SimpleNamespace(
        task=SimpleNamespace(actions={"agent_0_arm_reset_action": action}),
        current_episode=SimpleNamespace(episode_id="test", scene_id="room"),
    )
    invocation = CanonicalRelocationInvocation(
        mission_id="mission-reset",
        task_id="relocate-item",
        group_id="group-reset",
        role_id="carrier",
        operation="object.relocate@v1",
        attempt_id="attempt-reset",
        objective="Move the parcel to the receiving shelf.",
        parameters={"object": "parcel", "source": "loading-bay", "destination": "shelf"},
        resource_ids=("space:carrier",),
    )
    policy = SimpleNamespace(_skills={0: skill}, _name_to_idx={"reset_arm": 0})
    records: list[dict[str, Any]] = []
    binding = module.ResetCompletionBinding(
        environment, policy, 0, Stage2ExecutionContract.for_invocation(invocation), records.append
    )
    binding.install()
    return environment, skill, binding, records


def admit(
    binding: module.ResetCompletionBinding, call: str = "reset-one", sequence: int = 1
) -> None:
    """Admit the unchanged original mapped reset for the exact own endpoint."""
    binding.bind_call("agent_0", {"name": "reset_arm", "arguments": {}}, call, sequence)


def test_actual_controller_target_not_skill_entry_pose(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed-pick pose must not complete reset; original home must complete it."""
    _, skill, binding, records = harness(monkeypatch)
    before = skill._init_joint_pos
    assert skill._is_skill_done([2.0, 3.0]) == [True]
    admit(binding)
    assert skill._is_skill_done([2.0, 3.0]) == [False]
    assert skill._is_skill_done([0.0, 1.0]) == [True]
    assert skill._is_skill_done([0.01, 1.0]) == [False]
    assert skill._init_joint_pos is before and before.row == [2.0, 3.0]
    assert skill._max_skill_steps == 100 and skill._cur_skill_step == [17]
    assert records[-1]["controller_target"] == [0.0, 1.0]
    binding.close()
    binding.close()
    assert skill._is_skill_done([2.0, 3.0]) == [True]


def test_first_controller_step_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing original controller initialization cannot accept a zero-step completion."""
    environment, skill, binding, records = harness(monkeypatch)
    action = environment.task.actions["agent_0_arm_reset_action"].arm_ctrlr
    action.is_init = False
    admit(binding)
    assert skill._is_skill_done([2.0, 3.0]) == [False]
    assert records[-1]["event"] == "reset-reference-unavailable"
    action.is_init = True
    assert skill._is_skill_done([0.0, 1.0]) == [True]
    assert records[-1]["event"] == "reset-reference-aligned"


@pytest.mark.parametrize("change", ["episode", "controller", "target", "uninitialized"])
def test_reference_cannot_cross_world_or_change(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """Same labels do not let another reset/world/controller borrow the original reference."""
    environment, skill, binding, _ = harness(monkeypatch)
    admit(binding)
    if change == "episode":
        environment.current_episode = SimpleNamespace(episode_id="test", scene_id="room")
    elif change == "controller":
        environment.task.actions["agent_0_arm_reset_action"] = Parent()
    elif change == "target":
        environment.task.actions["agent_0_arm_reset_action"].arm_ctrlr._init_joint_pos = [4.0, 5.0]
    else:
        environment.task.actions["agent_0_arm_reset_action"].arm_ctrlr.is_init = False
    with pytest.raises(IntegrationError):
        skill._is_skill_done([0.0, 1.0])


def test_duplicate_peer_stale_and_closed_commands_refuse(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh reset identity never belongs to a previous call or another endpoint."""
    _, _, binding, _ = harness(monkeypatch)
    admit(binding)
    for name, action, call, sequence in [
        ("agent_1", {"name": "reset_arm", "arguments": {}}, "peer", 2),
        ("agent_0", {"name": "wait", "arguments": ["500"]}, "wrong", 2),
        ("agent_0", {"name": "reset_arm", "arguments": {}}, "reset-one", 2),
        ("agent_0", {"name": "reset_arm", "arguments": {}}, "stale", 1),
    ]:
        with pytest.raises(IntegrationError):
            binding.bind_call(name, action, call, sequence)
    admit(binding, "reset-two", 2)
    binding.close()
    with pytest.raises(IntegrationError):
        admit(binding, "reset-three", 3)


def test_original_exception_and_reference_survive(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing original checker cannot leak the temporary target or lose its exception."""
    _, skill, binding, _ = harness(monkeypatch)
    before = skill._init_joint_pos
    skill.error = RuntimeError("original checker failed")
    admit(binding)
    with pytest.raises(RuntimeError, match="original checker failed"):
        skill._is_skill_done([0.0, 1.0])
    assert skill._init_joint_pos is before


def test_failed_archival_does_not_change_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bounded reference trace is evidence, never an actuator or success authority."""
    _, skill, binding, _ = harness(monkeypatch)

    def fail(_: dict[str, Any]) -> None:
        """Represent unavailable storage independently from physical completion."""
        raise OSError("evidence volume unavailable")

    binding._record = fail
    admit(binding)
    assert skill._is_skill_done([2.0, 3.0]) == [False]
    assert skill._is_skill_done([0.0, 1.0]) == [True]


def test_reference_binding_composes_with_production_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Feedback must observe the qualified original check, not the stale entry reference."""
    from habitat_local_eaios.stage2_feedback import Stage2ExecutionFeedback
    from test_stage2_feedback import VendorModel

    environment, skill, original_binding, _ = harness(monkeypatch)
    contract = original_binding._contract
    original_binding.close()

    def terminate(instance: Skill, joints: list[float], **kwargs: Any) -> Any:
        """Expose one original-shaped HRL decision and keep its budget untouched."""
        del kwargs
        return instance._is_skill_done(joints), [False], {}

    monkeypatch.setattr(Skill, "should_terminate", terminate, raising=False)
    policy = SimpleNamespace(
        _skills={0: skill},
        _name_to_idx={"reset_arm": 0},
        _idx_to_name={0: "reset_arm"},
        _high_level_policy=SimpleNamespace(llm_agent=SimpleNamespace(name="agent_0")),
    )
    rows: list[dict[str, Any]] = []
    binding = module.ResetCompletionBinding(environment, policy, 0, contract, rows.append)
    feedback = Stage2ExecutionFeedback({"agent_0": contract}, rows.append, reset_completion=binding)
    feedback.install([policy])
    model = VendorModel()
    model.next_action = ("reset_arm", {})
    model.chat("reset using the original controller")
    feedback.admit("agent_0", model, {"name": "reset_arm", "arguments": {}})
    feedback.bind_observation_dispatch("agent_0", {"name": "reset_arm", "arguments": {}})
    try:
        assert skill.should_terminate(  # type: ignore[attr-defined]
            [2.0, 3.0], batch_idx=[0], skill_name=["reset_arm"], hl_wants_skill_term=[False]
        )[0] == [False]
        feedback.physical_step()
        assert skill.should_terminate(  # type: ignore[attr-defined]
            [0.0, 1.0], batch_idx=[0], skill_name=["reset_arm"], hl_wants_skill_term=[False]
        )[0] == [True]
        terminal = [row for row in rows if row.get("event") == "skill-terminated"][-1]
        assert terminal["termination"]["base_is_skill_done"] is True
        assert terminal["source"] == "controller-qualified-original-skill-should-terminate"
        assert terminal["reset_reference_profile"] == "controller-bound-arm-reset/v0.1"
        assert terminal["local_skill_completed"] is True
        assert skill._init_joint_pos.row == [2.0, 3.0]
    finally:
        feedback.close()
    assert skill._is_skill_done([2.0, 3.0]) == [True]
