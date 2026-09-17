"""F-06 deterministic tests for benchmark evidence tri-state semantics."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from roboguide_eval.benchmark_evidence import (
    BenchmarkOutcome,
    RunValidity,
    assess_benchmark_evidence,
    classify_run_validity,
)


def _outcome(state: str = "COMPLETED", skill: bool = True, **extra: Any) -> dict[str, Any]:
    """Build one agent local outcome document."""
    return {"state": state, "local_skill_completed": skill, **extra}


def _summary(
    pddl: Any = None,
    outcomes: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build one shared-world authority summary document."""
    return {
        "official_pddl_success": pddl,
        "outcomes": outcomes if outcomes is not None else {},
        **extra,
    }


def test_f06_summary_missing_yields_unavailable() -> None:
    """A missing authority document never produces a benchmark boolean."""
    assessment = assess_benchmark_evidence(None)
    assert assessment.outcome is BenchmarkOutcome.UNAVAILABLE
    assert not assessment.authority_document_present
    assert assessment.local_skill_completed is None
    assert assessment.local_agent_failure is None


def test_f06_malformed_summary_yields_unavailable() -> None:
    """A non-mapping authority document is treated as missing, not false."""
    assessment = assess_benchmark_evidence([1, 2, 3])
    assert assessment.outcome is BenchmarkOutcome.UNAVAILABLE
    assert assessment.outcome_reason == "authority_document_missing_or_malformed"


def test_f06_outcomes_empty_yields_unavailable_skill_not_true() -> None:
    """Empty outcomes must not produce all([])==True skill semantics."""
    assessment = assess_benchmark_evidence(_summary(pddl=True, outcomes={}))
    # pddl is authoritative even when outcomes are absent for the benchmark
    # outcome itself, but population-derived values stay unavailable.
    assert assessment.outcome is BenchmarkOutcome.TRUE
    assert assessment.local_skill_completed is None
    assert assessment.local_agent_failure is None
    assert assessment.numeric_aggregates == {}
    assert assessment.observed_agents == ()


def test_f06_only_agent0_outcome_is_incomplete() -> None:
    """A partial outcome population never derives population booleans."""
    summary = _summary(pddl=False, outcomes={"0": _outcome()})
    assessment = assess_benchmark_evidence(summary)
    assert assessment.outcome is BenchmarkOutcome.FALSE
    assert assessment.observed_agents == ("0",)
    assert assessment.local_skill_completed is None
    assert assessment.local_agent_failure is None
    assert assessment.numeric_aggregates == {}


def test_f06_complete_population_true() -> None:
    """A complete two-agent population with pddl true derives real values."""
    summary = _summary(
        pddl=True,
        outcomes={
            "0": _outcome(skill=True, local_llm_calls=2, local_tokens=100),
            "1": _outcome(skill=True, local_llm_calls=3, local_tokens=150),
        },
    )
    assessment = assess_benchmark_evidence(summary)
    assert assessment.outcome is BenchmarkOutcome.TRUE
    assert assessment.local_skill_completed is True
    assert assessment.local_agent_failure is False
    assert assessment.numeric_aggregates["local_llm_calls"] == 5
    assert assessment.numeric_aggregates["local_tokens"] == 250


def test_f06_complete_population_false() -> None:
    """An authoritative pddl false with complete outcomes stays FALSE."""
    summary = _summary(
        pddl=False,
        outcomes={
            "0": _outcome(state="FAILED", skill=False),
            "1": _outcome(skill=True),
        },
    )
    assessment = assess_benchmark_evidence(summary)
    assert assessment.outcome is BenchmarkOutcome.FALSE
    assert assessment.local_skill_completed is False
    assert assessment.local_agent_failure is True


def test_f06_pddl_not_bool_is_unavailable() -> None:
    """A non-bool pddl_success field is unavailable, never coerced."""
    for bad in ("true", 1, None):
        assessment = assess_benchmark_evidence(_summary(pddl=bad))
        assert assessment.outcome is BenchmarkOutcome.UNAVAILABLE
        assert assessment.outcome_reason == "official_pddl_success_missing_or_not_bool"


def test_f06_skill_field_not_bool_is_unavailable() -> None:
    """A non-bool skill field keeps the population-derived boolean unknown."""
    summary = _summary(
        pddl=True,
        outcomes={
            "0": _outcome(skill=True),
            "1": {"state": "COMPLETED", "local_skill_completed": "yes"},
        },
    )
    assessment = assess_benchmark_evidence(summary)
    assert assessment.outcome is BenchmarkOutcome.TRUE
    assert assessment.local_skill_completed is None


def test_f06_validity_classification_matrix(tmp_path: Path) -> None:
    """Run validity is classified independently of the benchmark outcome."""
    # Complete evidence, valid run.
    ok = classify_run_validity(
        authority_present=True,
        episode_started=True,
        episode_terminated=True,
        infrastructure_failure=False,
        mission_status="Completed",
        process_status="completed",
        benchmark_outcome=BenchmarkOutcome.TRUE,
    )
    assert ok.validity is RunValidity.VALID_RUN
    assert ok.valid_for_benchmark_population is True

    # Missing authority document -> invalid infra.
    missing = classify_run_validity(
        authority_present=False,
        episode_started=False,
        episode_terminated=None,
        infrastructure_failure=None,
        mission_status=None,
        process_status=None,
        benchmark_outcome=BenchmarkOutcome.UNAVAILABLE,
    )
    assert missing.validity is RunValidity.INVALID_INFRA
    assert not missing.valid_for_benchmark_population
    assert "authority_document_missing" in missing.reasons

    # Initialization failure (episode never started, authority absent).
    init = classify_run_validity(
        authority_present=False,
        episode_started=False,
        episode_terminated=None,
        infrastructure_failure=True,
        mission_status=None,
        process_status="failed",
        benchmark_outcome=BenchmarkOutcome.UNAVAILABLE,
    )
    assert init.validity is RunValidity.INVALID_INFRA
    assert "infrastructure_failure_explicit" in init.reasons

    # Episode started but no authoritative terminal fact.
    unterm = classify_run_validity(
        authority_present=True,
        episode_started=True,
        episode_terminated=None,
        infrastructure_failure=None,
        mission_status="Running",
        process_status=None,
        benchmark_outcome=BenchmarkOutcome.UNAVAILABLE,
    )
    assert unterm.validity is RunValidity.INVALID_INFRA
    assert "termination_fact_missing" in unterm.reasons

    # System failure keeps evidence but stays out of the population.
    sys_fail = classify_run_validity(
        authority_present=True,
        episode_started=True,
        episode_terminated=True,
        infrastructure_failure=False,
        mission_status="Failed",
        process_status="completed",
        benchmark_outcome=BenchmarkOutcome.FALSE,
    )
    assert sys_fail.validity is RunValidity.SYSTEM_FAILURE
    assert not sys_fail.valid_for_benchmark_population
