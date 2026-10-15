"""Verify real immutable-file effects, SQL crash windows and readonly original-provider queries."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

import pytest
from roboguide_memory_provider.store import MemoryStore, canonical, original_content_digest


def original(store: MemoryStore, payload: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build protocol metadata for actual test-file bytes, with no device or Task success claim."""
    identifier = uuid.uuid4().hex
    manifest = {
        "schema": "roboguide.memory-manifest/v0.1",
        "selector": {"memory_id": uuid.uuid4().hex, "revision_id": uuid.uuid4().hex},
        "artifact": {
            "content_digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
            "byte_size": len(payload),
        },
    }
    invocation = {
        "import_id": identifier,
        "command_id": f"memory-import-{identifier}",
        "consumer_provider_id": store.provider_id,
        "manifest_json": json.dumps(manifest, separators=(",", ":")),
        "mission_id": uuid.uuid4().hex,
        "group_id": uuid.uuid4().hex,
        "task_id": uuid.uuid4().hex,
        "role_id": uuid.uuid4().hex,
        "execution_id": uuid.uuid4().hex,
    }
    return {
        "import_id": identifier,
        "command_id": invocation["command_id"],
        "provider_id": store.provider_id,
        "identity_digest": hashlib.sha256(canonical(invocation)).hexdigest(),
        "original_content_digest": original_content_digest(invocation),
        "provider_instance_id": store.instance_id,
        "provider_contract_digest": "sha256:" + hashlib.sha256(b"test file contract").hexdigest(),
        "original_invocation": invocation,
        "admitted_at_ms": 1,
    }, manifest


def test_real_bytes_import_restart_query_and_no_reimport(tmp_path: Path) -> None:
    """Actual file copying persists instance/identity, and retry works after input removal."""
    payload = Path(__file__).read_bytes()
    staged = tmp_path / "input"
    staged.write_bytes(payload)
    store = MemoryStore(tmp_path / "backend", "file-content", (tmp_path,), len(payload) + 1)
    evidence, manifest = original(store, payload)
    before = store.query(evidence)
    assert before["outcome"] == "unknown" and before["durable"] is False
    assert store.import_artifact(evidence, manifest, str(staged))["replayed"] is False
    staged.unlink()
    instance = store.instance_id
    store.close()
    store = MemoryStore(tmp_path / "backend", "file-content", (tmp_path,), len(payload) + 1)
    try:
        assert store.instance_id == instance
        assert store.import_artifact(evidence, manifest, str(staged))["replayed"] is True
        assert store.query(evidence)["outcome"] == "completed"
        content = tmp_path / "backend/content" / manifest["artifact"]["content_digest"][7:]
        assert content.read_bytes() == payload
        content.unlink()
        assert store.query(evidence)["outcome"] == "unknown"
        with pytest.raises(ValueError, match="ambiguous"):
            store.import_artifact(evidence, manifest, str(staged))
        assert not content.exists()
    finally:
        store.close()


def test_final_sql_failure_leaves_readonly_proof_of_actual_file_effect(tmp_path: Path) -> None:
    """A real crash-window SQL abort after fsync is reconciled by reads with no DB mutation."""
    staged = tmp_path / "input"
    staged.write_bytes(Path(__file__).read_bytes())
    store = MemoryStore(tmp_path / "backend", "files", (tmp_path,), staged.stat().st_size)
    evidence, manifest = original(store, staged.read_bytes())
    db = sqlite3.connect(tmp_path / "backend/memory.sqlite3")
    try:
        db.execute(
            "CREATE TRIGGER abort_final_commit BEFORE UPDATE OF state ON imports "
            "BEGIN SELECT RAISE(ABORT,'injected final SQL failure'); END"
        )
        db.commit()
        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            store.import_artifact(evidence, manifest, str(staged))
        assert db.execute("SELECT state FROM imports").fetchone()[0] == "pending"
        version = db.execute("PRAGMA data_version").fetchone()[0]
        assert store.query(evidence)["outcome"] == "completed"
        assert db.execute("SELECT state FROM imports").fetchone()[0] == "pending"
        assert db.execute("PRAGMA data_version").fetchone()[0] == version
    finally:
        db.close()
        store.close()


def test_unknown_never_authorizes_copy_and_changed_instance_or_source_rejects(
    tmp_path: Path,
) -> None:
    """Missing content, changed backend, and symlink paths do not become successful imports."""
    staged = tmp_path / "input"
    payload = Path(__file__).read_bytes()
    staged.write_bytes(payload)
    store = MemoryStore(tmp_path / "backend", "files", (tmp_path,), len(payload))
    evidence, manifest = original(store, payload)
    db = sqlite3.connect(tmp_path / "backend/memory.sqlite3")
    try:
        db.execute(
            "INSERT INTO imports VALUES (?,?,?,?,?,'pending')",
            (
                evidence["import_id"],
                canonical(evidence),
                canonical(manifest),
                manifest["artifact"]["content_digest"],
                len(payload),
            ),
        )
        db.commit()
        with pytest.raises(ValueError, match="ambiguous"):
            store.import_artifact(evidence, manifest, str(staged))
        assert store.query(evidence)["outcome"] == "unknown"
        assert not tuple((tmp_path / "backend/content").iterdir())
        changed = dict(evidence, provider_instance_id=uuid.uuid4().hex)
        with pytest.raises(ValueError, match="differs"):
            store.query(changed)
        other, other_manifest = original(store, payload)
        symlink = tmp_path / "symlink"
        symlink.symlink_to(staged)
        with pytest.raises(ValueError, match="non-symlink"):
            store.import_artifact(other, other_manifest, str(symlink))
        assert store.query(other)["outcome"] == "unknown"
    finally:
        db.close()
        store.close()


def test_wrong_digest_never_publishes_and_writer_lock_is_exclusive(tmp_path: Path) -> None:
    """Changed staged bytes cannot be acknowledged, and another writer cannot reopen the backend."""
    staged = tmp_path / "input"
    staged.write_bytes(b"original content")
    store = MemoryStore(tmp_path / "backend", "files", (tmp_path,), 1024)
    evidence, manifest = original(store, staged.read_bytes())
    try:
        with pytest.raises(BlockingIOError):
            MemoryStore(tmp_path / "backend", "files", (tmp_path,), 1024)
        staged.write_bytes(b"modified content")
        with pytest.raises(ValueError, match="digest"):
            store.import_artifact(evidence, manifest, str(staged))
        assert store.query(evidence)["outcome"] == "unknown"
        assert not tuple((tmp_path / "backend/content").iterdir())
    finally:
        store.close()
