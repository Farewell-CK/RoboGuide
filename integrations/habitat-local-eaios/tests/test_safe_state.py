"""Deterministic tests for attempt-bound safe-state evidence."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

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


def test_safe_state_requires_terminal_cancel_and_generation() -> None:
    """Only a terminal cancelled attempt with local outcome produces a proof candidate."""
    valid = _row(_invocation())
    active = _row(_invocation(), state="RUNNING", local_outcome=None)
    missing_outcome = _row(_invocation(), local_outcome=None, has_outcome=False)
    missing_generation = _row(_invocation(generation=None))

    response = read_safe_state([valid, active, missing_outcome, missing_generation])

    assert response == {
        "observations": [
            {
                "execution_id": "attempt-4",
                "execution_generation": 4,
                "command_id": "cancel-attempt-4",
                "safe_state": True,
                "assurance": "software-process",
            }
        ]
    }


def test_safe_state_does_not_truncate_an_oversized_batch() -> None:
    """A bounded producer returns unavailable evidence instead of silently dropping rows."""
    rows = [
        _row(
            replace(
                _invocation(),
                attempt_id=f"attempt-{index}",
                execution_generation=index + 1,
            )
        )
        for index in range(257)
    ]

    assert read_safe_state(rows) == {"observations": []}


def test_safe_state_validator_rejects_identity_or_assurance_drift() -> None:
    """The adapter boundary rejects command, generation, and assurance mismatches."""
    response = read_safe_state([_row(_invocation())])
    observations = response["observations"]
    assert isinstance(observations, list)
    observation = observations[0]
    assert isinstance(observation, dict)
    observation["command_id"] = "cancel-other"
    with pytest.raises(IntegrationError, match="identity or assurance"):
        validate_safe_state_response(response)

    observation["command_id"] = "cancel-attempt-4"
    observation["assurance"] = "device-observation"
    with pytest.raises(IntegrationError, match="identity or assurance"):
        validate_safe_state_response(response)
