"""Formal B1 provenance chain and population admission.

A Formal B1 run must carry a machine-verifiable chain from the frozen
high-level input identity, through the Mission Intelligence invocation and the
accepted MissionPlan digest, to the Controller submission identity and the
Habitat benchmark evidence identity. String equality with the static B2
fixture is never provenance: a copied B2 plan with a new MissionId must fail
unless a genuine MI invocation produced the accepted plan.

Digests use SHA-256 over a fixed JSON canonicalization (sorted keys, no
insignificant whitespace, UTF-8, ``ensure_ascii=False``) so every link is
byte-stable across platforms.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

PROVENANCE_SCHEMA_VERSION = "roboguide.e1.b1-provenance/v0.1"


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize one value with the fixed provenance canonicalization."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def _as_mapping(value: Any) -> dict[str, Any] | None:
    """Narrow one decoded value to a string-keyed mapping or None."""
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        return None
    return value


def digest(value: Any) -> str:
    """Return the hex SHA-256 digest of one canonicalized value."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def digest_document(path: Path) -> str | None:
    """Digest one JSON document from disk, or None when absent/malformed."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return digest(value)


class ProvenanceFailure(StrEnum):
    """Machine-stable provenance verification failures."""

    INPUT_DIGEST_MISSING = "input_digest_missing"
    INPUT_DIGEST_MISMATCH = "input_digest_mismatch"
    MI_RUN_MISSING = "mi_run_missing"
    PLAN_MISSING = "plan_missing"
    PLAN_DIGEST_MISSING = "plan_digest_missing"
    PLAN_DIGEST_MISMATCH = "plan_digest_mismatch"
    MI_RUN_PLAN_DIGEST_MISMATCH = "mi_run_plan_digest_mismatch"
    CONTROLLER_MISSION_MISSING = "controller_mission_missing"
    CONTROLLER_PLAN_MISMATCH = "controller_plan_mismatch"
    BENCHMARK_EVIDENCE_MISSING = "benchmark_evidence_missing"
    BENCHMARK_IDENTITY_MISMATCH = "benchmark_identity_mismatch"
    STATIC_B2_PLAN_EQUALITY = "static_b2_plan_equality"
    PROVENANCE_RECORD_MISSING = "provenance_record_missing"
    CONTROLLER_SUBMISSION_IDENTITY_MISMATCH = "controller_submission_identity_mismatch"
    EXECUTION_IDENTITY_MISMATCH = "execution_identity_mismatch"
    CONTROLLER_GROUP_ID_MISSING = "controller_group_id_missing"
    EXECUTION_ATTEMPTS_EMPTY = "execution_attempts_empty"
    MI_GENERATION_EVIDENCE_MISSING = "mi_generation_evidence_missing"
    CONTROLLER_TASK_REGISTRATION_MISMATCH = "controller_task_registration_mismatch"


@dataclass(frozen=True, slots=True)
class B1ProvenanceRecord:
    """One run's provenance artifact fields.

    Fields are versioned: later semantic-ingress work may add canonical
    semantic input and grounding-context digest fields without breaking this
    format.
    """

    run_id: str
    input_digest: str
    mi_run_identity: str
    accepted_plan_digest: str
    mission_id: str
    controller_submission_identity: str
    execution_identity: str
    benchmark_evidence_digest: str
    schema_version: str = PROVENANCE_SCHEMA_VERSION

    def to_json(self) -> dict[str, str]:
        """Serialize the provenance artifact."""
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "input_digest": self.input_digest,
            "mi_run_identity": self.mi_run_identity,
            "accepted_plan_digest": self.accepted_plan_digest,
            "mission_id": self.mission_id,
            "controller_submission_identity": self.controller_submission_identity,
            "execution_identity": self.execution_identity,
            "benchmark_evidence_digest": self.benchmark_evidence_digest,
        }


def _structure_digest_ignoring_mission_id(plan: dict[str, Any] | None) -> str | None:
    """Digest a plan with ``mission.id`` blanked.

    The B2 forgery pattern copies the static fixture and changes only the
    MissionId, so byte-level digests differ while the structure is identical.
    Blanking only the mission identity field keeps the comparison exact for
    every other byte while ignoring exactly the field the forger changed.
    """
    if plan is None:
        return None
    redacted = dict(plan)
    mission = redacted.get("mission")
    if isinstance(mission, dict):
        redacted_mission = dict(mission)
        redacted_mission["id"] = ""
        redacted["mission"] = redacted_mission
    return digest(redacted)


@dataclass(frozen=True, slots=True)
class ProvenanceVerification:
    """Outcome of verifying one provenance chain against run evidence.

    Attributes:
        passed: Whether every link matched.
        failures: Machine-stable failure identifiers.
        plan_digest: The accepted-plan digest computed from evidence.
        is_static_b2_plan: Whether the accepted plan is byte-equal (in
            canonical form) to the B2 static fixture.
    """

    passed: bool
    failures: tuple[ProvenanceFailure, ...]
    plan_digest: str | None
    is_static_b2_plan: bool


def verify_b1_provenance(
    *,
    record: B1ProvenanceRecord | None,
    frozen_input: Any,
    request_record: Any,
    controller_submission: Any,
    benchmark_evidence: Any,
    static_b2_plan: Any,
    observed_execution_ids: tuple[str, ...] = (),
    instruction_text: str | None = None,
) -> ProvenanceVerification:
    """Verify the full B1 provenance chain against run evidence.

    The Controller never exposes the submitted plan document; the submission
    identity is therefore a *receipt*: the accepted-plan digest (from the MI
    record, the only plan that was POSTed), the Controller-observed
    mission_id + group_id, and the set of task ids the Controller actually
    registered (structured execution evidence). A mission status view is
    never digest-compared against a MissionPlan.

    Args:
        record: The claimed provenance artifact; mandatory for a valid run.
        frozen_input: The actual frozen input document used by the run.
        request_record: The MI request record (request_id, plan, draft
            digest/review evidence, dialogue).
        controller_submission: Controller-observed receipt mapping with
            ``mission_id``, ``group_id``, and ``registered_task_ids``.
        benchmark_evidence: The shared-world benchmark summary document.
        static_b2_plan: The static B2 fixture plan (forgery reference).
        observed_execution_ids: Execution identities observed in Node
            attempt evidence; required non-empty and matching the record.
        instruction_text: The instruction string from the frozen input; the
            MI dialogue's first user turn must equal it exactly.

    Returns:
        The verification result. Genuine MI invocation is decided inside from
        structured MI generation evidence, never from a lifecycle enum.
    """

    def mapping(value: Any) -> dict[str, Any] | None:
        """Narrow one decoded value to a string-keyed mapping or None."""
        if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
            return None
        return value

    failures: list[ProvenanceFailure] = []
    if record is None:
        failures.append(ProvenanceFailure.PROVENANCE_RECORD_MISSING)

    input_record = mapping(frozen_input)
    if input_record is None:
        failures.append(ProvenanceFailure.INPUT_DIGEST_MISSING)
        input_digest: str | None = None
    else:
        input_digest = digest(input_record)
        if record is not None and record.input_digest != input_digest:
            failures.append(ProvenanceFailure.INPUT_DIGEST_MISMATCH)

    request = mapping(request_record)
    plan_digest: str | None = None
    genuine_mi = False
    if request is None or not isinstance(request.get("request_id"), str):
        failures.append(ProvenanceFailure.MI_RUN_MISSING)
    else:
        if record is not None and record.mi_run_identity != request["request_id"]:
            failures.append(ProvenanceFailure.MI_RUN_MISSING)
        plan_mapping = mapping(request.get("plan"))
        if plan_mapping is None:
            failures.append(ProvenanceFailure.PLAN_MISSING)
        else:
            plan_digest = digest(plan_mapping)
        # Genuine MI generation evidence: the MI pipeline must have produced
        # and accepted this draft (draft digest/revision or review evidence)
        # and the first dialogue user turn must equal the actual instruction.
        has_draft_evidence = isinstance(request.get("draft_digest"), str) or (
            isinstance(request.get("review_history"), list) and bool(request["review_history"])
        )
        dialogue = request.get("dialogue")
        first_user = None
        if isinstance(dialogue, list) and dialogue:
            turn = mapping(dialogue[0])
            if turn is not None and turn.get("kind") in ("instruction", "Instruction"):
                first_user = turn.get("content")
        instruction_ok = instruction_text is None or (
            isinstance(first_user, str) and first_user == instruction_text
        )
        genuine_mi = (
            plan_digest is not None
            and has_draft_evidence
            and instruction_ok
            and (record is None or record.mi_run_identity == request["request_id"])
        )
        if not genuine_mi and plan_digest is not None:
            failures.append(ProvenanceFailure.MI_GENERATION_EVIDENCE_MISSING)

    if record is not None:
        if plan_digest is None:
            failures.append(ProvenanceFailure.PLAN_DIGEST_MISSING)
        elif record.accepted_plan_digest != plan_digest:
            failures.append(ProvenanceFailure.PLAN_DIGEST_MISMATCH)

    # Controller submission receipt: identity + structured task registration.
    submission = mapping(controller_submission)
    controller_mission_id = submission.get("mission_id") if submission else None
    if not isinstance(controller_mission_id, str) or not controller_mission_id:
        failures.append(ProvenanceFailure.CONTROLLER_MISSION_MISSING)
    elif record is not None and controller_mission_id != record.mission_id:
        failures.append(ProvenanceFailure.CONTROLLER_MISSION_MISSING)
    elif request is not None and str(request.get("mission_id", "")) != controller_mission_id:
        failures.append(ProvenanceFailure.CONTROLLER_MISSION_MISSING)

    controller_group_id = submission.get("group_id") if submission else None
    if not isinstance(controller_group_id, str) or not controller_group_id:
        # Gap 2: a missing group id is a failure, never a skipped comparison.
        failures.append(ProvenanceFailure.CONTROLLER_GROUP_ID_MISSING)
    elif record is not None and record.controller_submission_identity != controller_group_id:
        failures.append(ProvenanceFailure.CONTROLLER_SUBMISSION_IDENTITY_MISMATCH)

    registered_raw = submission.get("registered_task_ids") if submission else None
    registered_ids = (
        {tid for tid in registered_raw if isinstance(tid, str)}
        if isinstance(registered_raw, list)
        else set()
    )
    plan_task_ids: set[str] = set()
    if (
        request is not None
        and isinstance(request.get("plan"), dict)
        and isinstance(request["plan"].get("tasks"), list)
    ):
        plan_task_ids = {
            task["id"]
            for task in request["plan"]["tasks"]
            if isinstance(task, dict) and isinstance(task.get("id"), str)
        }
    if not plan_task_ids or registered_ids != plan_task_ids:
        failures.append(ProvenanceFailure.CONTROLLER_TASK_REGISTRATION_MISMATCH)

    if not observed_execution_ids:
        # Gap 2: empty execution attempt evidence is a failure.
        failures.append(ProvenanceFailure.EXECUTION_ATTEMPTS_EMPTY)
    elif record is not None:
        observed_set = frozenset(observed_execution_ids)
        recorded_set = frozenset(record.execution_identity.split(","))
        if recorded_set != observed_set:
            failures.append(ProvenanceFailure.EXECUTION_IDENTITY_MISMATCH)

    benchmark = mapping(benchmark_evidence)
    benchmark_digest = digest(benchmark) if benchmark is not None else None
    if benchmark is None:
        failures.append(ProvenanceFailure.BENCHMARK_EVIDENCE_MISSING)
    elif record is not None and benchmark_digest != record.benchmark_evidence_digest:
        failures.append(ProvenanceFailure.BENCHMARK_IDENTITY_MISMATCH)

    b2_mapping = mapping(static_b2_plan)
    request_plan = mapping(request.get("plan")) if request else None
    is_static_b2 = (
        b2_mapping is not None
        and request_plan is not None
        and _structure_digest_ignoring_mission_id(b2_mapping)
        == _structure_digest_ignoring_mission_id(request_plan)
    )
    if is_static_b2 and not genuine_mi:
        failures.append(ProvenanceFailure.STATIC_B2_PLAN_EQUALITY)

    return ProvenanceVerification(
        passed=not failures,
        failures=tuple(failures),
        plan_digest=plan_digest,
        is_static_b2_plan=is_static_b2,
    )


@dataclass(frozen=True, slots=True)
class FormalPopulationAdmission:
    """Formal population admission decision for one B1 run.

    Attributes:
        valid_for_formal_population: Whether the run enters formal statistics.
        provenance_passed: Provenance chain verdict.
        benchmark_tri_state: The tri-state benchmark outcome.
        valid_run: Run-validity classification.
        invalid_reasons: Machine-stable reasons when excluded.
    """

    valid_for_formal_population: bool
    provenance_passed: bool
    benchmark_tri_state: str
    valid_run: str
    invalid_reasons: tuple[str, ...] = field(default_factory=tuple)


def admit_to_formal_population(
    *,
    provenance_passed: bool,
    provenance_failures: tuple[ProvenanceFailure, ...],
    benchmark_tri_state: str,
    valid_run: str,
    valid_for_benchmark_population: bool,
) -> FormalPopulationAdmission:
    """Combine provenance and evidence gates into one admission decision.

    A run enters the formal population only when provenance passed, the run
    is valid, and the benchmark outcome is an authoritative boolean. Invalid
    runs keep their evidence and their machine-stable exclusion reasons.
    """
    reasons: list[str] = [failure.value for failure in provenance_failures]
    if not provenance_passed and not reasons:
        reasons.append("provenance_failed")
    if not valid_for_benchmark_population:
        reasons.append(f"run_validity_{valid_run}")
        if benchmark_tri_state == "BENCHMARK_UNAVAILABLE":
            reasons.append("benchmark_outcome_unavailable")
    admitted = provenance_passed and valid_for_benchmark_population
    return FormalPopulationAdmission(
        valid_for_formal_population=admitted,
        provenance_passed=provenance_passed,
        benchmark_tri_state=benchmark_tri_state,
        valid_run=valid_run,
        invalid_reasons=tuple(reasons),
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
) -> B1ProvenanceRecord:
    """Build one B1 provenance record from persisted run artifacts.

    Every digest is computed over the file's canonical JSON form using the
    fixed canonicalization, so the record is reproducible from the archived
    evidence alone. The controller submission identity is the Controller
    group id observed in the mission status view, cross-checkable against
    ExecutionGroupCreated evidence when an events file is supplied.
    """

    def load(path: Path) -> Any:
        """Load one JSON document or return None."""
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    frozen_input = load(frozen_input_path)
    if frozen_input is None:
        raise ValueError(f"frozen B1 input is missing or malformed: {frozen_input_path}")
    request_record = load(request_record_path)
    if request_record is None:
        raise ValueError(f"MI request record is missing: {request_record_path}")
    controller_mission = load(controller_mission_path)
    attempts = load(execution_attempts_path)
    benchmark = load(shared_world_summary_path)

    input_digest = digest(frozen_input)
    mi_run_identity = str(request_record.get("request_id", ""))
    mission_id = str(request_record.get("mission_id", ""))
    plan = _as_mapping(request_record.get("plan"))
    accepted_plan_digest = digest(plan) if plan is not None else ""
    controller_group = str((controller_mission or {}).get("group_id", ""))
    if not controller_group and controller_events_path is not None:
        events = load(controller_events_path)
        for event in (events or {}).get("events", []):
            payload = (event or {}).get("payload", {})
            created = payload.get("ExecutionGroupCreated")
            if isinstance(created, dict) and isinstance(created.get("group_id"), str):
                controller_group = created["group_id"]
                break
    controller_submission_identity = controller_group
    attempts_list = (attempts or {}).get("attempts", [])
    execution_ids = sorted(
        str(a.get("execution_id", ""))
        for a in attempts_list
        if isinstance(a, dict) and a.get("execution_id")
    )
    execution_identity = ",".join(execution_ids)
    benchmark_digest = digest(benchmark) if benchmark is not None else ""

    return B1ProvenanceRecord(
        run_id=run_id,
        input_digest=input_digest,
        mi_run_identity=mi_run_identity,
        accepted_plan_digest=accepted_plan_digest,
        mission_id=mission_id,
        controller_submission_identity=controller_submission_identity,
        execution_identity=execution_identity,
        benchmark_evidence_digest=benchmark_digest,
    )


def write_b1_provenance(record: B1ProvenanceRecord, path: Path) -> None:
    """Write one provenance artifact as JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record.to_json(), indent=2) + "\n", encoding="utf-8")
