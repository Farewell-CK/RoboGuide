"""Actual file/SQL migration and independent metadata counterexamples, with zero device facts."""

from __future__ import annotations

import copy
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from roboguide_memory_provider.scoped_maps import validate_typed_input
from roboguide_memory_provider.storage import DATABASES, SCOPED_MAP_WRITER_SCHEMA
from roboguide_memory_provider.store import MemoryStore, canonical
from test_store import original


def metadata() -> tuple[dict[str, Any], dict[str, Any]]:
    """Supply synthetic original map metadata; publication and use authority remain unavailable."""
    typed = {
        "schema": "roboguide.map-manifest/v0.2",
        "map_id": "unit-map",
        "revision_id": "root",
        "producer_node_id": "unit-node",
        "producer_local_system_id": "unit-files",
        "producer_provider_id": "unit-provider",
        "source_mission_id": "unit-producer",
        "source_execution_id": "unit-production-attempt",
        "source_task_ref": {"mission_id": "unit-producer", "task_id": "build"},
        "media_type": "unit/map",
        "payload_schema": "unit.map/v1",
        "content_digest": "sha256:" + "a" * 64,
        "byte_size": 4,
        "created_at_ms": 1,
        "root_frame": "unit-frame",
        "coordinate_convention": "unit-convention",
        "spatial_anchor": {"kind": "fixed", "id": "unit-anchor"},
    }
    child = {
        "schema": "roboguide.memory-manifest/v0.1",
        "selector": {"memory_id": "unit-map", "revision_id": "child"},
        "kind": "spatial",
        "provider_id": "unit-provider",
        "owner": {"owner": "node", "node_id": "unit-node", "local_system_id": "unit-files"},
        "scope": {"kind": "execution_group", "execution_group_id": "unit-consumer-group"},
        "visibility": "exchangeable",
        "payload_schema": "unit.map/v1",
        "media_type": "unit/map",
        "artifact": {"content_digest": typed["content_digest"], "byte_size": 4},
        "source_mission_id": "unit-producer",
        "source_execution_id": "unit-production-attempt",
        "source_task_ref": typed["source_task_ref"],
        "created_at": 2,
    }
    return typed, child


def test_scope_requires_explicit_contract_and_preserves_original_production() -> None:
    """A child cannot broaden scope, replace its root, or borrow old-provider interpretation."""
    typed, child = metadata()
    assert validate_typed_input(typed, child, "unit-consumer-group", True)
    with pytest.raises(ValueError):
        validate_typed_input(typed, child, "unit-consumer-group", False)
    for key in (
        "provider_id",
        "payload_schema",
        "media_type",
        "source_execution_id",
        "source_mission_id",
    ):
        changed = copy.deepcopy(child)
        changed[key] = "different-original"
        with pytest.raises(ValueError):
            validate_typed_input(typed, changed, "unit-consumer-group", True)
    for scope in (
        {"kind": "global"},
        {"kind": "execution_group", "execution_group_id": "another-group"},
    ):
        with pytest.raises(ValueError):
            validate_typed_input(typed, child | {"scope": scope}, "unit-consumer-group", True)


def test_actual_v5_migration_keeps_original_instance_inodes_imports_and_bytes(
    tmp_path: Path,
) -> None:
    """Retain real v3 history and composite leases while explicitly fencing old v4 writers."""
    root = tmp_path / "backend"
    payload = b"actual original source bytes retained across explicit scoped migration"
    staged = tmp_path / "source"
    staged.write_bytes(payload)
    store = MemoryStore(root, "unit-files", (tmp_path,), 1024)
    evidence, manifest = original(store, payload)
    store.import_artifact(evidence, manifest, str(staged))
    identity = store.instance_id
    imports = store._db.execute("SELECT * FROM imports").fetchall()
    inodes = {name: (root / name).stat().st_ino for name in DATABASES}
    store.close()
    store = MemoryStore(root, "unit-files", (tmp_path,), 1024, enable_scoped_typed_maps=True)
    try:
        assert store.instance_id == identity
        assert store._db.execute("PRAGMA user_version").fetchone() == (5,)
        assert store._db.execute(
            "SELECT schema,databases_json FROM provider_writer_format"
        ).fetchall() == [(SCOPED_MAP_WRITER_SCHEMA, canonical(list(DATABASES)).decode())]
        assert store._db.execute("SELECT * FROM imports").fetchall() == imports
        assert {name: (root / name).stat().st_ino for name in DATABASES} == inodes
        assert store.query(evidence)["outcome"] == "completed"
        assert (
            store.availability(evidence)["schema"] == "roboguide.memory-provider-availability/v0.2"
        )
        assert (
            root / "content" / manifest["artifact"]["content_digest"][7:]
        ).read_bytes() == payload
    finally:
        store.close()
    with pytest.raises(ValueError, match="v5 requires explicit"):
        MemoryStore(root, "unit-files", (tmp_path,), 1024, enable_snapshot_references=True)
    store = MemoryStore(root, "unit-files", (tmp_path,), 1024, enable_scoped_typed_maps=True)
    assert store.instance_id == identity
    store.close()


def test_actual_scoped_migration_sql_failure_retains_original_v4_authority(tmp_path: Path) -> None:
    """An actual SQL abort cannot publish the new marker or lose the original writer lease."""
    root = tmp_path / "backend"
    store = MemoryStore(root, "unit-files", (tmp_path,), 1024, enable_snapshot_references=True)
    identity = store.instance_id
    store._db.executescript(
        "CREATE TRIGGER reject_scoped BEFORE UPDATE ON provider_writer_format "
        "WHEN NEW.schema='roboguide.memory-provider-writer/v0.3' "
        "BEGIN SELECT RAISE(ABORT,'actual scoped migration SQL failure'); END;"
    )
    original_marker = store._db.execute("SELECT * FROM provider_writer_format").fetchall()
    store.close()
    with pytest.raises(sqlite3.IntegrityError, match="actual scoped migration SQL failure"):
        MemoryStore(root, "unit-files", (tmp_path,), 1024, enable_scoped_typed_maps=True)
    with closing(sqlite3.connect(root / "memory.sqlite3")) as sql:
        assert sql.execute("PRAGMA user_version").fetchone() == (4,)
        assert sql.execute("SELECT * FROM provider_writer_format").fetchall() == original_marker
        assert sql.execute("SELECT instance FROM identity").fetchone() == (identity,)
        sql.execute("DROP TRIGGER reject_scoped")
    store = MemoryStore(root, "unit-files", (tmp_path,), 1024, enable_scoped_typed_maps=True)
    assert store.instance_id == identity
    store.close()
