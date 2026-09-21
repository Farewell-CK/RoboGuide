"""Deterministic tests for the EMOS Stage2 local operation guard."""

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

from habitat_local_eaios.model import CanonicalMobilityInvocation  # noqa: E402
from habitat_local_eaios.stage2_contract import (  # noqa: E402
    EVIDENCE_FILENAME,
    LocalContractViolation,
    Stage2ContractGuard,
)


class FakeModel:
    """Return preselected provider results without executing a real tool."""

    def __init__(self, results: list[Any]) -> None:
        """Retain ordered executable results and observed call modes."""
        self.results = list(results)
        self.calls: list[tuple[str, bool]] = []

    def chat(self, content: str, crab_planning: bool = False) -> Any:
        """Return planning text or the next executable tool selection."""
        self.calls.append((content, crab_planning))
        if crab_planning:
            return "provider planning text"
        return self.results.pop(0)


class FailingModel:
    """Raise before returning a normalized tool, like the original EMOS parser."""

    def __init__(self, error: Exception) -> None:
        """Retain the exact exception instance used by the boundary test."""
        self.error = error

    def chat(self, content: str, crab_planning: bool = False) -> Any:
        """Return planning text but raise the original executable-call error."""
        del content
        if crab_planning:
            return "provider planning text"
        raise self.error


class FakeAgent:
    """Expose the mutable fields used by the original EMOS LLM policy."""

    def __init__(self, model: Any | None, *, initialized: bool) -> None:
        """Configure immediate or deferred model initialization."""
        self.llm_model = model
        self.initialized = initialized
        self.deferred_results: list[Any] = []

    def init_agent(self, *args: Any, **kwargs: Any) -> None:
        """Create a model and perform the original non-executable planning call."""
        del args, kwargs
        self.llm_model = FakeModel(self.deferred_results)
        self.llm_model.chat("plan", crab_planning=True)
        self.initialized = True


def _invocation(
    destination: str = "TARGET_any_targets|0",
    operation: str = "mobility.move@v1",
) -> CanonicalMobilityInvocation:
    """Build one exact committed mobility invocation."""
    return CanonicalMobilityInvocation(
        mission_id="mission",
        task_id="task",
        group_id="group",
        role_id="role",
        operation=operation,
        objective="move to the assigned semantic destination",
        parameters={"destination": destination},
        resource_ids=("space-a",),
    )


def _actor(agent: FakeAgent) -> Any:
    """Wrap one fake agent in the original active-policy object shape."""
    return SimpleNamespace(
        _active_policies=[SimpleNamespace(_high_level_policy=SimpleNamespace(llm_agent=agent))]
    )


def _agents_actor(agents: list[Any]) -> Any:
    """Wrap several agents in ordered original policy slots."""
    return SimpleNamespace(
        _active_policies=[
            SimpleNamespace(_high_level_policy=SimpleNamespace(llm_agent=agent)) for agent in agents
        ]
    )


def _records(path: Path) -> list[dict[str, Any]]:
    """Read contract evidence records from one isolated test directory."""
    return [
        json.loads(line)
        for line in (path / EVIDENCE_FILENAME).read_text(encoding="utf-8").splitlines()
    ]


@pytest.mark.parametrize("operation", ["mobility.move@v1", "mobility.navigate@v1"])
def test_exact_navigation_and_idle_are_preserved_and_recorded(
    tmp_path: Path, operation: str
) -> None:
    """Both mobility operations admit exact navigation and quiescent waiting."""
    selected = [
        ("nav_to_obj", {"target_obj": "TARGET_any_targets|0"}),
        ("wait", {}),
    ]
    model = FakeModel(selected)
    agent = FakeAgent(model, initialized=True)
    guard = Stage2ContractGuard(tmp_path, {0: _invocation(operation=operation)})
    original_chat = model.chat
    guard.install(_actor(agent))

    assert model.chat("first") == selected[0]
    assert model.chat("second") == selected[1]
    records = _records(tmp_path)
    assert [record["decision"] for record in records] == ["accepted", "accepted"]
    assert [record["decision_code"] for record in records] == [
        "mobility_navigation",
        "mobility_wait",
    ]
    assert records[0]["canonical_parameters"] == {"destination": "TARGET_any_targets|0"}
    assert records[0]["tool_arguments"] == {"target_obj": "TARGET_any_targets|0"}

    guard.restore()
    assert model.chat == original_chat


@pytest.mark.parametrize(
    ("selection", "code"),
    [
        (("nav_to_obj", {"target_obj": "any_targets|0"}), "destination_mismatch"),
        (("pick", {"target_obj": "any_targets|0"}), "tool_not_authorized"),
        (("place", {"target_obj": "x", "target_location": "y"}), "tool_not_authorized"),
        (("reset_arm", {}), "tool_not_authorized"),
        (("wait", {"duration": 500}), "invalid_wait_arguments"),
        (
            ("nav_to_obj", {"target_obj": "TARGET_any_targets|0", "robot": "agent_0"}),
            "invalid_navigation_arguments",
        ),
    ],
)
def test_semantic_drift_fails_closed_with_original_call_evidence(
    tmp_path: Path,
    selection: tuple[str, dict[str, Any]],
    code: str,
) -> None:
    """A mobility assignment cannot change destination or invoke manipulation."""
    model = FakeModel([selection])
    guard = Stage2ContractGuard(tmp_path, {0: _invocation()})
    guard.install(_actor(FakeAgent(model, initialized=True)))

    with pytest.raises(LocalContractViolation, match=code):
        model.chat("execute")

    [record] = _records(tmp_path)
    assert record["decision"] == "rejected"
    assert record["decision_code"] == code
    assert record["tool_name"] == selection[0]
    assert record["tool_arguments"] == selection[1]


def test_unassigned_agent_may_only_remain_idle(tmp_path: Path) -> None:
    """An unassigned EMOS policy cannot create a physical side effect."""
    idle_model = FakeModel([("wait", {})])
    idle_guard = Stage2ContractGuard(tmp_path / "idle", {})
    idle_guard.install(_actor(FakeAgent(idle_model, initialized=True)))
    assert idle_model.chat("idle") == ("wait", {})

    moving_model = FakeModel([("nav_to_obj", {"target_obj": "any_targets|0"})])
    moving_guard = Stage2ContractGuard(tmp_path / "moving", {})
    moving_guard.install(_actor(FakeAgent(moving_model, initialized=True)))
    with pytest.raises(LocalContractViolation, match="unassigned_agent_action"):
        moving_model.chat("move")


def test_deferred_model_is_guarded_after_original_initialization(tmp_path: Path) -> None:
    """The first real Stage2 action is guarded after CrabAgent creates its client."""
    agent = FakeAgent(None, initialized=False)
    agent.deferred_results = [
        ("nav_to_obj", {"target_obj": "TARGET_any_targets|0"}),
        ("pick", {"target_obj": "any_targets|0"}),
    ]
    guard = Stage2ContractGuard(tmp_path, {0: _invocation()})
    original_init = agent.init_agent
    guard.install(_actor(agent))

    agent.init_agent(robot_type="FetchRobot")
    assert agent.llm_model is not None
    assert agent.llm_model.calls == [("plan", True)]
    assert agent.llm_model.chat("execute") == (
        "nav_to_obj",
        {"target_obj": "TARGET_any_targets|0"},
    )
    assert len(_records(tmp_path)) == 1

    model = agent.llm_model
    guard.restore()
    assert agent.init_agent == original_init
    assert model.chat("after restore") == ("pick", {"target_obj": "any_targets|0"})


@pytest.mark.parametrize("result", [None, "wait", ("", {}), ("wait", []), ("wait", {}, 3)])
def test_malformed_model_result_is_rejected_and_attributed(tmp_path: Path, result: Any) -> None:
    """Malformed provider output cannot fall through to EMOS's implicit wait path."""
    model = FakeModel([result])
    guard = Stage2ContractGuard(tmp_path, {0: _invocation()})
    guard.install(_actor(FakeAgent(model, initialized=True)))
    with pytest.raises(LocalContractViolation, match="malformed_tool_call"):
        model.chat("execute")
    [record] = _records(tmp_path)
    assert record["decision_code"] == "malformed_tool_call"
    assert record["task_id"] == "task"


def test_model_client_parse_error_is_recorded_without_replacing_original(tmp_path: Path) -> None:
    """A pre-return parser failure remains primary and has bounded evidence."""
    original = json.JSONDecodeError("sensitive raw provider output", "{", 1)
    model = FailingModel(original)
    guard = Stage2ContractGuard(tmp_path, {0: _invocation()})
    guard.install(_actor(FakeAgent(model, initialized=True)))

    with pytest.raises(json.JSONDecodeError) as raised:
        model.chat("execute")

    assert raised.value is original
    [record] = _records(tmp_path)
    assert record["schema_version"] == "roboguide.local-eaios.stage2-tool-call/v0.2"
    assert record["decision"] == "unavailable"
    assert record["decision_code"] == "model_client_error"
    assert record["failure_stage"] == "model_client"
    assert record["model_client_error_type"] == "JSONDecodeError"
    assert "sensitive raw provider output" not in json.dumps(record)
    assert record["tool_name"] is None
    assert record["tool_arguments"] is None


def test_error_evidence_failure_does_not_replace_model_client_error(tmp_path: Path) -> None:
    """An unwritable error record cannot obscure the primary parser failure."""
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("occupied", encoding="utf-8")
    original = RuntimeError("provider parser failed")
    model = FailingModel(original)
    guard = Stage2ContractGuard(blocked, {0: _invocation()})
    guard.install(_actor(FakeAgent(model, initialized=True)))

    with pytest.raises(RuntimeError) as raised:
        model.chat("execute")

    assert raised.value is original


def test_evidence_failure_blocks_execution_instead_of_bypassing_guard(tmp_path: Path) -> None:
    """An unwritable evidence boundary fails before the accepted call is returned."""
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("occupied", encoding="utf-8")
    model = FakeModel([("nav_to_obj", {"target_obj": "TARGET_any_targets|0"})])
    guard = Stage2ContractGuard(blocked, {0: _invocation()})
    guard.install(_actor(FakeAgent(model, initialized=True)))

    with pytest.raises(LocalContractViolation, match="evidence could not be persisted"):
        model.chat("execute")


def test_partial_install_failure_restores_prior_agents(tmp_path: Path) -> None:
    """A bad later policy cannot leave an earlier policy silently wrapped."""
    model = FakeModel([("pick", {"target_obj": "any_targets|0"})])
    valid = FakeAgent(model, initialized=True)
    original_init = valid.init_agent
    invalid = SimpleNamespace(initialized=True, llm_model=model)
    guard = Stage2ContractGuard(tmp_path, {0: _invocation()})

    with pytest.raises(LocalContractViolation, match="no guardable Stage2 client"):
        guard.install(_agents_actor([valid, invalid]))

    assert valid.init_agent == original_init
    assert model.chat("unwrapped") == ("pick", {"target_obj": "any_targets|0"})
    assert not (tmp_path / EVIDENCE_FILENAME).exists()


def test_install_rejects_empty_or_unmapped_policy_topology(tmp_path: Path) -> None:
    """Every committed assignment must map to one installed policy guard."""
    with pytest.raises(LocalContractViolation, match="no active policies"):
        Stage2ContractGuard(tmp_path / "empty", {}).install(SimpleNamespace(_active_policies=[]))

    model = FakeModel([("wait", {})])
    actor = _actor(FakeAgent(model, initialized=True))
    with pytest.raises(LocalContractViolation, match="no EMOS policy slots"):
        Stage2ContractGuard(tmp_path / "missing", {1: _invocation()}).install(actor)
    assert model.chat("unwrapped") == ("wait", {})
