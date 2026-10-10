"""Deterministic zero-Provider process tests for durable batch supervision."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import cast

import pytest
from roboguide_eval.cli import main
from roboguide_eval.e1_batch import (
    SCHEMA,
    BatchSpec,
    _capture_log,
    _job_result,
    _pump,
    batch_command,
    capacity_after_probe,
    file_digest,
    process_identity,
    read_json,
    receipt_is_live,
    run_worker,
    summarize_batch,
    supervise,
    write_json,
)
from roboguide_eval.models import JSONObject, JSONValue

DRIVER = """import json, os, sys, time
from pathlib import Path
job, output, config, calls = sys.argv[1:]
data = json.loads(Path(config).read_text())
with Path(calls).open("a") as stream:
    stream.write(job + "\\n")
print(os.environ.get("FIXTURE_API_KEY", ""), flush=True)
if data.get("barrier"):
    Path(data["barrier"]).write_text("ready")
    until = time.monotonic() + 5
    while not Path(data["peer"]).exists() and time.monotonic() < until:
        time.sleep(0.01)
    if not Path(data["peer"]).exists():
        raise RuntimeError("configured peer did not run concurrently")
if data.get("delay"):
    time.sleep(data["delay"])
data["job_id"] = job
Path(output).write_text(json.dumps(data))
"""


def make_manifest(tmp_path: Path, outcomes: list[JSONObject], workers: int = 1) -> Path:
    """Configure external deterministic pair commands without importing a simulator."""
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER)
    jobs: list[JSONValue] = []
    for index, result in enumerate(outcomes):
        config = tmp_path / f"fixture-{index}.json"
        write_json(config, result)
        jobs.append(
            {
                "job_id": f"pair-{index}",
                "argv": [
                    sys.executable,
                    str(driver),
                    f"pair-{index}",
                    str(tmp_path / f"outcome-{index}.json"),
                    str(config),
                    str(tmp_path / "calls.txt"),
                ],
                "cwd": str(tmp_path),
                "outcome_path": str(tmp_path / f"outcome-{index}.json"),
                "environment": {},
                "ports": [41000 + index],
                "timeout_seconds": 10,
            }
        )
    path = tmp_path / "manifest.json"
    write_json(
        path,
        {
            "schema_version": SCHEMA,
            "jobs": jobs,
            "source_sha256": {str(driver): file_digest(driver)},
            "capacity": {
                "max_workers": workers,
                "gpu_total_mib": 24000,
                "gpu_baseline_mib": 3000,
                "gpu_fraction": 0.8,
            },
        },
    )
    return path


def outcome(infra: bool = False, fatal: bool = False) -> JSONObject:
    """Return a fixture where Completed does not imply benchmark success."""
    return {
        "infrastructure_failure": infra,
        "fatal_failure": fatal,
        "arms": [
            {"arm": "native", "official_pddl_success": False},
            {
                "arm": "roboguide",
                "mission_status": "Completed",
                "official_pddl_success": None,
                "admission": {"valid_for_formal_population": True},
            },
        ],
    }


def prepare_output(tmp_path: Path) -> Path:
    """Create only isolated fixture state, never touching a repository run directory."""
    path = tmp_path / "batch"
    path.mkdir()
    return path


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", "wrong"),
        ("jobs", []),
        ("capacity", {"max_workers": 3}),
        ("capacity", {"gpu_fraction": float("inf")}),
    ],
)
def test_bad_manifests_do_not_launch(tmp_path: Path, field: str, value: JSONValue) -> None:
    """Unsafe budget/schema declarations fail before an external command exists."""
    manifest = make_manifest(tmp_path, [outcome()])
    document = read_json(manifest)
    document[field] = value
    manifest.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        BatchSpec.load(manifest)
    assert not (tmp_path / "calls.txt").exists()


def test_port_overlap_and_duplicate_jobs_rejected(tmp_path: Path) -> None:
    """Two independent jobs cannot share the same configured transport resources."""
    manifest = make_manifest(tmp_path, [outcome(), outcome()])
    document = read_json(manifest)
    jobs = cast(list[JSONObject], document["jobs"])
    jobs[1]["ports"] = jobs[0]["ports"]
    write_json(manifest, document)
    with pytest.raises(ValueError, match="disjoint"):
        BatchSpec.load(manifest)
    jobs[1]["ports"] = [42000]
    jobs[1]["job_id"] = jobs[0]["job_id"]
    write_json(manifest, document)
    with pytest.raises(ValueError, match="duplicate"):
        BatchSpec.load(manifest)


def test_secret_references_remain_symbolic(tmp_path: Path) -> None:
    """Literal credentials and credential-bearing argv are rejected without printing values."""
    manifest = make_manifest(tmp_path, [outcome()])
    document = read_json(manifest)
    job = cast(list[JSONObject], document["jobs"])[0]
    job["environment"] = {"OPENAI_API_KEY": "fixture-private-value"}
    write_json(manifest, document)
    with pytest.raises(ValueError, match="references"):
        BatchSpec.load(manifest)
    job["environment"] = {"OPENAI_API_KEY": "${FIXTURE_API_KEY}"}
    write_json(manifest, document)
    assert BatchSpec.load(manifest).jobs[0].environment["OPENAI_API_KEY"] == "${FIXTURE_API_KEY}"
    job["argv"] = [sys.executable, "--api-key", "fixture-private-value"]
    write_json(manifest, document)
    with pytest.raises(ValueError, match="argv"):
        BatchSpec.load(manifest)


@pytest.mark.parametrize(
    "resources,expected",
    [
        ({}, 1),
        ({"both_arms_measured": False, "peak_job_gpu_mib": 6000}, 1),
        ({"both_arms_measured": True, "peak_job_gpu_mib": 6000}, 2),
        ({"both_arms_measured": True, "peak_job_gpu_mib": 9000}, 1),
        ({"both_arms_measured": True, "peak_job_gpu_mib": -1}, 1),
    ],
)
def test_capacity_uses_complete_measured_probe(
    tmp_path: Path,
    resources: JSONObject,
    expected: int,
) -> None:
    """Unknown or excessive memory cannot enable a second physical worker."""
    spec = BatchSpec.load(make_manifest(tmp_path, [outcome()], workers=2))
    assert capacity_after_probe(spec, {"resource_usage": resources}) == expected


def test_worker_claim_prevents_repeating_pair(tmp_path: Path) -> None:
    """A second invocation cannot relaunch either arm or replace the first result."""
    manifest = make_manifest(tmp_path, [outcome()])
    output = prepare_output(tmp_path)
    assert run_worker(manifest, output, "pair-0") == 0
    first = (output / "jobs/pair-0/result.json").read_bytes()
    assert run_worker(manifest, output, "pair-0") == 3
    assert (tmp_path / "calls.txt").read_text().splitlines() == ["pair-0"]
    assert (output / "jobs/pair-0/result.json").read_bytes() == first


def test_worker_logs_redact_inherited_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """External stdout cannot persist a known credential inherited only in memory."""
    secret = "fixture-credential-" + "z" * 37
    monkeypatch.setenv("FIXTURE_API_KEY", secret)
    manifest = make_manifest(tmp_path, [outcome()])
    output = prepare_output(tmp_path)
    run_worker(manifest, output, "pair-0")
    assert secret not in (output / "jobs/pair-0/stdout.log").read_text()
    assert "[REDACTED]" in (output / "jobs/pair-0/stdout.log").read_text()
    assert secret not in manifest.read_text()


def test_split_log_secret_is_redacted(tmp_path: Path) -> None:
    """A credential crossing the 4096-byte read boundary is not partially disclosed."""
    secret = b"private-fixture-credential"
    source = io.BytesIO(b"x" * 4090 + secret + b"done")
    target = tmp_path / "log"
    _pump(source, target, (secret,))
    assert target.read_bytes() == b"x" * 4090 + b"[REDACTED]done"


def test_receipt_does_not_trust_reused_pid() -> None:
    """A current PID with a different lifetime is never an owned live worker."""
    identity = process_identity(os.getpid())
    assert identity is not None
    assert receipt_is_live({"pid": os.getpid(), "process_identity": identity})
    identity["start_ticks"] = "different"
    assert not receipt_is_live({"pid": os.getpid(), "process_identity": identity})


def test_dead_claim_is_preserved_as_non_retryable(tmp_path: Path) -> None:
    """Loss of a worker never authorizes a new Mission Request for its claimed pair."""
    spec = BatchSpec.load(make_manifest(tmp_path, [outcome()]))
    output = prepare_output(tmp_path)
    directory = output / "jobs/pair-0"
    directory.mkdir(parents=True)
    write_json(directory / "receipt.json", {"pid": 2147483647, "process_identity": None})
    observed = _job_result(directory, spec.jobs[0])
    assert observed is not None and observed["fatal_failure"] is True
    assert not (tmp_path / "calls.txt").exists()


def test_all_sut_failures_continue(tmp_path: Path) -> None:
    """Ordinary unavailable/false outcomes do not stop or leave the Formal denominator."""
    manifest = make_manifest(tmp_path, [outcome() for _ in range(4)])
    output = prepare_output(tmp_path)
    assert supervise(manifest, output) == 0
    assert (tmp_path / "calls.txt").read_text().splitlines() == [f"pair-{i}" for i in range(4)]
    summary = read_json(output / "SUMMARY.json")
    arms = cast(JSONObject, summary["arms"])
    roboguide = cast(JSONObject, arms["roboguide"])
    assert roboguide["formal_admitted"] == 4
    assert roboguide["official_true"] == 0 and roboguide["official_unavailable"] == 4


def test_three_infrastructure_pairs_pause_queue(tmp_path: Path) -> None:
    """A service outage pauses unused work without appending attempts to completed pairs."""
    manifest = make_manifest(tmp_path, [outcome(infra=True) for _ in range(4)])
    output = prepare_output(tmp_path)
    assert supervise(manifest, output) == 2
    assert (tmp_path / "calls.txt").read_text().splitlines() == ["pair-0", "pair-1", "pair-2"]
    assert read_json(output / "status.json")["reason"] == "three_consecutive_infrastructure_pairs"


def test_source_drift_stops_before_first_call(tmp_path: Path) -> None:
    """Changed frozen code is an archival/identity blocker, not an invented task failure."""
    manifest = make_manifest(tmp_path, [outcome()])
    (tmp_path / "driver.py").write_text("changed")
    output = prepare_output(tmp_path)
    assert supervise(manifest, output) == 2
    assert not (tmp_path / "calls.txt").exists()


def test_background_status_resume_do_not_repeat_completed_work(tmp_path: Path) -> None:
    """Background lifecycle and read-only CLI observation preserve one launch per pair."""
    manifest = make_manifest(tmp_path, [outcome()])
    output = tmp_path / "batch"
    started = batch_command("start", output, manifest)
    pid = cast(int, started["pid"])
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if (output / "status.json").exists():
            if read_json(output / "status.json")["state"] == "completed":
                break
        time.sleep(0.02)
    assert batch_command("status", output)["state"] == "completed"
    assert batch_command("resume", output)["state"] == "completed"
    assert main(["e1-batch", "status", "--output", str(output)]) == 0
    assert (tmp_path / "calls.txt").read_text().splitlines() == ["pair-0"]
    os.waitpid(pid, 0)


def test_supervisor_attaches_to_live_worker_without_new_command(tmp_path: Path) -> None:
    """A supervisor restart only observes a worker's exact receipt and original outcome."""
    row = outcome()
    row["delay"] = 1.0
    manifest = make_manifest(tmp_path, [row])
    output = prepare_output(tmp_path)
    worker = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "roboguide_eval.e1_batch",
            "worker",
            str(manifest),
            str(output),
            "pair-0",
        ]
    )
    try:
        receipt = output / "jobs/pair-0/receipt.json"
        deadline = time.monotonic() + 10
        while not receipt.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert receipt.exists()
        assert supervise(manifest, output) == 0
        assert (tmp_path / "calls.txt").read_text().splitlines() == ["pair-0"]
    finally:
        worker.wait(timeout=10)


@pytest.mark.parametrize("native_label", ["native", "emos"])
def test_summary_preserves_false_and_unknown(tmp_path: Path, native_label: str) -> None:
    """Both native labels preserve tri-state official truth independently of local completion."""
    outcomes = [outcome() for _ in range(3)]
    for document, official in zip(outcomes, (True, False, None), strict=True):
        native = cast(list[JSONObject], document["arms"])[0]
        native["arm"] = native_label
        native["official_pddl_success"] = official
    spec = BatchSpec.load(make_manifest(tmp_path, outcomes))
    result = summarize_batch(spec, {f"pair-{index}": row for index, row in enumerate(outcomes)})
    assert result["strict_fairness_claim"] is False
    native_summary = cast(JSONObject, cast(JSONObject, result["arms"])["native"])
    assert native_summary["finished"] == 3
    assert native_summary["official_true"] == 1
    assert native_summary["official_false"] == 1
    assert native_summary["official_unavailable"] == 1
    roboguide_summary = cast(JSONObject, cast(JSONObject, result["arms"])["roboguide"])
    assert roboguide_summary["official_true"] == 0
    assert roboguide_summary["official_unavailable"] == 3
    assert roboguide_summary["formal_admitted"] == 3


def test_measured_probe_enables_two_actual_workers(tmp_path: Path) -> None:
    """The first pair stays serial; the next two rendezvous only after measured admission."""
    first = outcome()
    first["resource_usage"] = {"both_arms_measured": True, "peak_job_gpu_mib": 6000}
    second, third = outcome(), outcome()
    second.update({"barrier": str(tmp_path / "a"), "peer": str(tmp_path / "b")})
    third.update({"barrier": str(tmp_path / "b"), "peer": str(tmp_path / "a")})
    manifest = make_manifest(tmp_path, [first, second, third], workers=2)
    output = prepare_output(tmp_path)
    assert supervise(manifest, output) == 0
    assert read_json(output / "status.json")["workers"] == 2
    assert read_json(output / "SUMMARY.json")["infrastructure_pairs"] == 0
    assert (tmp_path / "calls.txt").read_text().splitlines()[0] == "pair-0"


def test_fatal_pair_preserves_evidence_and_pauses(tmp_path: Path) -> None:
    """Identity or reset inconsistency pauses new dispatch without rewriting raw arm truth."""
    manifest = make_manifest(tmp_path, [outcome(fatal=True), outcome()])
    output = prepare_output(tmp_path)
    assert supervise(manifest, output) == 2
    assert (tmp_path / "calls.txt").read_text().splitlines() == ["pair-0"]
    assert read_json(tmp_path / "outcome-0.json")["arms"] == outcome()["arms"]


def test_log_write_failure_is_explicit(tmp_path: Path) -> None:
    """Filesystem failure becomes harness evidence rather than a successful closed log."""
    target = tmp_path / "directory"
    target.mkdir()
    status: JSONObject = {"closed": False}
    _capture_log(io.BytesIO(b"physical result"), target, (), status)
    assert status["closed"] is False and status["error_type"] == "IsADirectoryError"


def test_wall_timeout_never_invents_official_failure(tmp_path: Path) -> None:
    """An owned timed-out driver is stopped once and its missing benchmark stays unavailable."""
    row = outcome()
    row["delay"] = 10
    manifest = make_manifest(tmp_path, [row])
    document = read_json(manifest)
    cast(list[JSONObject], document["jobs"])[0]["timeout_seconds"] = 0.2
    write_json(manifest, document)
    output = prepare_output(tmp_path)
    assert run_worker(manifest, output, "pair-0") == 0
    result = read_json(output / "jobs/pair-0/result.json")
    assert result["error_type"] == "pair_wall_timeout"
    assert result["arms"] == [] and result["infrastructure_failure"] is True
    assert run_worker(manifest, output, "pair-0") == 3


def test_user_pause_does_not_launch_queued_pairs(tmp_path: Path) -> None:
    """A stop marker prevents new work without claiming or running the remaining pair."""
    manifest = make_manifest(tmp_path, [outcome()])
    output = prepare_output(tmp_path)
    batch_command("stop", output)
    assert supervise(manifest, output) == 2
    assert not (tmp_path / "calls.txt").exists()


def test_corrupt_worker_result_is_preserved(tmp_path: Path) -> None:
    """Malformed derived evidence fails closed without deleting its original bytes."""
    spec = BatchSpec.load(make_manifest(tmp_path, [outcome()]))
    output = prepare_output(tmp_path)
    directory = output / "jobs/pair-0"
    directory.mkdir(parents=True)
    (directory / "result.json").write_text("partial{")
    result = _job_result(directory, spec.jobs[0])
    assert result is not None and result["fatal_failure"] is True
    assert (directory / "result.json").read_text() == "partial{"
