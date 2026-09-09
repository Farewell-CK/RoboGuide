"""Tests for run identity, manifests, run directories, traces, and summaries."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from roboguide_eval.metrics import METRICS_SCHEMA, MetricsPayload, RawEvidenceRef
from roboguide_eval.results import (
    MANIFEST_SCHEMA,
    RESERVED_RUN_FILES,
    RunArtifactWriter,
    RunManifest,
    create_run_directory,
    current_git_commit,
    harness_environment_information,
    new_run_id,
    summarize_results,
    utc_now_iso,
)

BASE_MANIFEST_FIELDS: dict[str, Any] = {
    "experiment_id": "fixture-experiment",
    "system": "emos",
    "benchmark": "fixture-bench",
    "task": "mobility",
    "episode_id": "ep-000",
    "seed": 7,
    "run_id": "20260908T000000Z-abcdef123456",
    "config_digest": "digest-1",
    "started_at": "2026-09-08T00:00:00+00:00",
    "ended_at": "2026-09-08T00:01:00+00:00",
    "command": ("python", "-u", "-m", "fixture"),
    "working_directory": "/tmp/workdir",
    "process_status": "completed",
    "exit_code": 0,
    "timed_out": False,
    "failure_reason": None,
    "llm_provider": "openai",
    "requested_model": "gpt-5.6-luna",
    "reasoning_effort": "medium",
    "reasoning_options": {"summary": "auto"},
    "roboguide_git_sha": "fixedsha",
    "system_version": "v-fixture-1.2.3",
    "dataset_identity": "fixture-dataset@r1",
    "dataset_digest": None,
    "reported_model": None,
    "environment_overrides": {"OPENAI_API_KEY": "${OPENAI_API_KEY}"},
    "context": {"robots": "spot+fetch"},
    "environment_information": harness_environment_information("emos-env"),
    "episode_selection": {
        "selector": "seed-pinned-sample",
        "seed": 7,
        "resolution": {
            "status": "resolved",
            "resolved_episode_id": "1173",
            "resolved_scene_id": None,
            "dataset_index": None,
            "evidence_source": "stdout: Episode Step Info banner",
        },
    },
}


def build_manifest(**overrides: Any) -> RunManifest:
    """Build a fully populated fixture manifest with optional overrides.

    Args:
        overrides: Field values replacing the documented fixture defaults.

    Returns:
        The assembled :class:`RunManifest`.
    """
    return RunManifest(**{**BASE_MANIFEST_FIELDS, **overrides})


def test_new_run_ids_are_unique_and_time_sortable() -> None:
    """Run ids combine a UTC stamp with a unique suffix."""
    first = new_run_id()
    second = new_run_id()
    assert first != second
    stamp, suffix = first.rsplit("-", 1)
    datetime.strptime(stamp, "%Y%m%dT%H%M%SZ")
    assert len(suffix) == 12


def test_manifest_serialization_carries_all_reproducibility_fields(tmp_path: Path) -> None:
    """Manifest JSON contains every field a reproduction requires."""
    manifest = build_manifest()
    document = manifest.to_json()
    assert document["schema"] == MANIFEST_SCHEMA
    for key in (
        "experiment_id",
        "system",
        "benchmark",
        "task",
        "episode_id",
        "seed",
        "run_id",
        "config_digest",
        "started_at",
        "ended_at",
        "command",
        "working_directory",
        "process_status",
        "exit_code",
        "timed_out",
        "failure_reason",
        "episode_selection",
    ):
        assert key in document
    selection = require_object(document["episode_selection"])
    resolution = require_object(selection["resolution"])
    assert resolution["resolved_episode_id"] == "1173"
    assert document["llm"] == {
        "provider": "openai",
        "requested_model": "gpt-5.6-luna",
        "reported_model": None,
        "reasoning_effort": "medium",
        "reasoning_options": {"summary": "auto"},
    }
    assert document["provenance"] == {
        "roboguide_git_sha": "fixedsha",
        "system_version": "v-fixture-1.2.3",
        "dataset_identity": "fixture-dataset@r1",
        "dataset_digest": None,
    }
    assert document["environment_overrides"] == {"OPENAI_API_KEY": "${OPENAI_API_KEY}"}
    writer = RunArtifactWriter(tmp_path / "run")
    writer.write_manifest(manifest)
    stored = json.loads((tmp_path / "run" / "manifest.json").read_text(encoding="utf-8"))
    assert stored["run_id"] == manifest.run_id


def test_reported_model_field_is_reserved_for_api_identifiers() -> None:
    """The manifest keeps configured alias and API-returned model separate."""
    manifest = replace(build_manifest(), reported_model="gpt-5.6-luna-2026-08-01")
    llm = require_object(manifest.to_json())["llm"]
    assert require_object(llm)["requested_model"] == "gpt-5.6-luna"
    assert require_object(llm)["reported_model"] == "gpt-5.6-luna-2026-08-01"


def test_manifest_timestamps_are_iso8601_utc() -> None:
    """Manifest timestamps parse as timezone-aware ISO-8601 values."""
    manifest = build_manifest()
    for value in (manifest.started_at, manifest.ended_at):
        parsed = datetime.fromisoformat(value)
        assert parsed.tzinfo is not None


def test_git_commit_prefers_explicit_environment_override(tmp_path: Path) -> None:
    """The provenance override wins over any repository state."""
    assert current_git_commit(tmp_path, {"ROBOGUIDE_EVAL_GIT_SHA": "pinnedsha"}) == "pinnedsha"


def test_git_commit_survives_missing_repository(tmp_path: Path) -> None:
    """A directory without Git degrades to 'unknown' instead of raising."""
    assert current_git_commit(tmp_path, {}) == "unknown"


def test_run_directory_layout_is_complete(tmp_path: Path) -> None:
    """The artifact writer creates manifest, metrics, and trace in place."""
    run_directory = create_run_directory(tmp_path, "fixture-experiment", "run-1")
    assert run_directory == tmp_path / "fixture-experiment" / "run-1"
    writer = RunArtifactWriter(run_directory)
    writer.write_manifest(build_manifest())
    writer.write_metrics(
        MetricsPayload(
            values={"success": True},
            details={},
            raw_evidence=(RawEvidenceRef(path="raw.json", description="raw"),),
        ).to_json()
    )
    writer.trace.append("run_started", {"ok": True})
    writer.trace.append("process_completed", {"exit_code": 0})
    assert {item.name for item in run_directory.iterdir()} == {
        "manifest.json",
        "metrics.json",
        "trace.jsonl",
    }
    lines = (run_directory / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    assert [event["event"] for event in events] == ["run_started", "process_completed"]
    assert [event["seq"] for event in events] == [1, 2]
    assert all("time" in event for event in events)
    metrics_document = json.loads((run_directory / "metrics.json").read_text(encoding="utf-8"))
    assert metrics_document["schema"] == METRICS_SCHEMA


def test_summarize_aggregates_runs_and_success_rate(tmp_path: Path) -> None:
    """Summarization reports per-run rows plus aggregate counters."""
    outcomes = [(0, True), (0, True), (2, False)]
    for index, (exit_code, success) in enumerate(outcomes):
        run_directory = create_run_directory(tmp_path, "fixture-experiment", f"run-{index}")
        writer = RunArtifactWriter(run_directory)
        writer.write_manifest(
            build_manifest(
                run_id=f"run-{index}",
                episode_id=f"ep-{index}",
                exit_code=exit_code,
                process_status="completed" if exit_code == 0 else "timeout",
                failure_reason=None
                if exit_code == 0
                else "process timed out after 1.0s and was terminated",
            )
        )
        writer.write_metrics(
            MetricsPayload(
                values={"success": success, "wall_time": 1.0 + index},
                details={},
                raw_evidence=(),
            ).to_json()
        )
    report = summarize_results(tmp_path)
    aggregate = require_object(report["aggregate"])
    assert aggregate["total"] == 3
    assert aggregate["completed"] == 2
    assert aggregate["failed"] == 1
    assert aggregate["success_metric_known"] == 3
    assert aggregate["success_count"] == 2
    assert aggregate["success_rate"] == pytest.approx(2 / 3)
    assert aggregate["total_wall_time"] == pytest.approx(6.0)
    runs = require_object(report).get("runs")
    assert isinstance(runs, list) and len(runs) == 3
    first_row = require_object(runs[0])
    # Paired-comparison grouping needs the resolved benchmark episode id in
    # the summary row, not just the selector label.
    assert first_row["resolved_episode_id"] == "1173"


def test_summarize_tolerates_empty_and_malformed_directories(tmp_path: Path) -> None:
    """Summarization over nothing yields a valid empty report."""
    assert require_object(summarize_results(tmp_path)["aggregate"])["total"] == 0
    (tmp_path / "run-broken").mkdir()
    (tmp_path / "run-broken" / "manifest.json").write_text("{not json", encoding="utf-8")
    assert require_object(summarize_results(tmp_path)["aggregate"])["total"] == 0


def require_object(value: object) -> dict[str, object]:
    """Narrow one JSON value to a plain object or fail the test.

    Args:
        value: The decoded JSON value to narrow.

    Returns:
        The value typed as a plain string-keyed mapping.

    Raises:
        AssertionError: If the value is not an object.
    """
    assert isinstance(value, dict)
    return value


def test_utc_now_and_reserved_files_contract() -> None:
    """Clock and reserved-file helpers keep their documented contract."""
    assert datetime.fromisoformat(utc_now_iso()).tzinfo is not None
    assert RESERVED_RUN_FILES == {
        "manifest.json",
        "metrics.json",
        "trace.jsonl",
        "stdout.log",
        "stderr.log",
    }
