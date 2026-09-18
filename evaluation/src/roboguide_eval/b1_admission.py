"""The single Formal B1 population decision; benchmark absence is not invalidity."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from roboguide_eval.benchmark_evidence import BenchmarkOutcome, RunValidity

ADMISSION_SCHEMA = "roboguide.e1.b1-admission/v0.1"


class FailureOwner(StrEnum):
    """Attribute failures from explicit boundary facts, never missing Habitat output."""

    EXTERNAL_INFRA = "EXTERNAL_INFRA"
    SUT_SYSTEM = "SUT_SYSTEM"
    MODEL = "MODEL"
    BENCHMARK_AUTHORITY_UNAVAILABLE = "BENCHMARK_AUTHORITY_UNAVAILABLE"
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class B1RunAssessment:
    """Persist the canonical decision consumed by both verifier and Harness."""

    provenance_valid: bool
    valid_for_formal_population: bool
    benchmark_authority_available: bool
    valid_for_benchmark_population: bool
    benchmark_outcome: BenchmarkOutcome
    run_validity: RunValidity
    failure_owner: FailureOwner
    system_failure: bool
    model_failure: bool
    reasons: tuple[str, ...]
    schema_version: str = ADMISSION_SCHEMA

    def to_json(self) -> dict[str, Any]:
        """Expose orthogonal admission and outcome facts as versioned JSON."""
        return {**asdict(self), "reasons": list(self.reasons)}


def assess_b1_run(
    *,
    provenance_valid: bool,
    provenance_failures: tuple[str, ...],
    failure_owner: FailureOwner,
    benchmark_outcome: BenchmarkOutcome,
    failure_reasons: tuple[str, ...] = (),
) -> B1RunAssessment:
    """Apply the protocol truth table without selecting only surviving episodes."""
    formal = provenance_valid and failure_owner is not FailureOwner.EXTERNAL_INFRA
    available = benchmark_outcome is not BenchmarkOutcome.UNAVAILABLE
    reasons = list(provenance_failures) + list(failure_reasons)
    if not provenance_valid and not provenance_failures:
        reasons.append("provenance_invalid")
    if failure_owner is FailureOwner.EXTERNAL_INFRA:
        validity = RunValidity.INVALID_INFRA
        reasons.append("external_infrastructure_failure_explicit")
    elif not provenance_valid:
        validity = RunValidity.INVALID_PROVENANCE
    elif failure_owner is FailureOwner.SUT_SYSTEM:
        validity = RunValidity.SYSTEM_FAILURE
    elif failure_owner is FailureOwner.MODEL:
        validity = RunValidity.MODEL_FAILURE
    else:
        validity = RunValidity.VALID_RUN
    if not available:
        reasons.append("benchmark_authority_unavailable")
        if failure_owner is FailureOwner.NONE:
            failure_owner = FailureOwner.BENCHMARK_AUTHORITY_UNAVAILABLE
    return B1RunAssessment(
        provenance_valid=provenance_valid,
        valid_for_formal_population=formal,
        benchmark_authority_available=available,
        valid_for_benchmark_population=formal and available,
        benchmark_outcome=benchmark_outcome,
        run_validity=validity,
        failure_owner=failure_owner,
        system_failure=failure_owner is FailureOwner.SUT_SYSTEM,
        model_failure=failure_owner is FailureOwner.MODEL,
        reasons=tuple(dict.fromkeys(reasons)),
    )
