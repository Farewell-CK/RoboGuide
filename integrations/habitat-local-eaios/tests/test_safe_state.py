"""Deterministic tests for attempt-bound safe-state evidence."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.model import CanonicalMobilityInvocation, IntegrationError  # noqa: E402
from habitat_local_eaios.safe_state import (  # noqa: E402
    read_safe_state,
    validate_safe_state_response,
)
from habitat_local_eaios.store import StoredExecution  # noqa: E402


def _invocation(*, generation: int | None = 4) -> CanonicalMobilityInvocation:
    """Build one canonical attempt with explicit optional generation evidence."""
    return CanonicalMobilityInvocation(
        mission_id="mission-safety",
        task_id="task-safety",
        group_id="group-safety",
        role_id="navigator",
        operation="mobility.navigate@v1",
        objective="Navigate to the declared target.",
        parameters={"destination": "target:1"},
        resource_ids=("slot-a",),
        attempt_id="attempt-4",
        execution_generation=generation,
    )


def _row(
    invocation: CanonicalMobilityInvocation,
    *,
    state: str = "CANCELLED",
    cancel_requested: bool = True,
    local_outcome: dict[str, object] | None = None,
    has_outcome: bool = True,
) -> StoredExecution:
    """Build the minimal trusted store row required by the safety projection."""
    return {
        "execution_id": "habitat-local-1",
        "request_key": "request-1",
        "invocation": invocation,
        "state": state,
        "detail": "terminal",
        "cancel_requested": cancel_requested,
        "local_outcome": (
            {"state": state} if has_outcome and local_outcome is None else local_outcome
        ),
        "created_at_unix_ms": 1,
        "updated_at_unix_ms": 2,
    }


def test_invocation_round_trip_preserves_generation() -> None:
    """The Local EAIOS parser retains Controller-provided generation identity exactly."""
    invocation = _invocation()
    restored = CanonicalMobilityInvocation.from_request({"invocation": invocation.as_dict()})
    assert restored.execution_generation == 4
    assert restored.attempt_id == "attempt-4"


def _proof(attempts: list[dict[str, object]] | None = None) -> dict[str, object]:
    """Build an explicitly synthetic witness; real-process tests verify the source separately."""
    return {
        "instance_id": "original-worker",
        "pid": 17,
        "exit_code": 0,
        "group_id": "group-safety",
        "attempts": attempts or [{"execution_id": "attempt-4", "execution_generation": 4}],
    }


def test_terminal_store_flags_alone_never_establish_process_exit() -> None:
    """Even terminal/cancel/outcome combinations supply no independent OS observation."""
    assert read_safe_state([_row(_invocation())]) == {"observations": []}


@pytest.mark.parametrize("state", ["FAILED", "COMPLETED", "CANCELLED"])
def test_original_process_exit_preserves_terminal_state(state: str) -> None:
    """An exited original process proves stopping without changing its execution result."""
    result = read_safe_state([_row(_invocation(), state=state, cancel_requested=False)], _proof())
    validate_safe_state_response(result)
    observations = result["observations"]
    assert isinstance(observations, list) and len(observations) == 1
    assert observations[0]["terminal_state"] == state
    assert observations[0]["command_id"] is None
    assert observations[0]["process_exit"] == _proof()


def test_process_witness_cannot_stop_active_or_unknown_generation() -> None:
    """A cached or mismatching witness grants no authority to a running/new attempt."""
    for row in [
        _row(_invocation(), state="RUNNING"),
        _row(_invocation(generation=None)),
        _row(replace(_invocation(), execution_generation=5)),
        _row(replace(_invocation(), group_id="different")),
    ]:
        assert read_safe_state([row], _proof()) == {"observations": []}


def test_safe_state_does_not_truncate_an_oversized_batch() -> None:
    """Oversized complete coverage fails closed instead of silently dropping attempts."""
    attempts = [{"execution_id": f"attempt-{i}", "execution_generation": i + 1} for i in range(257)]
    assert read_safe_state([_row(_invocation())], _proof(attempts)) == {"observations": []}


@pytest.mark.parametrize("fault", ["command", "generation", "assurance", "duplicate", "pid"])
def test_safe_state_validator_rejects_identity_or_assurance_drift(fault: str) -> None:
    """Malformed witness, invented receipt and mismatched coverage all reject explicitly."""
    response = read_safe_state([_row(_invocation())], _proof())
    observations = response["observations"]
    assert isinstance(observations, list)
    observation = observations[0]
    assert isinstance(observation, dict)
    if fault == "command":
        observation["command_id"] = "cancel-attempt-4"
    if fault == "generation":
        observation["execution_generation"] = 5
    if fault == "assurance":
        observation["assurance"] = "device-observation"
    if fault == "duplicate":
        observations.append(dict(observation))
    if fault == "pid":
        observation["process_exit"]["pid"] = 0
    with pytest.raises(IntegrationError):
        validate_safe_state_response(response)


def _software_child(connection: object, config: object, agent_ids: object) -> None:
    """Run a real OS child using only the production readiness/close protocol, no simulator."""
    from typing import cast

    channel = cast("Any", connection)
    channel.send(("READY", "synthetic software process"))
    assert channel.recv()[0] == "CLOSE"
    channel.close()


def test_original_process_handle_is_reaped_before_exit_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production process owner observes actual PID/exit status and never replaces it."""
    from typing import cast

    from habitat_local_eaios import shared_world
    from habitat_local_eaios.crabagent_backend import CrabAgentBackendConfig

    monkeypatch.setattr(shared_world, "_child_world_process", _software_child)
    world = shared_world.ProcessWorldService(cast(CrabAgentBackendConfig, None), (0,))
    try:
        assert world.start() == "synthetic software process"
        process: Any = world._process
        assert process.is_alive()
        assert world._exit_observation is None
        pid = process.pid
        observed = world.close_and_observe()
        assert observed is not None
        assert observed["pid"] == pid
        assert observed["exit_code"] == 0
        assert not process.is_alive()
        assert world.close_and_observe() == observed
        with pytest.raises(IntegrationError, match="cannot restart"):
            world.start()
    finally:
        world.shutdown()


def test_unresolved_original_process_never_publishes_exit() -> None:
    """A worker surviving terminate remains unavailable and its original handle is retained."""
    from types import SimpleNamespace
    from typing import cast

    from habitat_local_eaios.crabagent_backend import CrabAgentBackendConfig
    from habitat_local_eaios.shared_world import ProcessWorldService

    world = ProcessWorldService(cast(CrabAgentBackendConfig, None), (0,))
    worker = SimpleNamespace(
        is_alive=lambda: True, join=lambda **kwargs: None, terminate=lambda: None
    )
    world._process = worker
    assert world.close_and_observe() is None
    assert world._process is worker


def test_unknown_exit_status_stays_unavailable() -> None:
    """An exited handle without a readable exit status cannot supply process evidence."""
    from types import SimpleNamespace
    from typing import cast

    from habitat_local_eaios.crabagent_backend import CrabAgentBackendConfig
    from habitat_local_eaios.shared_world import ProcessWorldService

    world = ProcessWorldService(cast(CrabAgentBackendConfig, None), (0,))
    worker = SimpleNamespace(is_alive=lambda: False, join=lambda **kwargs: None)
    world._process = worker
    assert world.close_and_observe() is None
    assert world._process is worker
