"""Offline relocation lifecycle tests using original-shaped policies and one fake world."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios import idle_endpoint  # noqa: E402
from habitat_local_eaios.crabagent_backend import CrabAgentBackendConfig  # noqa: E402
from habitat_local_eaios.idle_endpoint import PassiveIdleAgent  # noqa: E402
from habitat_local_eaios.model import (  # noqa: E402
    CanonicalInvocation,
    CanonicalRelocationInvocation,
    IntegrationError,
)
from habitat_local_eaios.relocation_capability import inspect_relocation_capability  # noqa: E402
from habitat_local_eaios.retained_session import RetainedWorldSession  # noqa: E402
from habitat_local_eaios.shared_world import (  # noqa: E402
    InProcessWorldService,
    NodeEndpoint,
    SharedWorldCoordinator,
)
from habitat_local_eaios.store import ExecutionStore  # noqa: E402
from habitat_local_eaios.task_verifier import (  # noqa: E402
    build_task_verifier_source,
    build_task_verifier_verdict,
)
from test_relocation_contract import _FakeModel  # noqa: E402
from test_shared_world import (  # noqa: E402
    ContractLoopHarness,
    FakeTensor,
    PolicyActor,
    RecordingDiagnostics,
    _session_request,
    _slot,
    _wait_terminal,
)
from test_task_verifier import _semantic  # noqa: E402


def _request(index: int, *, object_ref: str | None = None) -> dict[str, Any]:
    """Build exact distinct object/source/destination semantics without benchmark hardcoding."""
    return {
        "invocation": {
            "mission_id": "mission-relocation",
            "group_id": "group-relocation",
            "task_id": f"task-{index}",
            "role_id": f"role-{index}",
            "attempt_id": f"attempt-{index}",
            "operation": "object.relocate@v1",
            "objective": "Relocate the specified object to its destination.",
            "parameters": {
                "object": object_ref or f"object:{index}",
                "source": f"source:{index}",
                "destination": f"destination:{index}",
            },
            "resource_ids": [f"slot-{index}"],
        }
    }


class ObservableSkill:
    """Mirror original completion, budget and method-call evidence without Habitat."""

    def __init__(self, required: int = 1, *, maximum: int = 100) -> None:
        """Keep scripted success and budget independently inspectable."""
        self.required = required
        self._max_skill_steps = maximum
        self._cur_skill_step = [0]
        self.termination_calls = 0
        self.predicate_calls = 0

    def _is_skill_done(self, **kwargs: Any) -> list[bool]:
        """Compute one original local predicate; it does not claim official goal truth."""
        del kwargs
        self.predicate_calls += 1
        return [self._cur_skill_step[0] >= self.required]

    def should_terminate(self, **kwargs: Any) -> tuple[list[bool], list[bool], list[bool]]:
        """Call the original predicate once and retain a separate budget exit."""
        self.termination_calls += 1
        done = self._is_skill_done(**kwargs)[0]
        over = self._max_skill_steps > 0 and self._cur_skill_step[0] >= self._max_skill_steps
        return ([done or over], [False], [False])


class WaitSkillPolicy(ObservableSkill):
    """Identify the existing vendor wait skill for scoped local idle transitions."""


class ScriptedModel(_FakeModel):
    """Use real guard/receipt envelopes but no Provider calls."""

    def __init__(self, choices: list[tuple[str, dict[str, str]]]) -> None:
        """Freeze the raw model choices; extra model calls fail the regression."""
        super().__init__(choices[0])
        self.choices = choices
        self.model = "scripted-offline-model"
        self.calls = 0

    def chat(self, content: str, crab_planning: bool = False) -> Any:
        """Return each unedited raw tool selection and its original receipt once."""
        assert not crab_planning
        assert self.calls < len(self.choices), "completed endpoint called its model again"
        self.response = self.choices[self.calls]
        self.calls += 1
        result = super().chat(content, crab_planning)
        row = self.chat_history[-1]
        row[1]["tool_calls"][0]["id"] = f"call-{self.calls}"
        row[2]["tool_call_id"] = f"call-{self.calls}"
        return result


class ScriptedAgent:
    """Retain each model-selected call reaching the original dispatch boundary."""

    def __init__(self, index: int, choices: list[tuple[str, dict[str, str]]]) -> None:
        """Create an assigned original-agent stand-in with the vendor tool surface."""
        self.name = f"agent_{index}"
        self.llm_model = ScriptedModel(choices)
        self.actions = [SimpleNamespace(name=action["name"]) for action in self.llm_model.actions]
        self.dispatched: list[Any] = []

    def chat(self, content: str) -> Any:
        """Dispatch only after the actual contract guard admits the raw selection."""
        result = self.llm_model.chat(content)
        self.dispatched.append(result)
        return result


def _choices(index: int) -> list[tuple[str, dict[str, str]]]:
    """Describe local relocation steps preserving both canonical entity identities."""
    return [
        ("nav_to_obj", {"target_obj": f"object:{index}"}),
        ("pick", {"target_obj": f"object:{index}"}),
        ("nav_to_obj", {"target_obj": f"destination:{index}"}),
        ("place", {"target_obj": f"object:{index}", "target_location": f"destination:{index}"}),
    ]


class RelocationActor(PolicyActor):
    """Select the next skill in the same act call that observes original termination."""

    def __init__(self, *, delayed: bool = True, wrong_target: bool = False) -> None:
        """Build two original-shaped integer-indexed policy and skill maps."""
        super().__init__()
        choices = [_choices(0), _choices(1)]
        if wrong_target:
            choices[1][0] = ("nav_to_obj", {"target_obj": "object:0"})
        self.originals = [ScriptedAgent(index, value) for index, value in enumerate(choices)]
        self.names = ["nav_to_obj", "pick", "place", "wait"]
        self._active_policies = [
            SimpleNamespace(
                _idx_to_name=dict(enumerate(self.names)),
                _name_to_idx={name: index for index, name in enumerate(self.names)},
                _skills={
                    index: (
                        WaitSkillPolicy()
                        if name == "wait"
                        else ObservableSkill(
                            2 if delayed and agent_index == 1 and name == "pick" else 1
                        )
                    )
                    for index, name in enumerate(self.names)
                },
                _high_level_policy=SimpleNamespace(
                    llm_agent=agent,
                    _skill_name_to_idx={name: index for index, name in enumerate(self.names)},
                ),
            )
            for agent_index, agent in enumerate(self.originals)
        ]
        self.current: list[str | None] = [None, None]
        self.selected: list[list[str]] = []

    def act(self, *args: object, **kwargs: object) -> object:
        """Run one original-shaped termination/selection/skill step per endpoint."""
        del args, kwargs
        self.calls += 1
        for index, policy in enumerate(self._active_policies):
            current = self.current[index]
            if current is not None:
                skill = policy._skills[self.names.index(current)]
                returned, _, _ = skill.should_terminate(
                    batch_idx=[0], skill_name=[current], hl_wants_skill_term=[False]
                )
                if returned[0]:
                    self.current[index] = None
            if self.current[index] is None:
                agent = policy._high_level_policy.llm_agent
                if isinstance(agent, PassiveIdleAgent) and not agent.initialized:
                    agent.init_agent("test robot", "mission", "Nothing to do")
                selected = agent.chat("actual policy observation")
                name = selected["name"] if isinstance(selected, dict) else selected[0]
                self.current[index] = name
                policy._skills[self.names.index(name)]._cur_skill_step[0] = 0
            current = self.current[index]
            assert current is not None
            policy._skills[self.names.index(current)]._cur_skill_step[0] += 1
        self.selected.append([str(value) for value in self.current])
        return SimpleNamespace(
            actions=FakeTensor(),
            env_actions=FakeTensor(),
            rnn_hidden_states=FakeTensor(),
            should_inserts=None,
        )


class RelocationGym:
    """Advance exactly the requested world steps, separately observing the official metric."""

    def __init__(self, *, fail_at: int | None = None, done_at: int | None = None) -> None:
        """Create one unreset world with an unchanged false official outcome."""
        self.steps = 0
        self.resets = 0
        self.fail_at = fail_at
        self.done_at = done_at

    def reset(self) -> dict[str, Any]:
        """Count the only reset and provide actual initial observations."""
        self.resets += 1
        return {"step": 0}

    def close(self) -> None:
        """Close the fake resource without creating a new episode or physical outcome."""

    def step(self, action: object) -> tuple[dict[str, Any], float, bool, dict[str, bool]]:
        """Emit finished-nav observations without making them relocation or official success."""
        del action
        if self.steps + 1 == self.fail_at:
            raise RuntimeError("original Gym failure")
        self.steps += 1
        return (
            {
                "step": self.steps,
                "agent_0_has_finished_oracle_nav": [1],
                "agent_1_has_finished_oracle_nav": [1],
            },
            0.0,
            self.steps == self.done_at,
            {"pddl_success": False},
        )


class RelocationRuntime(ContractLoopHarness):
    """Run production pair/serial loops and guard with bounded deterministic dependencies."""

    def __init__(self, tmp_path: Path, actor: RelocationActor, gym: RelocationGym) -> None:
        """Inject local simulator surfaces without replacing production operation lifecycle."""
        super().__init__(tmp_path, RecordingDiagnostics())
        self._config = CrabAgentBackendConfig(
            config_path=tmp_path / "unused.yaml",
            episode_id="offline",
            agent_id=0,
            max_steps=20,
            step_period_ms=0,
            evidence_dir=tmp_path / "evidence",
            enable_relocation=True,
        )
        self._actor = actor
        self._gym_env = gym
        self._habitat_env = SimpleNamespace(
            episodes=[],
            episode_over=False,
            get_metrics=lambda: {"pddl_success": False},
            current_episode=SimpleNamespace(episode_id="offline", scene_id="scene"),
            task=SimpleNamespace(
                get_task_text_context=lambda: {"scene_description": "scene"}, actions={}
            ),
            sim=SimpleNamespace(
                get_agent_data=lambda agent_id: SimpleNamespace(
                    articulated_agent=SimpleNamespace(base_pos=(float(agent_id), 0.0, 0.0))
                )
            ),
        )
        self._agent_access = SimpleNamespace(masks_shape=(1,))
        self._episode = object()
        self._stage2_feedback = None
        self._relocation_capability = inspect_relocation_capability(
            actor._active_policies
        ).as_dict()
        self._prepare_reset()

    def _current_skills(self, actor: Any) -> list[str]:
        """Read the existing selected skill identity without issuing another action."""
        return [str(name) for name in actor.current]

    def _pair_arguments(
        self, text_context: dict[str, Any], invocations: Mapping[int, CanonicalInvocation]
    ) -> dict[str, Any]:
        """Preserve both committed subtasks with vendor-shaped argument objects."""
        del text_context
        return {
            f"agent_{index}": SimpleNamespace(subtask_description=self._subtask(value))
            for index, value in invocations.items()
        }

    def _assigned_arguments(
        self, text_context: dict[str, Any], invocation: CanonicalInvocation
    ) -> dict[str, Any]:
        """Give only the assigned endpoint a subtask in each serial segment."""
        del text_context
        return {
            f"agent_{index}": SimpleNamespace(
                subtask_description=(
                    self._subtask(invocation) if index == self._config.agent_id else "Nothing to do"
                )
            )
            for index in self._agent_ids
        }


@pytest.fixture(autouse=True)
def original_wait_class(monkeypatch: pytest.MonkeyPatch) -> None:
    """Validate the same wait class used by every fake original skill map."""
    monkeypatch.setattr(idle_endpoint, "_original_wait_skill_type", lambda: WaitSkillPolicy)


def _pair_invocations() -> dict[int, CanonicalRelocationInvocation]:
    """Return two independent object relocation invocations."""
    return {
        index: CanonicalRelocationInvocation.from_request(_request(index)) for index in range(2)
    }


def test_pair_relocation_waits_for_place_and_preserves_official_failure(tmp_path: Path) -> None:
    """Navigation completion cannot end relocation or synthesize Habitat success."""
    actor, gym = RelocationActor(), RelocationGym()
    runtime = RelocationRuntime(tmp_path, actor, gym)
    outcomes, summary = runtime.execute_pair(_pair_invocations(), lambda: False, lambda *_: None)
    assert [value.state for value in outcomes.values()] == ["COMPLETED", "COMPLETED"]
    assert [value.terminal_basis for value in outcomes.values()] == ["relocation-place-skill"] * 2
    assert [value.local_llm_calls for value in outcomes.values()] == [4, 4]
    assert outcomes[0].simulator_steps == 5 < outcomes[1].simulator_steps == 6
    assert summary["final_info"]["pddl_success"] is False
    assert gym.resets == 1 and gym.steps == summary["identity"]["simulator_steps"] == 20
    assert [agent.llm_model.calls for agent in actor.originals] == [4, 4]
    assert runtime._stage2_accounting_agents == {}
    assert actor.selected[4] == ["wait", "place"]
    assert [
        policy._high_level_policy.llm_agent for policy in actor._active_policies
    ] == actor.originals
    assert all(
        skill.termination_calls == skill.predicate_calls
        for policy in actor._active_policies
        for skill in policy._skills.values()
    )
    records = [
        json.loads(line)
        for line in (runtime._evidence_dir() / "stage2-execution-feedback.jsonl")
        .read_text()
        .splitlines()
    ]
    assert sum(row.get("event") == "completed-endpoint-passive" for row in records) == 2
    spatial = json.loads((runtime._evidence_dir() / "spatial-feasibility.json").read_text())
    assert all(row["status"] == "unknown" for row in spatial["agent_records"])


def test_wrong_relocation_target_fails_before_first_world_step(tmp_path: Path) -> None:
    """The guard rejects an unedited wrong object while preserving one reset."""
    actor, gym = RelocationActor(wrong_target=True), RelocationGym()
    runtime = RelocationRuntime(tmp_path, actor, gym)
    outcomes, _ = runtime.execute_pair(_pair_invocations(), lambda: False, lambda *_: None)
    assert outcomes[1].terminal_basis == "local-contract-failure"
    assert actor.originals[1].dispatched == []
    assert gym.steps == 0 and gym.resets == 1


def test_place_budget_exit_is_failure_not_relocation_completion(tmp_path: Path) -> None:
    """A failed place exit never marks the canonical operation completed."""
    actor, gym = RelocationActor(delayed=False), RelocationGym(done_at=5)
    skill = actor._active_policies[1]._skills[2]
    skill.required, skill._max_skill_steps = 100, 1
    actor.originals[1].llm_model.choices.append(("wait", {}))
    runtime = RelocationRuntime(tmp_path, actor, gym)
    outcomes, summary = runtime.execute_pair(_pair_invocations(), lambda: False, lambda *_: None)
    assert outcomes[0].state == "COMPLETED"
    assert outcomes[1].state == "FAILED" and outcomes[1].local_skill_completed is False
    assert outcomes[1].terminal_basis == "episode-terminated-before-success"
    assert summary["final_info"]["pddl_success"] is False


def test_exception_retains_trace_and_restores_original_agents(tmp_path: Path) -> None:
    """A post-place sibling Gym exception keeps original failure and optional terminal evidence."""
    actor, gym = RelocationActor(), RelocationGym(fail_at=6)
    runtime = RelocationRuntime(tmp_path, actor, gym)
    with pytest.raises(IntegrationError, match="original Gym failure"):
        runtime.execute_pair(_pair_invocations(), lambda: False, lambda *_: None)
    assert gym.steps == 5 and actor.originals[0].llm_model.calls == 4
    assert [
        policy._high_level_policy.llm_agent for policy in actor._active_policies
    ] == actor.originals
    diagnostics = cast(RecordingDiagnostics, runtime._diagnostics)
    assert diagnostics.terminals == [(5, "execution_exception:gym_env_step:RuntimeError")]


def test_concurrent_same_object_is_rejected_before_stage2(tmp_path: Path) -> None:
    """Distinct logical actors cannot concurrently manipulate the same exact object."""
    actor, gym = RelocationActor(), RelocationGym()
    runtime = RelocationRuntime(tmp_path, actor, gym)
    invocations = _pair_invocations()
    invocations[1] = CanonicalRelocationInvocation.from_request(_request(1, object_ref="object:0"))
    with pytest.raises(IntegrationError, match="share one canonical object"):
        runtime.execute_pair(invocations, lambda: False, lambda *_: None)
    assert gym.steps == actor.calls == 0


def test_shared_relocation_opt_in_requires_actual_capability(tmp_path: Path) -> None:
    """The CLI flag is not readiness proof and navigation-only extensions fail closed."""
    runtime = RelocationRuntime(tmp_path, RelocationActor(), RelocationGym())
    assert "object.relocate@v1" in runtime.supported_operations()
    runtime._relocation_capability = None
    with pytest.raises(IntegrationError, match="readiness is unavailable"):
        runtime.supported_operations()
    runtime._config = replace(runtime._config, retain_stopped_session=True)
    with pytest.raises(IntegrationError, match="retained cancellation continuation"):
        runtime.supported_operations()


def test_relocation_continuation_cannot_guess_held_object_phase() -> None:
    """Navigation continuation cannot silently reconstruct a manipulation execution."""
    with pytest.raises(IntegrationError, match="navigation invocations only"):
        RetainedWorldSession(_pair_invocations(), 20)


def test_official_verifier_uses_metric_not_local_relocation_completion() -> None:
    """Widening invocation transport does not change the official satisfaction authority."""
    source = build_task_verifier_source(_semantic())
    invocations = list(_pair_invocations().values())
    verdict = build_task_verifier_verdict(source, {"pddl_success": False}, invocations)
    assert verdict is not None and verdict["satisfied"] is False
    assert len(verdict["tasks"]) == 2
    assert build_task_verifier_verdict(source, {}, invocations) is None


def _session_bound_request(index: int, slots: list[dict[str, object]]) -> dict[str, Any]:
    """Use the existing accepted-plan session format for a canonical relocation slot."""
    request = _request(index)
    invocation = request["invocation"]
    template = _session_request(
        invocation["mission_id"],
        invocation["parameters"]["destination"],
        invocation["task_id"],
        slots,
    )
    invocation["group_id"] = "group"
    invocation["execution_session"] = cast(dict[str, object], template["invocation"])[
        "execution_session"
    ]
    return request


def _endpoints(
    tmp_path: Path, runtime: RelocationRuntime
) -> tuple[SharedWorldCoordinator, NodeEndpoint, NodeEndpoint]:
    """Exercise actual durable endpoint/coordinator dispatch against the same fake world."""
    coordinator = SharedWorldCoordinator(
        InProcessWorldService(runtime), 5.0, runtime._evidence_dir(), wait_poll_s=0.01
    )
    return (
        coordinator,
        NodeEndpoint("node-a", 0, ExecutionStore(tmp_path / "a.sqlite3"), coordinator),
        NodeEndpoint("node-b", 1, ExecutionStore(tmp_path / "b.sqlite3"), coordinator),
    )


def test_endpoint_pair_dispatch_preserves_relocation_identity(tmp_path: Path) -> None:
    """Runtime readiness and raw canonical object identities survive actual durable dispatch."""
    actor, gym = RelocationActor(), RelocationGym()
    runtime = RelocationRuntime(tmp_path, actor, gym)
    coordinator, first, second = _endpoints(tmp_path, runtime)
    slots = [_slot(f"task-{index}", f"actor-{index}", role=f"role-{index}") for index in range(2)]
    try:
        for index, endpoint in enumerate((first, second)):
            assert endpoint.readiness("object.relocate@v1")["state"] == "READY"
            response = endpoint.submit(_session_bound_request(index, slots))
            if index == 0:
                first_handle = str(response["execution_id"])
            else:
                second_handle = str(response["execution_id"])
        assert _wait_terminal(first, first_handle)["state"] == "COMPLETED"
        assert _wait_terminal(second, second_handle)["state"] == "COMPLETED"
        admission = json.loads(
            (runtime._evidence_dir() / "shared-world-start-admission.json").read_text()
        )
        assert admission["state"] == "ADMITTED"
        assert [value["parameters"]["object"] for value in admission["arrived_assignments"]] == [
            "object:0",
            "object:1",
        ]
        arrivals = [
            json.loads(row)
            for row in (runtime._evidence_dir() / "assignment-arrival.jsonl")
            .read_text()
            .splitlines()
        ]
        assert all(row["operation"] == "object.relocate@v1" for row in arrivals)
        assert [row["parameters"]["source"] for row in arrivals] == ["source:0", "source:1"]
        assert gym.resets == 1 and actor.calls == 6
    finally:
        coordinator.shutdown()


def test_coordinator_rejects_same_object_without_any_stage2_call(tmp_path: Path) -> None:
    """A conflicting object pair produces explicit local rejection, not fabricated execution."""
    actor, gym = RelocationActor(), RelocationGym()
    runtime = RelocationRuntime(tmp_path, actor, gym)
    coordinator, first, second = _endpoints(tmp_path, runtime)
    slots = [_slot(f"task-{index}", f"actor-{index}", role=f"role-{index}") for index in range(2)]
    try:
        first_handle = str(first.submit(_session_bound_request(0, slots))["execution_id"])
        second_request = _session_bound_request(1, slots)
        second_request["invocation"]["parameters"]["object"] = "object:0"
        second_handle = str(second.submit(second_request)["execution_id"])
        for endpoint, handle in [(first, first_handle), (second, second_handle)]:
            terminal = _wait_terminal(endpoint, handle)
            assert terminal["state"] == "FAILED"
            assert "share one canonical object" in str(terminal["detail"])
        assert gym.steps == actor.calls == 0
        admission = json.loads(
            (runtime._evidence_dir() / "shared-world-start-admission.json").read_text()
        )
        assert admission["state"] == "REJECTED"
    finally:
        coordinator.shutdown()


def test_serial_relocations_reuse_one_world_and_leave_peer_model_uninvoked(tmp_path: Path) -> None:
    """One Actor can perform two exact operations after local Task release, without resetting."""
    actor, gym = RelocationActor(delayed=False), RelocationGym()
    actor.originals[0].llm_model.choices += _choices(1)
    runtime = RelocationRuntime(tmp_path, actor, gym)
    coordinator, first, second = _endpoints(tmp_path, runtime)
    slots = [
        _slot("task-0", "actor", role="role-0"),
        _slot("task-1", "actor", ["task-0"], role="role-1"),
    ]
    try:
        first_handle = str(first.submit(_session_bound_request(0, slots))["execution_id"])
        first_terminal = _wait_terminal(first, first_handle)
        assert first_terminal["state"] == "COMPLETED"
        assert cast(dict[str, Any], first_terminal["local_outcome"])["local_llm_calls"] == 4
        assert gym.steps == 5 and gym.resets == 1
        assert actor.originals[0].llm_model.calls == 4
        assert second.store().all_executions() == []
        second_handle = str(first.submit(_session_bound_request(1, slots))["execution_id"])
        second_terminal = _wait_terminal(first, second_handle)
        assert second_terminal["state"] == "COMPLETED"
        assert cast(dict[str, Any], second_terminal["local_outcome"])["local_llm_calls"] == 8
        assert runtime._stage2_accounting_agents == {}
        assert gym.steps == 10 and gym.resets == 1
        assert [agent.llm_model.calls for agent in actor.originals] == [8, 0]
        assert [
            policy._high_level_policy.llm_agent for policy in actor._active_policies
        ] == actor.originals
        summary = json.loads((runtime._evidence_dir() / "shared-world-summary.json").read_text())
        assert summary["official_pddl_success"] is False
        assert len(summary["serial_task_outcomes"]) == 2
        assert summary["identity"]["episode_reset_count"] == 1
        assert "stage2-action-audit.json" in {
            path.name for path in runtime._evidence_dir().iterdir()
        }
    finally:
        coordinator.shutdown()


def test_completed_peer_stays_completed_after_sibling_contract_failure(tmp_path: Path) -> None:
    """A genuine place completion observed inside act is retained before sibling dispatch fails."""
    actor, gym = RelocationActor(), RelocationGym()
    actor.originals[1].llm_model.choices[3] = (
        "place",
        {"target_obj": "object:1", "target_location": "destination:0"},
    )
    runtime = RelocationRuntime(tmp_path, actor, gym)
    outcomes, _ = runtime.execute_pair(_pair_invocations(), lambda: False, lambda *_: None)
    assert outcomes[0].state == "COMPLETED"
    assert outcomes[0].terminal_basis == "relocation-place-skill"
    assert outcomes[1].terminal_basis == "local-contract-failure"
    assert gym.steps == 4 and actor.originals[0].llm_model.calls == 4


def test_completed_idle_evidence_failure_does_not_change_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Optional idle journaling failure cannot mask completion or prevent policy restoration."""
    from habitat_local_eaios.stage2_contract import Stage2ActionAudit

    original = Stage2ActionAudit.record

    def fail_idle_record(audit: Stage2ActionAudit, document: dict[str, Any]) -> None:
        """Fail only the new diagnostic row after actual completion."""
        if document.get("event") == "completed-endpoint-passive":
            raise OSError("idle evidence fault")
        original(audit, document)

    monkeypatch.setattr(Stage2ActionAudit, "record", fail_idle_record)
    actor, gym = RelocationActor(), RelocationGym()
    runtime = RelocationRuntime(tmp_path, actor, gym)
    outcomes, _ = runtime.execute_pair(_pair_invocations(), lambda: False, lambda *_: None)
    assert all(value.state == "COMPLETED" for value in outcomes.values())
    assert [
        policy._high_level_policy.llm_agent for policy in actor._active_policies
    ] == actor.originals
    audit = json.loads(
        (runtime._evidence_dir() / "stage2-execution-feedback-audit.json").read_text()
    )
    assert audit["complete"] is False and audit["records_unavailable"] == 2


def test_relocation_cancellation_does_not_grant_manipulation_continuation(tmp_path: Path) -> None:
    """Actual cancellation before place retains a cancelled outcome and the consumed world."""
    actor, gym = RelocationActor(), RelocationGym()
    runtime = RelocationRuntime(tmp_path, actor, gym)
    outcomes, summary = runtime.execute_pair(
        _pair_invocations(), lambda: gym.steps == 2, lambda *_: None
    )
    assert all(value.state == "CANCELLED" for value in outcomes.values())
    assert summary["identity"]["simulator_steps"] == 2
    assert "continuation" not in summary
    assert gym.resets == 1 and [agent.llm_model.calls for agent in actor.originals] == [2, 2]


@pytest.mark.parametrize("command", ["pair", "serial"])
def test_real_child_ipc_preserves_operation_and_local_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    """Run the production child/parent IPC over a real pipe without importing Habitat."""
    import multiprocessing
    import threading

    from habitat_local_eaios import shared_world

    runtime = RelocationRuntime(tmp_path, RelocationActor(), RelocationGym())
    monkeypatch.setattr(shared_world, "SharedEmosStage2Runtime", lambda *_: runtime)
    parent, child = multiprocessing.Pipe()
    worker = threading.Thread(
        target=shared_world._child_world_process,
        args=(child, runtime._config, (0, 1)),
        daemon=True,
    )
    worker.start()
    service = shared_world.ProcessWorldService(runtime._config, (0, 1))
    service._connection = parent
    service._process = SimpleNamespace(is_alive=worker.is_alive, join=worker.join)
    try:
        assert parent.poll(2.0), "child failed to publish readiness"
        kind, payload = parent.recv()
        assert kind == "READY" and "object.relocate@v1" in payload["operations"]
        service._ready_detail, service._supported_operations = shared_world._parse_ready_payload(
            payload
        )
        service._ready = True
        service._start_requested = True
        running_agents: list[int] = []
        if command == "pair":
            outcomes, summary = service.run_pair(
                _pair_invocations(),
                lambda: False,
                lambda agent_id, _: running_agents.append(agent_id),
            )
            assert set(outcomes) == {0, 1}
            assert all(
                value.terminal_basis == "relocation-place-skill" for value in outcomes.values()
            )
            assert running_agents == [0, 1]
        else:
            outcome, summary = service.run_serial(
                _pair_invocations()[0],
                0,
                lambda: False,
                lambda agent_id, _: running_agents.append(agent_id),
                True,
            )
            assert (
                outcome.state == "COMPLETED" and outcome.terminal_basis == "relocation-place-skill"
            )
            assert running_agents == [0]
            assert runtime._actor is not None
            assert runtime._actor.originals[1].llm_model.calls == 0
        assert summary["official_metrics"]["pddl_success"] is False
        assert summary["identity"]["episode_reset_count"] == 1
    finally:
        service.shutdown()
        worker.join(timeout=2.0)
        assert not worker.is_alive()


@pytest.mark.parametrize("entry", ["single", "serial", "pair"])
def test_direct_runtime_entry_cannot_bypass_default_off_profile(tmp_path: Path, entry: str) -> None:
    """Internal IPC/direct callers cannot execute relocation by bypassing endpoint readiness."""
    actor, gym = RelocationActor(), RelocationGym()
    runtime = RelocationRuntime(tmp_path, actor, gym)
    runtime._config = replace(runtime._config, enable_relocation=False)
    with pytest.raises(IntegrationError, match="not enabled"):
        if entry == "single":
            runtime.execute(_pair_invocations()[0], lambda: False, lambda _: None)
        elif entry == "serial":
            runtime.execute_serial(_pair_invocations()[0], 0, lambda: False, lambda *_: None, True)
        else:
            runtime.execute_pair(_pair_invocations(), lambda: False, lambda *_: None)
    assert gym.steps == actor.calls == 0 and gym.resets == 1


def test_relocation_goal_region_extension_is_rejected_before_execution(tmp_path: Path) -> None:
    """The navigation any_at resolver cannot silently reinterpret an at relocation goal."""
    actor, gym = RelocationActor(), RelocationGym()
    runtime = RelocationRuntime(tmp_path, actor, gym)
    runtime._config = replace(runtime._config, goal_region_navigation=True)
    with pytest.raises(IntegrationError, match="any_at goal-region"):
        runtime.execute_pair(_pair_invocations(), lambda: False, lambda *_: None)
    assert gym.steps == actor.calls == 0
