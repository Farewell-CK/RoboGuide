"""Check actual CPU/file outcomes, replay and cancellation without a device fixture."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

import pytest
from roboguide_memory_provider.jobs import FileJobs
from roboguide_memory_provider.store import MemoryStore, content_digest


def declared_file(tmp_path: Path) -> tuple[MemoryStore, dict[str, Any], Path]:
    """Authorize one existing source file through the deployment export contract."""
    source = tmp_path / "source.py"
    source.write_bytes(Path(__file__).read_bytes())
    manifest: dict[str, Any] = {
        "selector": {"memory_id": uuid.uuid4().hex, "revision_id": uuid.uuid4().hex},
        "artifact": {
            "content_digest": "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest(),
            "byte_size": source.stat().st_size,
        },
    }
    exports = tmp_path / "exports.json"
    exports.write_text(json.dumps([{"manifest": manifest, "artifact_path": source.name}]))
    store = MemoryStore(
        tmp_path / "backend",
        "actual-files",
        (tmp_path,),
        16 * 1024 * 1024,
        export_config=exports,
        export_directory=tmp_path,
    )
    invocation = {
        "execution_id": uuid.uuid4().hex,
        "parameters": {
            "input_memory_id": manifest["selector"]["memory_id"],
            "input_revision_id": manifest["selector"]["revision_id"],
            "rounds": 2,
        },
    }
    return store, invocation, source


def test_actual_hash_is_independently_verified_and_replay_does_no_read(tmp_path: Path) -> None:
    """An independent hashlib result agrees; restart retains the result after source removal."""
    store, invocation, source = declared_file(tmp_path)
    jobs = FileJobs(store)
    try:
        expected = "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
        outcome = jobs.compute(invocation)
        assert outcome["state"] == "COMPLETED"
        result = outcome["result"]
        assert isinstance(result, dict)
        assert result["input_digests"] == [expected]
        assert result["output_digest"] == content_digest({"input_digests": [expected], "rounds": 2})
        source.unlink()
        assert jobs.compute(invocation) == outcome
        jobs.close()
        jobs = FileJobs(store)
        assert jobs.compute(invocation) == outcome
        assert jobs.health()["safe"] is True
        with pytest.raises(ValueError, match="identity conflicts"):
            jobs.compute({**invocation, "parameters": {**invocation["parameters"], "rounds": 3}})
    finally:
        jobs.close()
        store.close()


def test_mutated_bytes_fail_and_cancel_before_start_never_computes(tmp_path: Path) -> None:
    """Real corruption cannot become completion, and a durable cancel fences late Execute."""
    store, invocation, source = declared_file(tmp_path)
    jobs = FileJobs(store)
    try:
        payload = source.read_bytes()
        source.write_bytes(bytes([payload[0] ^ 1]) + payload[1:])
        outcome = jobs.compute(invocation)
        assert outcome["state"] == "FAILED" and outcome["result"] is None
        cancelled = {**invocation, "execution_id": uuid.uuid4().hex}
        jobs.cancel(cancelled["execution_id"])
        source.unlink()
        assert jobs.compute(cancelled)["state"] == "CANCELLED"
        assert jobs.health()["safe"] is True
    finally:
        jobs.close()
        store.close()


def test_interrupted_original_computation_remains_unknown_on_restart(tmp_path: Path) -> None:
    """Persisted admission without an outcome never authorizes a repeated computation."""
    store, invocation, source = declared_file(tmp_path)
    jobs = FileJobs(store)
    jobs.close()
    connection = sqlite3.connect(store.root / "file-jobs.sqlite3")
    try:
        from roboguide_memory_provider.store import canonical

        connection.execute(
            "INSERT INTO jobs VALUES (?,?,'RUNNING',NULL,'')",
            (invocation["execution_id"], canonical(invocation)),
        )
        connection.commit()
    finally:
        connection.close()
    source.unlink()
    jobs = FileJobs(store)
    try:
        assert jobs.compute(invocation)["state"] == "UNKNOWN"
        assert jobs.status(invocation["execution_id"])["result"] is None
    finally:
        jobs.close()
        store.close()


def test_async_admission_returns_original_handle_and_cancel_joins_actual_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pause a real worker to check durable admission, duplicate routing and cancellation."""
    store, invocation, _ = declared_file(tmp_path)
    jobs = FileJobs(store, max_job_seconds=120)
    entered, release = threading.Event(), threading.Event()
    perform = jobs._perform

    def boundary(
        call: dict[str, Any], admission: tuple[threading.Event, str | None, int, int]
    ) -> dict[str, object]:
        """Hold only the test's real worker boundary without fabricating any computation result."""
        entered.set()
        assert release.wait(timeout=5)
        return perform(call, admission)

    monkeypatch.setattr(jobs, "_perform", boundary)
    try:
        admitted = jobs.submit(invocation)
        assert admitted["state"] == "RUNNING" and entered.wait(timeout=5)
        original = jobs._db.execute("SELECT * FROM job_budgets").fetchall()
        assert len(original) == 1 and original[0][2] == 120_000
        assert jobs.submit(invocation) == admitted and len(jobs._workers) == 1
        assert jobs._db.execute("SELECT * FROM job_budgets").fetchall() == original
        jobs.cancel(invocation["execution_id"])
        assert jobs.status(invocation["execution_id"])["state"] == "RUNNING"
        release.set()
        for worker in jobs._workers.values():
            worker.join(timeout=5)
            assert not worker.is_alive()
        assert jobs.status(invocation["execution_id"])["state"] == "CANCELLED"
        assert jobs.status(invocation["execution_id"])["result"] is None
        assert jobs.health()["safe"] is True
    finally:
        release.set()
        jobs.close()
        store.close()


def test_software_stop_observation_requires_actual_cancel_and_worker_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An actual live worker cannot report a safe state from cancel acceptance alone."""
    store, invocation, _ = declared_file(tmp_path)
    invocation.update(group_id="group", task_id="task", role_id="role")
    invocation["execution_id"] = "attempt-5:group-4:task-4:role-1"
    jobs = FileJobs(store, max_job_seconds=120)
    entered, release = threading.Event(), threading.Event()
    perform = jobs._perform

    def boundary(
        call: dict[str, Any], admission: tuple[threading.Event, str | None, int, int]
    ) -> dict[str, object]:
        """Hold an actual owned worker before its first read without fabricating an outcome."""
        entered.set()
        assert release.wait(timeout=5)
        return perform(call, admission)

    monkeypatch.setattr(jobs, "_perform", boundary)
    try:
        jobs.submit(invocation)
        assert entered.wait(timeout=5)
        assert jobs.safe_state() == {"observations": []}
        jobs.cancel(invocation["execution_id"])
        assert jobs.safe_state() == {"observations": []}
        release.set()
        for worker in jobs._workers.values():
            worker.join(timeout=5)
            assert not worker.is_alive()
        observed = jobs.safe_state()["observations"]
        assert observed == [
            {
                "execution_id": invocation["execution_id"],
                "execution_generation": 1,
                "command_id": "cancel-" + invocation["execution_id"],
                "safe_state": True,
                "assurance": "software-process",
            }
        ]
        jobs.close()
        jobs = FileJobs(store)
        assert jobs.safe_state()["observations"] == observed
        assert jobs._original_generation({**invocation, "group_id": "another"}) is None
        assert (
            jobs._original_generation(
                {**invocation, "execution_id": invocation["execution_id"] + "0"}
            )
            == 10
        )
        assert (
            jobs._original_generation(
                {**invocation, "execution_id": invocation["execution_id"][:-1] + "01"}
            )
            is None
        )
    finally:
        release.set()
        jobs.close()
        store.close()


def test_compute_budget_sql_failure_rolls_back_admission_before_worker_start(
    tmp_path: Path,
) -> None:
    """An actual SQL failure cannot leave a job handle without its original budget or start work."""
    store, invocation, _ = declared_file(tmp_path)
    jobs = FileJobs(store)
    try:
        jobs._db.execute(
            "CREATE TRIGGER reject_budget BEFORE INSERT ON job_budgets "
            "BEGIN SELECT RAISE(ABORT,'unit budget commit failure'); END"
        )
        with pytest.raises(sqlite3.IntegrityError, match="budget commit failure"):
            jobs.submit(invocation)
        assert jobs._db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        assert jobs._db.execute("SELECT COUNT(*) FROM job_budgets").fetchone()[0] == 0
        assert jobs.health()["safe"] is True and not jobs._workers
    finally:
        jobs.close()
        store.close()


def test_round_limit_is_deployment_owned_and_historical_replay_never_recomputes(
    tmp_path: Path,
) -> None:
    """A local bound change cannot renew completed work or admit a new excessive request."""
    store, invocation, _ = declared_file(tmp_path)
    invocation["parameters"]["rounds"] = 512
    jobs = FileJobs(store, max_job_rounds=512)
    try:
        completed = jobs.compute(invocation)
        assert completed["state"] == "COMPLETED"
        budget = jobs._db.execute("SELECT * FROM job_budgets").fetchall()
        jobs.close()
        jobs = FileJobs(store)
        assert jobs.submit(invocation) == completed
        assert jobs._db.execute("SELECT * FROM job_budgets").fetchall() == budget
        changed = {**invocation, "execution_id": uuid.uuid4().hex}
        with pytest.raises(ValueError, match="deployment limit"):
            jobs.submit(changed)
        assert jobs._db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    finally:
        jobs.close()
        store.close()
