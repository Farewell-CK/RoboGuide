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
    controller_mission_id: str | None,
    controller_plan: Any,
    benchmark_evidence: Any,
    static_b2_plan: Any,
) -> ProvenanceVerification:
    """Verify the full B1 provenance chain against run evidence.

    Args:
        record: The claimed provenance artifact for the run.
        frozen_input: The frozen high-level input document.
        request_record: The Mission Service request record (the MI run
            evidence containing the accepted plan).
        controller_mission_id: The MissionId the Controller accepted.
        controller_plan: The plan document the Controller executed.
        benchmark_evidence: The shared-world benchmark summary document.
        static_b2_plan: The static B2 fixture plan (forgery reference).

    Returns:
        The verification result. A copied static B2 plan (regardless of
        MissionId) fails with ``STATIC_B2_PLAN_EQUALITY`` unless genuine MI
        provenance distinguishes it; genuine MI plans with different
        TaskIds pass when the chain matches.
    """

    def mapping(value: Any) -> dict[str, Any] | None:
        """Narrow one decoded value to a string-keyed mapping or None."""
        if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
            return None
        return value

    failures: list[ProvenanceFailure] = []

    input_record = mapping(frozen_input)
    if input_record is None:
        failures.append(ProvenanceFailure.INPUT_DIGEST_MISSING)
        input_digest: str | None = None
    else:
        input_digest = digest(input_record)
        if record is not None and record.input_digest != input_digest:
            failures.append(ProvenanceFailure.INPUT_DIGEST_MISMATCH)

    request = mapping(request_record)
    if request is None or not isinstance(request.get("request_id"), str):
        failures.append(ProvenanceFailure.MI_RUN_MISSING)
        plan_digest: str | None = None
        request_plan_digest: str | None = None
    else:
        if record is not None and record.mi_run_identity != request["request_id"]:
            failures.append(ProvenanceFailure.MI_RUN_MISSING)
        plan = request.get("plan")
        plan_mapping = mapping(plan)
        if plan_mapping is None:
            failures.append(ProvenanceFailure.PLAN_MISSING)
            plan_digest = None
        else:
            plan_digest = digest(plan_mapping)
        recorded_plan_digest = request.get("accepted_plan_digest")
        request_plan_digest = (
            recorded_plan_digest if isinstance(recorded_plan_digest, str) else None
        )
        if request_plan_digest is None and plan_digest is not None:
            # The request record may carry the digest explicitly (preferred) —
            # when absent, the plan body digest becomes the MI-run digest.
            request_plan_digest = plan_digest

    if record is not None:
        if plan_digest is None:
            failures.append(ProvenanceFailure.PLAN_DIGEST_MISSING)
        elif record.accepted_plan_digest != plan_digest:
            failures.append(ProvenanceFailure.PLAN_DIGEST_MISMATCH)
        if (
            request_plan_digest is not None
            and plan_digest is not None
            and request_plan_digest != plan_digest
        ):
            failures.append(ProvenanceFailure.MI_RUN_PLAN_DIGEST_MISMATCH)

    if not isinstance(controller_mission_id, str) or not controller_mission_id:
        failures.append(ProvenanceFailure.CONTROLLER_MISSION_MISSING)
    elif record is not None and controller_mission_id != record.mission_id:
        failures.append(ProvenanceFailure.CONTROLLER_MISSION_MISSING)

    controller_plan_mapping = mapping(controller_plan)
    if controller_plan_mapping is None:
        failures.append(ProvenanceFailure.CONTROLLER_PLAN_MISMATCH)
    elif plan_digest is not None and digest(controller_plan_mapping) != plan_digest:
        failures.append(ProvenanceFailure.CONTROLLER_PLAN_MISMATCH)

    benchmark = mapping(benchmark_evidence)
    benchmark_digest = digest(benchmark) if benchmark is not None else None
    if benchmark is None:
        failures.append(ProvenanceFailure.BENCHMARK_EVIDENCE_MISSING)
    elif record is not None and benchmark_digest != record.benchmark_evidence_digest:
        failures.append(ProvenanceFailure.BENCHMARK_IDENTITY_MISMATCH)

    b2_mapping = mapping(static_b2_plan)
    is_static_b2 = (
        b2_mapping is not None
        and plan_digest is not None
        and _structure_digest_ignoring_mission_id(b2_mapping)
        == _structure_digest_ignoring_mission_id(mapping(controller_plan))
    )
    if is_static_b2:
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
