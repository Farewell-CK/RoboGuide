"""Benchmark evidence tri-state authority and run-validity classification.

The official Habitat ``pddl_success`` is the only benchmark success authority.
A benchmark outcome may only become ``True``/``False`` when authoritative
Habitat evidence explicitly carries that boolean; missing, malformed,
partial, or non-executed evidence stays ``UNAVAILABLE`` and never becomes a
failure. Run validity (population admission) is classified independently from
the benchmark outcome so invalid runs never enter formal success-rate
denominators while their evidence is fully retained.
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
        document, missing field, non-bool field, empty outcomes — is
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
        # Malformed authority evidence (non-numeric agent keys) must fail
        # closed for the evaluator, never crash it.
        return BenchmarkEvidenceAssessment(
            outcome=BenchmarkOutcome.UNAVAILABLE,
            outcome_reason="authority_agent_keys_malformed",
            local_skill_completed=None,
            local_agent_failure=None,
            numeric_aggregates={},
            expected_agents=expected_agents,
            observed_agents=tuple(outcomes),
            authority_document_present=True,
        )

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
    """Classify run validity independently from the benchmark outcome.

    Evidence completeness (authority document present), real episode
    execution (episode started), and an authoritative termination fact are
    required before a run may count as population-valid; explicit
    infrastructure or system failure facts mark invalid classes with the
    evidence retained.
    """
    reasons: list[str] = []
    if not authority_present:
        reasons.append("authority_document_missing")
    if not episode_started:
        reasons.append("episode_never_started")
    if authority_present and episode_terminated is None:
        reasons.append("termination_fact_missing")

    if infrastructure_failure is True:
        return RunValidityAssessment(
            validity=RunValidity.INVALID_INFRA,
            valid_for_formal_population=False,
            benchmark_authority_available=False,
            valid_for_benchmark_population=False,
            system_failure=False,
            model_failure=None,
            reasons=tuple(reasons + ["infrastructure_failure_explicit"]),
        )
    if not authority_present or not episode_started:
        return RunValidityAssessment(
            validity=RunValidity.INVALID_INFRA,
            valid_for_formal_population=False,
            benchmark_authority_available=False,
            valid_for_benchmark_population=False,
            system_failure=False,
            model_failure=None,
            reasons=tuple(reasons),
        )
    if authority_present and episode_started and episode_terminated is None:
        # The episode genuinely ran but no authoritative terminal fact exists,
        # so the benchmark outcome cannot be trusted as final evidence.
        return RunValidityAssessment(
            validity=RunValidity.INVALID_INFRA,
            valid_for_formal_population=False,
            benchmark_authority_available=True,
            valid_for_benchmark_population=False,
            system_failure=False,
            model_failure=None,
            reasons=tuple(reasons),
        )
    if process_status is not None and process_status != "completed":
        return RunValidityAssessment(
            validity=RunValidity.INVALID_INFRA,
            valid_for_formal_population=False,
            benchmark_authority_available=True,
            valid_for_benchmark_population=False,
            system_failure=False,
            model_failure=None,
            reasons=tuple(reasons + [f"process_status_{process_status}"]),
        )
    if benchmark_outcome is BenchmarkOutcome.UNAVAILABLE:
        return RunValidityAssessment(
            validity=RunValidity.VALID_RUN,
            valid_for_formal_population=True,
            benchmark_authority_available=False,
            valid_for_benchmark_population=False,
            system_failure=False,
            model_failure=None,
            reasons=tuple(reasons + ["benchmark_outcome_unavailable"]),
        )
    # The episode ran to an authoritative terminal state. A SUT system or
    # model failure stays inside the formal population as an observation:
    # excluding it would create survivorship bias.
    system_failure = mission_status == "Failed"
    return RunValidityAssessment(
        validity=RunValidity.VALID_RUN,
        valid_for_formal_population=True,
        benchmark_authority_available=True,
        valid_for_benchmark_population=True,
        system_failure=system_failure,
        model_failure=False,
        reasons=tuple(reasons) or ("evidence_complete",),
    )
