"""Independent original-process exit observations, separate from execution outcomes."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

from .model import IntegrationError
from .store import TERMINAL_STATES, StoredExecution

SAFE_STATE_SCHEMA = "roboguide.safe-state-observation/v0.3"
MAX_OBSERVATIONS = 256


def validate_process_exit(value: object) -> dict[str, Any]:
    """Validate a bounded original-worker observation without inventing OS evidence."""
    if not isinstance(value, dict) or set(value) != {
        "instance_id",
        "pid",
        "exit_code",
        "group_id",
        "attempts",
    }:
        raise IntegrationError("process exit fields are invalid")
    if (
        not isinstance(value["instance_id"], str)
        or not 1 <= len(value["instance_id"]) <= 128
        or type(value["pid"]) is not int
        or value["pid"] <= 0
        or type(value["exit_code"]) is not int
        or not -(2**31) <= value["exit_code"] < 2**31
        or not isinstance(value["group_id"], str)
        or not 1 <= len(value["group_id"]) <= 256
    ):
        raise IntegrationError("process exit identity is invalid")
    attempts = value["attempts"]
    if not isinstance(attempts, list) or not 1 <= len(attempts) <= MAX_OBSERVATIONS:
        raise IntegrationError("process exit attempt coverage is invalid")
    seen: set[str] = set()
    for attempt in attempts:
        if (
            not isinstance(attempt, dict)
            or set(attempt) != {"execution_id", "execution_generation"}
            or not isinstance(attempt["execution_id"], str)
            or not 1 <= len(attempt["execution_id"]) <= 1024
            or attempt["execution_id"] in seen
            or type(attempt["execution_generation"]) is not int
            or not 1 <= attempt["execution_generation"] <= 2**64 - 1
        ):
            raise IntegrationError("process exit attempt identity is invalid")
        seen.add(attempt["execution_id"])
    return deepcopy(value)


def read_safe_state(
    executions: Iterable[StoredExecution], process_exit: Mapping[str, object] | None = None
) -> dict[str, object]:
    """Publish only terminal attempts covered by an independently reaped original worker.

    Store terminal flags, cancellation, and outcomes alone confer no stop assurance.
    This observer does not execute cancellation, join a worker, or fabricate receipt IDs.
    Old adapters without an independent process witness return unavailable observations.
    """
    if process_exit is None:
        return {"observations": []}
    try:
        proof = validate_process_exit(dict(process_exit))
    except IntegrationError:
        return {"observations": []}
    covered = {a["execution_id"]: a["execution_generation"] for a in proof["attempts"]}
    observations: list[dict[str, object]] = []
    seen: set[str] = set()
    for execution in sorted(executions, key=lambda row: row["execution_id"]):
        invocation = execution["invocation"]
        if (
            execution["state"] not in TERMINAL_STATES
            or invocation.attempt_id is None
            or invocation.execution_generation is None
            or invocation.group_id != proof["group_id"]
            or covered.get(invocation.attempt_id) != invocation.execution_generation
        ):
            continue
        if invocation.attempt_id in seen:
            return {"observations": []}
        seen.add(invocation.attempt_id)
        observations.append(
            {
                "execution_id": invocation.attempt_id,
                "execution_generation": invocation.execution_generation,
                "command_id": None,
                "safe_state": True,
                "assurance": "software-process",
                "process_exit": proof,
                "terminal_state": execution["state"],
            }
        )
    if len(observations) > MAX_OBSERVATIONS:
        return {"observations": []}
    return {"observations": observations}


def validate_safe_state_response(value: object) -> dict[str, object]:
    """Reject malformed, duplicate, or unbound v0.3 observations at the HTTP boundary."""
    if not isinstance(value, dict) or set(value) != {"observations"}:
        raise IntegrationError("safe-state response must contain only observations")
    observations = value["observations"]
    if not isinstance(observations, list) or len(observations) > MAX_OBSERVATIONS:
        raise IntegrationError("safe-state observations exceed the bounded contract")
    seen: set[str] = set()
    for observation in observations:
        if not isinstance(observation, dict) or set(observation) != {
            "execution_id",
            "execution_generation",
            "command_id",
            "safe_state",
            "assurance",
            "process_exit",
            "terminal_state",
        }:
            raise IntegrationError("safe-state observation fields are invalid")
        proof = validate_process_exit(observation["process_exit"])
        execution_id = observation["execution_id"]
        generation = observation["execution_generation"]
        if (
            not isinstance(execution_id, str)
            or not execution_id
            or execution_id in seen
            or type(generation) is not int
            or not 1 <= generation <= 2**64 - 1
            or observation["command_id"] is not None
            or observation["terminal_state"] not in TERMINAL_STATES
            or observation["safe_state"] is not True
            or observation["assurance"] != "software-process"
            or {"execution_id": execution_id, "execution_generation": generation}
            not in proof["attempts"]
        ):
            raise IntegrationError("safe-state observation identity or assurance is invalid")
        seen.add(execution_id)
    return {"observations": deepcopy(observations)}
