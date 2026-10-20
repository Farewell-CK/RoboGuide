"""Check complete actual provider storage leases and data recovery with zero Node facts."""

from __future__ import annotations

import copy
import hashlib
import importlib
import os
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery import backup, restore


def profile(root: Path) -> dict[str, Any]:
    """Select the actual composite storage contract and every original managed database."""
    return {
        "schema": "roboguide.recovery-profile/v0.4",
        "profile_id": "provider-storage",
        "revision": 1,
        "max_bytes": 8 * 1024 * 1024,
        "max_files": 128,
        "budget_seconds": 30,
        "artifact_replica_sets": [],
        "components": [
            {
                "component_id": "provider",
                "kind": "memory",
                "root": str(root),
                "database": None,
                "trees": [],
                "depends_on": [],
            }
        ],
    }


def owners(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any, Any, Any]:
    """Open the actual reference implementation solely as test data, keeping the CLI independent."""
    source = Path(__file__).resolve().parents[3] / "integrations/memory-provider"
    monkeypatch.syspath_prepend(str(source))
    store = importlib.import_module("roboguide_memory_provider.store").MemoryStore(
        tmp_path / "source", "files", (tmp_path,), 1024 * 1024
    )
    uses = importlib.import_module("roboguide_memory_provider.use_leases").UseLeases(store)
    jobs = importlib.import_module("roboguide_memory_provider.process_jobs").ProcessFileJobs(
        store, uses
    )
    maps = importlib.import_module("roboguide_memory_provider.active_map").ActiveMapLedger(
        store.root / "active-map", store, uses
    )
    return store, uses, jobs, maps


def test_complete_provider_recovery_waits_for_every_actual_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuse backup after parent close, then restore all four SQL stores and actual bytes."""
    store, uses, jobs, maps = owners(tmp_path, monkeypatch)
    identity = store.instance_id
    payload = b"actual independent storage recovery bytes"
    digest = hashlib.sha256(payload).hexdigest()
    (store._content / digest).write_bytes(payload)
    store.close()
    selected = profile(store.root)
    try:
        with pytest.raises(ValueError, match="writer is still active"):
            backup(selected, tmp_path / "live-refused", "live")
    finally:
        maps.close()
        jobs.close()
        uses.close()
        store.close()
    before = backup(selected, tmp_path / "bundle", "original")
    receipt = restore(
        tmp_path / "bundle", before["manifest_digest"], tmp_path / "restored", "restore"
    )
    assert receipt["schema"] == "roboguide.restore-receipt/v0.4"
    root = tmp_path / "restored/stores/provider"
    assert (root / "content" / digest).read_bytes() == payload
    for name in (
        "memory.sqlite3",
        "memory-use.sqlite3",
        "file-jobs.sqlite3",
        "active-map/active-map.sqlite3",
    ):
        assert (root / name).is_file()
    assert (
        restore(tmp_path / "bundle", before["manifest_digest"], tmp_path / "restored", "restore")
        == receipt
    )
    # A never-started restored tree has real empty lock files and can itself be frozen again.
    backup(profile(root), tmp_path / "second-bundle", "second")
    legacy = copy.deepcopy(selected)
    legacy["schema"] = "roboguide.recovery-profile/v0.3"
    with pytest.raises(ValueError, match="explicit recovery profile v0.4"):
        backup(legacy, tmp_path / "legacy-refused", "old-profile")
    reopened = importlib.import_module("roboguide_memory_provider.store").MemoryStore(
        root, "files", (tmp_path,), 1024 * 1024
    )
    assert reopened.instance_id == identity
    reopened.close()


def test_recovery_refuses_an_active_inode_through_another_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Different root lock names cannot conceal the actual shared database writer from backup."""
    store, uses, jobs, maps = owners(tmp_path, monkeypatch)
    alias = tmp_path / "alias"
    alias.mkdir()
    for name in ("writer.lock", ".memory-use.lock"):
        (alias / name).touch()
    for name in (
        "memory.sqlite3",
        "memory-use.sqlite3",
        "file-jobs.sqlite3",
        "active-map/active-map.sqlite3",
    ):
        target = alias / name
        target.parent.mkdir(exist_ok=True, parents=True)
        os.link(store.root / name, target)
    try:
        with pytest.raises(ValueError, match="writer is still active"):
            backup(profile(alias), tmp_path / "alias-refused", "alias")
    finally:
        maps.close()
        jobs.close()
        uses.close()
        store.close()
