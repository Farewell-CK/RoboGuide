"""Run-local evidence extraction and shared verifier/Harness admission adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from roboguide_eval.b1_admission import FailureOwner, assess_b1_run
from roboguide_eval.b1_provenance import (
    PROVENANCE_SCHEMA_VERSION,
    RUN_FAILURE_SCHEMA,
    B1ProvenanceRecord,
    digest,
    load_document,
    observed_request,
    plan_digest,
    request_failure,
    scoped_execution_evidence,
    verify_b1_provenance,
)
from roboguide_eval.benchmark_evidence import BenchmarkOutcome, assess_benchmark_evidence

B1_VERDICT_SCHEMA = "roboguide.e1-b1-verdict/v0.2"
B1_FILES = (
    "b1-input-used.json",
    "b1-request-record.json",
    "b1-provenance.json",
    "b1-request-observations.json",
    "mission.json",
    "events.json",
    "execution-attempts.json",
    "run-failure.json",
    "evidence/shared-world-summary.json",
)


def _object(value: Any) -> dict[str, Any]:
    """Read optional mapping evidence without inventing fields."""
    return value if isinstance(value, dict) else {}


def scoped_run_failure(run: Path, raw: Any, request: dict[str, Any]) -> dict[str, Any]:
    """Require explicit failure ownership and current run/Mission/Group identity."""
    doc = _object(raw)
    if (
        doc.get("schema_version") != RUN_FAILURE_SCHEMA
        or doc.get("run_id") != run.name
        or not doc.get("reason")
    ):
        return {}
    owner = doc.get("failure_owner")
    if not isinstance(owner, str) or not isinstance(doc.get("component"), str):
        return {}
    if owner == FailureOwner.EXTERNAL_INFRA.value:
        return (
            doc
            if doc.get("component")
            in {"host", "harness", "evaluator", "external_provider", "environment"}
            else {}
        )
    if not request and owner == FailureOwner.SUT_SYSTEM.value:
        return (
            doc
            if (
                doc.get("component") in {"controller", "node", "mission_service", "local_eaios"}
                and doc.get("observation_source") == "scenario_process_boundary"
                and doc.get("input_digest")
                == plan_digest(load_document(run / "b1-input-used.json"))
            )
            else {}
        )
    sent = _object(request.get("submission_evidence"))
    if (
        owner not in {FailureOwner.SUT_SYSTEM.value, FailureOwner.MODEL.value}
        or doc.get("component")
        not in {"controller", "node", "mission_service", "local_eaios", "model"}
        or doc.get("request_id") != request.get("request_id")
        or doc.get("mission_id") != request.get("mission_id")
        or doc.get("group_id") != sent.get("controller_group_id")
    ):
        return {}
    return doc


def assess_b1_directory(run: Path) -> dict[str, Any]:
    """Verify archived execution evidence, then apply the one admission authority."""
    documents = {name: load_document(run / name) for name in B1_FILES}
    request = observed_request(
        documents["b1-request-record.json"], documents["b1-request-observations.json"]
    )
    sent = _object(request.get("submission_evidence"))
    scoped = scoped_execution_evidence(
        str(request.get("mission_id") or ""),
        str(sent.get("controller_group_id") or ""),
        documents["mission.json"],
        documents["events.json"],
        documents["execution-attempts.json"],
    )
    raw_failure = documents["run-failure.json"]
    failure = scoped_run_failure(run, raw_failure, request)
    record = B1ProvenanceRecord.from_json(documents["b1-provenance.json"])
    provenance = verify_b1_provenance(
        record=record,
        frozen_input=documents["b1-input-used.json"],
        request_record=documents["b1-request-record.json"],
        request_observations=documents["b1-request-observations.json"],
        controller_submission=scoped,
        observed_execution_ids=tuple(scoped["execution_ids"]),
        failure_evidence=failure,
        run_id=run.name,
    )
    failures = [item.value for item in provenance.failures]
    if raw_failure is not None and not failure:
        failures.append("failure_evidence_unscoped_or_malformed")
    mi_failure = request_failure(request)
    owner = FailureOwner.NONE
    reasons: tuple[str, ...] = ()
    if failure:
        owner = FailureOwner(failure["failure_owner"])
        reasons = (str(failure["reason"]),)
    elif mi_failure:
        owner = FailureOwner(mi_failure["failure_owner"])
        reasons = (f"mission_request_{mi_failure['stage']}_failed",)
    elif scoped["mission_status"] == "Failed":
        owner = FailureOwner.SUT_SYSTEM
        reasons = ("controller_mission_failed",)
    benchmark = assess_benchmark_evidence(documents["evidence/shared-world-summary.json"])
    benchmark_outcome = benchmark.outcome
    if (
        record
        and record.benchmark_evidence_digest
        and documents["evidence/shared-world-summary.json"] is not None
        and record.benchmark_evidence_digest
        != digest(documents["evidence/shared-world-summary.json"])
    ):
        benchmark_outcome = BenchmarkOutcome.UNAVAILABLE
        reasons += ("benchmark_link_mismatch",)
    admission = assess_b1_run(
        provenance_valid=not failures,
        provenance_failures=tuple(failures),
        failure_owner=owner,
        benchmark_outcome=benchmark_outcome,
        failure_reasons=reasons,
    )
    system_outcome = (
        "FAILURE"
        if admission.system_failure or admission.model_failure
        else "COMPLETED"
        if scoped["mission_status"] == "Completed"
        else "UNKNOWN"
    )
    return {
        "schema": B1_VERDICT_SCHEMA,
        "run_id": run.name,
        "evidence_digest": digest(documents),
        "provenance_schema": PROVENANCE_SCHEMA_VERSION,
        "protocol_provenance": "VALID" if admission.provenance_valid else "INVALID",
        "system_outcome": system_outcome,
        "admission": admission.to_json(),
        "checks": {
            "provenance_chain_passed": admission.provenance_valid,
            "valid_for_formal_population": admission.valid_for_formal_population,
            "valid_for_benchmark_population": admission.valid_for_benchmark_population,
        },
        "context": {
            "provenance_failures": failures,
            "controller_receipt": scoped,
            "benchmark_tri_state": benchmark_outcome.value,
            "benchmark_outcome_reason": benchmark.outcome_reason,
        },
        # PASS is explicitly protocol admission, never system or benchmark success.
        "verdict": "PASS" if admission.valid_for_formal_population else "FAIL",
        "verdict_basis": "formal_protocol_admission",
    }


def write_b1_verdict(run: Path) -> dict[str, Any]:
    """Persist the canonical decision after provenance verification."""
    verdict = assess_b1_directory(run)
    (run / "b1-verdict.json").write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
    return verdict


def consume_b1_verdict(run: Path, *, harness_start_failure: str | None = None) -> dict[str, Any]:
    """Consume the persisted gate only when it equals current canonical verification."""
    expected = assess_b1_directory(run)
    persisted = load_document(run / "b1-verdict.json")
    matches = persisted == expected
    if matches and harness_start_failure is None:
        return expected
    # Missing, edited or stale admission is an observable protocol failure.
    reasons = tuple(expected["admission"]["reasons"])
    if not matches:
        reasons += ("b1_admission_missing_stale_or_tampered",)
    if harness_start_failure is not None:
        reasons += ("harness_process_start_failed", harness_start_failure)
    admission = assess_b1_run(
        provenance_valid=matches and expected["admission"]["provenance_valid"],
        provenance_failures=reasons,
        failure_owner=(
            FailureOwner.EXTERNAL_INFRA
            if harness_start_failure is not None
            else FailureOwner(expected["admission"]["failure_owner"])
        ),
        benchmark_outcome=BenchmarkOutcome(expected["admission"]["benchmark_outcome"]),
    )
    expected["admission"] = admission.to_json()
    expected["protocol_provenance"] = "VALID" if admission.provenance_valid else "INVALID"
    expected["verdict"] = "FAIL"
    expected["checks"] = {
        "provenance_chain_passed": admission.provenance_valid,
        "valid_for_formal_population": admission.valid_for_formal_population,
        "valid_for_benchmark_population": admission.valid_for_benchmark_population,
    }
    return expected
