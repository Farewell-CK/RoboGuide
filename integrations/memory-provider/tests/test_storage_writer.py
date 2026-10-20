"""Check actual provider inode/lifetime fencing without Node or device facts."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from roboguide_memory_provider.active_map import ActiveMapLedger
from roboguide_memory_provider.jobs import FileJobs
from roboguide_memory_provider.storage import DATABASES
from roboguide_memory_provider.store import MemoryStore
from roboguide_memory_provider.use_leases import UseLeases


def locked(root: Path) -> bool:
    """Observe actual flock contention independently in another OS process."""
    script = """import fcntl,json,sys
with open(sys.argv[1],'r+b') as f:
 try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);result=False
 except BlockingIOError:result=True
 print(json.dumps(result))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(root / "writer.lock")],
        check=True,
        capture_output=True,
        timeout=5,
    )
    return bool(json.loads(result.stdout))


@pytest.mark.parametrize("database", DATABASES)
def test_database_alias_cannot_open_another_provider_writer(tmp_path: Path, database: str) -> None:
    """An independently rooted alias refuses before touching the original authority rows."""
    root, alias = tmp_path / "original", tmp_path / "alias"
    store = MemoryStore(root, "files", (tmp_path,), 1024)
    try:
        before = list(store._db.iterdump())
        target = alias / database
        target.parent.mkdir(mode=0o700, parents=True)
        os.link(root / database, target)
        with pytest.raises(BlockingIOError):
            MemoryStore(alias, "files", (tmp_path,), 1024)
        assert list(store._db.iterdump()) == before
    finally:
        store.close()


@pytest.mark.parametrize("owner", ["use", "jobs", "active-map"])
def test_parent_close_retains_every_live_child_writer(tmp_path: Path, owner: str) -> None:
    """The root remains actually locked until each concrete child connection is closed."""
    root = tmp_path / "provider"
    store = MemoryStore(root, "files", (tmp_path,), 1024)
    child = (
        UseLeases(store)
        if owner == "use"
        else FileJobs(store)
        if owner == "jobs"
        else ActiveMapLedger(root / "active-map", store, None)
    )
    try:
        store.close()
        assert locked(root)
        assert child._db.execute("SELECT 1").fetchone() == (1,)
        with pytest.raises(BlockingIOError):
            MemoryStore(root, "files", (tmp_path,), 1024)
    finally:
        child.close()
        store.close()
    assert not locked(root)
    reopened = MemoryStore(root, "files", (tmp_path,), 1024)
    reopened.close()


def test_failed_child_initialization_releases_its_actual_connection(tmp_path: Path) -> None:
    """A real unsupported SQLite header cannot strand an unreturned child writer."""
    store = MemoryStore(tmp_path / "provider", "files", (tmp_path,), 1024)
    try:
        path = store.root / "file-jobs.sqlite3"
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("PRAGMA user_version=99")
        with pytest.raises(ValueError, match="worker mode"):
            FileJobs(store)
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("PRAGMA user_version=0")
        jobs = FileJobs(store)
        jobs.close()
    finally:
        store.close()
    assert not locked(store.root)


def test_missing_managed_database_stays_missing_after_refused_open(tmp_path: Path) -> None:
    """An original missing authority file cannot be silently replaced by an empty database."""
    store = MemoryStore(tmp_path / "provider", "files", (tmp_path,), 1024)
    store.close()
    missing = store.root / "memory-use.sqlite3"
    missing.unlink()
    for _ in range(2):
        with pytest.raises(ValueError, match="managed database is missing"):
            MemoryStore(store.root, "files", (tmp_path,), 1024)
        assert not missing.exists()
    assert not locked(store.root)
