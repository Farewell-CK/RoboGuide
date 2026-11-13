"""Actual child hashing/copy, wait proof, once-only replay and historical authority fences."""

from __future__ import annotations

import concurrent.futures
import copy
import hashlib
import json
import os
import select
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from roboguide_memory_provider.jobs import FileJobs
from roboguide_memory_provider.process_jobs import ProcessFileJobs
from roboguide_memory_provider.store import MemoryStore, canonical, original_content_digest
from roboguide_memory_provider.use_leases import UseLeases, kernel_clock
from test_jobs import declared_file
from test_use_leases import prepared as use_lease_prepared


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


def worker_replies(reader: Any) -> Iterator[dict[str, Any]]:
    """Yield bounded control replies without losing a coalesced pipe read."""
    pending = b""
    while True:
        while b"\n" not in pending:
            assert select.select([reader], [], [], 3)[0]
            chunk = os.read(reader.fileno(), 16385)
            assert chunk
            pending += chunk
        line, pending = pending.split(b"\n", 1)
        assert len(line) <= 16383
        message = json.loads(line)
        assert isinstance(message, dict)
        yield message


def fresh_worker_permit() -> bytes:
    """Freeze one same-kernel read interval for an actual direct worker test."""
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


def start_direct_worker(*descriptors: int) -> subprocess.Popen[bytes]:
    """Start the real file worker with only the explicitly inherited descriptors."""
    return subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "roboguide_memory_provider/file_worker.py"),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        pass_fds=descriptors,
        close_fds=True,
        env={
            "PATH": os.defpath,
            "LANG": "C.UTF-8",
            "ROBOGUIDE_FILE_WORKER_CPU_SECONDS": "30",
            "ROBOGUIDE_FILE_WORKER_AS_BYTES": str(256 * 1024 * 1024),
        },
    )


@pytest.fixture(name="prepared_use")
def prepared_use_fixture(
    tmp_path: Path,
) -> Iterator[tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]]]:
    """Reuse the actual import/use fixture before replacing its source with typed M."""
    fixture = cast(
        Callable[[Path], Iterator[tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]]]],
        cast(Any, use_lease_prepared).__wrapped__,
    )
    yield from fixture(tmp_path)


def typed_navigation(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], bytes, Path, str]:
    """Build one exact navigation attempt with a typed occupancy M and live-use request."""
    store, _, base_request, base_invocation = prepared
    control = copy.deepcopy(base_request["control"])
    payload = b"typed navigation M\n" * 1024
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    source = store.root.parent / f"typed-m-{uuid.uuid4().hex}.bin"
    source.write_bytes(payload)
    group, task, role = (
        base_invocation["group_id"],
        base_invocation["task_id"],
        base_invocation["role_id"],
    )
    execution = (
        "attempt-"
        + "-".join(f"{len(value.encode('utf-8'))}:{value}" for value in (group, task, role))
        + "-1"
    )
    map_id = "typed-m-" + uuid.uuid4().hex
    task_ref = {"mission_id": base_invocation["mission_id"], "task_id": task}
    typed = {
        "schema": "roboguide.map-manifest/v0.2",
        "map_id": map_id,
        "revision_id": "root",
        "producer_node_id": "unit-producer",
        "producer_local_system_id": store.provider_id,
        "producer_provider_id": store.provider_id,
        "source_mission_id": base_invocation["mission_id"],
        "source_execution_id": "unit-production-attempt",
        "source_task_ref": task_ref,
        "media_type": "application/vnd.roboguide.spatial-map",
        "payload_schema": "roboguide.spatial-occupancy-map/v0.1",
        "content_digest": digest,
        "byte_size": len(payload),
        "created_at_ms": 1,
        "root_frame": "map",
        "coordinate_convention": "ROS",
        "spatial_anchor": {"kind": "fixed", "id": "unit-anchor"},
    }
    manifest = {
        "schema": "roboguide.memory-manifest/v0.1",
        "selector": {"memory_id": map_id, "revision_id": "root"},
        "kind": "spatial",
        "provider_id": store.provider_id,
        "owner": {
            "owner": "node",
            "node_id": "unit-producer",
            "local_system_id": store.provider_id,
        },
        "scope": {"kind": "global"},
        "visibility": "exchangeable",
        "payload_schema": typed["payload_schema"],
        "media_type": typed["media_type"],
        "artifact": {"content_digest": digest, "byte_size": len(payload)},
        "source_mission_id": typed["source_mission_id"],
        "source_execution_id": typed["source_execution_id"],
        "source_task_ref": task_ref,
        "created_at": 1,
    }
    import_id = uuid.uuid4().hex
    original_invocation = {
        "mission_id": base_invocation["mission_id"],
        "group_id": group,
        "task_id": task,
        "role_id": role,
        "execution_id": execution,
        "import_id": import_id,
        "command_id": "memory-import-" + import_id,
        "consumer_provider_id": store.provider_id,
        "manifest_json": canonical(manifest).decode(),
        "memory_preparation_id": control["preparation_id"],
        "session_id": control["source_session_id"],
        "typed_map_manifest_json": canonical(typed).decode(),
    }
    original = {
        "import_id": import_id,
        "command_id": original_invocation["command_id"],
        "provider_id": store.provider_id,
        "provider_instance_id": store.instance_id,
        "identity_digest": hashlib.sha256(canonical(original_invocation)).hexdigest(),
        "provider_contract_digest": base_request["imports"][0]["provider_contract_digest"],
        "original_invocation": original_invocation,
        "admitted_at_ms": 1,
        "original_content_digest": original_content_digest(original_invocation),
    }
    store.import_artifact(original, manifest, str(source))
    inputs = [
        {
            "manifest": manifest,
            "typed_map": typed,
            "consumer": {"provider_id": store.provider_id},
            "import_id": import_id,
        }
    ]
    canonical_invocation = {
        "mission_id": base_invocation["mission_id"],
        "group_id": group,
        "task_id": task,
        "role_id": role,
        "execution_id": execution,
        "operation": "mobility.navigate@v1",
        "parameters": {"destination": {"x_m": 1.0, "y_m": 2.0}},
    }
    preparation = {
        "schema": "roboguide.memory-preparation/v0.2",
        "preparation_id": control["preparation_id"],
        "tenant_id": control["tenant_id"],
        "group_id": group,
        "execution_id": execution,
        "node_id": control["node_id"],
        "source_session_id": control["source_session_id"],
        "registry_id": control["registry_id"],
        "mission_id": base_invocation["mission_id"],
        "task_id": task,
        "role_id": role,
        "inputs": inputs,
        "invocation": canonical_invocation,
    }
    encoded = canonical(preparation)
    preparation_digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
    control["execution_id"] = execution
    control["preparation_digest"] = preparation_digest
    request = {
        "schema": base_request["schema"],
        "control": control,
        "preparation_json": encoded.decode(),
        "imports": [original],
        "receiver_budget": copy.deepcopy(base_request["receiver_budget"]),
    }
    invocation = {
        **canonical_invocation,
        "execution_generation": 1,
        "memory_preparation": {
            "preparation_id": control["preparation_id"],
            "preparation_digest": preparation_digest,
            "use_lease_id": control["lease_id"],
            "tenant_id": control["tenant_id"],
            "inputs": inputs,
        },
    }
    return invocation, request, payload, source, execution


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


def test_private_copy_returns_exact_anonymous_typed_m_descriptor(
    prepared_use: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
) -> None:
    """One navigation attempt copies typed M under its original process authority."""
    store, leases, _, _ = prepared_use
    invocation, request, payload, source, execution = typed_navigation(prepared_use)
    assert leases.apply(request)["status"] == "granted"
    jobs = ProcessFileJobs(store, leases, max_job_seconds=30)
    descriptor = -1
    try:
        descriptor, receipt = jobs.private_copy(invocation)
        metadata = os.fstat(descriptor)
        assert stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 0
        assert metadata.st_size == len(payload)
        assert os.read(descriptor, len(payload) + 1) == payload
        assert receipt == {
            "schema": "roboguide.file-private-copy-result/v0.1",
            "execution_id": execution,
            "backend_instance_id": store.instance_id,
            "content_digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
            "byte_size": len(payload),
        }
        assert not {"path", "bytes", "descriptor", "source"} & set(receipt)
        outcome = jobs.status(execution)
        assert outcome["state"] == "COMPLETED" and outcome["result"] == receipt
        observation = observations(jobs)[0]
        assert observation["terminal_state"] == "COMPLETED"
        assert observation["process_exit"]["exit_code"] == 0
        assert observation["process_exit"]["pid"] != os.getpid()
        assert jobs.quota_evidence(execution)["kernel_proof"] is not None
        source.unlink()
        with pytest.raises(ValueError, match="cannot be replayed"):
            jobs.private_copy(invocation)
        assert jobs.status(execution) == outcome
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        jobs.close()


def test_private_copy_refuses_non_navigation_or_untyped_m_before_admission(
    prepared_use: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
) -> None:
    """The explicit entry point cannot become a generic hash or untyped file export."""
    store, leases, _, _ = prepared_use
    invocation, _, _, _, _ = typed_navigation(prepared_use)
    jobs = ProcessFileJobs(store, leases)
    try:
        with pytest.raises(ValueError, match="mobility.navigate@v1"):
            jobs.private_copy({**invocation, "operation": "compute.hash-files@v1"})
        wrong_generation = copy.deepcopy(invocation)
        wrong_generation["execution_generation"] = 2
        with pytest.raises(ValueError, match="canonical attempt"):
            jobs.private_copy(wrong_generation)
        untyped = copy.deepcopy(invocation)
        untyped["memory_preparation"]["inputs"][0]["typed_map"] = None
        with pytest.raises(ValueError, match="typed spatial M"):
            jobs.private_copy(untyped)
        assert jobs._db.execute("SELECT COUNT(*) FROM process_jobs").fetchone() == (0,)
        assert jobs._db.execute("SELECT COUNT(*) FROM original_processes").fetchone() == (0,)
    finally:
        jobs.close()


def test_interrupted_private_copy_admission_remains_unknown_and_nonreplayable(
    prepared_use: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
) -> None:
    """A copied descriptor cannot be recreated from durable completion or Unknown intent."""
    store, leases, _, _ = prepared_use
    invocation, request, _, _, execution = typed_navigation(prepared_use)
    assert leases.apply(request)["status"] == "granted"
    jobs = ProcessFileJobs(store, leases)
    try:
        assert jobs._admit(invocation) is not None
        jobs._active.clear()  # Simulate loss after durable admission and before a child starts.
        jobs.close()
        jobs = ProcessFileJobs(store, leases)
        assert jobs.status(execution)["state"] == "UNKNOWN"
        with pytest.raises(ValueError, match="cannot be replayed"):
            jobs.private_copy(invocation)
        assert jobs.status(execution)["state"] == "UNKNOWN"
        assert jobs._processes == {}
    finally:
        jobs.close()


def test_private_copy_worker_copies_exact_bytes_under_per_chunk_permits(
    tmp_path: Path,
) -> None:
    """Copy only an authorized source into an unlinked temporary descriptor."""
    payload = bytes(range(256)) * 4096 + b"authorized-tail"
    source = tmp_path / "authorized-map.bin"
    source.write_bytes(payload)
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    source_descriptor = os.open(source, os.O_RDONLY)
    destination = tempfile.TemporaryFile()
    process = start_direct_worker(source_descriptor, destination.fileno())
    assert process.stdin is not None and process.stdout is not None
    replies = worker_replies(process.stdout)
    try:
        start = {
            "schema": "roboguide.file-private-copy-start/v0.1",
            "source": {
                "fd": source_descriptor,
                "digest": digest,
                "size": len(payload),
            },
            "destination_fd": destination.fileno(),
            "max_bytes": len(payload),
        }
        process.stdin.write(canonical(start) + b"\n")
        process.stdin.flush()
        assert next(replies) == {
            "kind": "os-quota",
            "schema": "roboguide.file-worker-os-quota/v0.1",
            "pid": process.pid,
            "cpu_seconds": [30, 30],
            "address_space_bytes": [256 * 1024 * 1024, 256 * 1024 * 1024],
        }
        assert next(replies) == {
            "kind": "ready",
            "schema": "roboguide.file-private-copy-start/v0.1",
            "digest": digest,
            "size": len(payload),
        }
        assert os.lseek(source_descriptor, 0, os.SEEK_CUR) == 0
        assert destination.tell() == 0

        for _ in range(2):
            assert next(replies) == {"kind": "permit"}
            process.stdin.write(fresh_worker_permit())
            process.stdin.flush()
        assert next(replies) == {
            "kind": "result",
            "digest": digest,
            "size": len(payload),
        }
        assert process.wait(timeout=3) == 0
        destination.seek(0)
        assert destination.read() == payload
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        process.stdin.close()
        process.stdout.close()
        destination.close()
        os.close(source_descriptor)


def test_private_copy_worker_rejects_digest_mismatch_without_result(tmp_path: Path) -> None:
    """A private copy reports success only when copied bytes equal the authorized digest."""
    payload = b"authorized-map-bytes"
    source = tmp_path / "authorized-map.bin"
    source.write_bytes(payload)
    source_descriptor = os.open(source, os.O_RDONLY)
    destination = tempfile.TemporaryFile()
    process = start_direct_worker(source_descriptor, destination.fileno())
    assert process.stdin is not None and process.stdout is not None
    replies = worker_replies(process.stdout)
    try:
        wrong_digest = "sha256:" + "0" * 64
        start = {
            "schema": "roboguide.file-private-copy-start/v0.1",
            "source": {"fd": source_descriptor, "digest": wrong_digest, "size": len(payload)},
            "destination_fd": destination.fileno(),
            "max_bytes": len(payload),
        }
        process.stdin.write(canonical(start) + b"\n")
        process.stdin.flush()
        assert next(replies)["kind"] == "os-quota"
        assert next(replies) == {
            "kind": "ready",
            "schema": "roboguide.file-private-copy-start/v0.1",
            "digest": wrong_digest,
            "size": len(payload),
        }
        assert next(replies) == {"kind": "permit"}
        process.stdin.write(fresh_worker_permit())
        process.stdin.flush()
        assert next(replies) == {
            "kind": "error",
            "reason": "private copy bytes differ from their authorized identity",
        }
        assert process.wait(timeout=3) == 1
        destination.seek(0)
        assert destination.read() == payload
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        process.stdin.close()
        process.stdout.close()
        destination.close()
        os.close(source_descriptor)


def test_private_copy_worker_refuses_over_budget_source_before_ready(tmp_path: Path) -> None:
    """The parent-supplied byte maximum refuses an oversized source before any permit."""
    source = tmp_path / "authorized-map.bin"
    source.write_bytes(b"map")
    source_descriptor = os.open(source, os.O_RDONLY)
    destination = tempfile.TemporaryFile()
    process = start_direct_worker(source_descriptor, destination.fileno())
    assert process.stdin is not None and process.stdout is not None
    replies = worker_replies(process.stdout)
    try:
        start = {
            "schema": "roboguide.file-private-copy-start/v0.1",
            "source": {
                "fd": source_descriptor,
                "digest": "sha256:" + hashlib.sha256(b"map").hexdigest(),
                "size": 3,
            },
            "destination_fd": destination.fileno(),
            "max_bytes": 2,
        }
        process.stdin.write(canonical(start) + b"\n")
        process.stdin.flush()
        assert next(replies) == {
            "kind": "error",
            "reason": "private copy bounds or source evidence are invalid",
        }
        assert process.wait(timeout=3) == 1
        assert os.lseek(source_descriptor, 0, os.SEEK_CUR) == 0
        assert destination.tell() == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        process.stdin.close()
        process.stdout.close()
        destination.close()
        os.close(source_descriptor)


def test_private_copy_worker_refuses_linked_destination_before_ready(tmp_path: Path) -> None:
    """Only a parent-owned unlinked temporary descriptor can receive copied bytes."""
    payload = b"map"
    source = tmp_path / "authorized-map.bin"
    source.write_bytes(payload)
    source_descriptor = os.open(source, os.O_RDONLY)
    with tempfile.NamedTemporaryFile() as destination:
        process = start_direct_worker(source_descriptor, destination.fileno())
        assert process.stdin is not None and process.stdout is not None
        replies = worker_replies(process.stdout)
        try:
            start = {
                "schema": "roboguide.file-private-copy-start/v0.1",
                "source": {
                    "fd": source_descriptor,
                    "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
                    "size": len(payload),
                },
                "destination_fd": destination.fileno(),
                "max_bytes": len(payload),
            }
            process.stdin.write(canonical(start) + b"\n")
            process.stdin.flush()
            assert next(replies) == {
                "kind": "error",
                "reason": "private copy destination is not an empty unlinked temporary output",
            }
            assert process.wait(timeout=3) == 1
            assert destination.tell() == 0
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            process.stdin.close()
            process.stdout.close()
            os.close(source_descriptor)


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
