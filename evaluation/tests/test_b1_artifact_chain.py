"""Prove artifacts -> verifier -> persisted admission -> Runner -> metrics -> summary."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from b1_helpers import ROOT, make_run, write_json
from roboguide_eval.models import EnvironmentSpec
from roboguide_eval.process import ProcessManager, ProcessOutcome, ProcessSpec
from roboguide_eval.results import summarize_results
from roboguide_eval.runner import PreparedSystem
from roboguide_eval.systems.roboguide import RoboGuideRunner


def verify(run: Path) -> dict[str, Any]:
    """Invoke the actual scenario verifier, which persists admission before collection."""
    spec = importlib.util.spec_from_file_location(
        "verify_b1", ROOT / "scenarios/e1-shared-world-episode-51/verify-b1.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result: dict[str, Any] = module.verify(run)
    assert json.loads((run / "b1-verdict.json").read_text()) == result
    return result


def collect(
    run: Path,
    *,
    exit_code: int = 0,
    start_failed: bool = False,
    raw_metrics: str | None = None,
    formal_b1: bool = True,
) -> dict[str, Any]:
    """Use the public Harness collect_result API and persist its actual MetricsPayload."""
    argv = ("bash", "run-b1-roboguide.sh" if formal_b1 else "run-shared-world.sh", str(run))
    prepared = PreparedSystem(
        system="roboguide",
        process_spec=ProcessSpec(argv, ROOT, {}, 60),
        environment_spec=EnvironmentSpec(
            name="roboguide", expected_output_paths=(), metrics_source_path=raw_metrics
        ),
        system_version=None,
        config_digest="offline-protocol-test",
    )
    outcome = ProcessOutcome(
        argv, ROOT, "completed", 0, False, 1, run / "stdout.log", run / "stderr.log"
    )
    outcome = replace(
        outcome, exit_code=exit_code, status="start_failed" if start_failed else "completed"
    )
    runner = RoboGuideRunner(process_manager=ProcessManager(), environment={}, repository_root=ROOT)
    payload = runner.collect_result(prepared, run, outcome)
    payload.validate()
    document = payload.to_json()
    write_json(run / "metrics.json", document)
    return dict(document)


def test_harness_nonzero_sut_exit_does_not_exclude_formal_observation(tmp_path: Path) -> None:
    """The process exit status cannot override explicit SUT failure attribution."""
    run = make_run(tmp_path, case="C")
    verify(run)
    metrics = collect(run, exit_code=1)
    assert metrics["values"]["valid_for_formal_population"] is True
    assert metrics["values"]["system_failure"] is True


def test_harness_own_launch_failure_is_external_infrastructure(tmp_path: Path) -> None:
    """An OS-level Harness launch failure is explicit infrastructure evidence."""
    run = tmp_path / "never-launched"
    run.mkdir()
    metrics = collect(run, start_failed=True)
    assert metrics["details"]["failure_owner"] == "EXTERNAL_INFRA"
    assert metrics["values"]["valid_for_formal_population"] is False


@pytest.mark.parametrize("case", ["A", "F"])
def test_invalid_optional_scalar_metrics_cannot_disable_b1_gate(tmp_path: Path, case: str) -> None:
    """The canonical gate still runs if an optional SUT metrics report fails validation."""
    run = make_run(tmp_path, case=case)
    verify(run)
    write_json(run / "raw-metrics.json", {"values": {"success": "not-a-boolean"}})
    metrics = collect(run, raw_metrics="raw-metrics.json")
    assert metrics["values"]["valid_for_formal_population"] is (case == "A")
    assert "raw_metrics_validation_error" in metrics["details"]


@pytest.mark.parametrize(
    ("case", "formal", "benchmark", "success", "owner"),
    [
        ("A", True, True, True, "NONE"),
        ("B", True, True, False, "NONE"),
        ("C", True, False, None, "SUT_SYSTEM"),
        ("C-rejected", True, False, None, "SUT_SYSTEM"),
        ("D", True, False, None, "MODEL"),
        ("E", False, False, True, "EXTERNAL_INFRA"),
        ("F", False, False, True, "NONE"),
        ("G", True, False, None, "BENCHMARK_AUTHORITY_UNAVAILABLE"),
    ],
)
def test_artifact_chain_truth_table(
    tmp_path: Path, case: str, formal: bool, benchmark: bool, success: bool | None, owner: str
) -> None:
    """Every A-G population decision survives the complete production consumption order."""
    run = make_run(tmp_path, case=case)
    assert not (run / "metrics.json").exists()
    verdict = verify(run)
    assert not (run / "metrics.json").exists()
    metrics = collect(run)
    values = metrics["values"]
    assert values["valid_for_formal_population"] is formal
    assert values["valid_for_benchmark_population"] is benchmark
    assert values.get("success") is success
    if success is None:
        assert "success" not in values
        assert metrics["details"]["benchmark_tri_state"] == "BENCHMARK_UNAVAILABLE"
    assert metrics["details"]["failure_owner"] == owner
    assert values["system_failure"] is (owner == "SUT_SYSTEM")
    assert values["model_failure"] is (owner == "MODEL")
    assert metrics["details"]["b1_admission"] == verdict["admission"]
    aggregate = summarize_results(tmp_path)["aggregate"]
    assert isinstance(aggregate, dict)
    assert aggregate["formal_population_admitted"] == int(formal)
    assert aggregate["benchmark_population_admitted"] == int(benchmark)
    assert aggregate["success_metric_known"] == int(benchmark)
    assert aggregate["success_count"] == int(benchmark and success is True)
    assert aggregate["invalid_infra_runs"] == int(owner == "EXTERNAL_INFRA")
    if case.startswith("C"):
        assert verdict["protocol_provenance"] == "VALID"
        assert verdict["system_outcome"] == "FAILURE"
        assert aggregate["system_failure_observations"] == 1
    if case == "D":
        assert aggregate["model_failure_observations"] == 1


def test_harness_rejects_missing_persisted_admission(tmp_path: Path) -> None:
    """Valid underlying evidence alone cannot bypass the persisted protocol gate."""
    run = make_run(tmp_path)
    metrics = collect(run)
    assert metrics["values"]["valid_for_formal_population"] is False
    verify(run)
    assert collect(run)["values"]["valid_for_formal_population"] is True


@pytest.mark.parametrize("tamper", ["admission", "request", "provenance"])
def test_harness_rejects_stale_or_tampered_gate(tmp_path: Path, tamper: str) -> None:
    """The Harness checks current evidence and never trusts a stale PASS document."""
    run = make_run(tmp_path)
    verify(run)
    filename = {
        "admission": "b1-verdict.json",
        "request": "b1-request-record.json",
        "provenance": "b1-provenance.json",
    }[tamper]
    document = json.loads((run / filename).read_text())
    if tamper == "admission":
        document["evidence_digest"] = "forged"
    elif tamper == "request":
        document["draft_digest"] = "digest-placeholder"
    else:
        document["accepted_plan_digest"] = "forged"
    write_json(run / filename, document)
    metrics = collect(run)
    assert metrics["values"]["valid_for_formal_population"] is False
    assert metrics["values"]["valid_for_benchmark_population"] is False


def test_harness_does_not_fall_back_when_all_b1_artifacts_missing(tmp_path: Path) -> None:
    """The configured B1 command still selects its mandatory gate without artifacts."""
    run = tmp_path / "empty"
    run.mkdir()
    write_json(
        run / "verdict.json",
        {
            "schema": "roboguide.e1-shared-world-verdict/v0.1",
            "checks": {"official_pddl_success": True},
            "context": {},
        },
    )
    assert collect(run)["values"]["valid_for_formal_population"] is False


def test_static_b2_success_cannot_enter_formal_b1_population(tmp_path: Path) -> None:
    """A static diagnostic retains raw pddl success but has no Formal B1 admission."""
    run = tmp_path / "static-b2"
    run.mkdir()
    write_json(
        run / "verdict.json",
        {
            "schema": "roboguide.e1-shared-world-verdict/v0.1",
            "checks": {},
            "context": {"mission_status": "Completed", "identity": {"episode_terminated": True}},
        },
    )
    write_json(run / "evidence/shared-world-summary.json", {"official_pddl_success": True})
    metrics = collect(run, formal_b1=False)
    assert metrics["values"]["success"] is True
    assert metrics["values"]["valid_for_formal_population"] is False
    assert metrics["values"]["valid_for_benchmark_population"] is False
