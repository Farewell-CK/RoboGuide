"""Offline failure-boundary archiving regressions without running the scenario."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from b1_helpers import build_provenance, make_run, write_json
from roboguide_eval.b1_admission import FailureOwner
from roboguide_eval.b1_artifacts import collect_b1_artifacts
from roboguide_eval.b1_provenance import plan_digest
from roboguide_eval.b1_run import assess_b1_directory
from test_b1_artifact_chain import collect, verify


@pytest.mark.parametrize("component", ["controller", "node", "mission_service", "local_eaios"])
def test_explicit_sut_startup_failure_remains_formal(
    tmp_path: Path, component: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Frozen workload plus failed launched SUT boundary admits even before MI has an id."""
    run = tmp_path / "startup-failure"
    run.mkdir()
    write_json(
        run / "b1-input-used.json",
        {
            "schema": "roboguide.e1.b1-input/v0.1",
            "instruction": "perform the frozen task",
            "episode_id": "51",
            "seed": 40,
            "scene_id": "scene-51",
            "dataset_revision": "dataset-1",
            "dataset_sha256": "a" * 64,
        },
    )
    monkeypatch.setattr("roboguide_eval.b1_artifacts._fetch", Mock(return_value=None))
    verdict = collect_b1_artifacts(
        run,
        request_id="",
        mission_endpoint="http://unused",
        controller_endpoint="http://unused",
        owner=FailureOwner.SUT_SYSTEM,
        component=component,
        reason="sut_process_exited_before_collection",
    )
    assert verdict["admission"]["provenance_valid"] is True
    assert verdict["admission"]["valid_for_formal_population"] is True
    assert verdict["admission"]["valid_for_benchmark_population"] is False
    assert collect(run)["values"]["system_failure"] is True


def test_controller_process_failure_precedes_missing_benchmark(tmp_path: Path) -> None:
    """A current accepted Mission with no attempts can end in an attributable process failure."""
    run = make_run(tmp_path, case="G")
    request = json.loads((run / "b1-request-record.json").read_text())
    obs = json.loads((run / "b1-request-observations.json").read_text())
    write_json(run / "execution-attempts.json", {"attempts": []})
    write_json(
        run / "run-failure.json",
        {
            "schema_version": "roboguide.e1.run-failure/v0.1",
            "run_id": run.name,
            "failure_owner": "SUT_SYSTEM",
            "component": "controller",
            "reason": "controller_process_exited",
            "request_id": request["request_id"],
            "mission_id": request["mission_id"],
            "group_id": obs["submission_evidence"]["controller_group_id"],
        },
    )
    build_provenance(run)
    verdict = verify(run)
    assert verdict["admission"]["failure_owner"] == "SUT_SYSTEM"
    assert collect(run)["values"]["valid_for_formal_population"] is True


def test_foreign_failure_cannot_rescue_current_run(tmp_path: Path) -> None:
    """Foreign Mission failure evidence cannot establish this Mission's execution."""
    run = make_run(tmp_path, case="G")
    write_json(run / "execution-attempts.json", {"attempts": []})
    write_json(
        run / "run-failure.json",
        {
            "schema_version": "roboguide.e1.run-failure/v0.1",
            "run_id": run.name,
            "failure_owner": "SUT_SYSTEM",
            "component": "controller",
            "reason": "failed",
            "request_id": "other",
            "mission_id": "other",
            "group_id": "other",
        },
    )
    build_provenance(run)
    assert assess_b1_directory(run)["admission"]["provenance_valid"] is False


def test_missing_benchmark_never_creates_external_failure(tmp_path: Path) -> None:
    """Neither local evidence absence nor an unknown process result implies infrastructure."""
    run = make_run(tmp_path, case="G")
    result = verify(run)
    assert result["admission"]["failure_owner"] == "BENCHMARK_AUTHORITY_UNAVAILABLE"
    assert collect(run)["values"]["infrastructure_failure"] is False


def test_collector_requires_matching_request_and_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read race retries the public record and sidecar until their digest matches."""
    run = make_run(tmp_path)
    request = json.loads((run / "b1-request-record.json").read_text())
    observations = json.loads((run / "b1-request-observations.json").read_text())
    stale = {**observations, "request_record_digest": "stale"}
    fetch = Mock(
        side_effect=[
            request,
            stale,
            request,
            observations,
            json.loads((run / "mission.json").read_text()),
            json.loads((run / "events.json").read_text()),
            json.loads((run / "execution-attempts.json").read_text()),
        ]
    )
    monkeypatch.setattr("roboguide_eval.b1_artifacts._fetch", fetch)
    result = collect_b1_artifacts(
        run,
        request_id=request["request_id"],
        mission_endpoint="http://unused",
        controller_endpoint="http://unused",
    )
    assert fetch.call_count == 7
    assert observations["request_record_digest"] == plan_digest(request)
    assert result["admission"]["provenance_valid"] is True
