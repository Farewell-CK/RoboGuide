"""Check actual CPU/file outcomes, replay and cancellation without a device fixture."""

from __future__ import annotations

import hashlib
import json
import sqlite3
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
