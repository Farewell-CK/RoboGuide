"""Bound-object completion and guard regressions without Torch, Habitat or a Provider."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.crabagent_backend import CrabAgentBackendConfig  # noqa: E402
from habitat_local_eaios.model import CanonicalRelocationInvocation, IntegrationError  # noqa: E402
from habitat_local_eaios.relocation_completion import (  # noqa: E402
    RelocationCompletionBinding,
    inspect_completion_interfaces,
)
from habitat_local_eaios.stage2_contract import (  # noqa: E402
    RelocationExecutionState,
    Stage2ContractViolation,
    Stage2ExecutionContract,
    install_stage2_contract_guard,
)
from habitat_local_eaios.stage2_feedback import Stage2ExecutionFeedback  # noqa: E402
from test_relocation_contract import _FakeAgent, _request  # noqa: E402


class BoolTensor(list[bool]):
    """Implement only the original check's boolean masking boundary."""

    def __and__(self, value: bool) -> BoolTensor:
        """Keep geometry separate from the release-qualified termination result."""
        return BoolTensor([item and value for item in self])


class DistanceTensor:
    """Supply a detached singleton original-shaped observation without importing Torch."""

    def __init__(self, value: float) -> None:
        """Retain one supplied distance; no simulation or sensor work occurs."""
        self.value = value

    def new_tensor(self, rows: list[list[float]]) -> DistanceTensor:
        """Construct only the corrected invocation-scoped distance tensor."""
        return DistanceTensor(rows[0][0])

    def detach(self) -> DistanceTensor:
        """Return the same immutable fake observation."""
        return self

    def cpu(self) -> DistanceTensor:
        """Mirror a CPU observation read without physical effects."""
        return self

    def reshape(self, size: int) -> DistanceTensor:
        """Retain the singleton observation for the bounded native diagnostic."""
        assert size == -1
        return self

    def tolist(self) -> list[float]:
        """Expose the native distance independently from the corrected observation."""
        return [self.value]


class OriginalPlace:
    """Mirror original geometric release, budget and high-level decisions separately."""

    def __init__(self) -> None:
        """Retain the original 100-step limit and inspectable method call counts."""
        self._cur_skill_step = [1]
        self._max_skill_steps = 100
        self.check_calls = self.act_calls = self.termination_calls = 0
        self.error: BaseException | None = None

    def _is_skill_done(self, observations: Any, **kwargs: Any) -> BoolTensor:
        """Use the supplied distance and original strict 0.02m threshold exactly once."""
        del kwargs
        self.check_calls += 1
        if self.error is not None:
            raise self.error
        key = next(key for key in observations if "object_to_goal_distance_sensor" in key)
        return BoolTensor([abs(observations[key].value) < 0.02])

    def _internal_act(self, observations: Any) -> dict[str, Any]:
        """Emit the original-shaped release component only on the geometric condition."""
        self.act_calls += 1
        near = self._is_skill_done(observations)[0]
        return {"destination_action_index": 23, "place_id": 2, "grip_action": -1.0 if near else 0.0}

    def should_terminate(self, observations: Any, **kwargs: Any) -> Any:
        """Preserve original budget and high-level exits independently of local completion."""
        self.termination_calls += 1
        done = self._is_skill_done(observations, **kwargs)[0]
        budget = self._cur_skill_step[0] >= self._max_skill_steps
        return ([done or budget or kwargs["hl_wants_skill_term"][0]], [False], [False])


class OriginalReset:
    """Mirror the native reset's first-action initialization and pre-step exit behavior."""

    def __init__(self) -> None:
        """Keep a fixed existing result, budget and independent exception for attribution tests."""
        self._cur_skill_step = [1]
        self._max_skill_steps = 100
        self.act_calls = self.check_calls = self.termination_calls = 0
        self.base_done = True
        self.bad_terminate = False
        self.error: BaseException | None = None
        self.action = object()

    def _internal_act(self) -> Any:
        """Generate one original-shaped reset action without a simulator or added call."""
        self.act_calls += 1
        if self.error is not None:
            raise self.error
        return self.action

    def _is_skill_done(self, **kwargs: Any) -> list[bool]:
        """Expose the native first-input completion without claiming a post-step arm result."""
        del kwargs
        self.check_calls += 1
        return [self.base_done]

    def should_terminate(self, **kwargs: Any) -> Any:
        """Return the original result once, independently of feedback bookkeeping."""
        self.termination_calls += 1
        done = self._is_skill_done(**kwargs)[0]
        budget = self._cur_skill_step[0] >= self._max_skill_steps
        return (
            [done or budget or kwargs["hl_wants_skill_term"][0]],
            [self.bad_terminate],
            object(),
        )


class CompletionHarness:
    """Compose production hooks, guard and feedback around an actual-shaped read-only world."""

    def __init__(
        self,
        *,
        index: int = 1,
        world: Any = None,
        object_index: int | None = None,
        enabled: bool = True,
    ) -> None:
        """Use deliberately nonordinal object names and absolute rigid IDs."""
        self.index = index
        self.name = f"agent_{index}"
        positions = {
            "box:blue": [0.0, 0.0, 0.0],
            "box:red": [8.0, 2.0, 1.0],
            "shelf:blue": [0.0, 0.0, 0.0],
            "shelf:red": [4.0, 2.0, 1.0],
        }
        entities = {name: SimpleNamespace(name=name) for name in positions}
        info = SimpleNamespace(
            obj_ids={"box:blue": 1, "box:red": 0},
            get_entity_pos=lambda entity: positions[entity.name],
            check_type_matches=lambda entity, kind: (
                kind
                == ("movable_entity_type" if entity.name.startswith("box:") else "goal_entity_type")
            ),
        )
        managers = [
            SimpleNamespace(is_grasped=True, snap_idx=202),
            SimpleNamespace(is_grasped=True, snap_idx=101),
        ]
        self.world = world or SimpleNamespace(
            current_episode=SimpleNamespace(episode_id="generic", scene_id="scene:generic"),
            task=SimpleNamespace(
                pddl_problem=SimpleNamespace(get_entity=entities.get, sim_info=info)
            ),
            sim=SimpleNamespace(
                scene_obj_ids=[101, 202],
                get_agent_data=lambda index: SimpleNamespace(grasp_mgr=managers[index]),
            ),
            positions=positions,
        )
        entity_index = index if object_index is None else object_index
        self.object = "box:blue" if entity_index == 0 else "box:red"
        self.destination = "shelf:blue" if entity_index == 0 else "shelf:red"
        request = _request(object=self.object, destination=self.destination)
        request["invocation"]["attempt_id"] = f"attempt:{index}:{entity_index}"
        self.contract = Stage2ExecutionContract.for_invocation(
            CanonicalRelocationInvocation.from_request(request)
        )
        self.agent = _FakeAgent(
            ("place", {"target_obj": self.object, "target_location": self.destination})
        )
        self.agent.name = self.name
        self.skill = OriginalPlace()
        self.reset = OriginalReset()
        self.policy = SimpleNamespace(
            _idx_to_name={23: "place", 24: "reset_arm"},
            _skills={23: self.skill, 24: self.reset},
            _high_level_policy=SimpleNamespace(llm_agent=self.agent),
        )
        self.rows: list[dict[str, Any]] = []
        self.state = RelocationExecutionState(phase="holding", allow_holding_reset=enabled)
        self.binding = RelocationCompletionBinding(self.world, {self.name: self.contract})
        self.feedback = Stage2ExecutionFeedback(
            {self.name: self.contract},
            self.rows.append,
            completion=lambda name, action, succeeded: self.state.complete(action, succeeded),
            completion_binding=self.binding if enabled else None,
        )
        self.feedback.install([self.policy])
        self.restore_guard = install_stage2_contract_guard(
            [self.agent],
            {self.name: self.contract},
            lambda record: None,
            feedback=self.feedback,
            relocation_states={self.name: self.state},
        )
        self.observations = {f"{self.name}_object_to_goal_distance_sensor": DistanceTensor(0.0)}

    def admit(self) -> None:
        """Dispatch the model's exact selected tool only after the production guard passes."""
        self.agent.chat("original observation")

    def terminate(self, *, skill: str = "place", high_level: bool = False) -> Any:
        """Run the one existing original termination call with exact skill attribution."""
        return self.skill.should_terminate(
            self.observations,
            batch_idx=[0],
            skill_name=[skill],
            hl_wants_skill_term=[high_level],
        )

    def release_at_goal(self) -> None:
        """Model the fake world's existing Gym release outcome, never a production action."""
        self.world.positions[self.object] = list(self.world.positions[self.destination])
        manager = self.world.sim.get_agent_data(self.index).grasp_mgr
        manager.is_grasped, manager.snap_idx = False, None

    def receipt(self) -> dict[str, Any]:
        """Read the exact receipt which the next original model call would consume."""
        result: dict[str, Any] = json.loads(self.agent.llm_model.chat_history[-1][-1]["content"])
        return result

    def close(self) -> None:
        """Restore the selected-action and original skill instance boundaries."""
        self.restore_guard()
        self.feedback.close()


def test_other_object_zero_distance_cannot_release_or_complete_this_object() -> None:
    """The archived shared-sensor pattern cannot complete a different canonical object."""
    harness = CompletionHarness()
    try:
        harness.admit()
        harness.feedback.physical_step()
        output = harness.skill._internal_act(harness.observations)
        assert output["grip_action"] == 0.0
        assert harness.terminate()[0] == [False]
        check = harness.binding.last_check(harness.name)
        assert check is not None and check["distance_m"] == 4.0
        assert check["native_sensor_under_threshold"] is True
        assert not harness.feedback.operation_completed(harness.name)
        assert harness.state.phase == "holding"
        assert harness.skill.act_calls == harness.skill.termination_calls == 1
        assert harness.skill.check_calls == 2
    finally:
        harness.close()


def test_original_release_precedes_actual_release_qualified_completion() -> None:
    """Bound geometry enables original release without falsely completing while still held."""
    harness = CompletionHarness()
    try:
        harness.world.positions[harness.object] = list(harness.world.positions[harness.destination])
        harness.observations[next(iter(harness.observations))] = DistanceTensor(4.0)
        before = deepcopy(harness.world.positions)
        harness.admit()
        harness.feedback.physical_step()
        assert harness.skill._internal_act(harness.observations)["grip_action"] == -1.0
        assert harness.terminate()[0] == [False]
        assert harness.world.positions == before
        assert harness.binding.observe(harness.name)["qualified_local_completion"] is False
        harness.release_at_goal()
        harness.feedback.physical_step()
        receipt = harness.receipt()
        assert receipt["schema_version"].endswith("/v0.4")
        assert receipt["local_skill_completed"] is True
        assert receipt["benchmark_goal_satisfied"] is None
        assert receipt["source"] == "post-step-bound-object-release"
        assert receipt["place_binding"]["tool_call_id"] == "call-1"
        assert receipt["place_binding"]["attempt_id"] == "attempt:1:1"
        assert receipt["place_binding"]["bound_object_id"] == 101
        assert harness.state.phase == "placed"
    finally:
        harness.close()


def test_last_budget_step_release_supersedes_false_without_erasing_budget() -> None:
    """Retain the original budget fact when the final Gym release completes placement."""
    harness = CompletionHarness()
    try:
        harness.admit()
        harness.feedback.physical_step()
        harness.skill._cur_skill_step = [100]
        harness.world.positions[harness.object] = list(harness.world.positions[harness.destination])
        assert harness.skill._internal_act(harness.observations)["grip_action"] == -1.0
        harness.terminate()
        assert harness.receipt()["status"] == "skill-budget-exhausted"
        assert harness.state.pending_action is None and harness.state.phase == "holding"
        harness.release_at_goal()
        harness.feedback.physical_step()
        assert harness.receipt()["termination"]["over_max_len"] is True
        assert harness.receipt()["termination"]["base_is_skill_done"] is False
        assert harness.receipt()["status"] == "local-skill-completed"
        assert harness.state.phase == "placed"
        evidence = harness.receipt()["place_policy_input"]
        assert evidence["source_timing"] == "before-completed-gym-step"
        before = evidence["observation"]
        assert before["check_purpose"] == "termination"
        assert before["native_sensor_under_threshold"] is True
        assert before["object_released"] is False
        assert before["tool_call_id"] == harness.receipt()["tool_call_id"]
        assert harness.receipt()["place_binding"]["object_released"] is True
    finally:
        harness.close()


def test_released_far_object_is_not_completed_and_unknown_never_advances() -> None:
    """Neither merely releasing nor unavailable coordinates meet the bound local postcondition."""
    harness = CompletionHarness()
    try:
        harness.admit()
        manager = harness.world.sim.get_agent_data(1).grasp_mgr
        manager.is_grasped, manager.snap_idx = False, None
        harness.feedback.physical_step()
        assert not harness.feedback.operation_completed(harness.name)
        harness.world.positions[harness.object] = [float("nan"), 0.0, 0.0]
        harness.skill._cur_skill_step = [100]
        harness.terminate()
        harness.feedback.physical_step()
        assert harness.receipt()["local_skill_completed"] is None
        assert harness.receipt()["place_binding"]["status"] == "unavailable"
        assert harness.state.phase == "holding"
    finally:
        harness.close()


def test_wrong_grasp_is_rejected_before_original_dispatch_and_during_skill() -> None:
    """Exact absolute grasp identity prevents a different object from being manipulated."""
    harness = CompletionHarness()
    try:
        manager = harness.world.sim.get_agent_data(1).grasp_mgr
        manager.snap_idx = 202
        with pytest.raises(Stage2ContractViolation, match="actual grasp"):
            harness.admit()
        assert harness.agent.dispatched == []
        with pytest.raises(IntegrationError, match="contradicts"):
            harness.skill._internal_act(harness.observations)
        assert harness.skill.check_calls == 0
    finally:
        harness.close()


def test_zero_step_and_stale_tool_call_cannot_complete() -> None:
    """Call and step attribution fence otherwise true observations from premature completion."""
    harness = CompletionHarness()
    try:
        harness.admit()
        harness.release_at_goal()
        harness.terminate()
        assert harness.receipt()["status"] == "completion-evidence-unavailable"
        assert harness.receipt()["local_skill_completed"] is None
        harness.binding.bind_call(harness.name, "another-call", 99)
        harness.feedback.physical_step()
        assert not harness.feedback.operation_completed(harness.name)
    finally:
        harness.close()


def test_holding_reset_is_allowed_only_after_observed_skill_exit() -> None:
    """The original reset tool can retract an occupied arm without implying placement."""
    harness = CompletionHarness()
    try:
        harness.admit()
        harness.agent.llm_model.response = ("reset_arm", {})
        with pytest.raises(Stage2ContractViolation, match="no observed completion"):
            harness.agent.chat("original next observation")
        harness.skill._cur_skill_step = [100]
        harness.feedback.physical_step()
        harness.terminate()
        harness.agent.chat("original next observation")
        assert harness.agent.dispatched[-1] == ("reset_arm", {})
        assert harness.state.phase == "holding"
        assert not harness.feedback.operation_completed(harness.name)
    finally:
        harness.close()


def test_first_reset_exit_is_confirmed_after_its_existing_step_without_inventing_success() -> None:
    """A current reset's native first-input exit clears pending only after its actual step."""
    harness = CompletionHarness()
    try:
        harness.agent.llm_model.response = ("reset_arm", {})
        harness.admit()
        assert harness.reset._internal_act() is harness.reset.action
        result = harness.reset.should_terminate(
            batch_idx=[0], skill_name=["reset_arm"], hl_wants_skill_term=[False]
        )
        assert result[0] == [True]
        assert harness.receipt()["status"] == "completion-evidence-unavailable"
        assert harness.state.pending_action == "reset_arm"
        harness.feedback.physical_step()
        receipt = harness.receipt()
        assert receipt["status"] == "skill-exited-completion-unconfirmed"
        assert receipt["local_skill_completed"] is None
        assert receipt["benchmark_goal_satisfied"] is None
        assert receipt["termination"]["base_is_skill_done"] is True
        assert receipt["physical_steps_since_call"] == 1
        assert harness.state.pending_action is None
        assert harness.state.phase == "holding"
        assert not harness.feedback.operation_completed(harness.name)
        harness.agent.llm_model.response = ("wait", {})
        harness.agent.chat("unchanged original observation")
        assert harness.agent.dispatched[-1] == ("wait", {})
        assert harness.reset.act_calls == harness.reset.termination_calls == 1
        assert sum(row["event"] == "reset-exit-confirmed" for row in harness.rows) == 1
    finally:
        harness.close()
    assert "_internal_act" not in vars(harness.reset)
    assert "should_terminate" not in vars(harness.reset)


@pytest.mark.parametrize("missing", ["act", "step", "termination", "skill", "bad", "call"])
def test_reset_exit_requires_current_action_step_and_definite_nonabort_termination(
    missing: str,
) -> None:
    """Peer steps, zero-step exits, aborts and replaced calls cannot release the pending fence."""
    harness = CompletionHarness()
    try:
        harness.agent.llm_model.response = ("reset_arm", {})
        harness.admit()
        if missing != "act":
            harness.reset._internal_act()
        if missing == "bad":
            harness.reset.bad_terminate = True
        if missing != "termination":
            harness.reset.should_terminate(
                batch_idx=[0],
                skill_name=["wait" if missing == "skill" else "reset_arm"],
                hl_wants_skill_term=[False],
            )
        if missing == "call":
            model = harness.agent.llm_model
            model.chat("a separately selected raw call")
            harness.feedback.admit(harness.name, model, {"name": "reset_arm", "arguments": {}})
        if missing != "step":
            harness.feedback.physical_step()
        assert harness.state.pending_action == "reset_arm"
        assert harness.receipt()["local_skill_completed"] is None
        assert not any(row["event"] == "reset-exit-confirmed" for row in harness.rows)
    finally:
        harness.close()


def test_disabled_profile_keeps_first_reset_observation_unknown() -> None:
    """Legacy feedback and guard retain their existing result under the default-off option."""
    harness = CompletionHarness(enabled=False)
    try:
        harness.state.phase = "before_pick"
        harness.agent.llm_model.response = ("reset_arm", {})
        harness.admit()
        harness.reset._internal_act()
        harness.reset.should_terminate(
            batch_idx=[0], skill_name=["reset_arm"], hl_wants_skill_term=[False]
        )
        harness.feedback.physical_step()
        assert harness.receipt()["status"] == "completion-evidence-unavailable"
        assert harness.state.pending_action == "reset_arm"
        assert "_internal_act" not in vars(harness.reset)
    finally:
        harness.close()


def test_reset_action_exception_keeps_original_error_and_pending_fence() -> None:
    """An original action exception never becomes a current reset witness or silent completion."""
    harness = CompletionHarness()
    try:
        harness.agent.llm_model.response = ("reset_arm", {})
        harness.admit()
        error = RuntimeError("original reset action failure")
        harness.reset.error = error
        with pytest.raises(RuntimeError) as captured:
            harness.reset._internal_act()
        assert captured.value is error
        harness.reset.should_terminate(
            batch_idx=[0], skill_name=["reset_arm"], hl_wants_skill_term=[False]
        )
        harness.feedback.physical_step()
        assert harness.state.pending_action == "reset_arm"
        assert harness.receipt()["local_skill_completed"] is None
    finally:
        harness.close()


def test_two_agents_complete_asynchronously_without_shared_sensor_contamination() -> None:
    """Each agent's exact object/grasp/call state stays isolated while its peer continues."""
    blue = CompletionHarness(index=0)
    red = CompletionHarness(index=1, world=blue.world)
    try:
        blue.admit()
        red.admit()
        blue.skill._internal_act(blue.observations)
        blue.release_at_goal()
        blue.feedback.physical_step()
        red.feedback.physical_step()
        assert blue.feedback.operation_completed(blue.name)
        assert not red.feedback.operation_completed(red.name)
        red.world.positions[red.object] = list(red.world.positions[red.destination])
        red.skill._internal_act(red.observations)
        red.release_at_goal()
        red.feedback.physical_step()
        assert red.feedback.operation_completed(red.name)
        assert (
            blue.receipt()["place_binding"]["object_entity_id"]
            != red.receipt()["place_binding"]["object_entity_id"]
        )
    finally:
        blue.close()
        red.close()


@pytest.mark.parametrize("error", [RuntimeError("original error"), KeyboardInterrupt()])
def test_original_exception_identity_and_instance_cleanup_are_preserved(
    error: BaseException,
) -> None:
    """Preserve exceptions and restore methods on cancelled and repeated cleanup exits."""
    harness = CompletionHarness()
    harness.admit()
    harness.skill.error = error
    try:
        with pytest.raises(type(error)) as raised:
            harness.terminate()
        assert raised.value is error
        assert harness.receipt()["status"] == "original-skill-exception"
    finally:
        harness.close()
    harness.close()
    assert "should_terminate" not in vars(harness.skill)
    assert "_is_skill_done" not in vars(harness.skill)
    assert "chat" not in vars(harness.agent)


def test_readiness_missing_interfaces_and_changed_world_fail_closed() -> None:
    """Missing readers and changed reset identity cannot prove local completion."""
    harness = CompletionHarness()
    try:
        inspect_completion_interfaces([harness.policy], harness.world)
        assert harness.skill.act_calls == harness.skill.check_calls == 0
        harness.world.current_episode.episode_id = "another-episode"
        assert harness.binding.observe(harness.name)["status"] == "unavailable"
        harness.policy._skills = {}
        with pytest.raises((IntegrationError, KeyError)):
            inspect_completion_interfaces([harness.policy], harness.world)
    finally:
        harness.close()


def test_completion_flag_defaults_off_and_requires_relocation(tmp_path: Path) -> None:
    """Ordinary navigation retains its existing path and cannot silently enable place hooks."""
    config = CrabAgentBackendConfig(tmp_path / "config", "episode", 0, 100, 0)
    assert config.relocation_completion_binding is False
    with pytest.raises(IntegrationError, match="requires relocation"):
        CrabAgentBackendConfig(
            tmp_path / "config", "episode", 0, 100, 0, relocation_completion_binding=True
        )


def test_serial_endpoint_reuse_does_not_inherit_the_previous_object_completion() -> None:
    """One reset world may host successive attempts without retaining the earlier call binding."""
    first = CompletionHarness(index=0)
    world = first.world
    first.admit()
    first.skill._internal_act(first.observations)
    first.release_at_goal()
    first.feedback.physical_step()
    assert first.feedback.operation_completed(first.name)
    digest = first.contract.invocation_digest
    first.close()
    second = CompletionHarness(index=0, world=world, object_index=1)
    try:
        assert second.contract.invocation_digest != digest
        assert not second.feedback.operation_completed(second.name)
        world.sim.get_agent_data(0).grasp_mgr.is_grasped = True
        world.sim.get_agent_data(0).grasp_mgr.snap_idx = 101
        second.admit()
        second.feedback.physical_step()
        assert second.skill._internal_act(second.observations)["grip_action"] == 0.0
        assert not second.feedback.operation_completed(second.name)
        second.world.positions[second.object] = list(second.world.positions[second.destination])
        second.skill._internal_act(second.observations)
        second.release_at_goal()
        second.feedback.physical_step()
        assert second.feedback.operation_completed(second.name)
        assert second.receipt()["place_binding"]["attempt_id"] == "attempt:0:1"
    finally:
        second.close()


def test_offered_navigation_target_matches_the_observed_guard_phase() -> None:
    """Schema and guard agree without editing the model's raw response or original tools."""
    harness = CompletionHarness()
    original_tools = deepcopy(harness.agent.llm_model.actions)
    try:
        harness.agent.llm_model.response = ("nav_to_obj", {"target_obj": harness.destination})
        harness.agent.chat("unchanged original observation")
        tools = harness.agent.llm_model.offered_actions
        assert tools is not None
        offered = next(tool for tool in tools if tool["name"] == "nav_to_obj")
        assert offered["parameters"]["properties"]["target_obj"]["enum"] == [harness.destination]
        assert harness.agent.dispatched[-1] == ("nav_to_obj", {"target_obj": harness.destination})
        assert harness.agent.llm_model.actions == original_tools
        harness.state.complete("nav_to_obj", True)
        harness.state.phase = "before_pick"
        harness.agent.llm_model.response = ("nav_to_obj", {"target_obj": harness.object})
        harness.agent.chat("unchanged original observation")
        tools = harness.agent.llm_model.offered_actions
        assert tools is not None
        offered = next(tool for tool in tools if tool["name"] == "nav_to_obj")
        assert offered["parameters"]["properties"]["target_obj"]["enum"] == [harness.object]
    finally:
        harness.close()


def test_feedback_storage_failure_never_changes_the_original_release_action() -> None:
    """Keep archive failure separate from actual geometry, release and exception results."""
    harness = CompletionHarness()
    try:

        def failed_record(row: dict[str, Any]) -> None:
            """Model a storage failure without changing the selected action or actual world."""
            raise OSError("offline full disk")

        harness.feedback._record = failed_record
        harness.world.positions[harness.object] = list(harness.world.positions[harness.destination])
        harness.admit()
        assert harness.skill._internal_act(harness.observations)["grip_action"] == -1.0
        harness.release_at_goal()
        harness.feedback.physical_step()
        assert harness.feedback.operation_completed(harness.name)
        assert harness.receipt()["benchmark_goal_satisfied"] is None
    finally:
        harness.close()


def test_peer_step_without_this_call_place_action_cannot_confirm_completion() -> None:
    """Shared time advancing is not proof that this agent's selected place action ran."""
    harness = CompletionHarness()
    try:
        harness.admit()
        harness.release_at_goal()
        harness.feedback.physical_step()
        harness.terminate()
        assert not harness.feedback.operation_completed(harness.name)
        assert harness.receipt()["local_skill_completed"] is None
        assert "_internal_act" in vars(harness.skill)
    finally:
        harness.close()
    assert "_internal_act" not in vars(harness.skill)
