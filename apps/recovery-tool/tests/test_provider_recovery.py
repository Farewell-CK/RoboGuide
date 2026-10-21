"""Check complete actual provider storage leases and data recovery with zero Node facts."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery import backup, restore, verify
from roboguide_recovery.bundle import Budget, _identity, canonical, digest


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


@pytest.fixture
def referenced_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, str, bytes]:
    """Close an actual provider with one explicit local content reference and its real bytes."""
    store, uses, jobs, maps = owners(tmp_path, monkeypatch)
    payload = b"reference-bound recovery bytes"
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    path = store._content / digest[7:]
    path.write_bytes(payload)
    references = importlib.import_module(
        "roboguide_memory_provider.content_references"
    ).ContentReferences(store)
    try:
        references.apply(
            {
                "schema": "roboguide.content-reference-command/v0.1",
                "operation": "acquire",
                "provider_id": store.provider_id,
                "provider_instance_id": store.instance_id,
                "reference_id": "lineage:recovery-test",
                "content_digest": digest,
                "kind": "lineage",
                "owner": "recovery-test",
                "generation": 1,
                "byte_size": len(payload),
            }
        )
    finally:
        maps.close()
        jobs.close()
        uses.close()
        store.close()
    return store.root, digest, payload


def test_recovery_binds_active_content_references_to_bundled_cas(
    referenced_provider: tuple[Path, str, bytes],
    tmp_path: Path,
) -> None:
    """An active Memory reference survives restore only with its exact CAS bytes included."""
    root, checksum, payload = referenced_provider
    path = root / "content" / checksum[7:]
    selected = profile(root)
    created = backup(selected, tmp_path / "bound", "bound")
    manifest = verify(tmp_path / "bound", created["manifest_digest"])
    assert manifest["physical_continuity"] == "not-established"
    restore(tmp_path / "bound", created["manifest_digest"], tmp_path / "restored", "restore")
    recovered = tmp_path / "restored/stores/provider"
    assert (recovered / "content" / checksum[7:]).read_bytes() == payload
    with closing(sqlite3.connect(recovered / "memory.sqlite3")) as connection:
        assert connection.execute(
            "SELECT content_digest,generation,active FROM content_references"
        ).fetchall() == [(checksum, 1, 1)]
    path.unlink()
    with pytest.raises(ValueError, match="lacks its bundled CAS content"):
        backup(selected, tmp_path / "missing", "missing")
    assert not (tmp_path / "missing").exists()


def test_repinned_bundle_cannot_omit_content_for_an_active_reference(
    referenced_provider: tuple[Path, str, bytes],
    tmp_path: Path,
) -> None:
    """Removing both the CAS file and its manifest row still leaves an invalid authority bundle."""
    root, checksum, _ = referenced_provider
    created = backup(profile(root), tmp_path / "bundle", "bound")
    manifest = verify(tmp_path / "bundle", created["manifest_digest"])
    path = "stores/provider/content/" + checksum[7:]
    (tmp_path / "bundle" / path).unlink()
    manifest["files"] = [row for row in manifest["files"] if row["path"] != path]
    raw = canonical(manifest)
    (tmp_path / "bundle/manifest.json").write_bytes(raw)
    with pytest.raises(ValueError, match="lacks its bundled CAS content"):
        verify(tmp_path / "bundle", digest(raw))
    with pytest.raises(ValueError, match="lacks its bundled CAS content"):
        restore(tmp_path / "bundle", digest(raw), tmp_path / "rejected", "reject")
    assert not (tmp_path / "rejected").exists()


def test_released_content_history_does_not_require_deleted_cas(
    referenced_provider: tuple[Path, str, bytes],
    tmp_path: Path,
) -> None:
    """An inactive original reference can be restored as history after local bytes were removed."""
    root, checksum, _ = referenced_provider
    with closing(sqlite3.connect(root / "memory.sqlite3")) as connection, connection:
        connection.execute("UPDATE content_references SET active=0")
    (root / "content" / checksum[7:]).unlink()
    created = backup(profile(root), tmp_path / "bundle", "inactive")
    verify(tmp_path / "bundle", created["manifest_digest"])
    restore(tmp_path / "bundle", created["manifest_digest"], tmp_path / "restored", "restore")


@pytest.mark.parametrize("mutation", ["downgrade-version", "remove-marker"])
def test_repinned_memory_sql_cannot_drop_its_original_writer_contract(
    referenced_provider: tuple[Path, str, bytes],
    tmp_path: Path,
    mutation: str,
) -> None:
    """Recomputed SQL/file/manifest hashes cannot authorize a weaker provider storage format."""
    root, _, _ = referenced_provider
    bundle = tmp_path / "bundle"
    created = backup(profile(root), bundle, "original")
    manifest = verify(bundle, created["manifest_digest"])
    selected = "stores/provider/memory.sqlite3"
    with closing(sqlite3.connect(bundle / selected)) as connection, connection:
        if mutation == "downgrade-version":
            connection.execute("PRAGMA user_version=2")
        else:
            connection.execute("DROP TABLE provider_writer_format")
    payload = (bundle / selected).read_bytes()
    row = next(row for row in manifest["files"] if row["path"] == selected)
    row.update(
        content_digest=digest(payload),
        byte_size=len(payload),
        sqlite=_identity(bundle / selected, Budget(manifest["profile"])),
    )
    raw = canonical(manifest)
    (bundle / "manifest.json").write_bytes(raw)
    with pytest.raises(ValueError, match="actual v3 composite writer marker"):
        verify(bundle, digest(raw))
    with pytest.raises(ValueError, match="actual v3 composite writer marker"):
        restore(bundle, digest(raw), tmp_path / "refused", "reject")
    assert not (tmp_path / "refused").exists()


@pytest.mark.parametrize("authority", ["reference", "map-ref", "map-head", "handoff"])
@pytest.mark.parametrize("failure", ["missing", "tombstone"])
def test_all_local_retention_authorities_require_live_bundled_content(
    referenced_provider: tuple[Path, str, bytes],
    tmp_path: Path,
    authority: str,
    failure: str,
) -> None:
    """Persist explicit SQL corruption counterexamples; these rows grant no deployment facts."""
    root, checksum, _ = referenced_provider
    with closing(sqlite3.connect(root / "memory.sqlite3")) as connection, connection:
        if authority != "reference":
            connection.execute("UPDATE content_references SET active=0")
        if failure == "tombstone":
            connection.execute(
                "INSERT INTO content_tombstones VALUES (?, 'corrupt-tombstone','deleted')",
                (checksum,),
            )
    if authority != "reference":
        with closing(sqlite3.connect(root / "active-map/active-map.sqlite3")) as maps, maps:
            if authority == "map-ref":
                maps.execute(
                    "INSERT INTO refs VALUES ('corrupt-ref','selector',?,'active_map',1)",
                    (checksum,),
                )
            elif authority == "map-head":
                maps.execute(
                    "INSERT INTO heads VALUES ('selector',1,'active',?,'{}',1)",
                    (json.dumps({"content_digest": checksum}),),
                )
            else:
                maps.execute(
                    "INSERT INTO handoffs VALUES ('barrier','group','prepared',?,1)",
                    (
                        json.dumps(
                            {side: {"content_digest": checksum} for side in ("source", "target")}
                        ),
                    ),
                )
    if failure == "missing":
        (root / "content" / checksum[7:]).unlink()
    message = "lacks its bundled CAS content" if failure == "missing" else "durable tombstone"
    with pytest.raises(ValueError, match=message):
        backup(profile(root), tmp_path / "refused", "reject")
    assert not (tmp_path / "refused").exists()


@pytest.mark.parametrize(
    ("database", "table"),
    [
        ("memory.sqlite3", "content_references"),
        ("memory.sqlite3", "content_tombstones"),
        ("active-map/active-map.sqlite3", "refs"),
        ("active-map/active-map.sqlite3", "heads"),
        ("active-map/active-map.sqlite3", "handoffs"),
    ],
)
def test_missing_reference_tables_cannot_pass_bundle_validation(
    referenced_provider: tuple[Path, str, bytes],
    tmp_path: Path,
    database: str,
    table: str,
) -> None:
    """A missing mandatory authority table cannot be interpreted as an empty reference set."""
    root, _, _ = referenced_provider
    with closing(sqlite3.connect(root / database)) as connection, connection:
        connection.execute('DROP TABLE "' + table + '"')
    with pytest.raises(ValueError, match="reference authority table is missing"):
        backup(profile(root), tmp_path / "refused", "reject")
