"""End-to-end F-06/F-07 artifact-chain integration test.

Builds a real run directory (frozen input, MI request record, Controller
mission view + events, execution attempts, benchmark summary), generates the
provenance artifact with the canonical builder, runs verify-b1, reduces to a
MetricsPayload-shaped values map, and passes it through summarize_results —
proving that a provenance-failing run never enters the wrong population, a
SUT system failure stays a formal observation, and a missing Habitat
authority never becomes a benchmark false.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_SRC = REPO_ROOT / "evaluation" / "src"
SCENARIO = REPO_ROOT / "scenarios" / "e1-shared-world-episode-51"

sys.path.insert(0, str(EVAL_SRC))

from roboguide_eval.b1_provenance import (  # noqa: E402
    build_b1_provenance_record,
    write_b1_provenance,
)
from roboguide_eval.results import summarize_results  # noqa: E402
from roboguide_eval.systems.roboguide import RoboGuideRunner  # # noqa: E402

MANIFEST = {
    "schema": "roboguide-eval.run-manifest/v0.1",
    "experiment_id": "chain-test",
    "system": "roboguide",
    "run_id": "chain-run-1",
    "episode_id": "51",
    "seed": 40,
    "process_status": "completed",
    "exit_code": 0,
}

FROZEN_INPUT: dict[str, Any] = {
    "schema": "roboguide.e1.b1-input/v0.1",
    "mission": {"episode_id": "51", "seed": 40},
    "instruction": "Two heterogeneous robots cover both official goals.",
}

MI_PLAN: dict[str, Any] = {
    "schema_version": "roboguide.mission-plan/v0.8",
    "mission": {
        "id": "mission-chain-test",
        "objective": "cover both goals",
        "actors": [{"id": "a"}, {"id": "b"}],
    },
    "contexts": [],
    "tasks": [
        {"id": "reach-object", "roles": [], "context_id": "ctx", "depends_on": []},
        {"id": "reach-goal", "roles": [], "context_id": "ctx", "depends_on": []},
    ],
}


def _request_record() -> dict[str, Any]:
    """Build one genuine MI request record with generation evidence."""
    return {
        "request_id": "request-chain-1",
        "mission_id": "mission-chain-test",
        "lifecycle": "Accepted",
        "plan": MI_PLAN,
        "draft_digest": "sha256:placeholder",
        "draft_revision": 1,
        "review_history": [{"revision": 1, "outcome": "approved"}],
        "dialogue": [
            {
                "kind": "instruction",
                "content": FROZEN_INPUT["instruction"],
            }
        ],
    }


def _mission_view() -> dict[str, Any]:
    """Build the Controller mission status view (identity only)."""
    return {
        "mission_id": "mission-chain-test",
        "group_id": "group-mission-chain-test",
        "status": "Completed",
        "tasks": [
            {"task_id": "reach-object", "status": "Completed"},
            {"task_id": "reach-goal", "status": "Completed"},
        ],
        "relations": [],
        "peer_channels": [],
    }


def _events() -> dict[str, Any]:
    """Build Controller events with group creation and task registration."""
    return {
        "events": [
            {
                "sequence": 1,
                "payload": {
                    "ExecutionGroupCreated": {
                        "group_id": "group-mission-chain-test",
                        "mission_id": "mission-chain-test",
                    }
                },
            },
            {
                "sequence": 2,
                "payload": {
                    "TaskExecutionRegistered": {
                        "group_id": "group-mission-chain-test",
                        "task_ref": {
                            "mission_id": "mission-chain-test",
                            "task_id": "reach-object",
                        },
                    }
                },
            },
            {
                "sequence": 3,
                "payload": {
                    "TaskExecutionRegistered": {
                        "group_id": "group-mission-chain-test",
                        "task_ref": {
                            "mission_id": "mission-chain-test",
                            "task_id": "reach-goal",
                        },
                    }
                },
            },
        ]
    }


def _attempts() -> dict[str, Any]:
    """Build Node execution attempt evidence."""
    return {
        "schema": "roboguide.execution-attempt-history/v0.1",
        "attempts": [
            {
                "execution_id": "attempt-1",
                "mission_id": "mission-chain-test",
                "task_id": "reach-object",
                "node_id": "node-a",
                "status": "Completed",
            },
            {
                "execution_id": "attempt-2",
                "mission_id": "mission-chain-test",
                "task_id": "reach-goal",
                "node_id": "node-b",
                "status": "Completed",
            },
        ],
    }


def _benchmark(pddl: bool | None) -> dict[str, Any] | None:
    """Build the shared-world authority summary."""
    if pddl is None:
        return {"identity": {"episode_id": "51"}, "outcomes": {}}
    return {
        "identity": {"episode_id": "51", "episode_terminated": True},
        "official_pddl_success": pddl,
        "outcomes": {
            "0": {"state": "COMPLETED", "local_skill_completed": True},
            "1": {"state": "COMPLETED", "local_skill_completed": True},
        },
    }


def _shared_world_verdict(benchmark: dict[str, Any] | None) -> dict[str, Any]:
    """Build the shared-world verdict shape the runner consumes."""
    return {
        "schema": "roboguide.e1-shared-world-verdict/v0.1",
        "verdict": "PASS",
        "checks": {
            "official_pddl_success": (
                benchmark.get("official_pddl_success") if benchmark is not None else None
            ),
            "controller_alive": True,
            "node_a_exactly_once": True,
            "node_b_exactly_once": True,
        },
        "context": {
            "mission_status": "Completed",
            "identity": {"episode_id": "51", "episode_terminated": True},
            "outcomes": (benchmark or {}).get("outcomes", {}),
            "task_statuses": {},
            "peer_communication": {},
        },
    }


def _write_run(
    tmp_path: Path,
    *,
    benchmark: dict[str, Any] | None,
    include_provenance: bool = True,
) -> Path:
    """Materialize one complete run directory from real artifacts."""
    run = tmp_path / "chain-run-1"
    run.mkdir(parents=True)
    (run / "b1-input-used.json").write_text(json.dumps(FROZEN_INPUT, indent=2), encoding="utf-8")
    (run / "b1-request-record.json").write_text(
        json.dumps(_request_record(), indent=2), encoding="utf-8"
    )
    (run / "mission.json").write_text(json.dumps(_mission_view(), indent=2), encoding="utf-8")
    (run / "events.json").write_text(json.dumps(_events()), encoding="utf-8")
    (run / "execution-attempts.json").write_text(
        json.dumps(_attempts(), indent=2), encoding="utf-8"
    )
    (run / "evidence").mkdir()
    if benchmark is not None:
        (run / "evidence" / "shared-world-summary.json").write_text(
            json.dumps(benchmark, indent=2), encoding="utf-8"
        )
    (run / "verdict.json").write_text(
        json.dumps(_shared_world_verdict(benchmark), indent=2), encoding="utf-8"
    )
    (run / "manifest.json").write_text(json.dumps(MANIFEST, indent=2), encoding="utf-8")
    if include_provenance:
        record = build_b1_provenance_record(
            run_id="chain-run-1",
            frozen_input_path=run / "b1-input-used.json",
            request_record_path=run / "b1-request-record.json",
            controller_mission_path=run / "mission.json",
            controller_events_path=run / "events.json",
            execution_attempts_path=run / "execution-attempts.json",
            shared_world_summary_path=run / "evidence/shared-world-summary.json",
        )
        write_b1_provenance(record, run / "b1-provenance.json")
    _reduce_metrics(run)
    return run


def _reduce_metrics(run: Path) -> None:
    """Reduce the run through the real RoboGuideRunner into metrics.json."""
    from roboguide_eval.models import EnvironmentSpec
    from roboguide_eval.process import ProcessOutcome, ProcessSpec
    from roboguide_eval.runner import PreparedSystem

    stdout_path = run / "stdout.log"
    stdout_path.write_text("", encoding="utf-8")
    stderr_path = run / "stderr.log"
    stderr_path.write_text("", encoding="utf-8")
    argv = ("bash", "run-b1-roboguide.sh", str(run))
    outcome = ProcessOutcome(
        argv=argv,
        working_directory=REPO_ROOT,
        status="completed",
        exit_code=0,
        timed_out=False,
        duration_seconds=1.0,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
    prepared = PreparedSystem(
        system="roboguide",
        process_spec=ProcessSpec(
            argv=argv,
            working_directory=REPO_ROOT,
            environment_overrides={},
            timeout_seconds=60.0,
        ),
        environment_spec=EnvironmentSpec(name="roboguide", expected_output_paths=()),
        system_version=None,
        config_digest="chain-test",
    )
    payload = _runner().collect_result(prepared, run, outcome)
    (run / "metrics.json").write_text(
        json.dumps(
            {"values": dict(payload.values), "details": dict(payload.details)},
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )


def _aggregate(summary: dict[str, Any]) -> dict[str, Any]:
    """Narrow the summarize_results aggregate to a string-keyed mapping."""
    aggregate = summary.get("aggregate")
    assert isinstance(aggregate, dict)
    return aggregate


def _verify_b1(run: Path) -> dict[str, Any]:
    """Load verify-b1.py from the scenario and run it on one directory."""
    spec = importlib.util.spec_from_file_location("verify_b1_chain", SCENARIO / "verify-b1.py")
    if spec is None or spec.loader is None:
        raise AssertionError("verify-b1.py could not be loaded")
    module: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result: dict[str, Any] = module.verify(run)
    return result


def _runner() -> RoboGuideRunner:
    """Instantiate the runner with an idle process manager (no spawns here)."""
    from roboguide_eval.process import ProcessManager
    from roboguide_eval.systems.roboguide import RoboGuideRunner as _Runner

    return _Runner(
        process_manager=ProcessManager(),
        environment={},
        repository_root=REPO_ROOT,
    )


def test_artifact_chain_valid_run_reaches_both_populations(tmp_path: Path) -> None:
    """A fully valid chain is admitted to both formal and benchmark populations."""
    run = _write_run(tmp_path, benchmark=_benchmark(True))
    verdict = _verify_b1(run)
    assert verdict["context"]["provenance_failures"] == []
    assert verdict["checks"]["provenance_chain_passed"] is True
    assert verdict["checks"]["valid_for_formal_population"] is True
    assert verdict["checks"]["valid_for_benchmark_population"] is True
    assert verdict["context"]["official_pddl_success"] is True

    summary = summarize_results(tmp_path)
    aggregate = _aggregate(summary)
    assert aggregate["formal_population_admitted"] == 1
    assert aggregate["benchmark_population_admitted"] == 1
    assert aggregate["success_metric_known"] == 1


def test_artifact_chain_missing_authority_never_becomes_false(tmp_path: Path) -> None:
    """A run whose Habitat authority is absent stays unavailable, not false."""
    run = _write_run(tmp_path, benchmark=_benchmark(None))
    verdict = _verify_b1(run)
    # Missing authority: no benchmark boolean. Provenance alone can never
    # admit the run — the verifier consumes the same canonical admission
    # authority as the harness, which requires an authoritative outcome.
    assert verdict["context"]["benchmark_tri_state"] == "BENCHMARK_UNAVAILABLE"
    assert verdict["context"]["official_pddl_success"] is None
    assert verdict["checks"]["provenance_chain_passed"] is True
    assert verdict["checks"]["valid_for_benchmark_population"] is False
    assert verdict["checks"]["valid_for_formal_population"] is False
    admission = verdict["context"]["population_admission"]
    assert admission["authority"] == "roboguide_eval.b1_provenance.admit_to_formal_population"
    assert admission["provenance_passed"] is True
    assert "benchmark_outcome_unavailable" in admission["invalid_reasons"]
    # The success value never becomes False; it is simply absent from the
    # benchmark denominator.
    summary = summarize_results(tmp_path)
    aggregate = _aggregate(summary)
    assert aggregate["success_metric_known"] == 0
    assert aggregate["benchmark_population_admitted"] == 0


def test_artifact_chain_system_failure_stays_formal_observation(tmp_path: Path) -> None:
    """A SUT system failure remains a formal-population observation."""
    run = _write_run(tmp_path, benchmark=_benchmark(False))
    mission = json.loads((run / "mission.json").read_text())
    mission["status"] = "Failed"
    (run / "mission.json").write_text(json.dumps(mission), encoding="utf-8")
    verdict = _verify_b1(run)
    # Provenance chain does not depend on mission status; the run remains
    # formally admitted and the benchmark authority stays available.
    assert verdict["checks"]["provenance_chain_passed"] is True
    assert verdict["checks"]["valid_for_formal_population"] is True
    assert verdict["context"]["benchmark_tri_state"] == "BENCHMARK_FALSE"
    # A SUT failure with an authoritative benchmark outcome is a VALID_RUN
    # observation: excluding it would create survivorship bias.
    assert verdict["context"]["population_admission"]["run_validity"] == "VALID_RUN"
    assert verdict["checks"]["valid_for_benchmark_population"] is True


def test_artifact_chain_provenance_fail_excluded_from_formal(tmp_path: Path) -> None:
    """A run without a provenance artifact fails closed and is excluded from formal."""
    run = _write_run(tmp_path, benchmark=_benchmark(True), include_provenance=False)
    verdict = _verify_b1(run)
    assert "provenance_record_missing" in verdict["context"]["provenance_failures"]
    assert verdict["checks"]["valid_for_formal_population"] is False
    assert verdict["verdict"] == "FAIL"
    # Authorities stay separate: the episode ran to an authoritative outcome,
    # so evidence validity (benchmark population) is unaffected by the
    # provenance failure; only the formal population is provenance-gated.
    admission = verdict["context"]["population_admission"]
    assert admission["run_validity"] == "VALID_RUN"
    assert verdict["checks"]["valid_for_benchmark_population"] is True
    assert admission["provenance_passed"] is False
    assert "provenance_record_missing" in admission["invalid_reasons"]
    # The formal gate consumes the B1 verdict: with provenance failed, the
    # verdict excludes the run and no formal consumer may count it.
    metrics = json.loads((run / "metrics.json").read_text())
    formal_gate = verdict["checks"]["valid_for_formal_population"] is False
    assert formal_gate
    assert metrics["details"].get("verdict") is not None
