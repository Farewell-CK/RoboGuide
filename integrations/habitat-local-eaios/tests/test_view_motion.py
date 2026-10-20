"""Physical rotation observations never turn a timer, missing data or a peer into progress."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from habitat_local_eaios import view_scan  # noqa: E402
from habitat_local_eaios.stage2_contract import Stage2ExecutionContract  # noqa: E402
from habitat_local_eaios.stage2_feedback import Stage2ExecutionFeedback  # noqa: E402
from habitat_local_eaios.view_motion import ScanRotationObservation  # noqa: E402
from habitat_local_eaios.view_scan import ViewScanBinding  # noqa: E402
from test_observation_control import ObservationModel, _control, _sample  # noqa: E402
from test_stage2_feedback import VendorSkill  # noqa: E402
from test_view_scan import BASE_TYPES, controls, vector_width  # noqa: E402


def quaternion(yaw: float) -> Any:
    """Represent the original quaternion interface without a simulator or extra observations."""
    return SimpleNamespace(vector=(0.0, math.sin(yaw / 2), 0.0), scalar=math.cos(yaw / 2))


def original_action() -> Any:
    """Expose only an existing rotation, with no callable command or sensor interface."""
    return SimpleNamespace(
        cur_articulated_agent=SimpleNamespace(sim_obj=SimpleNamespace(rotation=quaternion(0.0)))
    )


def test_counter_completion_without_rotation_is_truthfully_stationary() -> None:
    """Thirty-two successful physical observations do not prove that a yaw command took effect."""
    action = original_action()
    observer = ScanRotationObservation(action, "call-scan", 2)
    assert observer.snapshot()["rotation_observed"] is None
    for _ in range(32):
        observer.observe()
    row = observer.snapshot()
    assert row["status"] == "no-rotation-observed" and row["rotation_observed"] is False
    assert row["post_step_samples"] == 32 and row["unavailable_samples"] == 0
    assert row["max_rotation_from_start_radians"] == 0.0
    assert row["physical_blocking_cause"] == "unavailable"
    assert row["detection_satisfied"] is row["benchmark_goal_satisfied"] is None


def test_actual_rotation_and_equivalent_quaternion_signs() -> None:
    """Quaternion sign changes alone are not motion, while real orientation change is retained."""
    action = original_action()
    observer = ScanRotationObservation(action, "call", 1)
    action.cur_articulated_agent.sim_obj.rotation = SimpleNamespace(vector=(0, 0, 0), scalar=-1)
    observer.observe()
    assert observer.snapshot()["rotation_observed"] is False
    action.cur_articulated_agent.sim_obj.rotation = quaternion(-0.2)
    observer.observe()
    assert observer.snapshot()["rotation_observed"] is True
    assert observer.snapshot()["max_rotation_from_start_radians"] == pytest.approx(0.2)


@pytest.mark.parametrize("bad", [None, float("nan"), True, 0.0, 2.0])
def test_unavailable_or_malformed_rotation_cannot_prove_stationarity(bad: Any) -> None:
    """Missing, nonfinite and nonunit rotation observations remain unavailable."""
    action = original_action()
    observer = ScanRotationObservation(action, "call", 1)
    action.cur_articulated_agent.sim_obj.rotation = SimpleNamespace(vector=(0, 0, 0), scalar=bad)
    observer.observe()
    action.cur_articulated_agent.sim_obj.rotation = quaternion(0.0)
    observer.observe()
    row = observer.snapshot()
    assert row["status"] == "unavailable" and row["rotation_observed"] is None
    assert row["unavailable_samples"] == 1


def test_missing_original_orientation_never_acquires_a_fabricated_start() -> None:
    """Later readable poses cannot invent the original command's missing baseline."""
    action = SimpleNamespace()
    observer = ScanRotationObservation(action, "call", 1)
    action.cur_articulated_agent = original_action().cur_articulated_agent
    observer.observe()
    assert observer.snapshot()["rotation_observed"] is None


@pytest.mark.parametrize("move", [False, True])
def test_actual_motion_reaches_the_exact_tool_receipt_and_next_model_input(
    move: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Model-visible physical evidence differs while original actions and timer results agree."""
    monkeypatch.setattr(view_scan, "_original_base_types", lambda: BASE_TYPES)
    monkeypatch.setattr(view_scan, "_original_vector_width", vector_width)
    monkeypatch.setattr(view_scan, "_skill_factory", lambda *args: VendorSkill())
    world, policy = controls()
    action = world.task.actions["agent_0_base_velocity"]
    action.cur_articulated_agent = original_action().cur_articulated_agent
    control = _control()
    control.update(_sample(control, 10, False))
    rows: list[dict[str, Any]] = []
    scan = ViewScanBinding(world, policy, 0, control, rows.append)
    policy._cur_skills = [scan.index]
    contract = Stage2ExecutionContract.for_invocation(control.invocation, allow_view_scan=True)
    feedback = Stage2ExecutionFeedback(
        {"agent_0": contract}, rows.append, observation_control=control, view_scan=scan
    )
    model = ObservationModel()
    model.next_action = ("scan_for_object", {"target_obj": "marker:north"})
    raw = {"name": "scan_for_object", "arguments": {"target_obj": "marker:north"}}
    model.chat("unchanged model selection")
    feedback.admit("agent_0", model, raw)
    assert feedback.bind_observation_dispatch("agent_0", raw) == raw
    for step in range(32):
        if move:
            action.cur_articulated_agent.sim_obj.rotation = quaternion(step / 100)
        feedback.physical_step()
    feedback._termination(
        "agent_0",
        scan.skill,
        {"skill_name": ["scan_for_object"], "batch_idx": [0], "hl_wants_skill_term": [False]},
        ([True], [False], object()),
        True,
        1,
    )
    terminal = rows[-1]
    assert terminal["status"] == "view-scan-finished"
    assert terminal["local_skill_completed"] is True
    assert terminal["view_search_motion"]["rotation_observed"] is move
    assert terminal["view_search_motion"]["tool_call_id"] == terminal["tool_call_id"]
    content = feedback.before_call("agent_0", model, "Choose the next valid action.")
    assert '"rotation_observed": ' + str(move).lower() in content
    receipt = json.loads(feedback._pending["agent_0"].receipt["content"])
    assert receipt["view_search_motion"]["rotation_observed"] is move
    assert receipt["benchmark_goal_satisfied"] is None
    assert not feedback.operation_completed("agent_0")
    assert control.snapshot()["sample"]["condition_holds"] is False
    assert len(model.requests) == 1
    assert scan.motion_feedback("different-call", 1) is None
    # A peer or a different skill's later pose cannot refresh this call's evidence.
    policy._cur_skills = [policy._name_to_idx["wait"]]
    action.cur_articulated_agent.sim_obj.rotation = quaternion(1.5)
    feedback.physical_step()
    motion = scan.motion_feedback(terminal["tool_call_id"], 1)
    assert motion is not None and motion["post_step_samples"] == 32
    feedback.close()
