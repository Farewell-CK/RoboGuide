"""Deterministic selected-tool enforcement, evidence, and vendor hook regressions."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.model import CanonicalMobilityInvocation, IntegrationError  # noqa: E402
from habitat_local_eaios.stage2_contract import (  # noqa: E402
    Stage2ActionAudit,
    Stage2ContractViolation,
    Stage2ExecutionContract,
    install_stage2_contract_guard,
)


def _invocation(destination: str = "location:north") -> CanonicalMobilityInvocation:
    """Build a canonical operation with no Episode51-specific identity assumptions."""
    return CanonicalMobilityInvocation(
        mission_id="mission-contract",
        task_id="task-contract",
        group_id="group-contract",
        role_id="role-contract",
        operation="mobility.move@v1",
        objective=f"Reach {destination}.",
        parameters={"destination": destination},
        resource_ids=("slot-contract",),
    )


class FakeModel:
    """Return scripted raw model outputs while retaining exact call counts."""

    def __init__(self, response: Any) -> None:
        """Configure one response without any network or physical interaction."""
        self.response = response
        self.calls = 0
        self.planning_stage = False
        self.code_execution = False

    def chat(self, content: str, crab_planning: bool = False) -> Any:
        """Emulate text planning, Provider failure, or the raw selected-tool tuple."""
        del content
        self.calls += 1
        if isinstance(self.response, Exception):
            raise self.response
        return "planning text" if crab_planning else self.response


class FakeAgent:
    """Mirror CrabAgent's raw-call, message, wait, and robot-injection boundaries."""

    def __init__(self, name: str, response: Any) -> None:
        """Create a model and a per-instance side-effect ledger."""
        self.name = name
        self.llm_model = FakeModel(response)
        self.dispatched: list[Any] = []

    def chat(self, observation: str) -> Any:
        """Perform post-model side effects that a rejected action must never reach."""
        name, arguments = self.llm_model.chat(observation)
        self.dispatched.append((name, arguments))
        if name in {"wait", "send_request"}:
            return {"name": "wait", "arguments": ["500"]}
        arguments["robot"] = self.name
        return {"name": name, "arguments": arguments}


def _contracts() -> dict[str, Stage2ExecutionContract]:
    """Use two arbitrary agent IDs and two independent destination contracts."""
    return {
        "alpha": Stage2ExecutionContract.for_invocation(_invocation()),
        "beta": Stage2ExecutionContract.for_invocation(_invocation("location:south")),
    }


@pytest.mark.parametrize("operation", ["mobility.move@v1", "mobility.navigate@v1"])
def test_contract_freezes_exact_invocation_identity(operation: str) -> None:
    """Both supported operations bind the original destination and invocation digest."""
    invocation = _invocation()
    request = invocation.as_dict()
    request["operation"] = operation
    invocation = CanonicalMobilityInvocation.from_request({"invocation": request})
    contract = Stage2ExecutionContract.for_invocation(invocation)
    assert contract.invocation_digest == invocation.request_key()
    invocation.parameters["destination"] = "later mutation"
    contract.validate(
        "alpha",
        {"name": "nav_to_obj", "arguments": {"target_obj": "location:north"}},
        frozenset({"alpha"}),
    )
    assert contract.expected_destination == "location:north"


def test_unknown_operation_requires_an_explicit_local_profile() -> None:
    """Adding a capability elsewhere cannot implicitly authorize a new operation here."""
    invocation = replace(_invocation(), operation="manipulation.pick@v1")
    with pytest.raises(IntegrationError, match="no Stage2 execution profile"):
        Stage2ExecutionContract.for_invocation(invocation)


def test_guard_installed_before_lazy_model_creation_uses_the_new_model() -> None:
    """The production initialization order cannot bypass the per-instance hook."""
    agent = FakeAgent("alpha", ("wait", {}))
    del agent.llm_model
    restore = install_stage2_contract_guard(
        [agent], {"alpha": _contracts()["alpha"]}, lambda row: None
    )
    try:
        agent.llm_model = FakeModel(("nav_to_obj", {"target_obj": "wrong"}))
        with pytest.raises(Stage2ContractViolation):
            agent.chat("first action after initialization")
        assert agent.dispatched == []
    finally:
        restore()


@pytest.mark.parametrize(
    "response",
    [
        ("nav_to_obj", {"target_obj": "location:south"}),
        ("nav_to_obj", {"target_obj": "location:north", "robot": "beta"}),
        ("nav_to_obj", {}),
        ("nav_to_obj", {"target_obj": None}),
        ("pick", {"target_obj": "location:north"}),
        ("place", {"target_obj": "location:north"}),
        ("reset_arm", {}),
        ("unknown", {}),
        ("wait", ["500"]),
        ("wait", {"duration": 1}),
        ("send_request", {"target_agent": "alpha", "request": "help"}),
        ("send_request", {"target_agent": "unbound", "request": "help"}),
        ("send_request", {"target_agent": "beta", "request": ""}),
        ("send_request", {"target_agent": "beta"}),
        (None, {}),
        {"name": "wait", "arguments": {}},
        None,
        "model text",
    ],
)
def test_rejected_action_never_reaches_vendor_dispatch(response: Any, tmp_path: Path) -> None:
    """Wrong targets and malformed tools fail without retries, mutations, or side effects."""
    agents = [FakeAgent("alpha", response), FakeAgent("beta", ("wait", {}))]
    audit = Stage2ActionAudit(tmp_path)
    original_class_chat = FakeAgent.chat
    restore = install_stage2_contract_guard(agents, _contracts(), audit.record)
    try:
        with pytest.raises(Stage2ContractViolation):
            agents[0].chat("observation")
        assert agents[0].dispatched == []
        assert agents[0].llm_model.calls == 1
        assert "chat" not in vars(agents[0].llm_model)
    finally:
        restore()
        restore()
        audit.close()
    assert FakeAgent.chat is original_class_chat
    assert "chat" not in vars(agents[0])
    row = json.loads((tmp_path / "stage2-actions.jsonl").read_text())
    assert row["decision"] == "rejected"
    assert row["contract"]["expected_destination"] == "location:north"
    assert json.loads((tmp_path / "stage2-action-audit.json").read_text())["complete"] is True


def test_valid_navigation_is_identical_to_vendor_and_audit_precedes_mutation(
    tmp_path: Path,
) -> None:
    """Correct target and skill mapping stay byte-equivalent to unguarded execution."""
    response = ("nav_to_obj", {"target_obj": "location:north"})
    reference = FakeAgent("alpha", ("nav_to_obj", {"target_obj": "location:north"}))
    agent = FakeAgent("alpha", response)
    audit = Stage2ActionAudit(tmp_path)
    restore = install_stage2_contract_guard([agent], {"alpha": _contracts()["alpha"]}, audit.record)
    try:
        assert agent.chat("same observation") == reference.chat("same observation")
    finally:
        restore()
        audit.close()
    row = json.loads((tmp_path / "stage2-actions.jsonl").read_text())
    assert row["selected_action"]["arguments"] == {"target_obj": "location:north"}
    assert row["decision"] == "allowed"
    assert agent.llm_model.calls == reference.llm_model.calls == 1


def test_peer_message_does_not_grant_the_receiver_new_navigation_authority(tmp_path: Path) -> None:
    """Peer communication remains local How; the receiving contract still fences its target."""
    alpha = FakeAgent("alpha", ("send_request", {"target_agent": "beta", "request": "help north"}))
    beta = FakeAgent("beta", ("nav_to_obj", {"target_obj": "location:north"}))
    audit = Stage2ActionAudit(tmp_path)
    restore = install_stage2_contract_guard([alpha, beta], _contracts(), audit.record)
    try:
        assert alpha.chat("observation") == {"name": "wait", "arguments": ["500"]}
        with pytest.raises(Stage2ContractViolation, match="canonical destination"):
            beta.chat("alpha requested north")
        assert beta.dispatched == []
    finally:
        restore()
        audit.close()
    rows = [
        json.loads(line) for line in (tmp_path / "stage2-actions.jsonl").read_text().splitlines()
    ]
    assert [row["decision"] for row in rows] == ["allowed", "rejected"]


def test_idle_wait_and_same_agent_sequential_contracts() -> None:
    """Guard scopes do not force different robots or prevent later legitimate assignments."""
    agent = FakeAgent("alpha", ("wait", {}))
    for contract, response in [
        (Stage2ExecutionContract.idle(), ("wait", {})),
        (_contracts()["alpha"], ("nav_to_obj", {"target_obj": "location:north"})),
        (_contracts()["beta"], ("nav_to_obj", {"target_obj": "location:south"})),
    ]:
        agent.llm_model.response = response
        restore = install_stage2_contract_guard([agent], {"alpha": contract}, lambda row: None)
        try:
            agent.chat("observation")
        finally:
            restore()
    assert len(agent.dispatched) == 3
    with pytest.raises(Stage2ContractViolation, match="unassigned"):
        Stage2ExecutionContract.idle().validate(
            "alpha",
            {"name": "nav_to_obj", "arguments": {"target_obj": "location:north"}},
            frozenset({"alpha"}),
        )


def test_planning_untouched_provider_failure_not_retried_and_methods_restored() -> None:
    """Lazy model planning stays original; Provider errors preserve identity and call count."""
    failure = RuntimeError("provider sentinel")
    agent = FakeAgent("alpha", ("wait", {}))
    restore = install_stage2_contract_guard(
        [agent], {"alpha": _contracts()["alpha"]}, lambda row: None
    )
    try:
        assert agent.llm_model.chat("plan", crab_planning=True) == "planning text"
        agent.llm_model.response = failure
        before = agent.llm_model.calls
        with pytest.raises(RuntimeError) as caught:
            agent.chat("observation")
        assert caught.value is failure
        assert agent.llm_model.calls == before + 1
        assert "chat" not in vars(agent.llm_model)
    finally:
        restore()


@pytest.mark.parametrize("names", [["alpha"], ["alpha", "alpha"], ["alpha", "unknown"]])
def test_agent_contract_coverage_fails_before_installing_any_hook(names: list[str]) -> None:
    """No partial guard installation can leave an active agent unprotected."""
    agents = [FakeAgent(name, ("wait", {})) for name in names]
    with pytest.raises(IntegrationError, match="match execution contracts exactly"):
        install_stage2_contract_guard(agents, _contracts(), lambda row: None)
    assert all("chat" not in vars(agent) for agent in agents)


@pytest.mark.parametrize("option", ["planning_stage", "code_execution"])
def test_internally_executing_model_is_fenced_before_call(option: str) -> None:
    """Unsupported vendor modes cannot execute tools before the admission hook."""
    agent = FakeAgent("alpha", ("wait", {}))
    setattr(agent.llm_model, option, True)
    restore = install_stage2_contract_guard(
        [agent], {"alpha": _contracts()["alpha"]}, lambda row: None
    )
    try:
        with pytest.raises(IntegrationError, match="internally executing"):
            agent.chat("observation")
        assert agent.llm_model.calls == 0
    finally:
        restore()


def test_unserializable_and_oversized_audit_records_are_explicitly_unavailable(
    tmp_path: Path,
) -> None:
    """Evidence limits bound disk output without claiming missing raw actions were captured."""
    audit = Stage2ActionAudit(tmp_path)
    for action in [object(), "x" * 70_000]:
        audit.record({"decision": "rejected", "selected_action": action})
    audit.close()
    stats = json.loads((tmp_path / "stage2-action-audit.json").read_text())
    assert stats["complete"] is False
    assert stats["records_unavailable"] == 2
    assert (tmp_path / "stage2-actions.jsonl").stat().st_size < 1000


def test_write_failure_keeps_contract_failure_and_marks_incomplete(tmp_path: Path) -> None:
    """A broken evidence path cannot turn a rejected target into an allowed action."""
    (tmp_path / "stage2-actions.jsonl").mkdir()
    audit = Stage2ActionAudit(tmp_path)
    agent = FakeAgent("alpha", ("nav_to_obj", {"target_obj": "wrong"}))
    restore = install_stage2_contract_guard([agent], {"alpha": _contracts()["alpha"]}, audit.record)
    try:
        with pytest.raises(Stage2ContractViolation):
            agent.chat("observation")
        assert agent.dispatched == []
    finally:
        restore()
        audit.close()
    stats = json.loads((tmp_path / "stage2-action-audit.json").read_text())
    assert stats["complete"] is False
    assert stats["records_dropped"] == 1


def test_record_callback_failure_does_not_mask_violation() -> None:
    """Even an unexpected recording exception preserves the rejected-action terminal cause."""

    def broken_record(document: dict[str, Any]) -> None:
        """Emulate unexpected diagnostic failure."""
        del document
        raise OSError("audit unavailable")

    agent = FakeAgent("alpha", ("pick", {}))
    restore = install_stage2_contract_guard(
        [agent], {"alpha": _contracts()["alpha"]}, broken_record
    )
    try:
        with pytest.raises(Stage2ContractViolation):
            agent.chat("observation")
        assert agent.dispatched == []
    finally:
        restore()
