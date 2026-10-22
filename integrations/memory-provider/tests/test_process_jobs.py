"""Actual child hashing, original wait proof, once-only replay and historical authority fences."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import select
import signal
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from roboguide_memory_provider.jobs import FileJobs
from roboguide_memory_provider.process_jobs import ProcessFileJobs
from roboguide_memory_provider.store import canonical
from roboguide_memory_provider.use_leases import kernel_clock
from test_jobs import declared_file


def attempt(invocation: dict[str, Any]) -> dict[str, Any]:
    """Declare exact software operation metadata, with no Node, device or physical observation."""
    return {
        **invocation,
        "execution_id": "attempt-5:group-4:hash-6:worker-1",
        "group_id": "group",
        "task_id": "hash",
        "role_id": "worker",
        "execution_generation": 1,
    }


def until(predicate: Callable[[], object]) -> None:
    """Wait only for owned actual software effects under a short independent test deadline."""
    deadline = time.monotonic() + 5
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("owned original process did not reach its expected boundary")
        time.sleep(0.01)


def observations(jobs: ProcessFileJobs) -> list[dict[str, Any]]:
    """Require actual typed observation objects before inspecting original process evidence."""
    rows = jobs.safe_state()["observations"]
    assert isinstance(rows, list)
    assert all(isinstance(row, dict) for row in rows)
    return [dict(row) for row in rows]


def test_actual_file_child_exit_and_independent_hash_survive_restart(tmp_path: Path) -> None:
    """An independent digest and actual reaped PID persist; replay performs no further read."""
    store, original, source = declared_file(tmp_path)
    invocation = attempt(original)
    jobs = ProcessFileJobs(store)
    try:
        outcome = jobs.compute(invocation)
        assert outcome["state"] == "COMPLETED", outcome
        result = outcome["result"]
        assert isinstance(result, dict)
        assert result["input_digests"] == [
            "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
        ]
        observation = observations(jobs)[0]
        proof = observation["process_exit"]
        assert proof["pid"] != os.getpid() and proof["exit_code"] == 0
        assert observation["terminal_state"] == "COMPLETED" and observation["command_id"] is None
        with pytest.raises(ProcessLookupError):
            os.kill(proof["pid"], 0)
        source.unlink()
        jobs.close()
        with pytest.raises(ValueError, match="explicitly configured"):
            FileJobs(store)
        jobs = ProcessFileJobs(store)
        assert jobs.compute(invocation) == outcome
        assert jobs.safe_state()["observations"] == [observation]
        assert jobs._processes == {}
    finally:
        jobs.close()
        store.close()


def test_actual_running_child_has_no_proof_until_cancel_and_reap(tmp_path: Path) -> None:
    """A durable cancellation flag cannot fabricate exit while the original process lives."""
    store, original, _ = declared_file(tmp_path)
    invocation = attempt(original)
    invocation["parameters"]["rounds"] = 16_777_216
    jobs = ProcessFileJobs(store, max_job_seconds=30, max_job_rounds=16_777_216)
    try:
        jobs.submit(invocation)
        until(lambda: invocation["execution_id"] in jobs._processes)
        process = jobs._processes[invocation["execution_id"]]
        assert process.poll() is None
        assert jobs.safe_state()["observations"] == []
        jobs.cancel(invocation["execution_id"])
        until(lambda: jobs.status(invocation["execution_id"])["state"] == "CANCELLED")
        proof = observations(jobs)[0]["process_exit"]
        assert proof["pid"] == process.pid and proof["exit_code"] < 0
        assert process.returncode == proof["exit_code"]
        assert not jobs._active
    finally:
        jobs.close()
        store.close()


def test_actual_child_receives_explicit_os_quota_before_read_permission(
    tmp_path: Path,
) -> None:
    """The admitted worker carries real POSIX CPU/address-space limits before hashing bytes."""
    store, original, _ = declared_file(tmp_path)
    invocation = attempt(original)
    invocation["parameters"]["rounds"] = 16_777_216
    jobs = ProcessFileJobs(store, max_job_seconds=30, max_job_rounds=16_777_216)
    try:
        jobs.submit(invocation)
        until(lambda: invocation["execution_id"] in jobs._processes)
        process = jobs._processes[invocation["execution_id"]]
        until(
            lambda: (
                "30"
                in next(
                    line
                    for line in Path(f"/proc/{process.pid}/limits").read_text().splitlines()
                    if line.startswith("Max cpu time")
                )
            )
        )
        limits = Path(f"/proc/{process.pid}/limits").read_text()
        assert "Max cpu time" in limits and "30" in next(
            line for line in limits.splitlines() if line.startswith("Max cpu time")
        )
        address_line = next(
            line for line in limits.splitlines() if line.startswith("Max address space")
        )
        assert "268435456" in address_line
        until(lambda: jobs.quota_evidence(invocation["execution_id"])["kernel_proof"] is not None)
        evidence = jobs.quota_evidence(invocation["execution_id"])
        assert evidence["pid"] == process.pid
        assert evidence["declaration"] == {
            "schema": "roboguide.file-worker-os-quota/v0.1",
            "cpu_seconds": 30,
            "address_space_bytes": 256 * 1024 * 1024,
        }
        assert evidence["kernel_proof"] == {
            "kind": "os-quota",
            "schema": "roboguide.file-worker-os-quota/v0.1",
            "pid": process.pid,
            "cpu_seconds": [30, 30],
            "address_space_bytes": [256 * 1024 * 1024, 256 * 1024 * 1024],
        }
        jobs.cancel(invocation["execution_id"])
        until(lambda: jobs.status(invocation["execution_id"])["state"] == "CANCELLED")
        assert jobs.health()["os_quota"] == {
            "schema": "roboguide.file-worker-os-quota/v0.1",
            "configured_enforcement": "posix-setrlimit",
            "scope": "new-admissions",
            "cpu_seconds": 30,
            "address_space_bytes": 256 * 1024 * 1024,
        }
    finally:
        jobs.close()
        store.close()


def test_natural_child_failure_reports_real_exit_without_cancel_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Actual byte corruption fails hashing; original OS failure remains distinct from Cancel."""
    store, original, source = declared_file(tmp_path)
    invocation = attempt(original)
    payload = source.read_bytes()
    jobs = ProcessFileJobs(store)
    original_popen = subprocess.Popen

    def corrupt_after_input_validation(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        """Change real bytes only after the parent's independent source validation and opening."""
        source.write_bytes(bytes([payload[0] ^ 1]) + payload[1:])
        return original_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", corrupt_after_input_validation)
    try:
        outcome = jobs.compute(invocation)
        assert outcome["state"] == "FAILED", outcome
        proof = observations(jobs)[0]
        assert proof["terminal_state"] == "FAILED"
        assert proof["process_exit"]["exit_code"] == 1
        assert jobs._db.execute("SELECT count(*) FROM software_stop_commands").fetchone() == (0,)
    finally:
        jobs.close()
        store.close()


def test_child_exit_before_first_write_retains_wait_after_actual_broken_pipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An actual dead child may break flush/close; neither may erase its native wait result."""
    store, original, _ = declared_file(tmp_path)
    invocation = attempt(original)
    jobs = ProcessFileJobs(store)
    original_popen = subprocess.Popen
    children: list[subprocess.Popen[bytes]] = []

    def reap_before_parent_control_write(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        """Terminate and wait only for this real owned child before returning its actual pipe."""
        process = original_popen(*args, **kwargs)
        process.terminate()
        process.wait(timeout=5)
        children.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", reap_before_parent_control_write)
    try:
        outcome = jobs.compute(invocation)
        assert outcome["state"] == "FAILED" and "Broken pipe" in str(outcome["reason"])
        proof = observations(jobs)[0]
        assert proof["terminal_state"] == "FAILED"
        assert proof["process_exit"]["pid"] == children[0].pid
        assert proof["process_exit"]["exit_code"] == children[0].returncode < 0
        assert jobs.quota_evidence(invocation["execution_id"])["kernel_proof"] is None
        assert jobs._db.execute("SELECT count(*) FROM software_stop_commands").fetchone() == (0,)
        assert not jobs.reclamation_blockers()
        jobs.close()
        jobs = ProcessFileJobs(store)
        assert observations(jobs) == [proof]
        assert jobs.compute(invocation) == outcome and jobs._processes == {}
    finally:
        jobs.close()
        store.close()


def test_old_outcomes_migrate_without_acquiring_process_evidence(tmp_path: Path) -> None:
    """Original thread results remain byte-for-byte history and never acquire an invented PID."""
    store, original, _ = declared_file(tmp_path)
    invocation = attempt(original)
    legacy = FileJobs(store)
    outcome = legacy.compute(invocation)
    legacy.close()
    with sqlite3.connect(store.root / "file-jobs.sqlite3") as connection:
        before = connection.execute("SELECT * FROM jobs").fetchall()
    jobs = ProcessFileJobs(store)
    try:
        assert jobs.compute(invocation) == outcome
        assert jobs._db.execute("SELECT * FROM process_jobs").fetchall() == before
        assert jobs.safe_state()["observations"] == []
        assert jobs._db.execute("SELECT count(*) FROM original_processes").fetchone() == (0,)
        with pytest.raises(sqlite3.OperationalError):
            jobs._db.execute("UPDATE jobs SET state='COMPLETED'")
        assert json.loads(before[0][1]) == invocation
    finally:
        jobs.close()
        store.close()


def test_v1_process_rows_migrate_with_quota_evidence_unavailable(tmp_path: Path) -> None:
    """Legacy actual process history receives v2 columns but never current quota defaults."""
    store, _, _ = declared_file(tmp_path)
    execution = "attempt-legacy:group:hash:worker-1"
    with sqlite3.connect(store.root / "file-jobs.sqlite3") as connection:
        connection.executescript(
            "CREATE TABLE process_jobs (execution_id TEXT PRIMARY KEY, invocation_json BLOB, "
            "state TEXT NOT NULL, result_json BLOB, reason TEXT NOT NULL);"
            "CREATE TABLE file_job_format (schema TEXT PRIMARY KEY);"
            "INSERT INTO file_job_format VALUES ('roboguide.file-process-jobs/v0.1');"
            "CREATE TABLE original_processes (execution_id TEXT PRIMARY KEY, "
            "instance_id TEXT NOT NULL UNIQUE, backend_instance_id TEXT NOT NULL, "
            "invocation_digest TEXT NOT NULL, budget_json BLOB NOT NULL, pid INTEGER, "
            "exit_json BLOB);"
            "INSERT INTO original_processes VALUES "
            "('attempt-legacy:group:hash:worker-1','legacy-instance','legacy-backend',"
            "'sha256:legacy','{}',NULL,NULL);"
            "PRAGMA user_version=1;"
        )
    jobs = ProcessFileJobs(store)
    try:
        assert jobs._db.execute("PRAGMA user_version").fetchone() == (2,)
        assert jobs.quota_evidence(execution) == {
            "execution_id": execution,
            "pid": None,
            "declaration": None,
            "kernel_proof": None,
        }
    finally:
        jobs.close()
        store.close()


def test_pid_commit_failure_never_grants_child_computation_or_exit_authority(
    tmp_path: Path,
) -> None:
    """Fail PID commit; reaping the real child cannot invent its missing durable PID."""
    store, original, _ = declared_file(tmp_path)
    invocation = attempt(original)
    jobs = ProcessFileJobs(store)
    try:
        jobs._db.execute(
            "CREATE TRIGGER fail_original_pid BEFORE UPDATE OF pid ON original_processes "
            "BEGIN SELECT RAISE(ABORT,'original PID commit failure'); END"
        )
        outcome = jobs.compute(invocation)
        assert outcome["state"] == "FAILED", outcome
        assert observations(jobs) == []
        assert jobs._db.execute("SELECT pid,exit_json FROM original_processes").fetchone() == (
            None,
            None,
        )
        process = jobs._processes[invocation["execution_id"]]
        assert process.returncode is not None
        assert jobs.health()["safe"] is False
        assert (
            jobs.reclamation_blockers() == []
        )  # This fixture reads an external export, not local CAS.
    finally:
        jobs.close()
        store.close()


def test_original_process_budget_includes_real_admission_sql_wait(tmp_path: Path) -> None:
    """A real SQLite writer delay consumes the original kernel interval before any Popen."""
    store, original, _ = declared_file(tmp_path)
    jobs = ProcessFileJobs(store, max_job_seconds=1)
    try:
        with sqlite3.connect(store.root / "file-jobs.sqlite3") as lock:
            lock.execute("BEGIN IMMEDIATE")
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                computation = pool.submit(jobs.compute, attempt(original))
                until(lambda: jobs._admitting_clock is not None)
                time.sleep(1.1)
                lock.rollback()
                outcome = computation.result(timeout=5)
        assert outcome["state"] == "FAILED", outcome
        assert jobs._processes == {} and observations(jobs) == []
        budget = json.loads(
            jobs._db.execute("SELECT budget_json FROM original_processes").fetchone()[0]
        )
        assert budget["deadline_lower_ms"] - budget["received_lower_ms"] == 1000
        assert kernel_clock()[2] >= budget["deadline_lower_ms"]
    finally:
        jobs.close()
        store.close()


def test_suspended_child_cannot_read_with_a_queued_expired_permit(tmp_path: Path) -> None:
    """Suspend a real worker before permit delivery; expiry causes a fresh request, with no read."""
    source = tmp_path / "input.bin"
    source.write_bytes(Path(__file__).read_bytes())
    descriptor = os.open(source, os.O_RDONLY)
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "roboguide_memory_provider/file_worker.py"),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        pass_fds=(descriptor,),
        close_fds=True,
        env={
            "PATH": os.defpath,
            "LANG": "C.UTF-8",
            "ROBOGUIDE_FILE_WORKER_CPU_SECONDS": "30",
            "ROBOGUIDE_FILE_WORKER_AS_BYTES": str(256 * 1024 * 1024),
        },
    )
    assert process.stdin is not None and process.stdout is not None
    reader = process.stdout

    def reply() -> dict[str, Any]:
        """Bound every read of the actual child control channel independently."""
        assert select.select([reader], [], [], 3)[0]
        message = json.loads(reader.readline(16385))
        assert isinstance(message, dict)
        return message

    def fresh_permit() -> bytes:
        """Freeze an actual same-kernel read interval without changing the worker's clock."""
        boot, namespace, lower, _ = kernel_clock()
        return (
            canonical(
                {
                    "boot_id": boot,
                    "time_namespace": namespace,
                    "received_lower_ms": lower,
                    "deadline_lower_ms": lower + 100,
                }
            )
            + b"\n"
        )

    try:
        start = {
            "schema": "roboguide.file-process-start/v0.1",
            "rounds": 1,
            "inputs": [
                {
                    "fd": descriptor,
                    "size": source.stat().st_size,
                    "digest": "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest(),
                }
            ],
        }
        process.stdin.write(canonical(start) + b"\n")
        process.stdin.flush()
        assert reply() == {
            "kind": "os-quota",
            "schema": "roboguide.file-worker-os-quota/v0.1",
            "pid": process.pid,
            "cpu_seconds": [30, 30],
            "address_space_bytes": [256 * 1024 * 1024, 256 * 1024 * 1024],
        }
        assert reply() == {"kind": "permit"}
        process.stdin.write(fresh_permit())
        process.stdin.flush()
        assert reply() == {"kind": "permit"}
        os.kill(process.pid, signal.SIGSTOP)
        until(
            lambda: (
                Path(f"/proc/{process.pid}/status")
                .read_text()
                .split("State:", 1)[1]
                .lstrip()
                .startswith("T")
            )
        )
        process.stdin.write(fresh_permit())
        process.stdin.flush()
        time.sleep(0.15)
        os.kill(process.pid, signal.SIGCONT)
        assert reply() == {"kind": "permit"}
        assert os.lseek(descriptor, 0, os.SEEK_CUR) == 0
        process.stdin.close()
        process.wait(timeout=3)
        assert process.returncode != 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        if not process.stdin.closed:
            process.stdin.close()
        process.stdout.close()
        os.close(descriptor)


def test_actual_worker_without_original_os_quota_fails_before_read_permit(
    tmp_path: Path,
) -> None:
    """Missing quota is unavailable; an actual child cannot begin input work from defaults."""
    source = tmp_path / "input.bin"
    source.write_bytes(Path(__file__).read_bytes())
    descriptor = os.open(source, os.O_RDONLY)
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "roboguide_memory_provider/file_worker.py"),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        pass_fds=(descriptor,),
        close_fds=True,
        env={"PATH": os.defpath, "LANG": "C.UTF-8"},
    )
    assert process.stdin is not None and process.stdout is not None
    try:
        assert select.select([process.stdout], [], [], 3)[0]
        message = json.loads(process.stdout.readline(16385))
        assert message == {
            "kind": "error",
            "reason": "original file worker OS quota declaration is unavailable",
        }
        assert process.wait(timeout=3) == 1
        assert os.lseek(descriptor, 0, os.SEEK_CUR) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        process.stdin.close()
        process.stdout.close()
        os.close(descriptor)


def test_real_original_process_proofs_rotate_within_state_payload_bound(tmp_path: Path) -> None:
    """Forty real reaped workers remain discoverable within the 64KiB State contract."""
    store, original, _ = declared_file(tmp_path)
    jobs = ProcessFileJobs(store)
    expected = set()
    try:
        for index in range(40):
            group = "g" * 900 + str(index)
            invocation = {
                **original,
                "group_id": group,
                "task_id": "hash",
                "role_id": "worker",
                "execution_generation": 1,
                "execution_id": f"attempt-{len(group)}:{group}-4:hash-6:worker-1",
            }
            outcome = jobs.compute(invocation)
            assert outcome["state"] == "COMPLETED", outcome
            expected.add(invocation["execution_id"])
        observed: set[str] = set()
        for _ in range(4):
            page = jobs.safe_state()
            assert len(canonical(page)) <= 60 * 1024
            values = page["observations"]
            assert isinstance(values, list)
            observed.update(row["execution_id"] for row in values)
        assert observed == expected
        assert all(process.returncode == 0 for process in jobs._processes.values())
    finally:
        jobs.close()
        store.close()
