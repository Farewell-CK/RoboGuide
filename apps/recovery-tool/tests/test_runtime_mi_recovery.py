"""Actual worker SQL/lease/clock recovery; transport results are clearly labelled unit fixtures."""

from __future__ import annotations

import copy
import fcntl
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from mission.runtime_work_store import RuntimeWorkStore
from roboguide_recovery import backup, restore, verify
from roboguide_recovery.profile import validate


def profile(root: Path) -> dict[str, Any]:
    """Declare the actual runtime worker inode and sibling lease under the new exact profile."""
    return {
        "schema": "roboguide.recovery-profile/v0.3",
        "profile_id": "unit-runtime-mi",
        "revision": 1,
        "max_bytes": 8 * 1024 * 1024,
        "max_files": 64,
        "budget_seconds": 30,
        "artifact_replica_sets": [],
        "components": [
            {
                "component_id": "runtime-mi",
                "kind": "runtime-mi",
                "root": str(root),
                "database": "runtime.sqlite3",
                "trees": [],
                "depends_on": [],
            }
        ],
    }


def rows(path: Path) -> dict[str, Any]:
    """Read all actual schema and rows independently, without rewriting storage evidence."""
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        schema = connection.execute(
            "SELECT type,name,sql FROM sqlite_schema ORDER BY name"
        ).fetchall()
        return {
            "schema": schema,
            "tables": {
                name: connection.execute(
                    'SELECT * FROM "' + name.replace('"', '""') + '" ORDER BY rowid'
                ).fetchall()
                for kind, name, _ in schema
                if kind == "table"
            },
        }


def test_runtime_mi_complete_sql_and_original_cached_response_restore(tmp_path: Path) -> None:
    """Preserve every original table/nonce/budget, then replay original bytes without I/O."""
    root = tmp_path / "source"
    root.mkdir()
    store = RuntimeWorkStore(root / "runtime.sqlite3")
    store.admit("work", {"source": "unit"}, {"config": "original"}, 10000)
    calls = []

    def fixture(timeout: float) -> dict[str, Any]:
        """Return one explicit unit response after observing the original pending journal."""
        calls.append(timeout)
        return {"fixture": "original-response", "devices": 0}

    response = store.perform("work", "provider-generate", {"fixture": "original-input"}, fixture)
    instance = store.instance_id
    store.close()
    original = rows(root / "runtime.sqlite3")
    created = backup(profile(root), tmp_path / "bundle", "unit-backup")
    verify(tmp_path / "bundle", created["manifest_digest"])
    receipt = restore(
        tmp_path / "bundle", created["manifest_digest"], tmp_path / "restored", "unit-restore"
    )
    assert receipt["schema"] == "roboguide.restore-receipt/v0.3"
    restored = tmp_path / "restored/stores/runtime-mi/runtime.sqlite3"
    assert rows(restored) == original
    assert (
        restore(
            tmp_path / "bundle", created["manifest_digest"], tmp_path / "restored", "unit-restore"
        )
        == receipt
    )
    worker = RuntimeWorkStore(restored)
    try:
        assert worker.instance_id == instance
        assert (
            worker.perform("work", "provider-generate", {"fixture": "original-input"}, fixture)
            == response
        )
        assert len(calls) == 1
    finally:
        worker.close()
    with pytest.raises(ValueError, match="changed after receipt"):
        restore(
            tmp_path / "bundle", created["manifest_digest"], tmp_path / "restored", "unit-restore"
        )


def test_runtime_mi_freeze_requires_actual_worker_and_inode_leases(tmp_path: Path) -> None:
    """The real active worker and a separately held inode each prevent backup publication."""
    root = tmp_path / "source"
    root.mkdir()
    store = RuntimeWorkStore(root / "runtime.sqlite3")
    try:
        with pytest.raises(ValueError, match="still active"):
            backup(profile(root), tmp_path / "active-refused", "unit-active")
        assert not (tmp_path / "active-refused").exists()
    finally:
        store.close()
    with (root / "runtime.sqlite3").open("rb") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="still active"):
            backup(profile(root), tmp_path / "inode-refused", "unit-inode")
        assert not (tmp_path / "inode-refused").exists()


def test_runtime_mi_old_profiles_and_unknown_original_markers_refuse(tmp_path: Path) -> None:
    """Neither a wrong marker nor a legacy profile can assume the new writer protocol."""
    root = tmp_path / "source"
    root.mkdir()
    store = RuntimeWorkStore(root / "runtime.sqlite3")
    store.close()
    original = profile(root)
    legacy = copy.deepcopy(original)
    legacy["schema"] = "roboguide.recovery-profile/v0.2"
    with pytest.raises(ValueError, match="v0.3"):
        validate(legacy)
    with sqlite3.connect(root / "runtime.sqlite3") as connection:
        connection.execute("UPDATE format SET schema='explicit-invalid-unit-fixture'")
    with pytest.raises(ValueError, match="marker"):
        backup(original, tmp_path / "invalid-refused", "unit-invalid-marker")
    assert not (tmp_path / "invalid-refused").exists()
