"""Verify B1 semantic and execution identity independently of Habitat availability.

v0.3 binds authoritative semantic evidence, the final draft, actual HTTP submission,
and scoped execution.
Attributable failures validate the chain up to the observed failure boundary.
Hashes check archive consistency; evidence producers remain trusted.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

PROVENANCE_SCHEMA_VERSION = "roboguide.e1.b1-provenance/v0.3"
SUBMISSION_SCHEMA = "roboguide.controller-submission-evidence/v0.1"
REQUEST_FAILURE_SCHEMA = "roboguide.mission-request-failure/v0.1"
RUN_FAILURE_SCHEMA = "roboguide.e1.run-failure/v0.1"
OBSERVATIONS_SCHEMA = "roboguide.mission-request-observations/v0.1"
_DIGEST = re.compile(r"sha256:[a-f0-9]{64}$")


def canonical_json_bytes(value: Any) -> bytes:
    """Use exactly the MI sorted compact UTF-8 JSON canonicalization."""
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    """Return unprefixed SHA-256 for artifact links, not MI draft fields."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def plan_digest(value: Any) -> str:
    """Return the explicitly prefixed MI draft/submission representation."""
    return f"sha256:{digest(value)}"


def load_document(path: Path) -> Any:
    """Read optional JSON; absent or malformed evidence stays unavailable."""

    def reject_nonfinite(value: str) -> None:
        """Reject non-JSON numeric constants before digesting evidence."""
        raise ValueError(f"non-JSON number: {value}")

    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_nonfinite)
    except (OSError, ValueError):
        return None


def digest_document(path: Path) -> str | None:
    """Digest present JSON without inventing missing evidence."""
    value = load_document(path)
    return digest(value) if value is not None else None


def _object(value: Any) -> dict[str, Any]:
    """Narrow evidence to an object, preserving absence as an empty object."""
    return value if isinstance(value, dict) else {}


def _array(value: Any) -> list[Any]:
    """Keep malformed collections from crashing fail-closed verification."""
    return value if isinstance(value, list) else []


class ProvenanceFailure(StrEnum):
    """Stable failed gates; benchmark availability is deliberately absent."""

    PROVENANCE_RECORD_MISSING = "provenance_record_missing"
    SCHEMA_UNSUPPORTED = "provenance_schema_unsupported"
    RUN_ID_MISMATCH = "run_id_mismatch"
    INPUT_DIGEST_MISSING = "input_digest_missing"
    INPUT_DIGEST_MISMATCH = "input_digest_mismatch"
    MI_RUN_MISSING = "mi_run_missing"
    REQUEST_RECORD_MISMATCH = "request_record_mismatch"
    MI_GENERATION_EVIDENCE_MISSING = "mi_generation_evidence_missing"
    MI_RUN_PLAN_DIGEST_MISMATCH = "mi_run_plan_digest_mismatch"
    PLAN_MISSING = "plan_missing"
    PLAN_DIGEST_MISMATCH = "plan_digest_mismatch"
    FINAL_REVIEW_MISMATCH = "final_review_mismatch"
    CONTROLLER_MISSION_MISSING = "controller_mission_missing"
    CONTROLLER_GROUP_ID_MISSING = "controller_group_id_missing"
    CONTROLLER_SUBMISSION_IDENTITY_MISMATCH = "controller_submission_identity_mismatch"
    ACTUAL_SUBMISSION_MISSING = "actual_submission_missing"
    CONTROLLER_PLAN_MISMATCH = "controller_plan_mismatch"
    SUBMISSION_EVIDENCE_MISMATCH = "submission_evidence_mismatch"
    SEMANTIC_EVIDENCE_MISSING = "semantic_evidence_missing"
    SEMANTIC_EVIDENCE_INVALID = "semantic_evidence_invalid"
    SEMANTIC_EVIDENCE_IDENTITY_MISMATCH = "semantic_evidence_identity_mismatch"
    MI_SEMANTIC_EVIDENCE_MISMATCH = "mi_semantic_evidence_mismatch"
    CONTROLLER_TASK_REGISTRATION_MISMATCH = "controller_task_registration_mismatch"
    EXECUTION_IDENTITY_MISMATCH = "execution_identity_mismatch"
    EXECUTION_ATTEMPTS_EMPTY = "execution_attempts_empty"
    FAILURE_EVIDENCE_MISMATCH = "failure_evidence_mismatch"
    STATIC_B2_PLAN_EQUALITY = "static_b2_plan_equality"


@dataclass(frozen=True, slots=True)
class B1ProvenanceRecord:
    """Link run-local execution evidence; the benchmark link is optional."""

    run_id: str
    input_digest: str
    mi_run_identity: str
    accepted_plan_digest: str
    mission_id: str
    controller_submission_identity: str
    execution_identity: str
    request_record_digest: str
    observations_digest: str
    submission_evidence_digest: str
    failure_evidence_digest: str
    semantic_evidence_digest: str
    benchmark_evidence_digest: str | None = None
    schema_version: str = PROVENANCE_SCHEMA_VERSION

    def to_json(self) -> dict[str, Any]:
        """Serialize the versioned execution provenance links."""
        return asdict(self)

    @classmethod
    def from_json(cls, value: Any) -> B1ProvenanceRecord | None:
        """Reject missing/old/malformed artifacts instead of upgrading v0.1."""
        if not isinstance(value, dict) or value.get("schema_version") != PROVENANCE_SCHEMA_VERSION:
            return None
        try:
            record = cls(**value)
        except TypeError:
            return None
        if any(
            not isinstance(item, str)
            for name, item in asdict(record).items()
            if name != "benchmark_evidence_digest"
        ):
            return None
        return record


@dataclass(frozen=True, slots=True)
class ProvenanceVerification:
    """Report chain consistency without making a population decision."""

    passed: bool
    failures: tuple[ProvenanceFailure, ...]
    plan_digest: str | None
    is_static_b2_plan: bool
    semantic_diagnostics: tuple[dict[str, Any], ...] = ()


def request_failure(request: Any) -> dict[str, Any]:
    """Admit typed MI failure observations scoped to this request and Mission."""
    doc = _object(request)
    failure = _object(doc.get("failure_evidence"))
    if (
        not isinstance(failure.get("failure_owner"), str)
        or not isinstance(failure.get("stage"), str)
        or not isinstance(doc.get("lifecycle"), str)
        or doc.get("lifecycle") not in {"Failed", "Blocked"}
        or failure.get("schema_version") != REQUEST_FAILURE_SCHEMA
        or failure.get("request_id") != doc.get("request_id")
        or failure.get("mission_id") != doc.get("mission_id")
        or failure.get("failure_owner") not in {"MODEL", "SUT_SYSTEM"}
        or failure.get("stage")
        not in {
            "grounding",
            "interpreter",
            "planner",
            "draft_validation",
            "reviewer",
            "repairer",
            "controller_submission",
        }
        or not failure.get("detail")
        or type(failure.get("observed_at_ms")) is not int
    ):
        return {}
    return failure


def observed_request(request: Any, observations: Any) -> dict[str, Any]:
    """Attach read-only observations only to their exact public request snapshot."""
    doc = _object(request)
    obs = _object(observations)
    if (
        obs.get("schema_version") != OBSERVATIONS_SCHEMA
        or obs.get("request_id") != doc.get("request_id")
        or obs.get("mission_id") != doc.get("mission_id")
        or obs.get("request_record_digest") != plan_digest(doc)
    ):
        return {
            key: value
            for key, value in doc.items()
            if key not in {"submission_evidence", "failure_evidence"}
        }
    return {
        **doc,
        "submission_evidence": obs.get("submission_evidence"),
        "failure_evidence": obs.get("failure_evidence"),
    }


def scoped_execution_evidence(
    mission_id: str, group_id: str, mission: Any, events: Any, attempts: Any
) -> dict[str, Any]:
    """Filter both identities before consuming Controller Task or attempt evidence."""
    view = _object(mission)
    if view.get("mission_id") != mission_id or view.get("group_id") != group_id:
        view = {}
    tasks: set[str] = set()
    group_seen = bool(view and group_id)
    for event in _array(_object(events).get("events")):
        payload = _object(_object(event).get("payload"))
        created = _object(payload.get("ExecutionGroupCreated"))
        if created.get("mission_id") == mission_id and created.get("group_id") == group_id:
            group_seen = True
        registered = _object(payload.get("TaskExecutionRegistered"))
        task_ref = _object(registered.get("task_ref"))
        if registered.get("group_id") == group_id and task_ref.get("mission_id") == mission_id:
            if isinstance(task_ref.get("task_id"), str):
                tasks.add(task_ref["task_id"])
    execution_ids = sorted(
        {
            attempt["execution_id"]
            for attempt in _array(_object(attempts).get("attempts"))
            if isinstance(attempt, dict)
            and attempt.get("mission_id") == mission_id
            and attempt.get("group_id") == group_id
            and isinstance(attempt.get("execution_id"), str)
        }
    )
    return {
        "mission_id": mission_id if group_seen else None,
        "group_id": group_id if group_seen else None,
        "registered_task_ids": sorted(tasks),
        "execution_ids": execution_ids,
        "mission_status": view.get("status"),
    }


def _check_draft(request: dict[str, Any], early: bool) -> list[ProvenanceFailure]:
    """Bind generation and final review to the current immutable draft."""
    failures: list[ProvenanceFailure] = []
    plan = _object(request.get("plan"))
    if not plan:
        return [] if early else [ProvenanceFailure.PLAN_MISSING]
    if (
        request.get("draft_digest") != plan_digest(plan)
        or type(request.get("draft_revision")) is not int
        or request["draft_revision"] < 1
    ):
        failures.append(ProvenanceFailure.MI_RUN_PLAN_DIGEST_MISMATCH)
    if _object(plan.get("mission")).get("id") != request.get("mission_id"):
        failures.append(ProvenanceFailure.CONTROLLER_MISSION_MISSING)
    history = request.get("review_history", [])
    if not isinstance(history, list):
        failures.append(ProvenanceFailure.FINAL_REVIEW_MISMATCH)
        history = []
    revisions: list[int] = []
    for item in history:
        attempt = _object(item)
        revision = attempt.get("draft_revision")
        if (
            type(revision) is not int
            or revision < 1
            or not _DIGEST.fullmatch(str(attempt.get("draft_digest", "")))
        ):
            failures.append(ProvenanceFailure.FINAL_REVIEW_MISMATCH)
            continue
        revisions.append(revision)
    if revisions != sorted(set(revisions)):
        failures.append(ProvenanceFailure.FINAL_REVIEW_MISMATCH)
    if history and not early:
        final = _object(history[-1]) if isinstance(history, list) else {}
        if (
            final.get("draft_revision") != request.get("draft_revision")
            or final.get("draft_digest") != plan_digest(plan)
            or _object(final.get("review")).get("approved") is not True
        ):
            failures.append(ProvenanceFailure.FINAL_REVIEW_MISMATCH)
    if request.get("repair_attempts", 0) and not history:
        failures.append(ProvenanceFailure.FINAL_REVIEW_MISMATCH)
    return failures


def _check_submission(
    request: dict[str, Any],
    controller: dict[str, Any],
    execution_ids: tuple[str, ...],
    failure_evidence: dict[str, Any],
) -> list[ProvenanceFailure]:
    """Require actual-body evidence, then Controller identity and execution/failure."""
    failures: list[ProvenanceFailure] = []
    sent = _object(request.get("submission_evidence"))
    plan = _object(request.get("plan"))
    if (
        sent.get("schema_version") != SUBMISSION_SCHEMA
        or sent.get("request_id") != request.get("request_id")
        or type(sent.get("submitted_at_ms")) is not int
        or not _DIGEST.fullmatch(str(sent.get("raw_request_body_sha256", "")))
    ):
        failures.append(ProvenanceFailure.ACTUAL_SUBMISSION_MISSING)
    if not plan or sent.get("submitted_plan_digest") != plan_digest(plan):
        failures.append(ProvenanceFailure.CONTROLLER_PLAN_MISMATCH)
    if sent.get("submitted_mission_id") != request.get("mission_id"):
        failures.append(ProvenanceFailure.CONTROLLER_MISSION_MISSING)
    failure = request_failure(request)
    if (
        failure
        and failure["stage"] == "controller_submission"
        and (type(sent.get("controller_status_code")) is int or sent.get("transport_error"))
    ):
        return failures
    if (
        request.get("lifecycle") != "Accepted"
        or type(sent.get("controller_status_code")) is not int
        or sent.get("controller_status_code") not in {200, 202}
        or sent.get("controller_mission_id") != request.get("mission_id")
        or controller.get("mission_id") != request.get("mission_id")
    ):
        failures.append(ProvenanceFailure.CONTROLLER_MISSION_MISSING)
    group = sent.get("controller_group_id")
    if not group or controller.get("group_id") != group:
        failures.append(ProvenanceFailure.CONTROLLER_GROUP_ID_MISSING)
    task_ids = {
        task["id"]
        for task in _array(plan.get("tasks"))
        if isinstance(task, dict) and isinstance(task.get("id"), str)
    }
    if not task_ids or set(controller.get("registered_task_ids", [])) != task_ids:
        failures.append(ProvenanceFailure.CONTROLLER_TASK_REGISTRATION_MISMATCH)
    attributable_failure = controller.get("mission_status") == "Failed" or bool(
        failure_evidence.get("failure_owner") in {"SUT_SYSTEM", "MODEL"}
        and failure_evidence.get("mission_id") == request.get("mission_id")
        and failure_evidence.get("group_id") == group
    )
    if not execution_ids and not attributable_failure:
        failures.append(ProvenanceFailure.EXECUTION_ATTEMPTS_EMPTY)
    return failures


def _semantic_expression_valid(value: Any) -> bool:
    """Validate the neutral expression tree without importing benchmark libraries."""
    item = _object(value)
    if item.get("kind") == "predicate":
        return (
            set(item) == {"kind", "name", "arguments"}
            and isinstance(item.get("name"), str)
            and bool(item["name"])
            and isinstance(item.get("arguments"), list)
            and all(isinstance(argument, str) and argument for argument in item["arguments"])
        )
    if item.get("kind") != "logical":
        return False
    return (
        set(item) == {"kind", "operator", "operands", "quantifier", "variables"}
        and item.get("operator") in {"and", "or", "nand", "nor"}
        and isinstance(item.get("operands"), list)
        and bool(item["operands"])
        and all(_semantic_expression_valid(operand) for operand in item["operands"])
        and (item.get("quantifier") is None or isinstance(item.get("quantifier"), str))
        and isinstance(item.get("variables"), list)
        and all(isinstance(variable, str) and variable for variable in item["variables"])
    )


def _semantic_predicates(value: Any) -> list[dict[str, Any]]:
    """Flatten only predicate leaves for non-authoritative coverage diagnostics."""
    item = _object(value)
    if item.get("kind") == "predicate":
        return [
            {
                "name": item.get("name"),
                "arguments": list(item.get("arguments", [])),
            }
        ]
    return [
        predicate
        for operand in _array(item.get("operands"))
        for predicate in _semantic_predicates(operand)
    ]


def _semantic_plan_text(value: Any) -> str:
    """Collect plan text without treating it as authoritative semantic proof."""
    if isinstance(value, str):
        return value.casefold()
    if isinstance(value, list):
        return " ".join(_semantic_plan_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_semantic_plan_text(item) for item in value.values())
    return ""


def semantic_goal_diagnostic(semantic_evidence: Any, plan: Any) -> dict[str, Any]:
    """Report goal coverage without turning model omission into provenance invalidity."""
    semantic = _object(semantic_evidence)
    goal = _object(semantic.get("goal"))
    predicates = _semantic_predicates(goal)
    if not predicates:
        return {
            "diagnostic_only": True,
            "status": "unavailable",
            "reason": "authoritative_goal_predicates_unavailable",
        }
    text = _semantic_plan_text(plan)
    argument_matches = [
        predicate
        for predicate in predicates
        if all(str(argument).casefold() in text for argument in predicate["arguments"])
    ]
    exact_matches = [
        predicate
        for predicate in argument_matches
        if isinstance(predicate.get("name"), str) and predicate["name"].casefold() in text
    ]
    missing = [predicate for predicate in predicates if predicate not in argument_matches]
    operator = goal.get("operator") if goal.get("kind") == "logical" else None
    return {
        "diagnostic_only": True,
        "status": "complete" if not missing else "partial",
        "coverage_basis": "predicate argument text in final MissionPlan semantic outcomes",
        "objective_scope": semantic.get("objective_scope"),
        "authoritative_operator": operator,
        "authoritative_predicate_count": len(predicates),
        "exactly_covered_predicates": exact_matches,
        "argument_mentioned_predicates": argument_matches,
        "missing_predicates": missing,
        "joint_goal_shape_preserved_in_mi_input": semantic.get("objective_scope")
        == "joint_terminal_state"
        and operator == "and"
        and len(_array(goal.get("operands"))) >= 2,
    }


def _check_semantic_evidence(
    document: Any,
    request: dict[str, Any],
    frozen: dict[str, Any],
    record: B1ProvenanceRecord | None,
    run_id: str | None,
) -> list[ProvenanceFailure]:
    """Bind adapter evidence, frozen episode identity, and the actual MI context."""
    failures: list[ProvenanceFailure] = []
    semantic = _object(document)
    required = {
        "schema_version",
        "authority",
        "identity",
        "objective_scope",
        "goal",
        "world_context",
        "digest",
    }
    if not semantic or set(semantic) != required:
        return [ProvenanceFailure.SEMANTIC_EVIDENCE_MISSING]
    identity = _object(semantic.get("identity"))
    body = {key: value for key, value in semantic.items() if key != "digest"}
    valid = (
        semantic.get("schema_version") == "roboguide.authoritative-semantic-evidence/v0.1"
        and semantic.get("authority") == "environment-authoritative"
        and semantic.get("objective_scope") == "joint_terminal_state"
        and isinstance(semantic.get("digest"), str)
        and semantic.get("digest") == plan_digest(body)
        and set(identity) == {"run_id", "episode_id", "revision"}
        and isinstance(identity.get("run_id"), str)
        and isinstance(identity.get("episode_id"), str)
        and isinstance(identity.get("revision"), str)
        and _semantic_expression_valid(semantic.get("goal"))
        and isinstance(semantic.get("world_context"), dict)
    )
    if not valid:
        failures.append(ProvenanceFailure.SEMANTIC_EVIDENCE_INVALID)
        return failures
    if (run_id is not None and identity["run_id"] != run_id) or (
        isinstance(frozen.get("episode_id"), str) and identity["episode_id"] != frozen["episode_id"]
    ):
        failures.append(ProvenanceFailure.SEMANTIC_EVIDENCE_IDENTITY_MISMATCH)
    grounding = _object(request.get("grounding_context"))
    admitted = _object(grounding.get("semantic_evidence"))
    context_body = {
        key: value
        for key, value in grounding.items()
        if key not in {"schema_version", "context_digest"}
    }
    if (
        grounding.get("schema_version") != "roboguide.grounding-context/v0.2"
        or grounding.get("context_digest") != plan_digest(context_body)
        or admitted != semantic
        or admitted.get("digest") != semantic.get("digest")
    ):
        failures.append(ProvenanceFailure.MI_SEMANTIC_EVIDENCE_MISMATCH)
    if record and record.semantic_evidence_digest != semantic.get("digest"):
        failures.append(ProvenanceFailure.SEMANTIC_EVIDENCE_INVALID)
    return failures


def verify_b1_provenance(
    *,
    record: B1ProvenanceRecord | None,
    frozen_input: Any,
    request_record: Any,
    request_observations: Any,
    controller_submission: Any,
    static_b2_plan: Any = None,
    observed_execution_ids: tuple[str, ...] = (),
    failure_evidence: Any = None,
    run_id: str | None = None,
    semantic_evidence: Any = None,
) -> ProvenanceVerification:
    """Check each reached boundary, allowing only attributable early failures."""
    failures: list[ProvenanceFailure] = []
    if record is None:
        failures.append(ProvenanceFailure.PROVENANCE_RECORD_MISSING)
    elif record.schema_version != PROVENANCE_SCHEMA_VERSION:
        failures.append(ProvenanceFailure.SCHEMA_UNSUPPORTED)
    if record and run_id is not None and record.run_id != run_id:
        failures.append(ProvenanceFailure.RUN_ID_MISMATCH)
    frozen = _object(frozen_input)
    if not isinstance(frozen.get("instruction"), str) or not frozen["instruction"]:
        failures.append(ProvenanceFailure.INPUT_DIGEST_MISSING)
    elif record and record.input_digest != digest(frozen):
        failures.append(ProvenanceFailure.INPUT_DIGEST_MISMATCH)
    request = observed_request(request_record, request_observations)
    observed_failure = _object(failure_evidence)
    # The workload may fail at SUT startup before MI can mint any request.
    # This is a reached process boundary, not an invented accepted-plan chain.
    if (
        request_record is None
        and request_observations is None
        and (
            observed_failure.get("schema_version") == RUN_FAILURE_SCHEMA
            and observed_failure.get("run_id") == run_id
            and observed_failure.get("failure_owner") == "SUT_SYSTEM"
            and observed_failure.get("component")
            in {"controller", "node", "mission_service", "local_eaios"}
            and observed_failure.get("observation_source") == "scenario_process_boundary"
            and observed_failure.get("input_digest") == plan_digest(frozen)
            and observed_failure.get("request_id") is None
            and observed_failure.get("mission_id") is None
            and observed_failure.get("group_id") is None
            and observed_failure.get("reason")
        )
    ):
        if record and (
            record.request_record_digest != digest(request_record)
            or record.observations_digest != digest(request_observations)
            or record.failure_evidence_digest != digest(observed_failure)
            or any(
                (
                    record.accepted_plan_digest,
                    record.mi_run_identity,
                    record.mission_id,
                    record.controller_submission_identity,
                    record.submission_evidence_digest,
                    record.execution_identity,
                )
            )
        ):
            failures.append(ProvenanceFailure.FAILURE_EVIDENCE_MISMATCH)
        return ProvenanceVerification(not failures, tuple(failures), None, False)
    failures.extend(_check_semantic_evidence(semantic_evidence, request, frozen, record, run_id))
    if not request.get("request_id") or not request.get("mission_id"):
        failures.append(ProvenanceFailure.MI_RUN_MISSING)
    if record and (
        record.mi_run_identity != request.get("request_id")
        or record.mission_id != request.get("mission_id")
    ):
        failures.append(ProvenanceFailure.MI_RUN_MISSING)
    if record and (
        record.request_record_digest != digest(request_record)
        or record.observations_digest != digest(request_observations)
    ):
        failures.append(ProvenanceFailure.REQUEST_RECORD_MISMATCH)
    if "submission_evidence" not in request:
        failures.append(ProvenanceFailure.SUBMISSION_EVIDENCE_MISMATCH)
    dialogue = request.get("dialogue")
    first = _object(dialogue[0]) if isinstance(dialogue, list) and dialogue else {}
    if (
        first.get("kind") != "Instruction"
        or first.get("speaker") != "User"
        or first.get("content") != frozen.get("instruction")
    ):
        failures.append(ProvenanceFailure.MI_GENERATION_EVIDENCE_MISSING)
    failure = request_failure(request)
    early = bool(failure and failure["stage"] != "controller_submission")
    failures.extend(_check_draft(request, early))
    plan = _object(request.get("plan"))
    computed = digest(plan) if plan else None
    sent = _object(request.get("submission_evidence"))
    if record:
        if record.accepted_plan_digest != (computed or ""):
            failures.append(ProvenanceFailure.PLAN_DIGEST_MISMATCH)
        if record.submission_evidence_digest != (digest(sent) if sent else ""):
            failures.append(ProvenanceFailure.SUBMISSION_EVIDENCE_MISMATCH)
        if record.failure_evidence_digest != (digest(observed_failure) if observed_failure else ""):
            failures.append(ProvenanceFailure.FAILURE_EVIDENCE_MISMATCH)
        if record.controller_submission_identity != (sent.get("controller_group_id") or ""):
            failures.append(ProvenanceFailure.CONTROLLER_SUBMISSION_IDENTITY_MISMATCH)
        if record.execution_identity != ",".join(sorted(set(observed_execution_ids))):
            failures.append(ProvenanceFailure.EXECUTION_IDENTITY_MISMATCH)
    if not early:
        failures.extend(
            _check_submission(
                request, _object(controller_submission), observed_execution_ids, observed_failure
            )
        )
    b2 = _object(static_b2_plan)

    def without_identity(document: dict[str, Any]) -> dict[str, Any]:
        """Compare B2 structure without treating a renamed Mission as generation."""
        return {**document, "mission": {**_object(document.get("mission")), "id": ""}}

    static = bool(plan and b2 and digest(without_identity(plan)) == digest(without_identity(b2)))
    if static and any(
        failure in failures
        for failure in (
            ProvenanceFailure.MI_GENERATION_EVIDENCE_MISSING,
            ProvenanceFailure.MI_RUN_PLAN_DIGEST_MISMATCH,
        )
    ):
        failures.append(ProvenanceFailure.STATIC_B2_PLAN_EQUALITY)
    return ProvenanceVerification(
        not failures,
        tuple(dict.fromkeys(failures)),
        computed,
        static,
        (semantic_goal_diagnostic(semantic_evidence, plan),),
    )


def build_b1_provenance_record(
    *,
    run_id: str,
    frozen_input_path: Path,
    request_record_path: Path,
    controller_mission_path: Path,
    controller_events_path: Path | None = None,
    execution_attempts_path: Path,
    shared_world_summary_path: Path,
    semantic_evidence_path: Path | None = None,
    failure_evidence_path: Path | None = None,
    request_observations_path: Path | None = None,
) -> B1ProvenanceRecord:
    """Link archived evidence without granting admission or assuming episode completion."""
    frozen = load_document(frozen_input_path)
    public_request = load_document(request_record_path)
    observations = load_document(
        request_observations_path or request_record_path.with_name("b1-request-observations.json")
    )
    request = observed_request(public_request, observations)
    sent = _object(request.get("submission_evidence"))
    mission_id = str(request.get("mission_id", ""))
    group_id = str(sent.get("controller_group_id") or "")
    scoped = scoped_execution_evidence(
        mission_id,
        group_id,
        load_document(controller_mission_path),
        load_document(controller_events_path) if controller_events_path else None,
        load_document(execution_attempts_path),
    )
    failure = load_document(failure_evidence_path) if failure_evidence_path else None
    semantic = load_document(semantic_evidence_path) if semantic_evidence_path else None
    return B1ProvenanceRecord(
        run_id=run_id,
        input_digest=digest(frozen),
        mi_run_identity=str(request.get("request_id", "")),
        accepted_plan_digest=digest(request["plan"]) if request.get("plan") else "",
        mission_id=mission_id,
        controller_submission_identity=group_id,
        execution_identity=",".join(scoped["execution_ids"]),
        request_record_digest=digest(public_request),
        observations_digest=digest(observations),
        submission_evidence_digest=digest(sent) if sent else "",
        failure_evidence_digest=digest(failure) if failure else "",
        semantic_evidence_digest=(
            str(semantic.get("digest")) if isinstance(semantic, dict) else ""
        ),
        benchmark_evidence_digest=digest_document(shared_world_summary_path),
    )


def write_b1_provenance(record: B1ProvenanceRecord, path: Path) -> None:
    """Persist evidence links before verification and population admission."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record.to_json(), indent=2) + "\n", encoding="utf-8")
