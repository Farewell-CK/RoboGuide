"""Verify original cohort permissions and active hazard references before immutable publication."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from .capacity_recovery import _checkpoint, _closed, _integer
from .snapshot_references import canonical

WRAPPER = "roboguide.controller-checkpoint/v59"
REVISION = "roboguide.runtime-plan-revision/v0.6"
KINDS = {"running-preemption", "coupled-recovery", "handoff", "regrasp"}


def _digest(value: Any) -> str:
    """Join the actual Rust canonical JSON identity without normalizing original source evidence."""
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def _coordination(
    mission: str,
    state: dict[str, Any],
    candidate: dict[str, Any],
    runtime: dict[str, Any],
    stops: dict[str, Any],
) -> None:
    """Join original permission, Review, Runtime commands and switch coverage."""
    value = candidate.get("coordination")
    review = candidate.get("review")
    if value is None:
        if any(
            switch.get("beneficiary_admission") is not None
            for switch in state["switches"]
            if switch["ticket_id"] == candidate["ticket_id"]
        ):
            raise ValueError("beneficiary admission lost its original coordination permission")
        if isinstance(review, dict) and review.get("coordination_digest") is not None:
            raise ValueError("coordination Review lost its original permission")
        return
    record = _closed(
        value,
        {
            "request",
            "command_id",
            "principal",
            "certificate_digest",
            "admitted_at",
            "admitted_kernel",
            "source_group",
            "membership",
            "source_revision",
            "attempts",
            "safety",
            "beneficiary",
            "digest",
        },
    )
    request = _closed(
        record["request"],
        {
            "schema",
            "kind",
            "beneficiary_mission_id",
            "stop_assurance",
            "continuation_operations",
            "reason",
        },
    )
    receipt = state["commands"].get(record["command_id"])
    identity = _digest(record | {"digest": ""})
    if (
        request["schema"] != "roboguide.execution-coordination/v0.1"
        or request["kind"] not in KINDS
        or request["stop_assurance"] not in {"software-process", "device-observation"}
        or request["kind"] in {"handoff", "regrasp"}
        and request["stop_assurance"] != "device-observation"
        or not isinstance(request["reason"], str)
        or not request["reason"].strip()
        or len(request["reason"].encode()) > 1024
        or not isinstance(request["continuation_operations"], list)
        or not 1 <= len(request["continuation_operations"]) <= 32
        or len({canonical(item) for item in request["continuation_operations"]})
        != len(request["continuation_operations"])
        or identity != record["digest"]
        or not isinstance(receipt, dict)
        or receipt["role"] != "recovery-admin"
        or receipt["principal"] != record["principal"]
        or receipt["leaf_digest"] != record["certificate_digest"]
        or receipt["received_at"] != record["admitted_at"]
        or receipt["result"].get("schema_version") != REVISION
        or receipt["result"].get("coordination_digest") != identity
        or receipt["result"].get("ticket_id") != candidate["ticket_id"]
        or record["source_revision"] != candidate["base_revision"]
        or isinstance(review, dict)
        and review.get("coordination_digest") != identity
        or not isinstance(record["attempts"], list)
        or not 1 <= len(record["attempts"]) <= 256
        or not isinstance(record["safety"], dict)
        or len(record["safety"]) != len(record["attempts"])
    ):
        raise ValueError("coordination original permission, command or Review differs")
    command = {
        "schema_version": REVISION,
        "command_id": record["command_id"],
        "expected_revision": receipt["result"]["revision"] - 1,
        "action": {
            "kind": "authorize-coordination",
            "ticket_id": candidate["ticket_id"],
            "authorization": request,
        },
    }
    if receipt["digest"] != _digest(command):
        raise ValueError("original coordination input changed its retained command digest")
    if isinstance(review, dict) and review["digest"] != _digest(review | {"digest": ""}):
        raise ValueError("coordination independent Review digest differs")
    if request["stop_assurance"] == "device-observation" and not candidate.get("memory"):
        raise ValueError("device coordination lost its original reviewed Memory context")
    for operation in request["continuation_operations"]:
        _closed(operation, {"namespace", "name", "version"})
        if any(not isinstance(value, str) or not value.strip() for value in operation.values()):
            raise ValueError("coordination successor operation identity is absent")
    budget, observed = candidate["budget"], record["admitted_kernel"]
    issued = budget["issued"]
    if (
        observed["boot_id"] != issued["boot_id"]
        or observed["namespace"] != issued["namespace"]
        or not _integer(observed["elapsed_ms"])
        or not issued["elapsed_ms"]
        <= observed["elapsed_ms"]
        < issued["elapsed_ms"]
        + budget["duration_ms"]
        - max(issued["resolution_ms"], observed["resolution_ms"])
    ):
        raise ValueError("coordination changed its original admission clock or candidate budget")
    seen = set()
    for attempt in record["attempts"]:
        _closed(
            attempt, {"execution_id", "generation", "task_ref", "role_id", "node_id", "session_id"}
        )
        execution = runtime["executions"].get(attempt["execution_id"])
        if execution is None:
            raise ValueError("coordination original Runtime attempt is missing")
        command = execution["command"]
        support = record["safety"].get(attempt["execution_id"])
        recovery = command.get("recovery_support", {}).get("support", {})
        if (
            attempt["execution_id"] in seen
            or command["task_ref"] != attempt["task_ref"]
            or command["task_ref"]["mission_id"] != mission
            or command["group_id"] != record["source_group"]
            or command["role_id"] != attempt["role_id"]
            or command["node_id"] != attempt["node_id"]
            or command.get("execution_generation") != attempt["generation"]
            or not isinstance(support, dict)
            or support["operation"] != command["intent"]["operation"]
            or support["assurance"] != request["stop_assurance"]
            or recovery.get("continuation") != "repeat-after-stop"
            or recovery.get("stop_scope") not in {"execution", "execution-group"}
            or attempt["execution_id"] in stops
            and stops[attempt["execution_id"]].get("support") != support
        ):
            raise ValueError(
                "coordination changed its original Runtime owner, generation or stopping assurance"
            )
        seen.add(attempt["execution_id"])
    for switch in state["switches"]:
        if switch["ticket_id"] == candidate["ticket_id"] and (
            switch["attempts"] != record["attempts"]
            or switch["membership"] != record["membership"]
            or switch["source_group"] != record["source_group"]
        ):
            raise ValueError("coordination lost complete original switch coverage")
    beneficiary = record["beneficiary"]
    if request["kind"] == "running-preemption":
        _closed(
            beneficiary,
            {
                "mission",
                "group",
                "revision",
                "plan_digest",
                "priority",
                "source_priority",
                "conflicts",
            },
        )
        if (
            beneficiary["mission"] != request["beneficiary_mission_id"]
            or beneficiary["mission"] == mission
            or not _integer(beneficiary["priority"], 255)
            or not _integer(beneficiary["source_priority"], 255)
            or beneficiary["priority"] <= beneficiary["source_priority"]
            or not isinstance(beneficiary["conflicts"], list)
            or not beneficiary["conflicts"]
        ):
            raise ValueError(
                "running preemption lost original beneficiary or exact blocker evidence"
            )
    elif beneficiary is not None or request["beneficiary_mission_id"] is not None:
        raise ValueError("non-preemption coordination hides beneficiary authority")
    for switch in state["switches"]:
        if switch["ticket_id"] != candidate["ticket_id"]:
            continue
        proof = switch.get("beneficiary_admission")
        required = beneficiary is not None and switch["phase"] == "applied"
        if not required:
            if proof is not None:
                raise ValueError("beneficiary admission is hidden outside applied preemption")
            continue
        proof = _closed(proof, {"execution_id", "resource_ids", "admitted_kernel"})
        actual = runtime["executions"].get(proof["execution_id"])
        if (
            not isinstance(actual, dict)
            or actual["command"]["group_id"] != beneficiary["group"]
            or actual["command"]["task_ref"]["mission_id"] != beneficiary["mission"]
            or actual["resource_ids"] != proof["resource_ids"]
            or not set(proof["resource_ids"]) & set(beneficiary["conflicts"])
        ):
            raise ValueError("applied preemption lost actual normal beneficiary resource admission")
        original, observed = switch["budget"]["issued"], proof["admitted_kernel"]
        if (
            original["boot_id"] != observed["boot_id"]
            or original["namespace"] != observed["namespace"]
            or not original["elapsed_ms"]
            <= observed["elapsed_ms"]
            < original["elapsed_ms"]
            + switch["budget"]["duration_ms"]
            - max(original["resolution_ms"], observed["resolution_ms"])
        ):
            raise ValueError("beneficiary admission renewed or exceeded the original switch budget")


def validate(connection: sqlite3.Connection) -> None:
    """Verify original coordination and hazard joins before publication."""
    wrapper = _checkpoint(
        connection,
        schemas=frozenset(
            f"roboguide.controller-checkpoint/v{version}" for version in range(49, 61)
        ),
    )
    if wrapper is None:
        return
    integration = json.loads(wrapper["integration_json"])
    histories = integration["control"].get("decision_policy_history", {})
    revisions = wrapper.get("runtime_revisions", {})
    if wrapper["schema"] not in {WRAPPER, "roboguide.controller-checkpoint/v60"}:
        if (
            (wrapper.get("hazards") or {}).get("watches")
            or any(
                registry["schema_version"] == "roboguide.decision-policy-registry/v0.3"
                for registry in histories.values()
            )
            or any(
                candidate.get("coordination")
                for state in revisions.values()
                for candidate in state["candidates"].values()
            )
        ):
            raise ValueError("old Controller wrapper hides cohort or active hazard authority")
        return
    hazards = _closed(wrapper.get("hazards"), {"watches"})["watches"]
    if not isinstance(hazards, dict) or len(hazards) > 8192:
        raise ValueError("complete active hazard reference table is missing or unbounded")
    current = integration["control"].get("decision_policy_registry")
    if current is not None and histories.get(str(current["revision"])) != current:
        raise ValueError("current active hazard registry lost its original history")
    for registry in histories.values():
        for gate in registry["gates"].values():
            timeout = gate.get("active_stop_timeout_ms")
            if timeout is not None and (
                registry["schema_version"] != "roboguide.decision-policy-registry/v0.3"
                or not _integer(timeout, 3_600_000)
                or timeout == 0
                or "readiness" not in gate["policy"]["allowed_decisions"]
            ):
                raise ValueError("active hazard gate hides its original schema or stop bound")
    runtime = integration["runtime"]
    for identity, watch in hazards.items():
        _closed(
            watch,
            {
                "execution_id",
                "command_digest",
                "generation",
                "registry_revision",
                "registry_digest",
                "gates",
                "admitted_at",
                "trip",
            },
        )
        execution = runtime["executions"].get(identity)
        registry = histories.get(str(watch["registry_revision"]))
        if (
            watch["execution_id"] != identity
            or not isinstance(execution, dict)
            or _digest(execution["command"]) != watch["command_digest"]
            or execution["command"].get("execution_generation") != watch["generation"]
            or not isinstance(registry, dict)
            or _digest(registry) != watch["registry_digest"]
            or not isinstance(watch["gates"], list)
            or not 1 <= len(watch["gates"]) <= 128
            or len(set(watch["gates"])) != len(watch["gates"])
            or any(
                registry["gates"].get(gate, {}).get("active_stop_timeout_ms") is None
                for gate in watch["gates"]
            )
        ):
            raise ValueError("active hazard original command, generation or gate identity differs")
        trip = watch["trip"]
        if trip is not None:
            _closed(trip, {"observed_at", "failed", "receipt_digest"})
            if (
                not _integer(trip["observed_at"])
                or trip["observed_at"] < watch["admitted_at"]
                or not isinstance(trip["failed"], list)
                or not trip["failed"]
                or not set(trip["failed"]) <= set(watch["gates"])
                or identity not in wrapper["safety_stops"]
                or not isinstance(trip["receipt_digest"], str)
                or len(trip["receipt_digest"]) != 71
            ):
                raise ValueError("active hazard trip lost its original complete stop intent")
    coordination_bytes = sum(
        len(canonical(candidate["coordination"]))
        for state in revisions.values()
        for candidate in state["candidates"].values()
        if candidate.get("coordination") is not None
    )
    if coordination_bytes > 512 * 1024:
        raise ValueError("retained original coordination metadata exceeds its complete bound")
    for mission, state in revisions.items():
        for candidate in state["candidates"].values():
            _coordination(mission, state, candidate, runtime, wrapper["safety_stops"])
