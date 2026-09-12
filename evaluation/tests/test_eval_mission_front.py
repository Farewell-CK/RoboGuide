"""Offline tests for the Mission Front-half eval framework.

No network and no real model: cases, recording seams, and semantic
invariants are exercised against synthetic records, while the runner's
failure-evidence path is validated with a stub transport that fails every
stage (the engine must degrade to FAILED lifecycle evidence, and the suite
must still produce complete results).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from roboguide_eval.mission_front.cases import (
    MissionFrontCaseError,
    load_cases,
)
from roboguide_eval.mission_front.invariants import (
    check_capability_coverage,
    check_clarification_behavior,
    check_decomposition_sanity,
    check_final_lifecycle,
    check_integrated_operation,
    check_local_how_leakage,
    check_objective_fidelity,
)
from roboguide_eval.mission_front.recording import (
    RecordingTransport,
    StageScope,
    StageTimedPort,
)
from roboguide_eval.mission_front.runner import SuiteComponents, run_suite

BASE_CASES: Path = Path(__file__).resolve().parents[1] / "mission_front_cases" / "baseline.yaml"

CANNED_USAGE: dict[str, object] = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}


def test_baseline_case_set_loads_with_expected_coverage() -> None:
    """The committed baseline set parses into 29 cases across 7 categories."""
    cases = load_cases(BASE_CASES)
    assert len(cases) == 29
    categories = {case.category for case in cases}
    assert categories == {
        "normal",
        "ambiguity",
        "heterogeneous",
        "integrated",
        "cross_node",
        "resource_timing",
        "multi_role",
    }
    clarify = [case for case in cases if case.expectations.clarify_first_pass]
    assert len(clarify) == 5
    integrated = [
        case for case in cases if case.expectations.integrated_operation == "object.relocate@v1"
    ]
    assert len(integrated) == 9


def test_case_loader_rejects_invalid_definitions(tmp_path: Path) -> None:
    """Unknown keys, bad categories, wrong schemas, and duplicates fail closed."""
    schema_line = "schema: roboguide-eval.mission-front-cases/v0.1\n"
    cases_line = "cases:\n"
    case_body = '  - id: x12\n    category: normal\n    instruction: "把杯子搬到卧室"\n'

    def write(name: str, text: str) -> Path:
        """Write one YAML file into the test directory.

        Args:
            name: Target file name.
            text: File content.

        Returns:
            The written file path.
        """
        target = tmp_path / name
        target.write_text(text, encoding="utf-8")
        return target

    with pytest.raises(MissionFrontCaseError, match="schema"):
        load_cases(
            write("bad-schema.yaml", schema_line.replace("v0.1", "v9.9") + cases_line + case_body)
        )
    with pytest.raises(MissionFrontCaseError, match="unknown keys"):
        load_cases(
            write(
                "unknown-key.yaml",
                schema_line
                + cases_line
                + case_body.replace(
                    'instruction: "把杯子搬到卧室"',
                    'instruction: "把杯子搬到卧室"\n    unexpected: 1',
                ),
            )
        )
    with pytest.raises(MissionFrontCaseError, match="category"):
        load_cases(
            write(
                "bad-category.yaml",
                schema_line
                + cases_line
                + case_body.replace("category: normal", "category: nonsense"),
            )
        )
    with pytest.raises(MissionFrontCaseError, match="duplicate case id"):
        load_cases(
            write("duplicate-id.yaml", schema_line + cases_line + case_body + case_body),
        )


def test_recording_transport_captures_latency_usage_and_stage() -> None:
    """The transport records stage, latency, and provider usage per call."""
    stages = StageScope()

    class StubTransport:
        """Return one canned response, recording nothing itself."""

        def post_json(
            self, url: str, headers: object, payload: object, timeout_seconds: float
        ) -> dict[str, object]:
            """Return a canned provider response with usage.

            Args:
                url: Ignored endpoint URL.
                headers: Ignored headers.
                payload: Ignored request payload.
                timeout_seconds: Ignored timeout.

            Returns:
                A canned OpenAI-style response body.
            """
            return {"id": "resp-1", "usage": dict(CANNED_USAGE)}

    transport = RecordingTransport(StubTransport(), stages)
    stages.enter("planner")
    response = transport.post_json("http://relay/v1/responses", {}, {"prompt": "plan this"}, 90.0)
    stages.leave()
    assert response["id"] == "resp-1"
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call.stage == "planner"
    assert call.ok is True
    assert call.usage == CANNED_USAGE
    assert call.latency_ms >= 0.0


class _StubPort:
    """Minimal port double for the timing proxy."""

    def __init__(self) -> None:
        """Track invocation count."""
        self.calls = 0

    def plan(self, mission_id: str) -> str:
        """Record one call and return a marker.

        Args:
            mission_id: Forwarded mission id.

        Returns:
            A constant marker string.
        """
        self.calls += 1
        return f"plan-for-{mission_id}"


def test_stage_timed_port_tags_and_measures() -> None:
    """The timing proxy attributes transport calls and records duration."""
    stages = StageScope()
    stub = _StubPort()
    port = StageTimedPort(stub, "planner", stages)
    result = port.plan("m-1")
    assert result == "plan-for-m-1"
    assert stub.calls == 1
    assert stages.current == "unattributed"
    assert port.timing.summary()["calls"] == 1


def _record(
    *,
    lifecycle: str = "Accepted",
    objective: str = "把杯子搬到卧室",
    tasks: list[dict[str, object]] | None = None,
    actors: list[dict[str, object]] | None = None,
    dialogue: list[dict[str, object]] | None = None,
    issues: list[str] | None = None,
    repair_attempts: int = 0,
) -> dict[str, object]:
    """Build a minimal serialized record for invariant checks.

    Args:
        lifecycle: Final lifecycle value.
        objective: Plan mission objective.
        tasks: Serialized task list.
        actors: Serialized actor list.
        dialogue: Serialized dialogue turns.
        issues: Recorded issues.
        repair_attempts: Repair attempt count.

    Returns:
        The record JSON used by invariant checks.
    """
    return {
        "lifecycle": lifecycle,
        "issues": issues or [],
        "repair_attempts": repair_attempts,
        "dialogue": dialogue or [],
        "review_history": [],
        "plan": {
            "mission": {
                "id": "m-1",
                "objective": objective,
                "actors": actors if actors is not None else [{"id": "spot"}],
            },
            "contexts": [],
            "tasks": tasks
            if tasks is not None
            else [
                {
                    "id": "t1",
                    "description": "把杯子搬到卧室",
                    "depends_on": [],
                    "roles": [
                        {
                            "id": "r1",
                            "requirements": {
                                "capabilities": [
                                    {
                                        "contract": {
                                            "namespace": "object",
                                            "name": "relocate",
                                            "version": "v1",
                                        },
                                        "constraints": [],
                                    }
                                ],
                                "resources": [],
                            },
                            "execution_intent": {
                                "capability_contract": {
                                    "namespace": "object",
                                    "name": "relocate",
                                    "version": "v1",
                                },
                                "parameters": {},
                            },
                            "context_role": "cr1",
                            "resource_scope": "task",
                        }
                    ],
                    "context_id": "ctx1",
                }
            ],
        },
    }


def test_invariants_pass_on_well_formed_record() -> None:
    """All invariants pass for a record satisfying a relocate-style case."""
    record = _record()
    assert check_final_lifecycle(object(), record, "Accepted").passed
    assert check_clarification_behavior(object(), record, False).passed
    assert check_objective_fidelity(object(), record, ("杯子", "卧室")).passed
    assert check_decomposition_sanity(
        object(), record, min_tasks=1, min_roles=1, min_actors=1
    ).passed
    assert check_capability_coverage(object(), record, ("object.relocate@v1",)).passed
    assert check_integrated_operation(object(), record, "object.relocate@v1", ()).passed
    assert check_local_how_leakage(object(), record, ()).passed


def test_invariants_catch_leakage_and_wrong_operation() -> None:
    """Local How leakage and wrong operation choice both fail their checks."""
    leaking = _record(
        tasks=[
            {
                "id": "t1",
                "description": "drive base_velocity to waypoint",
                "depends_on": [],
                "roles": [
                    {
                        "id": "r1",
                        "requirements": {
                            "capabilities": [
                                {
                                    "contract": {
                                        "namespace": "mobility",
                                        "name": "navigate",
                                        "version": "v1",
                                    },
                                    "constraints": [],
                                }
                            ],
                            "resources": [],
                        },
                        "execution_intent": {
                            "capability_contract": {
                                "namespace": "mobility",
                                "name": "navigate",
                                "version": "v1",
                            },
                            "parameters": {},
                        },
                        "context_role": "cr1",
                        "resource_scope": "task",
                    }
                ],
                "context_id": "ctx1",
            }
        ]
    )
    leakage = check_local_how_leakage(object(), leaking, ())
    assert not leakage.passed
    assert "base_velocity" in leakage.detail
    operation = check_integrated_operation(object(), leaking, "object.relocate@v1", ())
    assert not operation.passed
    assert "not used" in operation.detail


def test_review_repair_and_lifecycle_failures_are_reported() -> None:
    """Exhausted repair and wrong lifecycle both produce failing outcomes."""
    exhausted = _record(
        lifecycle="Failed", issues=["mission review repair attempts exhausted"], repair_attempts=2
    )
    convergence = check_local_how_leakage  # placeholder to keep import symmetry
    del convergence
    from roboguide_eval.mission_front.invariants import check_review_repair_convergence

    outcome = check_review_repair_convergence(object(), exhausted)
    assert not outcome.passed
    lifecycle = check_final_lifecycle(object(), exhausted, "Accepted")
    assert not lifecycle.passed
    assert "issues" in lifecycle.detail


def test_run_suite_produces_complete_evidence_on_stub_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing stub transport still yields full per-case evidence.

    The engine degrades every stage error into a FAILED lifecycle record; the
    suite must write case files, the JSONL log, and the summary with failing
    invariants instead of crashing.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "mission.toml").write_text("placeholder", encoding="utf-8")
    (tmp_path / "config" / "mission-service.toml").write_text("placeholder", encoding="utf-8")
    cases = load_cases(BASE_CASES)

    class _ExplodingSuite:
        """Stand in for the assembled suite with a transport that always fails."""

    import roboguide_eval.mission_front.runner as runner_module

    original_build = runner_module.build_suite_components

    real_root = BASE_CASES.resolve().parents[2]
    # The production adapters validate provider endpoint safety and credential
    # presence at construction; grant both for this offline run.
    monkeypatch.setenv("ROBOGUIDE_ALLOW_INSECURE_LLM_HTTP", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-secret")

    class _FailingTransport:
        """Raise on every call so the engine degrades to FAILED."""

        def post_json(
            self, url: str, headers: object, payload: object, timeout_seconds: float
        ) -> dict[str, object]:
            """Simulate an unreachable provider.

            Args:
                url: Ignored endpoint URL.
                headers: Ignored headers.
                payload: Ignored payload.
                timeout_seconds: Ignored timeout.

            Raises:
                RuntimeError: Always, with a stable message.
            """
            raise RuntimeError("stub provider unreachable")

    def fake_build(
        repository_root: Path,
        transport: RecordingTransport,
        stages: StageScope,
    ) -> SuiteComponents:
        """Return components whose transport delegates always fail.

        Args:
            repository_root: Unused repository root from the runner.
            transport: Unused recording transport created by the runner.
            stages: The stage scope shared with the runner.

        Returns:
            Suite components built from the real configuration but a failing
            transport delegate, so every stage call degrades to FAILED
            evidence while call recording still works.
        """
        del repository_root, transport
        return original_build(real_root, RecordingTransport(_FailingTransport(), stages), stages)

    monkeypatch.setattr(runner_module, "build_suite_components", fake_build)
    summary = run_suite(
        cases[:2],
        repository_root=tmp_path,
        out_dir=tmp_path / "results",
    )
    assert summary["cases_executed"] == 2
    assert summary["cases_passed"] == 0
    run_dirs = list((tmp_path / "results").iterdir())
    assert len(run_dirs) == 1
    suite_dir = run_dirs[0]
    case_files = sorted((suite_dir / "cases").glob("*.json"))
    assert len(case_files) == 2
    first_case = json.loads(case_files[0].read_text(encoding="utf-8"))
    assert first_case["final_lifecycle"] == "Failed"
    assert first_case["failure_reasons"]
    jsonl_lines = (suite_dir / "cases.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(jsonl_lines) == 2
