"""Verify real SQLite/WAL, CAS, file-lock and corruption behavior without device evidence."""

from __future__ import annotations

import copy
import fcntl
import hashlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery import backup, restore, verify
from roboguide_recovery.bundle import canonical, digest
from roboguide_recovery.profile import validate


@pytest.fixture
def storage(tmp_path: Path) -> Iterator[tuple[dict[str, Any], sqlite3.Connection]]:
    """Create actual generic SQLite/WAL records and immutable bytes; no Node facts are created."""
    authority, cas = tmp_path / "authority", tmp_path / "cas"
    authority.mkdir()
    cas.mkdir()
    (authority / "journal.sqlite3.writer.lock").touch()
    (cas / ".writer.lock").touch()
    payload = Path(__file__).read_bytes()
    checksum = hashlib.sha256(payload).hexdigest()
    path = cas / "blobs/sha256" / checksum[:2] / checksum
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)
    (cas / "staging").mkdir()
    connection = sqlite3.connect(authority / "journal.sqlite3")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute(
        "CREATE TABLE budgets (identity TEXT PRIMARY KEY, start INTEGER, end INTEGER)"
    )
    connection.execute("INSERT INTO budgets VALUES ('original',100,200)")
    connection.execute("CREATE TABLE history (id INTEGER PRIMARY KEY,evidence BLOB)")
    connection.execute("INSERT INTO history VALUES (1,?)", (payload,))
    connection.execute("CREATE VIEW original_budget AS SELECT start,end FROM budgets")
    connection.commit()
    profile = {
        "schema": "roboguide.recovery-profile/v0.1",
        "profile_id": "actual-files",
        "revision": 1,
        "max_bytes": 16 * 1024 * 1024,
        "max_files": 100,
        "budget_seconds": 30,
        "components": [
            {
                "component_id": "authority",
                "kind": "controller",
                "root": str(authority),
                "database": "journal.sqlite3",
                "trees": [],
                "depends_on": ["content"],
            },
            {
                "component_id": "content",
                "kind": "artifact",
                "root": str(cas),
                "database": None,
                "trees": [],
                "depends_on": [],
            },
        ],
    }
    try:
        yield profile, connection
    finally:
        connection.close()


def test_snapshot_preserves_complete_wal_tables_cas_and_original_budget(
    storage: tuple[dict[str, Any], sqlite3.Connection],
    tmp_path: Path,
) -> None:
    """A complete SQLite backup includes WAL-only rows and actual content in the same bundle."""
    profile, connection = storage
    assert (Path(profile["components"][0]["root"]) / "journal.sqlite3-wal").stat().st_size > 0
    original = list(connection.iterdump())
    outcome = backup(profile, tmp_path / "bundle", "original-backup")
    manifest = verify(tmp_path / "bundle", outcome["manifest_digest"])
    assert len(manifest["files"]) == 2
    receipt = restore(
        tmp_path / "bundle", outcome["manifest_digest"], tmp_path / "restored", "restore"
    )
    restored_db = tmp_path / "restored/stores/authority/journal.sqlite3"
    with sqlite3.connect(restored_db) as restored:
        assert list(restored.iterdump()) == original
        assert restored.execute("SELECT * FROM original_budget").fetchall() == [(100, 200)]
    assert receipt["physical_continuity"] == "not-established"
    assert (
        restore(tmp_path / "bundle", outcome["manifest_digest"], tmp_path / "restored", "restore")
        == receipt
    )
    assert backup(profile, tmp_path / "bundle", "original-backup") == outcome
    with pytest.raises(ValueError, match="identity conflicts"):
        backup(profile, tmp_path / "bundle", "another-backup")


def test_any_actual_writer_refuses_before_creating_a_partial_bundle(
    storage: tuple[dict[str, Any], sqlite3.Connection],
    tmp_path: Path,
) -> None:
    """An actual process lease, including CAS, prevents a falsely quiescent authority snapshot."""
    profile, _ = storage
    path = Path(profile["components"][1]["root"]) / ".writer.lock"
    with path.open("rb") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="still active"):
            backup(profile, tmp_path / "bundle", "active-writer")
        assert not (tmp_path / "bundle").exists()
        assert not (tmp_path / "bundle.partial-active-writer").exists()


def test_published_bundle_verification_creates_no_sqlite_sidecars_or_writes(
    storage: tuple[dict[str, Any], sqlite3.Connection],
    tmp_path: Path,
) -> None:
    """Readonly verification preserves every published byte and never creates WAL/SHM files."""
    profile, _ = storage
    outcome = backup(profile, tmp_path / "bundle", "readonly-inspection")
    before = {
        path.relative_to(tmp_path / "bundle").as_posix(): path.read_bytes()
        for path in (tmp_path / "bundle").rglob("*")
        if path.is_file()
    }
    verify(tmp_path / "bundle", outcome["manifest_digest"])
    assert {
        path.relative_to(tmp_path / "bundle").as_posix(): path.read_bytes()
        for path in (tmp_path / "bundle").rglob("*")
        if path.is_file()
    } == before


@pytest.mark.parametrize("mutation", ["bytes", "missing", "symlink", "extra"])
def test_restoration_rejects_corrupt_missing_linked_or_undeclared_content(
    storage: tuple[dict[str, Any], sqlite3.Connection],
    tmp_path: Path,
    mutation: str,
) -> None:
    """Neither metadata nor a valid manifest authorizes restoring different or incomplete bytes."""
    profile, _ = storage
    outcome = backup(profile, tmp_path / "bundle", "corruption")
    manifest = verify(tmp_path / "bundle", outcome["manifest_digest"])
    record = next(row for row in manifest["files"] if row["kind"] == "content")
    path = tmp_path / "bundle" / record["path"]
    if mutation == "bytes":
        path.write_bytes(b"different bytes")
    elif mutation == "missing":
        path.unlink()
    elif mutation == "symlink":
        path.unlink()
        path.symlink_to(Path(__file__))
    else:
        (tmp_path / "bundle/undeclared.bin").write_bytes(b"unlisted")
    with pytest.raises((ValueError, OSError)):
        restore(tmp_path / "bundle", outcome["manifest_digest"], tmp_path / "restored", "reject")
    assert not (tmp_path / "restored").exists()


@pytest.mark.parametrize(
    "mutation", ["duplicate", "traversal", "missing-database", "wrong-component"]
)
def test_pinned_malformed_manifests_still_cannot_restore_invalid_storage(
    storage: tuple[dict[str, Any], sqlite3.Connection],
    tmp_path: Path,
    mutation: str,
) -> None:
    """Even an explicitly pinned input must satisfy full storage coverage and path invariants."""
    profile, _ = storage
    outcome = backup(profile, tmp_path / "bundle", "manifest-check")
    manifest = verify(tmp_path / "bundle", outcome["manifest_digest"])
    if mutation == "duplicate":
        manifest["files"].append(manifest["files"][0])
    elif mutation == "traversal":
        manifest["files"][0]["path"] = "stores/authority/../../escaped.sqlite3"
    elif mutation == "missing-database":
        manifest["files"] = [row for row in manifest["files"] if row["kind"] != "sqlite"]
    else:
        manifest["files"][0]["path"] = "stores/unrelated/data.bin"
    raw = canonical(manifest)
    (tmp_path / "bundle/manifest.json").write_bytes(raw)
    with pytest.raises((ValueError, OSError)):
        restore(tmp_path / "bundle", digest(raw), tmp_path / "restored", "reject")
    assert not (tmp_path / "escaped.sqlite3").exists()


def test_source_cas_corruption_and_budget_exhaustion_never_publish_a_bundle(
    storage: tuple[dict[str, Any], sqlite3.Connection],
    tmp_path: Path,
) -> None:
    """Actual CAS identity and original size limits are checked before immutable publication."""
    profile, _ = storage
    small = copy.deepcopy(profile)
    small["max_bytes"] = 1
    with pytest.raises(ValueError, match="byte budget"):
        backup(small, tmp_path / "small", "too-small")
    assert not (tmp_path / "small").exists()
    root = Path(profile["components"][1]["root"])
    next(path for path in (root / "blobs").rglob("*") if path.is_file()).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="CAS path"):
        backup(profile, tmp_path / "corrupt", "corrupt-source")
    assert not (tmp_path / "corrupt").exists()


def test_topology_rejects_missing_dependencies_cycles_and_overlapping_managed_paths(
    storage: tuple[dict[str, Any], sqlite3.Connection],
) -> None:
    """An omitted or cyclic writer dependency cannot stand in for actual quiescence."""
    profile, _ = storage
    missing = copy.deepcopy(profile)
    missing["components"][0]["depends_on"] = ["missing"]
    with pytest.raises(ValueError, match="dependency"):
        validate(missing)
    cyclic = copy.deepcopy(profile)
    cyclic["components"][1]["depends_on"] = ["authority"]
    with pytest.raises(ValueError, match="cycle"):
        validate(cyclic)
    overlap = copy.deepcopy(profile)
    repeated = copy.deepcopy(overlap["components"][0])
    repeated["component_id"] = "another-authority"
    overlap["components"].append(repeated)
    with pytest.raises(ValueError, match="overlap"):
        validate(overlap)


def test_expected_manifest_digest_and_existing_destination_fence_restore(
    storage: tuple[dict[str, Any], sqlite3.Connection],
    tmp_path: Path,
) -> None:
    """A caller must pin original evidence, and restoration never overwrites an existing store."""
    profile, _ = storage
    outcome = backup(profile, tmp_path / "bundle", "pin")
    with pytest.raises(ValueError, match="expected digest"):
        restore(tmp_path / "bundle", "sha256:" + "0" * 64, tmp_path / "restored", "reject")
    (tmp_path / "restored").mkdir()
    evidence = tmp_path / "restored/existing.bin"
    evidence.write_bytes(b"preserve existing data")
    with pytest.raises(ValueError, match="already exists"):
        restore(tmp_path / "bundle", outcome["manifest_digest"], tmp_path / "restored", "reject")
    assert evidence.read_bytes() == b"preserve existing data"


def test_deep_configured_dependency_graph_does_not_use_python_recursion(
    storage: tuple[dict[str, Any], sqlite3.Connection],
    tmp_path: Path,
) -> None:
    """Validate a bounded deep storage graph without inventing Nodes or reading missing roots."""
    profile, _ = storage
    configured = copy.deepcopy(profile)
    previous = "content"
    for index in range(1000):
        identifier = f"archive-layer-{index}"
        configured["components"].append(
            {
                "component_id": identifier,
                "kind": "files",
                "root": str(tmp_path / identifier),
                "database": None,
                "trees": [],
                "depends_on": [previous],
            }
        )
        previous = identifier
    assert len(validate(configured)) == 1002
