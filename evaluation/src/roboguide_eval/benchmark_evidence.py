"""Benchmark evidence tri-state authority and run-validity classification.

The official Habitat ``pddl_success`` is the only benchmark success authority.
A benchmark outcome may only become ``True``/``False`` when authoritative
Habitat evidence explicitly carries that boolean; missing, malformed,
or non-boolean authority stays ``UNAVAILABLE`` and never becomes a failure.
Formal observations need no benchmark authority; benchmark-rate denominators
are selected separately by the canonical provenance-aware admission policy.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# Agents one shared-world E1-I workload is expected to report outcomes for.
EXPECTED_SHARED_WORLD_AGENTS: tuple[str, ...] = ("0", "1")


class BenchmarkOutcome(StrEnum):
    """Tri-state benchmark outcome derived only from authoritative evidence."""

    TRUE = "BENCHMARK_TRUE"
    FALSE = "BENCHMARK_FALSE"
    UNAVAILABLE = "BENCHMARK_UNAVAILABLE"


class RunValidity(StrEnum):
    """Independent run-validity classification for formal population admission."""

    VALID_RUN = "VALID_RUN"
    INVALID_PROVENANCE = "INVALID_PROVENANCE"
    INVALID_INFRA = "INVALID_INFRA"
    SYSTEM_FAILURE = "SYSTEM_FAILURE"
    MODEL_FAILURE = "MODEL_FAILURE"


@dataclass(frozen=True, slots=True)
class BenchmarkEvidenceAssessment:
    """Complete tri-state assessment of one run's benchmark evidence.

    Attributes:
        outcome: Tri-state benchmark outcome (never a bare bool).
        outcome_reason: Machine-stable reason for the outcome class.
        local_skill_completed: Tri-state local-skill completeness over the
            expected agent population; ``None`` when incomplete/unavailable.
        local_agent_failure: True only when a present outcome proves a local
            failure; never derived from missing outcomes.
        numeric_aggregates: Per-field sums computed only when every expected
            agent reports an integer for that field; missing means omitted.
        expected_agents: Agent population the completeness checks used.
        observed_agents: Agents actually present in the authority document.
        authority_document_present: Whether the authority summary existed and
            parsed at all.
    """

    outcome: BenchmarkOutcome
    outcome_reason: str
    local_skill_completed: bool | None
    local_agent_failure: bool | None
    numeric_aggregates: Mapping[str, int]
    expected_agents: tuple[str, ...]
    observed_agents: tuple[str, ...]
    authority_document_present: bool


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    """Narrow one decoded value to a string-keyed mapping or None."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        return None
    return value


def _bool_field(value: Any) -> bool | None:
    """Return a strict bool or None; ints and strings never coerce."""
    return value if isinstance(value, bool) else None


def assess_benchmark_evidence(
    shared_world_summary: Any,
    expected_agents: tuple[str, ...] = EXPECTED_SHARED_WORLD_AGENTS,
) -> BenchmarkEvidenceAssessment:
    """Assess benchmark evidence with strict tri-state semantics.

    Args:
        shared_world_summary: The decoded shared-world authority document
            (``shared-world-summary.json`` content). ``None`` or a
            non-mapping value means the authority document is missing or
            malformed.
        expected_agents: Agent keys the workload must report local outcomes
            for before completeness-derived booleans may be produced.

    Returns:
        The complete assessment. The benchmark outcome is ``TRUE``/``FALSE``
        only when the authority document explicitly carries
        ``official_pddl_success`` as a bool; everything else — missing
        document, missing field, or non-bool field — is
        ``UNAVAILABLE``. ``local_skill_completed`` additionally requires
        every expected agent's outcome with a strict bool field, so an empty
        or partial population never yields ``all([]) is True``.
    """
    summary = _as_mapping(shared_world_summary)
    if summary is None:
        return BenchmarkEvidenceAssessment(
            outcome=BenchmarkOutcome.UNAVAILABLE,
            outcome_reason="authority_document_missing_or_malformed",
            local_skill_completed=None,
            local_agent_failure=None,
            numeric_aggregates={},
            expected_agents=expected_agents,
            observed_agents=(),
            authority_document_present=False,
        )

    outcomes_raw = _as_mapping(summary.get("outcomes")) or {}
    narrowed: dict[str, Mapping[str, Any]] = {}
    for agent, raw in outcomes_raw.items():
        mapping = _as_mapping(raw)
        if mapping is not None:
            narrowed[agent] = mapping
    outcomes: Mapping[str, Mapping[str, Any]] = narrowed
    try:
        observed = tuple(sorted(outcomes, key=int))
    except ValueError:
        # Local outcome metadata never overrides official pddl_success.
        observed = tuple(sorted(outcomes))

    pddl = _bool_field(summary.get("official_pddl_success"))
    if pddl is None:
        outcome = BenchmarkOutcome.UNAVAILABLE
        reason = "official_pddl_success_missing_or_not_bool"
    else:
        outcome = BenchmarkOutcome.TRUE if pddl else BenchmarkOutcome.FALSE
        reason = "official_pddl_success_authoritative"

    # Completeness gate: every expected agent must report an outcome before
    # any population-derived boolean may exist.
    population_complete = all(agent in outcomes for agent in expected_agents)
    if not population_complete:
        local_skill: bool | None = None
        local_failure: bool | None = None
        aggregates: dict[str, int] = {}
    else:
        skill_values = [
            _bool_field(outcomes[agent].get("local_skill_completed")) for agent in expected_agents
        ]
        local_skill = (
            all(value for value in skill_values)
            if all(value is not None for value in skill_values)
            else None
        )
        state_values = [outcomes[agent].get("state") for agent in expected_agents]
        local_failure = (
            any(state == "FAILED" for state in state_values)
            if all(isinstance(state, str) for state in state_values)
            else None
        )
        aggregates = {}
        for field in (
            "local_llm_calls",
            "local_tokens",
            "local_replans",
            "invalid_outputs",
            "send_request_count",
            "message_pipe_activity_count",
        ):
            values = [outcomes[agent].get(field) for agent in expected_agents]
            if all(isinstance(value, int) and not isinstance(value, bool) for value in values):
                aggregates[field] = sum(value for value in values if isinstance(value, int))

    return BenchmarkEvidenceAssessment(
        outcome=outcome,
        outcome_reason=reason,
        local_skill_completed=local_skill,
        local_agent_failure=local_failure,
        numeric_aggregates=aggregates,
        expected_agents=expected_agents,
        observed_agents=observed,
        authority_document_present=True,
    )


@dataclass(frozen=True, slots=True)
class RunValidityAssessment:
    """Run-validity classification for formal population admission.

    Attributes:
        validity: The independent validity class.
        valid_for_benchmark_population: Whether the run may enter formal
            success-rate statistics.
        reasons: Machine-stable reasons backing the classification.
    """

    validity: RunValidity
    valid_for_formal_population: bool
    benchmark_authority_available: bool
    valid_for_benchmark_population: bool
    system_failure: bool
    model_failure: bool | None
    reasons: tuple[str, ...]


def classify_run_validity(
    *,
    authority_present: bool,
    episode_started: bool,
    episode_terminated: bool | None,
    infrastructure_failure: bool | None,
    mission_status: str | None,
    process_status: str | None,
    benchmark_outcome: BenchmarkOutcome,
) -> RunValidityAssessment:
    """Adapt non-B1 callers to the canonical policy without guessing infrastructure.

    Formal B1 must use provenance-aware assess_b1_run instead. Historical
    completeness/episode arguments remain observational compatibility inputs.
    """
    from roboguide_eval.b1_admission import FailureOwner, assess_b1_run

    del authority_present, episode_started, episode_terminated, process_status
    owner = (
        FailureOwner.EXTERNAL_INFRA
        if infrastructure_failure is True
        else FailureOwner.SUT_SYSTEM
        if mission_status == "Failed"
        else FailureOwner.NONE
    )
    result = assess_b1_run(
        provenance_valid=True,
        provenance_failures=(),
        failure_owner=owner,
        benchmark_outcome=benchmark_outcome,
    )
    return RunValidityAssessment(
        validity=RunValidity(result.run_validity.value),
        valid_for_formal_population=result.valid_for_formal_population,
        benchmark_authority_available=result.benchmark_authority_available,
        valid_for_benchmark_population=result.valid_for_benchmark_population,
        system_failure=result.system_failure,
        model_failure=result.model_failure,
        reasons=result.reasons,
    )
