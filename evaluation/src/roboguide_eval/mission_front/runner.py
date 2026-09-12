"""Runner for the Mission Front-half eval: drive the real pipeline per case.

The runner composes the production ``MissionRequestEngine`` exactly as
``apps/mission-service`` does — same Interpreter/Planner/Reviewer/Repairer
adapters, same Capability Catalog v0.3, same approval policy — with two
evaluation-only seams: a recording transport (per-LLM-call latency and
provider usage) and a recording submitter (plan acceptance without a
Controller). It drives each case through clarification, review, repair, and
approval, records the complete ``MissionRequestRecord``, and evaluates the
case's semantic invariants.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.config import current_environment, load_settings
from mission.requests import (
    MissionRequestEngine,
    MissionRequestLifecycle,
    MissionRequestStore,
)
from mission.responses import (
    ResponsesMissionInterpreter,
    ResponsesMissionPlanner,
    ResponsesMissionRepairer,
    ResponsesMissionReviewer,
)
from mission.service_config import load_service_settings

from roboguide_eval.mission_front.cases import MissionFrontCase
from roboguide_eval.mission_front.invariants import (
    InvariantOutcome,
    check_capability_coverage,
    check_clarification_behavior,
    check_decomposition_sanity,
    check_final_lifecycle,
    check_integrated_operation,
    check_local_how_leakage,
    check_objective_fidelity,
    check_resource_kinds,
    check_review_repair_convergence,
    check_timing_presence,
)
from roboguide_eval.mission_front.recording import (
    RecordingSubmitter,
    RecordingTransport,
    StageScope,
    StageTimedPort,
)

RESULTS_DIR_NAME: Final = "mission-front"


def new_suite_id() -> str:
    """Return a unique, time-sortable suite identifier.

    Returns:
        ``mission-front-<UTC timestamp>-<random suffix>``.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"mission-front-{stamp}-{uuid.uuid4().hex[:8]}"


@dataclass
class SuiteComponents:
    """Carry the assembled engine and its recording seams."""

    engine: MissionRequestEngine
    transport: RecordingTransport
    stages: StageScope
    stage_timings: dict[str, object]
    model: str
    relay_base_url: str


def build_suite_components(
    repository_root: Path, transport: RecordingTransport, stages: StageScope
) -> SuiteComponents:
    """Compose the production front-half engine with evaluation-only seams.

    All four LLM ports are the production Responses adapters over the
    configured provider; the only differences from ``apps/mission-service``
    are the recording transport, per-stage timing proxies, a temporary
    durable store, and a recording submitter (no Controller is contacted).

    Args:
        repository_root: Repository root used to resolve configuration and
            prompt/schema/catalog assets.
        transport: The recording transport wrapping the real HTTP delegate.
        stages: The shared stage attribution scope.

    Returns:
        The assembled suite components.

    Raises:
        Exception: Configuration or provider access errors surface as-is so
            the operator sees the exact remediation (for example a missing
            ``OPENAI_API_KEY``).
    """
    mission_settings = load_settings(
        repository_root / "config" / "mission.toml", repository_root=repository_root
    )
    service_settings = load_service_settings(
        repository_root / "config" / "mission-service.toml", repository_root=repository_root
    )
    catalog = CanonicalCapabilityCatalog.load(mission_settings.capability_catalog_path)
    environment = current_environment()
    interpreter = StageTimedPort(
        ResponsesMissionInterpreter(mission_settings, environment, transport=transport),
        "interpreter",
        stages,
    )
    planner = StageTimedPort(
        ResponsesMissionPlanner(mission_settings, environment, transport=transport),
        "planner",
        stages,
    )
    reviewer = (
        StageTimedPort(
            ResponsesMissionReviewer(mission_settings, environment, transport=transport),
            "reviewer",
            stages,
        )
        if mission_settings.review_enabled
        else None
    )
    repairer = (
        StageTimedPort(
            ResponsesMissionRepairer(mission_settings, environment, transport=transport),
            "repairer",
            stages,
        )
        if reviewer is not None and mission_settings.max_repair_attempts > 0
        else None
    )
    store_dir = transport_calls_store_hint(transport) / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    engine = MissionRequestEngine(
        MissionRequestStore(store_dir / "requests.sqlite3"),
        interpreter,
        planner,
        RecordingSubmitter(),
        catalog,
        service_settings.approval_policy,
        reviewer=reviewer,
        repairer=repairer,
        max_repair_attempts=(mission_settings.max_repair_attempts if reviewer is not None else 0),
    )
    return SuiteComponents(
        engine=engine,
        transport=transport,
        stages=stages,
        stage_timings={
            "interpreter": interpreter.timing.summary(),
            "planner": planner.timing.summary(),
            "reviewer": reviewer.timing.summary() if reviewer is not None else None,
            "repairer": repairer.timing.summary() if repairer is not None else None,
        },
        model=mission_settings.llm.model,
        relay_base_url=mission_settings.provider.base_url,
    )


def transport_calls_store_hint(transport: RecordingTransport) -> Path:
    """Return a stable per-suite scratch directory derived from the transport.

    The mission engine needs a durable SQLite store; the eval uses a run-local
    temporary path so suite runs never share request state with the live
    Mission Service.

    Args:
        transport: The recording transport (only used as an identity anchor).

    Returns:
        A filesystem path unique to this suite run.
    """
    return Path("artifacts") / "mission-front-eval" / f"suite-{id(transport):x}"


@dataclass
class CaseResult:
    """Hold the complete evidence of one evaluated front-half case."""

    case_id: str
    category: str
    instruction: str
    expected_lifecycle: str
    final_lifecycle: str
    passed: bool
    wall_seconds: float
    follow_ups_used: int
    invariants: list[InvariantOutcome]
    llm_calls: list[dict[str, object]]
    stage_timings: dict[str, object]
    record: dict[str, object]
    harness_error: str | None = None

    def failure_reasons(self) -> list[str]:
        """Collect the failing invariant details for reporting.

        Returns:
            One ``name: detail`` string per failed invariant.
        """
        return [
            f"{outcome.name}: {outcome.detail}" for outcome in self.invariants if not outcome.passed
        ]

    def summary_line(self) -> dict[str, object]:
        """Return the compact per-case summary for the JSONL log.

        Returns:
            Identifier, outcome, lifecycle, invariant counts, and token sums.
        """
        return {
            "case_id": self.case_id,
            "category": self.category,
            "passed": self.passed,
            "expected_lifecycle": self.expected_lifecycle,
            "final_lifecycle": self.final_lifecycle,
            "wall_seconds": round(self.wall_seconds, 1),
            "failed_invariants": [
                outcome.name for outcome in self.invariants if not outcome.passed
            ],
            "llm_calls": len(self.llm_calls),
        }


def _usage_totals(calls: list[dict[str, object]]) -> dict[str, object]:
    """Sum provider usage over the recorded calls of one case.

    Args:
        calls: The recorded transport calls for the case.

    Returns:
        Totals for input/output/total tokens when the provider reports them;
        otherwise explicit ``None`` values.
    """
    totals: dict[str, int] = {}
    for call in calls:
        usage = call.get("usage")
        if not isinstance(usage, dict):
            continue
        for field in ("input_tokens", "output_tokens", "total_tokens"):
            value = usage.get(field)
            if isinstance(value, int) and not isinstance(value, bool):
                totals[field] = totals.get(field, 0) + value
    return {
        "input_tokens": totals.get("input_tokens"),
        "output_tokens": totals.get("output_tokens"),
        "total_tokens": totals.get("total_tokens"),
    }


def evaluate_case_invariants(
    case: MissionFrontCase, record: dict[str, object]
) -> list[InvariantOutcome]:
    """Evaluate every semantic invariant for one finished case.

    Args:
        case: The case definition carrying the expectations.
        record: The serialized MissionRequestRecord.

    Returns:
        All invariant outcomes in reporting order.
    """
    expectations = case.expectations
    return [
        check_final_lifecycle(case, record, expectations.final_lifecycle),
        check_clarification_behavior(case, record, expectations.clarify_first_pass),
        check_objective_fidelity(case, record, expectations.must_cover_objective),
        check_decomposition_sanity(
            case,
            record,
            min_tasks=expectations.min_tasks,
            min_roles=expectations.min_roles,
            min_actors=expectations.min_actors,
        ),
        check_capability_coverage(case, record, expectations.require_capabilities),
        check_integrated_operation(
            case,
            record,
            expectations.integrated_operation,
            expectations.forbid_task_description_patterns,
        ),
        check_resource_kinds(case, record, expectations.resource_kinds),
        check_timing_presence(case, record, expectations.timing_required),
        check_local_how_leakage(case, record, expectations.extra_forbidden_patterns),
        check_review_repair_convergence(case, record),
    ]


def run_case(
    case: MissionFrontCase,
    suite: SuiteComponents,
    call_offset: int,
) -> CaseResult:
    """Drive one case through the real front-half pipeline and evaluate it.

    The case runs create → clarification follow-ups → bounded review/repair →
    revision-bound auto-approval, mirroring the live Mission Service command
    sequence. Every intermediate state is captured from the durable record.

    Args:
        case: The case definition.
        suite: The assembled suite components.
        call_offset: Number of transport calls recorded before this case, for
            per-case call attribution.

    Returns:
        The complete :class:`CaseResult` for the case.

    Raises:
        Exception: Never — harness-level errors are captured into the result
            so one broken case cannot abort the suite.
    """
    engine = suite.engine
    started = time.monotonic()
    follow_ups_used = 0
    record_json: dict[str, object] = {}
    harness_error: str | None = None
    try:
        record = engine.create(case.instruction)
        while (
            record.lifecycle == MissionRequestLifecycle.NEEDS_CLARIFICATION
            and follow_ups_used < len(case.follow_ups)
        ):
            record = engine.add_message(record.request_id, case.follow_ups[follow_ups_used])
            follow_ups_used += 1
        if record.lifecycle == MissionRequestLifecycle.AWAITING_APPROVAL:
            record = engine.approve(record.request_id, record.draft_revision, record.draft_digest)
        record_json = record.to_json()
    except Exception as error:  # noqa: BLE001 - one broken case must not abort the suite
        harness_error = f"{type(error).__name__}: {error}"
        try:
            record_json = record.to_json()
        except Exception:
            record_json = {}
    wall = time.monotonic() - started
    calls = [
        call.__dict__
        for call in suite.transport.calls[call_offset:]
        if isinstance(call.__dict__, dict)
    ]
    invariants = evaluate_case_invariants(case, record_json)
    expected = case.expectations.final_lifecycle
    final_lifecycle = str(record_json.get("lifecycle", "unknown"))
    passed = harness_error is None and all(outcome.passed for outcome in invariants)
    return CaseResult(
        case_id=case.case_id,
        category=case.category,
        instruction=case.instruction,
        expected_lifecycle=expected,
        final_lifecycle=final_lifecycle,
        passed=passed,
        wall_seconds=wall,
        follow_ups_used=follow_ups_used,
        invariants=invariants,
        llm_calls=calls,
        stage_timings={},
        record=record_json,
        harness_error=harness_error,
    )


def run_suite(
    cases: tuple[MissionFrontCase, ...],
    *,
    repository_root: Path,
    out_dir: Path,
    only: tuple[str, ...] = (),
    limit: int = 0,
) -> dict[str, object]:
    """Run a suite of front-half cases and write the complete evidence set.

    Args:
        cases: The loaded case definitions.
        repository_root: Repository root for configuration resolution.
        out_dir: Output directory receiving ``cases/``, ``cases.jsonl``, and
            ``summary.json``.
        only: Optional case-id filter (repeatable).
        limit: Optional maximum number of cases to run (0 = all).

    Returns:
        The summary document written to ``summary.json``.

    Raises:
        Exception: Configuration/provider assembly errors propagate — the
            suite cannot start without a usable pipeline.
    """
    selected = [case for case in cases if not only or case.case_id in only]
    if limit > 0:
        selected = selected[:limit]
    suite_id = new_suite_id()
    stages = StageScope()
    transport = RecordingTransport(_default_transport_factory(), stages)
    suite = build_suite_components(repository_root, transport, stages)
    run_dir = out_dir / suite_id
    cases_dir = run_dir / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    results: list[CaseResult] = []
    call_offset = 0
    for case in selected:
        result = run_case(case, suite, call_offset)
        call_offset = len(suite.transport.calls)
        result.stage_timings = dict(suite.stage_timings)
        results.append(result)
        (cases_dir / f"{case.case_id}.json").write_text(
            json.dumps(result_summary_json(result), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    summary = build_summary(suite_id, suite, selected, results)
    (run_dir / "cases.jsonl").write_text(
        "\n".join(json.dumps(result.summary_line(), ensure_ascii=False) for result in results)
        + ("\n" if results else ""),
        encoding="utf-8",
    )
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def _default_transport_factory() -> object:
    """Create the real HTTP transport used under the recording wrapper.

    Returns:
        A fresh ``UrllibJsonTransport`` from the mission package.
    """
    from mission.responses import UrllibJsonTransport

    return UrllibJsonTransport()


def result_summary_json(result: CaseResult) -> dict[str, object]:
    """Serialize one case result with full evidence.

    Args:
        result: The finished case result.

    Returns:
        The JSON document written into ``cases/<id>.json``.
    """
    return {
        "case_id": result.case_id,
        "category": result.category,
        "instruction": result.instruction,
        "expected_lifecycle": result.expected_lifecycle,
        "final_lifecycle": result.final_lifecycle,
        "passed": result.passed,
        "wall_seconds": round(result.wall_seconds, 1),
        "follow_ups_used": result.follow_ups_used,
        "harness_error": result.harness_error,
        "invariants": [
            {"name": outcome.name, "passed": outcome.passed, "detail": outcome.detail}
            for outcome in result.invariants
        ],
        "failure_reasons": result.failure_reasons(),
        "llm_calls": result.llm_calls,
        "token_totals": _usage_totals(result.llm_calls),
        "stage_timings": result.stage_timings,
        "record": result.record,
    }


def build_summary(
    suite_id: str,
    suite: SuiteComponents,
    cases: list[MissionFrontCase],
    results: list[CaseResult],
) -> dict[str, object]:
    """Aggregate suite results into the summary document.

    Args:
        suite_id: The suite identifier.
        suite: The assembled suite components.
        cases: The executed case definitions.
        results: The finished case results.

    Returns:
        The summary document with per-category outcomes, invariant failure
        histogram, token totals, and stage timings.
    """
    by_category: dict[str, dict[str, int]] = {}
    failed_invariants: dict[str, int] = {}
    for result in results:
        bucket = by_category.setdefault(result.category, {"total": 0, "passed": 0})
        bucket["total"] += 1
        bucket["passed"] += int(result.passed)
        for outcome in result.invariants:
            if not outcome.passed:
                failed_invariants[outcome.name] = failed_invariants.get(outcome.name, 0) + 1
    all_calls = [call for result in results for call in result.llm_calls]
    return {
        "suite_id": suite_id,
        "model": suite.model,
        "relay_base_url": suite.relay_base_url,
        "cases_executed": len(results),
        "cases_passed": sum(1 for result in results if result.passed),
        "by_category": by_category,
        "failed_invariants": dict(sorted(failed_invariants.items(), key=lambda item: -item[1])),
        "token_totals": _usage_totals(all_calls),
        "llm_calls": len(all_calls),
        "stage_timings": suite.stage_timings,
    }
