"""Offline tests for grounding A/B canaries and NOT_EVALUATED semantics.

No network and no real model: fixture readers build official
``GroundingContextSnapshot`` contract objects, digest binding is verified
against the engine's own validation helper, and the canary runner is proven
with a stub transport. NOT_EVALUATED behavior is checked against synthetic
records.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest
from mission.grounding_context import dialogue_digest
from mission.request_record import DialogueSpeaker, DialogueTurn, DialogueTurnKind
from roboguide_eval.mission_front.grounding import (
    GroundingScenarioError,
    build_scenario_reader,
    load_grounding_scenarios,
    run_grounding_suite,
)
from roboguide_eval.mission_front.invariants import (
    check_decomposition_sanity,
    check_integrated_operation,
)
from roboguide_eval.mission_front.recording import StageScope, StageTimedPort

CANARIES: Path = Path(__file__).resolve().parents[1] / "mission_front_grounding" / "canaries.yaml"


def _dialogue(text: str) -> tuple[DialogueTurn, ...]:
    """Build a one-turn user dialogue.

    Args:
        text: The instruction text.

    Returns:
        A single user instruction dialogue tuple.
    """
    return (
        DialogueTurn(
            turn_id="t1",
            speaker=DialogueSpeaker.USER,
            kind=DialogueTurnKind.INSTRUCTION,
            content=text,
            created_at_ms=1,
            in_reply_to=None,
        ),
    )


def test_canary_set_loads_with_expected_pair_coverage() -> None:
    """The canary file parses into 7 A/B pairs across the declared modes."""
    scenarios = load_grounding_scenarios(CANARIES)
    assert len(scenarios) == 14
    pairs = {scenario.pair_id for scenario in scenarios}
    assert pairs == {
        "fresh-unique-place",
        "conflict",
        "stale",
        "no-evidence",
        "metadata-only",
        "fresh-vs-stale",
        "gap-only",
    }
    for pair in pairs:
        modes = [s.mode for s in scenarios if s.pair_id == pair]
        assert sorted(modes) == ["context_free", "fixture_grounded"]
    grounded = [s for s in scenarios if s.mode == "fixture_grounded"]
    # Every grounded scenario declares evidence or gaps except the deliberate
    # empty-evidence control pair.
    assert all(
        s.state_entries or s.memory_entries or s.gap_entries or s.pair_id == "no-evidence"
        for s in grounded
    )
    free = [s for s in scenarios if s.mode == "context_free"]
    assert all(not (s.state_entries or s.memory_entries or s.gap_entries) for s in free)


def test_fixture_reader_builds_contract_valid_snapshots_with_dynamic_digest() -> None:
    """Snapshots bind to the CURRENT dialogue revision on every capture."""
    scenarios = load_grounding_scenarios(CANARIES)
    grounded = next(s for s in scenarios if s.scenario_id == "g1-fresh-unique-place-grounded")
    built_reader = build_scenario_reader(grounded)
    assert built_reader is not None
    reader = built_reader
    snapshot_one = reader.capture("req-1", _dialogue("把包裹送到前台"), 1000)
    assert snapshot_one.request_id == "req-1"
    assert snapshot_one.dialogue_digest == dialogue_digest(
        tuple(turn.to_json() for turn in _dialogue("把包裹送到前台"))
    )
    assert len(snapshot_one.state_evidence) == 1
    assert snapshot_one.state_evidence[0].freshness.value == "Fresh"
    assert snapshot_one.state_evidence[0].evidence_id.startswith("state:")
    # A second capture after the dialogue changed (clarification answer)
    # must bind to the NEW revision.
    updated = _dialogue("把包裹送到前台") + (
        DialogueTurn(
            turn_id="t2",
            speaker=DialogueSpeaker.USER,
            kind=DialogueTurnKind.CLARIFICATION_ANSWER,
            content="送到东侧前台",
            created_at_ms=2,
            in_reply_to="t1",
        ),
    )
    snapshot_two = reader.capture("req-1", updated, 2000)
    assert snapshot_two.dialogue_digest == dialogue_digest(
        tuple(turn.to_json() for turn in updated)
    )
    assert snapshot_two.dialogue_digest != snapshot_one.dialogue_digest


def test_fixture_reader_rejects_contract_violations(tmp_path: Path) -> None:
    """Fixture entries that break the official contract fail loudly."""
    bad_state = (
        "schema: roboguide-eval.mission-front-grounding/v0.1\n"
        "scenarios:\n"
        "  - id: bad-state\n"
        "    pair: p\n"
        "    mode: fixture_grounded\n"
        '    instruction: "x"\n'
        "    grounding:\n"
        "      state:\n"
        "        - object_type: place\n"
        "          object_id: o1\n"
        "          semantic: forbidden-semantic\n"
        "          source: s\n"
        "          channel_id: c\n"
        "          payload_schema: p\n"
        "          value: {label: x}\n"
        "          freshness: Fresh\n"
    )
    bad_file = tmp_path / "bad-state.yaml"
    bad_file.write_text(bad_state, encoding="utf-8")
    with pytest.raises(GroundingScenarioError, match="forbidden semantic"):
        scenarios = load_grounding_scenarios(bad_file)
        bad_reader = build_scenario_reader(scenarios[0])
        assert bad_reader is not None
        bad_reader.capture("r", _dialogue("x"), 1)
    bad_memory = (
        "schema: roboguide-eval.mission-front-grounding/v0.1\n"
        "scenarios:\n"
        "  - id: bad-memory\n"
        "    pair: p2\n"
        "    mode: fixture_grounded\n"
        '    instruction: "x"\n'
        "    grounding:\n"
        "      memory:\n"
        "        - memory_id: m1\n"
        "          revision_id: r1\n"
        "          kind: not-a-kind\n"
        "          provider_id: p\n"
        "          payload_schema: s\n"
        "          visibility: discoverable\n"
        "          created_at_ms: 1\n"
    )
    bad_file2 = tmp_path / "bad-memory.yaml"
    bad_file2.write_text(bad_memory, encoding="utf-8")
    with pytest.raises(GroundingScenarioError, match="outside the grounding allowlist"):
        scenarios = load_grounding_scenarios(bad_file2)
        bad_reader = build_scenario_reader(scenarios[0])
        assert bad_reader is not None
        bad_reader.capture("r", _dialogue("x"), 1)


def test_context_free_mode_rejects_inline_grounding(tmp_path: Path) -> None:
    """context_free scenarios may not carry grounding entries."""
    text = (
        "schema: roboguide-eval.mission-front-grounding/v0.1\n"
        "scenarios:\n"
        "  - id: mixed\n"
        "    pair: p\n"
        "    mode: context_free\n"
        '    instruction: "x"\n'
        "    grounding:\n"
        "      gaps:\n"
        "        - code: c\n"
        "          source: s\n"
        "          detail: d\n"
    )
    bad_file = tmp_path / "mixed.yaml"
    bad_file.write_text(text, encoding="utf-8")
    with pytest.raises(GroundingScenarioError, match="context_free"):
        load_grounding_scenarios(bad_file)


def _record_without_plan(lifecycle: str = "NeedsClarification") -> dict[str, object]:
    """Build a serialized record that never reached the planner.

    Args:
        lifecycle: The final lifecycle value.

    Returns:
        The record JSON with no plan block.
    """
    return {"lifecycle": lifecycle, "issues": [], "dialogue": [], "review_history": []}


def test_plan_invariants_mark_not_evaluated_without_plan() -> None:
    """Plan-dependent invariants return NOT_EVALUATED, never FAIL."""
    record = _record_without_plan()
    outcome = check_decomposition_sanity(object(), record, min_tasks=1, min_roles=1, min_actors=1)
    assert outcome.passed is None and outcome.not_evaluated
    outcome = check_integrated_operation(object(), record, "object.relocate@v1", ())
    assert outcome.passed is None and outcome.not_evaluated
    assert "planner stage not reached" in outcome.detail


def test_not_evaluated_does_not_count_as_failure() -> None:
    """A record blocked at clarification passes overall via NOT_EVALUATED."""
    from roboguide_eval.mission_front.cases import CaseExpectations, MissionFrontCase
    from roboguide_eval.mission_front.runner import evaluate_case_invariants

    case = MissionFrontCase(
        case_id="blocked",
        category="normal",
        instruction="把杯子搬到卧室",
        expectations=CaseExpectations(final_lifecycle="NeedsClarification"),
    )
    record = _record_without_plan("NeedsClarification")
    outcomes = evaluate_case_invariants(case, record)
    assert all(outcome.passed is not False for outcome in outcomes)
    not_evaluated = [outcome.name for outcome in outcomes if outcome.not_evaluated]
    assert "decomposition_sanity" in not_evaluated
    assert "integrated_operation" in not_evaluated


def test_stage_timing_delta_attributes_per_case(
    tmp_path: Path, make_stub_ports: None = None
) -> None:
    """delta_since reports only the invocations after the snapshot."""
    stages = StageScope()
    calls: list[str] = []

    class StubPlanner:
        """Record one call per invocation."""

        def plan(self, mission_id: str) -> str:
            """Record and return a marker.

            Args:
                mission_id: Forwarded mission id.

            Returns:
                A marker string.
            """
            calls.append(mission_id)
            time.sleep(0.001)
            return f"plan-{mission_id}"

    port = StageTimedPort(StubPlanner(), "planner", stages)
    snap = port.timing.snapshot()
    port.plan("a")
    port.plan("b")
    delta = port.timing.delta_since(snap)
    assert delta["calls"] == 2
    total_ms = delta["total_ms"]
    assert isinstance(total_ms, (int, float)) and total_ms >= 0.0
    # A later snapshot covers nothing new.
    assert port.timing.delta_since(port.timing.snapshot())["calls"] == 0


import time  # noqa: E402


def test_run_grounding_suite_ab_pairs_with_stub_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The A/B runner produces paired evidence using a stub LLM transport."""
    import roboguide_eval.mission_front.grounding as grounding_module
    from roboguide_eval.mission_front.cases import MissionFrontCase
    from roboguide_eval.mission_front.runner import SuiteComponents
    from roboguide_eval.mission_front.runner import run_case as real_run_case

    real_root = CANARIES.resolve().parents[2]
    monkeypatch.setenv("ROBOGUIDE_ALLOW_INSECURE_LLM_HTTP", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-secret")
    monkeypatch.chdir(tmp_path)
    scenarios = load_grounding_scenarios(CANARIES)

    class StubTransport:
        """Return canned responses for every stage call."""

        def post_json(
            self, url: str, headers: object, payload: object, timeout_seconds: float
        ) -> dict[str, object]:
            """Return a canned provider response.

            Args:
                url: Ignored endpoint URL.
                headers: Ignored headers.
                payload: Ignored payload.
                timeout_seconds: Ignored timeout.

            Returns:
                A minimal provider-style response body.
            """
            return {
                "id": "resp",
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            }

    def fake_run_case(case: MissionFrontCase, suite: SuiteComponents, call_offset: int) -> object:
        """Drive the real runner against a stub engine and canned transport.

        Args:
            case: The projected case.
            suite: The assembled suite.
            call_offset: Unused call offset.

        Returns:
            The real run_case result against stubbed collaborators.
        """
        del call_offset

        def stub_record() -> object:
            """Build a minimal NeedsClarification record for the stub engine.

            Returns:
                An object exposing to_json() and the fields run_case reads.
            """
            return types.SimpleNamespace(
                lifecycle="NeedsClarification",
                request_id="r",
                draft_revision=1,
                draft_digest="d",
                to_json=lambda: {
                    "lifecycle": "NeedsClarification",
                    "issues": [],
                    "review_history": [],
                    "dialogue": [
                        {
                            "turn_id": "t1",
                            "speaker": "User",
                            "kind": "Instruction",
                            "content": case.instruction,
                            "created_at_ms": 1,
                            "in_reply_to": None,
                        }
                    ],
                },
            )

        engine = types.SimpleNamespace(
            create=lambda instruction: stub_record(),
            add_message=lambda request_id, text: stub_record(),
            approve=lambda *args: stub_record(),
        )
        object.__setattr__(suite, "engine", engine)
        from roboguide_eval.mission_front.recording import TransportCall

        suite.transport.calls.append(
            TransportCall(
                stage="interpreter",
                url="stub",
                latency_ms=1.0,
                request_bytes=1,
                response_bytes=1,
                ok=True,
                error=None,
                usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            )
        )
        return real_run_case(case, suite, 0)

    monkeypatch.setattr(grounding_module, "run_case", fake_run_case)
    summary = run_grounding_suite(
        scenarios,
        repository_root=real_root,
        out_dir=tmp_path / "out",
    )
    assert summary["scenarios_executed"] == 14
    pairs = summary["pairs"]
    assert isinstance(pairs, dict) and len(pairs) == 7
    for pair_id, pair in pairs.items():
        pair_object = pair if isinstance(pair, dict) else {}
        modes = pair_object.get("modes")
        assert isinstance(modes, dict)
        assert set(modes) == {"context_free", "fixture_grounded"}, pair_id
    run_dirs = list((tmp_path / "out").iterdir())
    assert len(run_dirs) == 1
    assert len(list((run_dirs[0] / "cases").glob("*.json"))) == 14


def test_canary_clarification_expectations_are_observe_only() -> None:
    """All canaries record clarification counts without asserting them."""
    scenarios = load_grounding_scenarios(CANARIES)
    assert len(scenarios) == 14
    for scenario in scenarios:
        assert scenario.expectations.clarify_first_pass is None


def test_fresh_vs_stale_pair_is_neutral_no_fusion_policy() -> None:
    """The former fresh-beats-stale pair is renamed and keeps Any lifecycle."""
    scenarios = load_grounding_scenarios(CANARIES)
    pair_ids = {scenario.pair_id for scenario in scenarios}
    assert "fresh-vs-stale" in pair_ids
    assert "fresh-beats-stale" not in pair_ids
    for scenario in scenarios:
        if scenario.pair_id == "fresh-vs-stale":
            assert scenario.expectations.final_lifecycle == "Any"
    names = {scenario.scenario_id for scenario in scenarios}
    assert "g6-fresh-vs-stale-free" in names
    assert "g6-fresh-vs-stale-grounded" in names


def test_none_clarification_outcome_is_observe_only() -> None:
    """clarify_first_pass=None records the count and never fails."""
    from roboguide_eval.mission_front.invariants import check_clarification_behavior

    record: dict[str, object] = {
        "lifecycle": "NeedsClarification",
        "dialogue": [
            {
                "turn_id": "t1",
                "speaker": "User",
                "kind": "Instruction",
                "content": "x",
                "created_at_ms": 1,
                "in_reply_to": None,
            },
            {
                "turn_id": "t2",
                "speaker": "MissionIntelligence",
                "kind": "ClarificationQuestion",
                "content": "which one?",
                "created_at_ms": 2,
                "in_reply_to": "t1",
            },
        ],
    }
    outcome = check_clarification_behavior(object(), record, None)
    assert outcome.passed is None
    assert outcome.not_evaluated
    assert "observe-only" in outcome.detail
    assert "which one?" in outcome.detail


def test_summary_line_excludes_not_evaluated_from_failures() -> None:
    """summary_line and failure_reasons never treat None as a failure."""
    from roboguide_eval.mission_front.invariants import InvariantOutcome
    from roboguide_eval.mission_front.runner import CaseResult

    result = CaseResult(
        case_id="c",
        category="grounding",
        instruction="i",
        expected_lifecycle="Any",
        final_lifecycle="NeedsClarification",
        passed=True,
        wall_seconds=1.0,
        follow_ups_used=0,
        invariants=[
            InvariantOutcome("a", True, "ok"),
            InvariantOutcome("b", False, "broken"),
            InvariantOutcome("c", None, "not evaluated"),
        ],
        llm_calls=[],
        stage_timings={},
        record={},
    )
    assert result.failure_reasons() == ["b: broken"]
    line = result.summary_line()
    assert line["failed_invariants"] == ["b"]
    assert line["not_evaluated_invariants"] == ["c"]
