"""Deterministic object-relocation invocation and Stage2 guard tests."""

from __future__ import annotations

import hashlib
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

from habitat_local_eaios.model import (  # noqa: E402
    CanonicalRelocationInvocation,
    IntegrationError,
)
from habitat_local_eaios.stage2_contract import (  # noqa: E402
    RelocationExecutionState,
    Stage2ContractViolation,
    Stage2ExecutionContract,
    install_stage2_contract_guard,
)
from habitat_local_eaios.stage2_feedback import Stage2ExecutionFeedback  # noqa: E402


def _request(**parameters: Any) -> dict[str, Any]:
    """Build a generic relocation request without benchmark-specific identities."""
    values = {
        "object": "object:sample",
        "source": "receptacle:source",
        "destination": "receptacle:destination",
    }
    values.update(parameters)
    return {
        "invocation": {
            "mission_id": "mission-relocation",
            "task_id": "task-relocation",
            "group_id": "group-relocation",
            "role_id": "role-relocation",
            "operation": "object.relocate@v1",
            "objective": "Move the selected object to the selected destination.",
            "parameters": values,
            "resource_ids": ["slot-relocation"],
        }
    }


def _invocation() -> CanonicalRelocationInvocation:
    """Return one exact relocation invocation for all contract tests."""
    return CanonicalRelocationInvocation.from_request(_request())


def _contract() -> Stage2ExecutionContract:
    """Bind the generic relocation request to a local execution profile."""
    return Stage2ExecutionContract.for_invocation(_invocation())


def _action(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the raw tool envelope before CrabAgent transforms it."""
    return {"name": name, "arguments": arguments or {}}


def test_relocation_invocation_round_trips_and_binds_digest() -> None:
    """The parser preserves exact object/source/destination semantics and identity."""
    invocation = _invocation()
    restored = CanonicalRelocationInvocation.from_request({"invocation": invocation.as_dict()})
    assert restored == invocation
    assert restored.object_ref == "object:sample"
    assert restored.source == "receptacle:source"
    assert restored.destination == "receptacle:destination"
    assert restored.request_key()
    contract = Stage2ExecutionContract.for_invocation(invocation)
    assert contract.as_dict()["profile"] == "emos-relocation/v0.1"
    assert contract.as_dict()["expected_source"] == "receptacle:source"


@pytest.mark.parametrize(
    "mutation",
    [
        {"parameters": {"object": "object:sample", "destination": "receptacle:destination"}},
        {
            "parameters": {
                "object": "object:sample",
                "source": "receptacle:source",
                "destination": "receptacle:destination",
                "extra": "local-how",
            }
        },
        {
            "parameters": {
                "object": None,
                "source": "receptacle:source",
                "destination": "receptacle:destination",
            }
        },
        {"resource_ids": ["slot-relocation", "slot-relocation"]},
        {"operation": "mobility.move@v1"},
    ],
)
def test_relocation_invocation_rejects_inexact_transport(mutation: dict[str, Any]) -> None:
    """Missing, extra, null, duplicate, and wrong operation fields fail closed."""
    request = deepcopy(_request())
    request["invocation"].update(mutation)
    with pytest.raises(IntegrationError):
        CanonicalRelocationInvocation.from_request(request)


def test_relocation_contract_accepts_only_ordered_semantic_workflow() -> None:
    """A valid relocation sequence advances state only after each admitted action."""
    contract = _contract()
    state = RelocationExecutionState()
    peers = frozenset({"agent-0"})
    sequence = [
        _action("reset_arm"),
        _action("nav_to_obj", {"target_obj": "object:sample"}),
        _action("pick", {"target_obj": "object:sample"}),
        _action("nav_to_obj", {"target_obj": "receptacle:destination"}),
        _action(
            "place",
            {"target_obj": "object:sample", "target_location": "receptacle:destination"},
        ),
        _action("reset_arm"),
    ]
    phases = ["before_pick", "before_pick", "holding", "holding", "placed", "placed"]
    for action, phase in zip(sequence, phases, strict=True):
        contract.validate("agent-0", action, peers, state)
        contract.advance(action, state)
        state.complete(action["name"], True)
        assert state.phase == phase


def test_relocation_contract_rejects_wrong_or_out_of_order_actions() -> None:
    """Wrong identities and invalid phases fail without changing the execution state."""
    contract = _contract()
    peers = frozenset({"agent-0"})
    cases = [
        (_action("pick", {"target_obj": "object:other"}), "canonical object"),
        (
            _action(
                "place",
                {
                    "target_obj": "object:sample",
                    "target_location": "receptacle:destination",
                },
            ),
            "held",
        ),
        (_action("nav_to_obj", {"target_obj": "receptacle:destination"}), "phase"),
    ]
    for action, message in cases:
        state = RelocationExecutionState()
        with pytest.raises(Stage2ContractViolation, match=message):
            contract.validate("agent-0", action, peers, state)
        assert state.phase == "before_pick"
    state = RelocationExecutionState("holding")
    with pytest.raises(Stage2ContractViolation, match="reset_arm"):
        contract.validate("agent-0", _action("reset_arm"), peers, state)
    assert state.phase == "holding"


class _FakeModel:
    """Return scripted raw tool choices and preserve offered schemas."""

    def __init__(self, response: Any) -> None:
        """Create an offline model with original EMOS-like relocation tools."""
        self.response = response
        self.planning_stage = False
        self.code_execution = False
        self.chat_history: list[list[dict[str, Any]]] = []
        self.offered_actions: list[dict[str, Any]] | None = None
        self.actions = [
            {
                "name": "nav_to_obj",
                "parameters": {
                    "type": "object",
                    "properties": {"target_obj": {"type": "string"}},
                    "required": ["target_obj"],
                },
            },
            {
                "name": "pick",
                "parameters": {
                    "type": "object",
                    "properties": {"target_obj": {"type": "string"}},
                    "required": ["target_obj"],
                },
            },
            {
                "name": "place",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target_obj": {"type": "string"},
                        "target_location": {"type": "string"},
                    },
                    "required": ["target_obj", "target_location"],
                },
            },
            {"name": "reset_arm", "parameters": {"type": "object", "properties": {}}},
            {"name": "wait", "parameters": {"type": "object", "properties": {}}},
        ]

    def chat(self, content: str, crab_planning: bool = False) -> Any:
        """Emit one raw tool call and record the schema seen by the provider."""
        del content
        self.offered_actions = deepcopy(self.actions)
        if crab_planning:
            return "planning"
        name, arguments = self.response
        self.chat_history.append(
            [
                {"role": "user", "content": "observation"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ],
                },
                {"role": "tool", "content": "selected", "tool_call_id": "call-1", "name": name},
            ]
        )
        return name, arguments


class _FakeSkill:
    """Expose one original skill termination decision for feedback tests."""

    def __init__(self) -> None:
        """Create a completed skill with a nonzero physical-step witness."""
        self._cur_skill_step = [1]
        self._max_skill_steps = 10
        self.base_done = True

    def _is_skill_done(self, **kwargs: Any) -> list[bool]:
        """Return the scripted original skill completion result."""
        del kwargs
        return [self.base_done]

    def should_terminate(self, **kwargs: Any) -> tuple[list[bool], list[bool], list[bool]]:
        """Mirror the vendor termination tuple while invoking its base predicate once."""
        self._is_skill_done(**kwargs)
        return ([True], [False], [False])


class _FakeAgent:
    """Capture whether a raw selected action reaches the vendor dispatcher."""

    def __init__(self, response: Any) -> None:
        """Create one agent with no physical side effects."""
        self.name = "agent-0"
        self.llm_model = _FakeModel(response)
        self.dispatched: list[Any] = []

    def chat(self, observation: str) -> Any:
        """Mirror the dispatch boundary used by CrabAgent."""
        result = self.llm_model.chat(observation)
        self.dispatched.append(result)
        return result


def test_relocation_guard_binds_tools_and_rejects_wrong_action_before_dispatch() -> None:
    """The guard exposes exact enums and blocks a wrong model-selected destination."""
    agent = _FakeAgent(("nav_to_obj", {"target_obj": "receptacle:wrong"}))
    restore = install_stage2_contract_guard([agent], {"agent-0": _contract()}, lambda row: None)
    try:
        with pytest.raises(Stage2ContractViolation, match="relocation phase"):
            agent.chat("observation")
    finally:
        restore()
    assert agent.dispatched == []
    assert agent.llm_model.offered_actions is not None
    offered = {item["name"]: item for item in agent.llm_model.offered_actions}
    assert offered["nav_to_obj"]["parameters"]["properties"]["target_obj"]["enum"] == [
        "object:sample"
    ]
    assert offered["place"]["parameters"]["properties"]["target_location"]["enum"] == [
        "receptacle:destination"
    ]


def test_relocation_schema_failure_does_not_reach_provider_or_mutate_tools() -> None:
    """An incomplete relocation schema fails before Provider invocation and restores actions."""
    agent = _FakeAgent(("pick", {"target_obj": "object:sample"}))
    original = deepcopy(agent.llm_model.actions)
    agent.llm_model.actions = [
        action for action in agent.llm_model.actions if action["name"] != "place"
    ]
    missing = deepcopy(agent.llm_model.actions)
    restore = install_stage2_contract_guard([agent], {"agent-0": _contract()}, lambda row: None)
    try:
        with pytest.raises(IntegrationError, match="tool declarations"):
            agent.chat("observation")
    finally:
        restore()
    assert agent.llm_model.chat_history == []
    assert agent.dispatched == []
    assert agent.llm_model.actions == missing
    assert original != missing


def test_relocation_phase_advances_only_from_observed_skill_completion() -> None:
    """A selected pick cannot authorize place until the original skill reports success."""
    agent = _FakeAgent(("pick", {"target_obj": "object:sample"}))
    contract = _contract()
    state = RelocationExecutionState()
    skill = _FakeSkill()
    feedback = Stage2ExecutionFeedback(
        {"agent-0": contract},
        lambda row: None,
        completion=lambda name, action, succeeded: state.complete(action, succeeded),
    )
    feedback.install(
        [SimpleNamespace(_high_level_policy=SimpleNamespace(llm_agent=agent), _skills={0: skill})]
    )
    restore = install_stage2_contract_guard(
        [agent],
        {"agent-0": contract},
        lambda row: None,
        feedback=feedback,
        relocation_states={"agent-0": state},
    )
    try:
        agent.chat("pick")
        assert state.phase == "before_pick"
        with pytest.raises(Stage2ContractViolation, match="no observed completion"):
            contract.validate(
                "agent-0",
                _action("nav_to_obj", {"target_obj": "object:sample"}),
                frozenset({"agent-0"}),
                state,
            )
        feedback.physical_step()
        skill.should_terminate(skill_name=["pick"], batch_idx=[0], hl_wants_skill_term=[False])
        assert state.phase == "holding"
        contract.validate(
            "agent-0",
            _action("nav_to_obj", {"target_obj": "receptacle:destination"}),
            frozenset({"agent-0"}),
            state,
        )
    finally:
        restore()
        feedback.close()


def _session_request() -> dict[str, Any]:
    """Freeze exact accepted-plan topology beside the existing relocation request."""
    request = _request()
    value = request["invocation"]
    session = {
        "schema_version": "roboguide.execution-session/v0.1",
        "mission_id": value["mission_id"],
        "group_id": value["group_id"],
        "slots": [
            {
                "task_id": value["task_id"],
                "role_id": value["role_id"],
                "actor_id": "actor",
                "dependencies": [],
                "independent": True,
            }
        ],
    }
    encoded = json.dumps(
        session, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    session["digest"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
    value["execution_session"] = session
    return request


def test_relocation_with_execution_session_parses_and_round_trips() -> None:
    """The common parser's validated session is retained without treating it as raw JSON again."""
    request = _session_request()
    invocation = CanonicalRelocationInvocation.from_request(request)
    assert invocation.execution_session is not None
    assert invocation.execution_session.topology() == "single_actor_sequential"
    assert invocation.as_dict() == request["invocation"]
    assert (
        CanonicalRelocationInvocation.from_request({"invocation": invocation.as_dict()})
        == invocation
    )


@pytest.mark.parametrize("mutation", ["digest", "mission_id", "group_id", "task_id", "role_id"])
def test_relocation_session_validation_remains_fail_closed(mutation: str) -> None:
    """Reusing validated metadata never bypasses exact topology identity or digest checks."""
    request = _session_request()
    if mutation == "digest":
        request["invocation"]["execution_session"]["digest"] = "sha256:" + "0" * 64
    else:
        request["invocation"][mutation] = "different-identity"
    with pytest.raises(IntegrationError):
        CanonicalRelocationInvocation.from_request(request)
