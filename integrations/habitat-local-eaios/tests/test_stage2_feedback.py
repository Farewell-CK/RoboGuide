"""Deterministic local-result feedback through original-shaped Stage2 boundaries."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

_ROOT = Path(__file__).parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from habitat_local_eaios.model import CanonicalMobilityInvocation, IntegrationError  # noqa: E402
from habitat_local_eaios.stage2_contract import (  # noqa: E402
    Stage2ContractViolation,
    Stage2ExecutionContract,
    install_stage2_contract_guard,
)
from habitat_local_eaios.stage2_feedback import (  # noqa: E402
    FEEDBACK_PROFILE,
    Stage2ExecutionFeedback,
)


def _contract(attempt: str = "attempt-first") -> Stage2ExecutionContract:
    """Use a generic attributed navigation operation, never a benchmark target name."""
    invocation = CanonicalMobilityInvocation(
        mission_id="mission-feedback",
        task_id="task-feedback",
        group_id="group-feedback",
        role_id="role-feedback",
        operation="mobility.move@v1",
        objective="Reach the north entrance.",
        parameters={"destination": "entrance:north"},
        resource_ids=("space:north",),
        attempt_id=attempt,
    )
    return Stage2ExecutionContract.for_invocation(invocation)


class VendorModel:
    """Record actual next-call messages and emit the vendor's premature Success receipt."""

    def __init__(self) -> None:
        """Expose one original-shaped navigation schema and synchronous model history."""
        self.actions = [
            {
                "name": "nav_to_obj",
                "parameters": {
                    "properties": {"target_obj": {"type": "string"}},
                    "required": ["target_obj"],
                },
            }
        ]
        self.planning_stage = False
        self.code_execution = False
        self.chat_history: list[list[Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.next_action: tuple[str, dict[str, Any]] = (
            "nav_to_obj",
            {"target_obj": "entrance:north"},
        )
        self.error: BaseException | None = None

    def chat(self, content: str, crab_planning: bool = False) -> Any:
        """Issue one scripted response, preserving raw call IDs and SDK object shapes."""
        self.requests.append({"content": content, "history": deepcopy(self.chat_history)})
        if self.error is not None:
            raise self.error
        if crab_planning:
            self.chat_history.append(
                [{"role": "user", "content": content}, {"role": "assistant", "content": "plan"}]
            )
            return "plan"
        name, arguments = deepcopy(self.next_action)
        identity = f"tool-{len(self.requests)}"
        self.chat_history.append(
            [
                {"role": "user", "content": content},
                SimpleNamespace(
                    role="assistant",
                    tool_calls=[
                        SimpleNamespace(
                            id=identity,
                            function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
                        )
                    ],
                ),
                {"role": "tool", "tool_call_id": identity, "name": name, "content": "Success"},
            ]
        )
        return name, arguments


class VendorAgent:
    """Retain CrabAgent's post-model target injection and non-navigation wait mapping."""

    def __init__(self, name: str = "robot-north") -> None:
        """Create an isolated model and dispatch ledger."""
        self.name = name
        self.llm_model = VendorModel()
        self.dispatches: list[dict[str, Any]] = []

    def chat(self, observation: str) -> dict[str, Any]:
        """Apply local mapping only after the original model and independent guard return."""
        name, parameters = self.llm_model.chat(observation)
        if name in {"wait", "send_request"}:
            result = {"name": "wait", "arguments": ["500"]}
        else:
            parameters["robot"] = self.name
            result = {"name": name, "arguments": parameters}
        self.dispatches.append(result)
        return result


class VendorSkill:
    """Mirror the original completion OR budget OR high-level termination decision."""

    def __init__(self) -> None:
        """Expose singleton counters and independent completion/termination signals."""
        self._cur_skill_step = [0]
        self._max_skill_steps = 1000
        self._force_end_on_timeout = False
        self.base_done: Any = False
        self.base_calls = 0
        self.termination_calls = 0
        self.error: BaseException | None = None
        self.last_result: Any = None

    def _is_skill_done(self, **kwargs: Any) -> list[Any]:
        """Return the existing completion result exactly once per termination query."""
        del kwargs
        self.base_calls += 1
        if self.error is not None:
            raise self.error
        return [self.base_done]

    def should_terminate(self, **kwargs: Any) -> Any:
        """Preserve vendor return-control and episode-abort distinctions."""
        self.termination_calls += 1
        base = self._is_skill_done(**kwargs)[0]
        budget = self._max_skill_steps > 0 and self._cur_skill_step[0] >= self._max_skill_steps
        bad = budget and self._force_end_on_timeout
        returned_control = bool(base) or (budget and not bad) or kwargs["hl_wants_skill_term"][0]
        self.last_result = ([returned_control], [bad], object())
        return self.last_result


class BoundaryHarness:
    """Compose production feedback and guard around fake original model and skill methods."""

    def __init__(self, *, record: Any = None, skill: VendorSkill | None = None) -> None:
        """Keep all action counts and callback evidence locally for deterministic assertions."""
        self.agent = VendorAgent()
        self.skill = skill if skill is not None else VendorSkill()
        self.rows: list[dict[str, Any]] = []
        self.contract = _contract()
        self.feedback = Stage2ExecutionFeedback(
            {self.agent.name: self.contract}, record if record is not None else self.rows.append
        )
        self.policy = SimpleNamespace(
            _high_level_policy=SimpleNamespace(llm_agent=self.agent),
            _skills={0: self.skill, 1: self.skill},
        )
        self.feedback.install([self.policy])
        self.restore = install_stage2_contract_guard(
            [self.agent],
            {self.agent.name: self.contract},
            lambda row: None,
            feedback=self.feedback,
        )

    def terminate(self, skill_name: str = "nav_to_obj", *, high_level: bool = False) -> Any:
        """Run the original-shaped decision, retaining its exact returned object."""
        result = self.skill.should_terminate(
            skill_name=[skill_name], batch_idx=[0], hl_wants_skill_term=[high_level]
        )
        assert result is self.skill.last_result
        return result

    def receipt(self) -> dict[str, Any]:
        """Decode the actual tool content which will reach the next Provider request."""
        return cast(
            dict[str, Any], json.loads(self.agent.llm_model.chat_history[-1][-1]["content"])
        )

    def close(self) -> None:
        """Restore both original boundaries on every test exit."""
        self.restore()
        self.feedback.close()


def test_budget_exit_is_not_success_in_the_next_real_shaped_model_request() -> None:
    """A false arrival at step 1000 returns budget exhaustion rather than optimistic Success."""
    harness = BoundaryHarness()
    try:
        first_action = harness.agent.chat("Current scene: north entrance.")
        raw = deepcopy(harness.agent.llm_model.chat_history[0][1])
        assert first_action == {
            "name": "nav_to_obj",
            "arguments": {"target_obj": "entrance:north", "robot": harness.agent.name},
        }
        assert harness.receipt()["status"] == "accepted-awaiting-observation"
        harness.skill._cur_skill_step = [1000]
        harness.terminate()
        assert harness.receipt()["status"] == "skill-budget-exhausted"
        assert harness.receipt()["local_skill_completed"] is False
        assert harness.receipt()["benchmark_goal_satisfied"] is None
        harness.agent.llm_model.next_action = ("wait", {})
        harness.agent.chat(
            "You have completed your previous action. Based on the task, select your next action."
        )
        actual_request = harness.agent.llm_model.requests[1]
        assert actual_request["content"].startswith("Use the recorded local execution feedback")
        previous = json.loads(actual_request["history"][0][-1]["content"])
        assert previous["status"] == "skill-budget-exhausted"
        assert previous["termination"]["base_is_skill_done"] is False
        assert previous["termination"]["over_max_len"] is True
        assert previous["contract"]["invocation_digest"] == harness.contract.invocation_digest
        assert actual_request["history"][0][1].tool_calls == raw.tool_calls
        assert harness.skill.base_calls == harness.skill.termination_calls == 1
        assert len(harness.agent.llm_model.requests) == len(harness.agent.dispatches) == 2
        assert [row["event"] for row in harness.rows] == [
            "admitted",
            "skill-terminated",
            "model-input-prepared",
            "admitted",
        ]
    finally:
        harness.close()


@pytest.mark.parametrize(
    ("done", "counter", "high_level", "abort", "expected"),
    [
        (True, 24, False, False, "local-skill-completed"),
        (True, 1000, True, False, "local-skill-completed"),
        (False, 12, True, False, "high-level-interrupted"),
        (False, 1000, False, True, "skill-budget-exhausted"),
        ("unreadable", 2, False, False, "termination-result-unavailable"),
    ],
)
def test_direct_completion_budget_and_high_level_causes_remain_distinct(
    done: Any, counter: int, high_level: bool, abort: bool, expected: str
) -> None:
    """Returning control does not automatically prove navigation or official success."""
    harness = BoundaryHarness()
    try:
        harness.agent.chat("first action")
        harness.skill.base_done = done
        if done is True:
            harness.feedback.physical_step()
        harness.skill._cur_skill_step = [counter]
        harness.skill._force_end_on_timeout = abort
        harness.terminate(high_level=high_level)
        receipt = harness.receipt()
        assert receipt["status"] == expected
        assert receipt["profile"] == FEEDBACK_PROFILE
        assert receipt["benchmark_goal_satisfied"] is None
        assert receipt["local_skill_completed"] == (done if isinstance(done, bool) else None)
        assert receipt["termination"]["bad_terminate"] is abort
    finally:
        harness.close()


def test_nonterminal_skill_does_not_emit_per_step_json_or_claim_arrival() -> None:
    """Repeated original skill queries add no model calls, action rewrites, or event records."""
    harness = BoundaryHarness()
    try:
        harness.agent.chat("first action")
        for step in range(20):
            harness.skill._cur_skill_step = [step]
            harness.terminate()
        assert harness.receipt()["status"] == "accepted-awaiting-observation"
        assert harness.skill.base_calls == harness.skill.termination_calls == 20
        assert len(harness.rows) == len(harness.agent.llm_model.requests) == 1
    finally:
        harness.close()


def test_final_gym_step_completion_supersedes_an_earlier_budget_decision() -> None:
    """The policy-input stop reason cannot hide actual local arrival on the last Gym step."""
    harness = BoundaryHarness()
    try:
        harness.agent.chat("first action")
        harness.skill._cur_skill_step = [1000]
        harness.terminate()
        harness.feedback.physical_step()
        harness.feedback.local_completion(harness.agent.name)
        receipt = harness.receipt()
        assert receipt["status"] == "local-skill-completed"
        assert receipt["source"] == "post-step-oracle-nav-terminal-measure"
        assert receipt["local_skill_completed"] is True
        assert receipt["termination"]["base_is_skill_done"] is False
        assert receipt["termination"]["over_max_len"] is True
        assert receipt["benchmark_goal_satisfied"] is None
        assert harness.skill.termination_calls == 1
        assert len(harness.agent.llm_model.requests) == 1
    finally:
        harness.close()


def test_pre_execution_sensor_cannot_complete_a_new_action() -> None:
    """The first policy input may retain another action's sensor and is not arrival evidence."""
    harness = BoundaryHarness()
    try:
        harness.agent.chat("new navigation")
        harness.skill.base_done = True
        harness.skill._cur_skill_step = [1]
        harness.terminate()
        receipt = harness.receipt()
        assert receipt["status"] == "completion-evidence-unavailable"
        assert receipt["local_skill_completed"] is None
        assert receipt["termination"]["base_is_skill_done"] is True
        assert receipt["physical_steps_since_call"] == 0
    finally:
        harness.close()


def test_actual_feedback_is_in_current_input_even_if_vendor_history_is_not_used() -> None:
    """A current observation carries the result independently of a vendor's history window."""
    harness = BoundaryHarness()
    try:
        harness.agent.chat("first action")
        harness.skill._cur_skill_step = [1000]
        harness.terminate()
        content = harness.feedback.before_call(harness.agent.name, harness.agent.llm_model, "next")
        delivered = json.loads(content.split("Local execution feedback:\n", 1)[1])
        assert delivered["status"] == "skill-budget-exhausted"
        assert delivered["tool_call_id"] == "tool-1"
        assert delivered["contract"]["invocation_digest"] == harness.contract.invocation_digest
    finally:
        harness.close()


def test_wait_completion_does_not_complete_the_canonical_navigation() -> None:
    """Finishing an intentional wait remains wait-finished with benchmark truth unavailable."""
    harness = BoundaryHarness()
    try:
        harness.agent.llm_model.next_action = ("wait", {})
        assert harness.agent.chat("wait by model choice") == {"name": "wait", "arguments": ["500"]}
        harness.skill.base_done = True
        harness.feedback.physical_step()
        harness.terminate("wait")
        assert harness.receipt()["status"] == "wait-finished"
        assert harness.receipt()["tool_name"] == "wait"
        assert harness.receipt()["benchmark_goal_satisfied"] is None
    finally:
        harness.close()


def test_request_mapped_to_wait_does_not_claim_peer_delivery_or_navigation_success() -> None:
    """A peer request may finish its mapped wait but cannot prove another agent took action."""
    first, second = VendorAgent("north"), VendorAgent("south")
    contracts = {name: _contract() for name in ("north", "south")}
    skill = VendorSkill()
    feedback = Stage2ExecutionFeedback(contracts, lambda row: None)
    feedback.install(
        [SimpleNamespace(_high_level_policy=SimpleNamespace(llm_agent=first), _skills={0: skill})]
    )
    restore = install_stage2_contract_guard(
        [first, second], contracts, lambda row: None, feedback=feedback
    )
    try:
        first.llm_model.next_action = (
            "send_request",
            {"target_agent": "south", "request": "status?"},
        )
        assert first.chat("request") == {"name": "wait", "arguments": ["500"]}
        skill.base_done = True
        feedback.physical_step()
        skill.should_terminate(skill_name=["wait"], batch_idx=[0], hl_wants_skill_term=[False])
        receipt = json.loads(first.llm_model.chat_history[-1][-1]["content"])
        assert receipt["tool_name"] == "send_request"
        assert receipt["status"] == "wait-finished"
        assert receipt["benchmark_goal_satisfied"] is None
        assert len(second.llm_model.requests) == 0
    finally:
        restore()
        feedback.close()


def test_unrelated_skill_termination_cannot_complete_the_pending_navigation() -> None:
    """An old wait completion does not cross-bind to a newly selected navigation call."""
    harness = BoundaryHarness()
    try:
        harness.agent.chat("first action")
        harness.skill.base_done = True
        harness.terminate("wait")
        assert harness.receipt()["status"] == "accepted-awaiting-observation"
        assert len(harness.rows) == 1
    finally:
        harness.close()


def test_unknown_budget_never_supplies_a_guessed_exhaustion_reason() -> None:
    """Malformed bookkeeping stays unknown even when the original skill returns control."""

    class UnexposedBudgetSkill(VendorSkill):
        """Represent a vendor which does not retain its completed skill's bookkeeping."""

        def should_terminate(self, **kwargs: Any) -> Any:
            """Remove the counter only after the original decision has returned."""
            result = super().should_terminate(**kwargs)
            del self._cur_skill_step
            return result

    harness = BoundaryHarness(skill=UnexposedBudgetSkill())
    try:
        harness.agent.chat("first action")
        harness.skill._cur_skill_step = [1000]
        harness.terminate()
        receipt = harness.receipt()
        assert receipt["status"] == "termination-result-unavailable"
        assert receipt["termination"]["over_max_len"] is None
        assert receipt["local_skill_completed"] is False
    finally:
        harness.close()


def test_feedback_storage_failure_does_not_change_actual_input_or_returned_action() -> None:
    """A disk failure cannot restore synthetic Success or mask the observed budget exit."""

    def unavailable(row: dict[str, Any]) -> None:
        """Represent an unavailable evidence volume without touching model history."""
        del row
        raise OSError("read-only evidence volume")

    harness = BoundaryHarness(record=unavailable)
    try:
        result = harness.agent.chat("first action")
        harness.skill._cur_skill_step = [1000]
        harness.terminate()
        assert result["arguments"]["target_obj"] == "entrance:north"
        assert harness.receipt()["status"] == "skill-budget-exhausted"
    finally:
        harness.close()


def test_original_skill_exception_propagates_and_internal_method_is_restored() -> None:
    """Feedback cannot swallow a vendor error or leave an observer on its completion method."""
    harness = BoundaryHarness()
    error = RuntimeError("original termination sentinel")
    try:
        harness.agent.chat("first action")
        harness.skill.error = error
        with pytest.raises(RuntimeError) as observed:
            harness.terminate()
        assert observed.value is error
        assert "_is_skill_done" not in vars(harness.skill)
    finally:
        harness.close()
    assert "should_terminate" not in vars(harness.skill)
    assert "chat" not in vars(harness.agent)
    assert harness.receipt()["status"] == "original-skill-exception"
    assert harness.receipt()["error_type"] == "RuntimeError"


@pytest.mark.parametrize("original_error", [False, True])
def test_observation_serialization_failure_preserves_completion_or_original_exception(
    monkeypatch: pytest.MonkeyPatch, original_error: bool
) -> None:
    """Terminal feedback serialization cannot turn arrival into failure or mask a vendor error."""
    harness = BoundaryHarness()
    harness.agent.chat("first action")
    error = RuntimeError("original physical sentinel")

    def serialization_unavailable(*args: Any, **kwargs: Any) -> Any:
        """Represent a failed observation encoder after model action admission."""
        del args, kwargs
        raise TypeError("observation encoder unavailable")

    with monkeypatch.context() as context:
        context.setattr("habitat_local_eaios.stage2_feedback.json.dumps", serialization_unavailable)
        try:
            if original_error:
                harness.skill.error = error
                with pytest.raises(RuntimeError) as observed:
                    harness.terminate()
                assert observed.value is error
            else:
                harness.feedback.physical_step()
                harness.feedback.local_completion(harness.agent.name)
                assert harness.rows[-1]["local_skill_completed"] is True
            assert harness.rows[-1]["receipt_update_status"] == "unavailable"
        finally:
            harness.close()
    assert "chat" not in vars(harness.agent)
    assert "should_terminate" not in vars(harness.skill)
    assert "_is_skill_done" not in vars(harness.skill)


def test_original_provider_exception_propagates_without_extra_call_or_dispatch() -> None:
    """No local feedback retry consumes another model request after a Provider exception."""
    harness = BoundaryHarness()
    error = TimeoutError("provider sentinel")
    try:
        harness.agent.llm_model.error = error
        with pytest.raises(TimeoutError) as observed:
            harness.agent.chat("first action")
        assert observed.value is error
        assert len(harness.agent.llm_model.requests) == 1
        assert harness.agent.dispatches == []
        assert "chat" not in vars(harness.agent.llm_model)
    finally:
        harness.close()


def test_rejected_target_is_never_admitted_by_feedback_or_dispatched() -> None:
    """Honest outcome reporting remains independent of the exact-target action guard."""
    harness = BoundaryHarness()
    try:
        harness.agent.llm_model.next_action = ("nav_to_obj", {"target_obj": "entrance:south"})
        with pytest.raises(Stage2ContractViolation, match="canonical destination"):
            harness.agent.chat("first action")
        assert harness.rows == []
        assert harness.agent.dispatches == []
        assert harness.agent.llm_model.chat_history[0][-1]["content"] == "Success"
    finally:
        harness.close()


def test_incompatible_receipt_fails_before_physical_dispatch() -> None:
    """Unsupported vendor history cannot pass optimistic text off as attributed feedback."""

    class UnboundModel(VendorModel):
        """Return a call and receipt with different opaque IDs."""

        def chat(self, content: str, crab_planning: bool = False) -> Any:
            """Keep raw response unchanged while injecting a malformed vendor receipt."""
            result = super().chat(content, crab_planning)
            self.chat_history[-1][-1]["tool_call_id"] = "another-tool"
            return result

    harness = BoundaryHarness()
    try:
        harness.agent.llm_model = UnboundModel()
        with pytest.raises(IntegrationError, match="exact tool receipt"):
            harness.agent.chat("first action")
        assert harness.agent.dispatches == []
    finally:
        harness.close()


def test_new_attempt_does_not_receive_stale_completion_feedback() -> None:
    """Execution hooks close old pending work and bind new feedback to a fresh invocation digest."""
    harness = BoundaryHarness()
    harness.agent.chat("first attempt")
    harness.close()
    old_receipt = harness.receipt()
    assert old_receipt["status"] == "segment-ended-without-observed-skill-termination"
    next_contract = _contract("attempt-next")
    next_feedback = Stage2ExecutionFeedback(
        {harness.agent.name: next_contract}, harness.rows.append
    )
    next_feedback.install([harness.policy])
    restore = install_stage2_contract_guard(
        [harness.agent],
        {harness.agent.name: next_contract},
        lambda row: None,
        feedback=next_feedback,
    )
    try:
        harness.agent.chat("new canonical attempt")
        assert harness.receipt()["status"] == "accepted-awaiting-observation"
        assert harness.receipt()["action_sequence"] == 1
        assert harness.receipt()["contract"]["invocation_digest"] == next_contract.invocation_digest
        assert next_contract.invocation_digest != old_receipt["contract"]["invocation_digest"]
        harness.skill.base_done = True
        harness.terminate()
        assert json.loads(harness.agent.llm_model.chat_history[0][-1]["content"]) == old_receipt
    finally:
        restore()
        next_feedback.close()


def test_shared_skill_instance_is_rejected_and_all_hooks_are_rolled_back() -> None:
    """One mutable skill object cannot provide independently attributed outcomes for two agents."""
    first, second = VendorAgent("north"), VendorAgent("south")
    skill = VendorSkill()
    feedback = Stage2ExecutionFeedback(
        {name: _contract() for name in ("north", "south")}, lambda row: None
    )
    with pytest.raises(IntegrationError, match="share agent ownership"):
        feedback.install(
            [
                SimpleNamespace(
                    _high_level_policy=SimpleNamespace(llm_agent=agent), _skills={0: skill}
                )
                for agent in (first, second)
            ]
        )
    assert "should_terminate" not in vars(skill)
    assert "_is_skill_done" not in vars(skill)


def test_unavailable_termination_interface_preserves_unknown_feedback() -> None:
    """An unsupported vendor skill cannot synthesize a completed action."""
    harness = BoundaryHarness()
    harness.close()
    feedback = Stage2ExecutionFeedback({harness.agent.name: harness.contract}, harness.rows.append)
    feedback.install(
        [
            SimpleNamespace(
                _high_level_policy=SimpleNamespace(llm_agent=harness.agent), _skills={0: object()}
            )
        ]
    )
    restore = install_stage2_contract_guard(
        [harness.agent], {harness.agent.name: harness.contract}, lambda row: None, feedback=feedback
    )
    try:
        harness.agent.chat("first action")
        assert harness.receipt()["status"] == "accepted-awaiting-observation"
        feedback.local_completion("unassigned-agent")
        assert harness.receipt()["local_skill_completed"] is None
    finally:
        restore()
        feedback.close()


def test_planning_only_exchange_is_not_rewritten_as_an_execution() -> None:
    """The guard's existing non-tool planning branch does not generate outcome receipts."""
    harness = BoundaryHarness()
    try:
        assert harness.agent.llm_model.chat("plan only", crab_planning=True) == "plan"
        assert harness.rows == []
        assert harness.agent.llm_model.chat_history[-1][-1]["content"] == "plan"
    finally:
        harness.close()


def test_same_tool_ids_in_different_agents_cannot_cross_bind_outcomes() -> None:
    """Feedback isolation uses original agent/model/attempt ownership beyond Provider call IDs."""
    first, second = VendorAgent("north"), VendorAgent("south")
    first_skill, second_skill = VendorSkill(), VendorSkill()
    contracts = {
        "north": _contract(),
        "south": _contract("attempt-second"),
    }
    rows: list[dict[str, Any]] = []
    feedback = Stage2ExecutionFeedback(contracts, rows.append)
    feedback.install(
        [
            SimpleNamespace(_high_level_policy=SimpleNamespace(llm_agent=agent), _skills={0: skill})
            for agent, skill in ((first, first_skill), (second, second_skill))
        ]
    )
    restore = install_stage2_contract_guard(
        [first, second], contracts, lambda row: None, feedback=feedback
    )
    try:
        first.chat("first")
        second.chat("first")
        first_skill.base_done = True
        feedback.physical_step()
        first_skill.should_terminate(
            skill_name=["nav_to_obj"], batch_idx=[0], hl_wants_skill_term=[False]
        )
        first_receipt = json.loads(first.llm_model.chat_history[-1][-1]["content"])
        second_receipt = json.loads(second.llm_model.chat_history[-1][-1]["content"])
        assert first_receipt["tool_call_id"] == second_receipt["tool_call_id"]
        assert first_receipt["status"] == "local-skill-completed"
        assert second_receipt["status"] == "accepted-awaiting-observation"
    finally:
        restore()
        feedback.close()
