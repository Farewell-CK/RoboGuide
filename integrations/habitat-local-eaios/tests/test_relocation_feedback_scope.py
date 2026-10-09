"""Observed grasp failures and truthful peer scope without model or simulator calls."""

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

from habitat_local_eaios.model import IntegrationError  # noqa: E402
from habitat_local_eaios.relocation_completion import RelocationCompletionBinding  # noqa: E402
from habitat_local_eaios.stage2_contract import (  # noqa: E402
    Stage2ContractViolation,
    install_stage2_contract_guard,
)
from habitat_local_eaios.stage2_feedback import Stage2ExecutionFeedback  # noqa: E402
from test_relocation_completion import CompletionHarness  # noqa: E402
from test_relocation_contract import _FakeModel  # noqa: E402
from test_stage2_feedback import VendorSkill  # noqa: E402


class ObservedArm:
    """Expose existing geometry getters, with no control, IK or random sampling interface."""

    def __init__(self) -> None:
        """Keep copied-observation behavior and read counts independently inspectable."""
        self.base_pos = [6.0, 0.0, 1.0]
        self.end_effector = [7.0, 2.0, 1.0]
        self.arm_joint_pos = [0.1, 0.2, 0.3]
        self.reads = 0
        self.error: Exception | None = None

    def ee_transform(self) -> Any:
        """Read the original-shaped end-effector transform without mutating physical state."""
        self.reads += 1
        if self.error is not None:
            raise self.error
        return SimpleNamespace(translation=self.end_effector)


def _attach_arm(harness: CompletionHarness) -> ObservedArm:
    """Attach optional stable robot readers to the selected real-shaped agent slot."""
    data = [harness.world.sim.get_agent_data(index) for index in range(2)]
    arm = ObservedArm()
    data[harness.index].articulated_agent = arm
    harness.world.sim.get_agent_data = lambda index: data[index]
    return arm


class RecordingModel(_FakeModel):
    """Retain actual adapter input and raw output while never contacting a Provider."""

    def __init__(self, original: _FakeModel) -> None:
        """Copy only the original tools and keep invocation-specific call identities."""
        super().__init__(("wait", {}))
        self.actions: list[dict[str, Any]] = deepcopy(original.actions)
        self.requests: list[dict[str, Any]] = []

    def chat(self, content: str, crab_planning: bool = False) -> Any:
        """Record the offered schema and emit the next unedited original-shaped selection."""
        self.requests.append({"content": content, "tools": deepcopy(self.actions)})
        result = super().chat(content, crab_planning)
        if not crab_planning:
            exchange = self.chat_history[-1]
            identity = f"call-{len(self.requests)}"
            exchange[1]["tool_calls"][0]["id"] = identity
            exchange[2]["tool_call_id"] = identity
        return result


class PeerHarness:
    """Compose both exact-object bindings with production feedback, guard and original skills."""

    def __init__(self, *, enabled: bool = True) -> None:
        """Use distinct nonordinal objects and explicit current attempt identities."""
        self.first = CompletionHarness(index=0)
        self.second = CompletionHarness(index=1, world=self.first.world)
        self.first_model = RecordingModel(self.first.agent.llm_model)
        self.second_model = RecordingModel(self.second.agent.llm_model)
        for harness, model in (
            (self.first, self.first_model),
            (self.second, self.second_model),
        ):
            harness.close()
            harness.agent.llm_model = model
            model.actions.append(
                {
                    "name": "send_request",
                    "description": "Send a text message to a fellow agent.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "target_agent": {"type": "string"},
                            "request": {"type": "string"},
                        },
                        "required": ["request", "target_agent"],
                    },
                }
            )
        self.arm = _attach_arm(self.first)
        self.first.state.phase = "before_pick"
        manager = self.first.world.sim.get_agent_data(0).grasp_mgr
        manager.is_grasped, manager.snap_idx = False, None
        self.pick = VendorSkill()
        self.pick._max_skill_steps = 100
        self.wait = VendorSkill()
        self.wait.base_done = True
        self.first.policy._skills.update({30: self.pick, 31: self.wait})
        self.first.policy._idx_to_name.update({30: "pick", 31: "wait"})
        self.contracts = {harness.name: harness.contract for harness in (self.first, self.second)}
        self.states = {harness.name: harness.state for harness in (self.first, self.second)}
        self.rows: list[dict[str, Any]] = []
        self.decisions: list[dict[str, Any]] = []
        self.binding = RelocationCompletionBinding(self.first.world, self.contracts)
        self.feedback = Stage2ExecutionFeedback(
            self.contracts,
            self.rows.append,
            completion=lambda name, action, done: self.states[name].complete(action, done),
            completion_binding=self.binding if enabled else None,
        )
        self.feedback.install([self.first.policy, self.second.policy])
        self.restore = install_stage2_contract_guard(
            [self.first.agent, self.second.agent],
            self.contracts,
            self.decisions.append,
            feedback=self.feedback,
            relocation_states=self.states,
        )

    def complete_peer(self) -> None:
        """Use original place generation and an actual-shaped Gym release observation."""
        self.second.agent.llm_model.response = (
            "place",
            {"target_obj": self.second.object, "target_location": self.second.destination},
        )
        self.second.agent.chat("actual original observation")
        self.second.world.positions[self.second.object] = list(
            self.second.world.positions[self.second.destination]
        )
        self.second.skill._internal_act(self.second.observations)
        self.second.release_at_goal()
        self.feedback.physical_step()
        assert self.feedback.operation_completed(self.second.name)

    def receipt(self) -> dict[str, Any]:
        """Decode the exact latest tool receipt of the first assigned endpoint."""
        result: dict[str, Any] = json.loads(
            self.first.agent.llm_model.chat_history[-1][-1]["content"]
        )
        return result

    def close(self) -> None:
        """Restore both original instance boundaries even after guard or reader failures."""
        self.restore()
        self.feedback.close()


def test_pick_budget_feedback_contains_bound_actual_arm_state_for_next_model_call() -> None:
    """A failed pick reports exact geometry and grasp, without fabricating an impossibility."""
    harness = PeerHarness()
    try:
        harness.first.world.positions[harness.first.object] = [8.0, 2.0, 1.0]
        harness.first.agent.llm_model.response = ("pick", {"target_obj": harness.first.object})
        original = deepcopy(harness.first.agent.llm_model.response)
        harness.first.agent.chat("actual pick observation")
        assert harness.first.agent.dispatched[-1] == original
        admission = deepcopy(harness.receipt()["manipulation_observation"])
        harness.arm.end_effector = [7.5, 2.0, 1.0]
        harness.arm.arm_joint_pos[0] = 0.4
        for _ in range(100):
            harness.feedback.physical_step()
        # Existing Gym counters alone do not trigger additional diagnostic reads.
        assert harness.arm.reads == 1
        harness.pick._cur_skill_step = [100]
        result = harness.pick.should_terminate(
            skill_name=["pick"], batch_idx=[0], hl_wants_skill_term=[False]
        )
        assert result is harness.pick.last_result
        failed = harness.receipt()
        assert failed["status"] == "skill-budget-exhausted"
        assert failed["local_skill_completed"] is False
        assert failed["benchmark_goal_satisfied"] is None
        observation = failed["manipulation_observation"]
        assert observation["attempt_id"] == harness.first.contract.attempt_id
        assert observation["invocation_digest"] == harness.first.contract.invocation_digest
        assert observation["object_entity_id"] == harness.first.object
        assert observation["bound_object_id"] == 202
        assert observation["object_position"] == [8.0, 2.0, 1.0]
        assert observation["base_position"] == [6.0, 0.0, 1.0]
        assert observation["end_effector_position"] == [7.5, 2.0, 1.0]
        assert observation["end_effector_to_object_distance_m"] == 0.5
        assert observation["is_grasped"] is False and observation["snap_object_id"] is None
        assert observation["physical_reachability"] == "unknown"
        assert admission["end_effector_to_object_distance_m"] == 1.0
        assert admission["arm_joint_positions"] == [0.1, 0.2, 0.3]
        assert harness.first.state.phase == "before_pick"
        harness.first.agent.llm_model.response = ("wait", {})
        harness.first.agent.chat("You have completed your previous action. Select next action.")
        content = harness.first_model.requests[-1]["content"]
        next_feedback = json.loads(content.split("Local execution feedback:\n")[1].split("\n\n")[0])
        assert next_feedback["manipulation_observation"] == observation
        assert next_feedback["current_manipulation_observation"]["is_grasped"] is False
        assert harness.pick.base_calls == harness.pick.termination_calls == 1
        assert len(harness.first_model.requests) == 2
    finally:
        harness.close()


@pytest.mark.parametrize("fault", ["ee-reader", "nonfinite", "joint-budget", "world-changed"])
def test_optional_arm_faults_remain_unknown_and_preserve_original_budget_result(fault: str) -> None:
    """Unavailable observations never change termination, targets or physical feasibility."""
    harness = PeerHarness()
    try:
        harness.first.agent.llm_model.response = ("pick", {"target_obj": harness.first.object})
        harness.first.agent.chat("original observation")
        if fault == "ee-reader":
            harness.arm.error = RuntimeError("unavailable pose")
        elif fault == "nonfinite":
            harness.arm.end_effector = [float("nan"), 0.0, 0.0]
        elif fault == "joint-budget":
            harness.arm.arm_joint_pos = [0.0] * 65
        else:
            harness.first.world.current_episode.episode_id = "another-world"
        harness.feedback.physical_step()
        harness.pick._cur_skill_step = [100]
        result = harness.pick.should_terminate(
            skill_name=["pick"], batch_idx=[0], hl_wants_skill_term=[False]
        )
        assert result is harness.pick.last_result
        receipt = harness.receipt()
        assert receipt["status"] == "skill-budget-exhausted"
        assert receipt["manipulation_observation"]["status"] in {"partial", "unavailable"}
        assert receipt["manipulation_observation"]["gaps"]
        assert receipt["manipulation_observation"]["physical_reachability"] == "unknown"
        assert harness.pick.base_calls == harness.pick.termination_calls == 1
        if fault == "world-changed":
            assert harness.arm.reads == 1
    finally:
        harness.close()


@pytest.mark.parametrize("fault", ["reader", "serialization", "byte-budget"])
def test_observation_collection_fault_does_not_block_exact_selected_action(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Optional observation and serialization failures stay separate from action admission."""
    harness = PeerHarness()

    def observe(name: str) -> dict[str, Any]:
        """Model an unavailable reader or bounded serialization failure, not physical state."""
        del name
        if fault == "reader":
            raise RuntimeError("read failed")
        return {"bad": object() if fault == "serialization" else "x" * 8193}

    monkeypatch.setattr(harness.binding, "observe_manipulation", observe)
    try:
        harness.first.agent.llm_model.response = ("pick", {"target_obj": harness.first.object})
        assert harness.first.agent.chat("original observation")[0] == "pick"
        assert harness.receipt()["manipulation_observation"]["status"] == "unavailable"
        assert len(harness.first.agent.dispatched) == 1
    finally:
        harness.close()


def test_active_peer_message_is_scoped_communication_not_task_transfer_or_ack() -> None:
    """An accepted message preserves both commitments; its wait does not prove assistance."""
    harness = PeerHarness()
    try:
        model = harness.first_model
        original_tools = deepcopy(model.actions)
        model.response = (
            "send_request",
            {"target_agent": harness.second.name, "request": "status?"},
        )
        harness.first.agent.chat("actual original observation")
        request = model.requests[-1]
        tools = {tool["name"]: tool for tool in request["tools"]}
        assert tools["send_request"]["parameters"]["properties"]["target_agent"]["enum"] == [
            harness.second.name
        ]
        assert "does not delegate or reassign" in tools["send_request"]["description"]
        scope = json.loads(request["content"].split("Committed peer execution scope:\n")[1])
        assert scope["operation_delegation_supported"] is False
        peer = scope["peers"][0]
        assert peer["canonical_contract"]["expected_object"] == harness.second.object
        assert peer["canonical_contract"]["expected_destination"] == harness.second.destination
        assert peer["canonical_contract"]["attempt_id"] == harness.second.contract.attempt_id
        assert peer["accepting_model_messages"] is True
        assert harness.first.agent.dispatched[-1] == model.response
        harness.feedback.physical_step()
        harness.wait.should_terminate(
            skill_name=["wait"], batch_idx=[0], hl_wants_skill_term=[False]
        )
        receipt = harness.receipt()
        assert receipt["status"] == "wait-finished"
        assert receipt["peer_request"]["delivery_status"] == "unobserved"
        assert receipt["peer_request"]["peer_action_completed"] is None
        assert receipt["peer_request"]["wait_completion_is_acknowledgement"] is False
        assert not harness.feedback.operation_completed(harness.first.name)
        assert not harness.feedback.operation_completed(harness.second.name)
        assert harness.second_model.requests == []
        assert model.actions == original_tools
    finally:
        harness.close()


def test_completed_peer_is_not_offered_and_raw_request_rejects_before_dispatch() -> None:
    """The archived dead-wait pattern becomes explicit local failure with no invented transfer."""
    harness = PeerHarness()
    try:
        harness.complete_peer()
        original_tools = deepcopy(harness.first.agent.llm_model.actions)
        harness.first.agent.llm_model.response = (
            "send_request",
            {"target_agent": harness.second.name, "request": "report status"},
        )
        with pytest.raises(Stage2ContractViolation, match="another active agent"):
            harness.first.agent.chat("original observation")
        request = harness.first_model.requests[-1]
        assert "send_request" not in {tool["name"] for tool in request["tools"]}
        scope = json.loads(request["content"].split("Committed peer execution scope:\n")[1])
        assert scope["peers"][0]["model_status"] == "completed-idle"
        assert scope["peers"][0]["accepting_model_messages"] is False
        assert harness.first.agent.dispatched == []
        assert harness.feedback.operation_completed(harness.second.name)
        assert harness.second.state.phase == "placed"
        assert harness.decisions[-1]["decision"] == "rejected"
        assert harness.decisions[-1]["selected_action"] == {
            "name": "send_request",
            "arguments": harness.first.agent.llm_model.response[1],
        }
        assert harness.first.agent.llm_model.actions == original_tools
    finally:
        harness.close()


@pytest.mark.parametrize("fault", ["missing-target", "null-enum", "duplicate-tool"])
def test_bad_peer_schema_fails_before_model_and_restores_original_tools(fault: str) -> None:
    """A missing target contract cannot leave a partially narrowed model schema installed."""
    harness = PeerHarness()
    try:
        model = harness.first_model
        if fault == "missing-target":
            model.actions[-1]["parameters"]["properties"].pop("target_agent")
        elif fault == "null-enum":
            model.actions[-1]["parameters"]["properties"]["target_agent"]["enum"] = None
        else:
            model.actions.append(deepcopy(model.actions[-1]))
        original = deepcopy(model.actions)
        with pytest.raises(IntegrationError, match="peer message"):
            harness.first.agent.chat("original observation")
        assert model.requests == []
        assert model.actions == original
    finally:
        harness.close()


def test_peer_schema_never_broadens_original_declared_recipients() -> None:
    """A model-bearing peer still needs the deployment's original messaging tool authority."""
    harness = PeerHarness()
    try:
        model = harness.first_model
        model.actions[-1]["parameters"]["properties"]["target_agent"]["enum"] = ["other-agent"]
        model.response = (
            "send_request",
            {"target_agent": harness.second.name, "request": "status?"},
        )
        with pytest.raises(Stage2ContractViolation, match="outside the offered tool schema"):
            harness.first.agent.chat("original observation")
        assert harness.first.agent.dispatched == []
        assert "send_request" not in {tool["name"] for tool in model.requests[0]["tools"]}
        assert model.actions[-1]["parameters"]["properties"]["target_agent"]["enum"] == [
            "other-agent"
        ]
    finally:
        harness.close()


def test_single_assigned_endpoint_cannot_request_an_unassigned_model() -> None:
    """Serial one-Actor sessions cannot communicate with the deployment's passive idle peer."""
    harness = CompletionHarness(index=1)
    try:
        harness.agent.llm_model.response = (
            "send_request",
            {"target_agent": "agent_0", "request": "status?"},
        )
        with pytest.raises(Stage2ContractViolation, match="another active agent"):
            harness.agent.chat("original observation")
        assert harness.agent.dispatched == []
        assert harness.feedback.peer_execution_scope(harness.name)["peers"] == []
        assert harness.feedback.request_peer_names(harness.name) == frozenset()
    finally:
        harness.close()


def test_disabled_binding_preserves_old_model_input_and_peer_tool_schema() -> None:
    """Default/legacy feedback keeps its original action and unbound communication surface."""
    harness = PeerHarness(enabled=False)
    try:
        model = harness.first_model
        original = deepcopy(model.actions)
        model.response = (
            "send_request",
            {"target_agent": harness.second.name, "request": "status?"},
        )
        harness.first.agent.chat("original observation")
        assert "Committed peer execution scope" not in model.requests[0]["content"]
        assert model.requests[0]["tools"][-1] == original[-1]
        assert harness.arm.reads == 0
        assert "manipulation_observation" not in harness.receipt()
        assert harness.receipt()["schema_version"] == "roboguide.stage2-execution-feedback/v0.1"
    finally:
        harness.close()
