"""Attempt-bound Local EAIOS safe-state observations for the Controller stop contract."""

from __future__ import annotations

from collections.abc import Iterable

from .model import IntegrationError
from .store import TERMINAL_STATES, StoredExecution

SAFE_STATE_SCHEMA = "roboguide.safe-state-observation/v0.2"
MAX_OBSERVATIONS = 256


def read_safe_state(executions: Iterable[StoredExecution]) -> dict[str, object]:
    """Return only independently terminal, explicitly cancelled local attempts.

    A command receipt, an active execution, an execution that was marked failed without a
    local outcome, or a missing Runtime generation is deliberately omitted.  The Controller
    then treats the observation as unavailable instead of promoting an ambiguous local state.
    Returning an empty batch on an oversized result avoids silently truncating evidence.
    """
    observations: list[dict[str, object]] = []
    for execution in sorted(executions, key=lambda value: value["execution_id"]):
        if (
            execution["state"] not in TERMINAL_STATES
            or not execution["cancel_requested"]
            or execution["local_outcome"] is None
        ):
            continue
        invocation = execution["invocation"]
        if invocation.attempt_id is None or invocation.execution_generation is None:
            continue
        observations.append(
            {
                "execution_id": invocation.attempt_id,
                "execution_generation": invocation.execution_generation,
                "command_id": f"cancel-{invocation.attempt_id}",
                "safe_state": True,
                "assurance": "software-process",
            }
        )
    if len(observations) > MAX_OBSERVATIONS:
        return {"observations": []}
    return {"observations": observations}


def validate_safe_state_response(value: object) -> dict[str, object]:
    """Validate the bounded adapter response before exposing it through HTTP tests."""
    if not isinstance(value, dict) or set(value) != {"observations"}:
        raise IntegrationError("safe-state response must contain only observations")
    observations = value["observations"]
    if not isinstance(observations, list) or len(observations) > MAX_OBSERVATIONS:
        raise IntegrationError("safe-state observations exceed the bounded contract")
    for observation in observations:
        if not isinstance(observation, dict) or set(observation) != {
            "execution_id",
            "execution_generation",
            "command_id",
            "safe_state",
            "assurance",
        }:
            raise IntegrationError("safe-state observation fields are invalid")
        execution_id = observation["execution_id"]
        generation = observation["execution_generation"]
        command_id = observation["command_id"]
        if (
            not isinstance(execution_id, str)
            or not execution_id
            or type(generation) is not int
            or not 1 <= generation <= 2**64 - 1
            or command_id != f"cancel-{execution_id}"
            or observation["safe_state"] is not True
            or observation["assurance"] != "software-process"
        ):
            raise IntegrationError("safe-state observation identity or assurance is invalid")
    return {"observations": [dict(item) for item in observations]}
