"""Verify real SQLite/WAL, CAS, file-lock and corruption behavior without device evidence."""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery import backup, restore, verify
from roboguide_recovery.bundle import Budget, _identity, canonical, digest
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


def test_replica_supervisor_lease_requires_explicit_new_profile_and_quiescence(
    storage: tuple[dict[str, Any], sqlite3.Connection], tmp_path: Path
) -> None:
    """A live actual supervisor lock fences backup even when its Raft database writer is free."""
    original, _ = storage
    profile = copy.deepcopy(original)
    component = profile["components"][0]
    component["kind"] = "replica"
    root = Path(component["root"])
    lease = root / "supervisor.writer.lock"
    lease.touch()
    with pytest.raises(ValueError, match="explicit recovery profile v0.2"):
        backup(profile, tmp_path / "old", "old-profile")
    profile["schema"] = "roboguide.recovery-profile/v0.2"
    profile["artifact_replica_sets"] = []
    with lease.open("rb") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="still active"):
            backup(profile, tmp_path / "live", "live-supervisor")
        assert not (tmp_path / "live").exists()
        assert not (tmp_path / "live.partial-live-supervisor").exists()
    result = backup(profile, tmp_path / "bundle", "stopped-supervisor")
    manifest = verify(tmp_path / "bundle", result["manifest_digest"])
    assert manifest["profile"]["schema"] == "roboguide.recovery-profile/v0.2"
    assert manifest["profile"]["components"][0]["kind"] == "replica"


@pytest.fixture
def replica_files(tmp_path: Path) -> dict[str, Any]:
    """Create storage fixtures for scope/corruption tests; these are not live cluster proof."""
    components: list[dict[str, Any]] = []
    for index in range(1, 4):
        root = tmp_path / f"replica-{index}"
        cas = tmp_path / f"cas-{index}"
        root.mkdir()
        cas.mkdir()
        for name in ("replica.sqlite3.writer.lock", "supervisor.writer.lock"):
            (root / name).touch()
        (cas / ".writer.lock").touch()
        (cas / "blobs").mkdir()
        (cas / "staging").mkdir()
        with sqlite3.connect(root / "replica.sqlite3") as database:
            database.execute("CREATE TABLE replica_meta (key TEXT PRIMARY KEY,value TEXT)")
            database.executemany(
                "INSERT INTO replica_meta VALUES (?,?)",
                [
                    ("identity", json.dumps(["original-cluster", "original-schema"])),
                    (
                        "certificate_trust",
                        json.dumps(
                            {
                                "bootstrap": {
                                    str(member): f"sha256:{member:064x}" for member in range(1, 4)
                                }
                            }
                        ),
                    ),
                ],
            )
        components.extend(
            [
                {
                    "component_id": f"replica-{index}",
                    "kind": "replica",
                    "root": str(root),
                    "database": "replica.sqlite3",
                    "trees": [],
                    "depends_on": [f"cas-{index}"],
                },
                {
                    "component_id": f"cas-{index}",
                    "kind": "artifact",
                    "root": str(cas),
                    "database": None,
                    "trees": [],
                    "depends_on": [],
                },
            ]
        )
    payload = Path(__file__).read_bytes()
    checksum = hashlib.sha256(payload).hexdigest()
    blob = tmp_path / "cas-1/blobs/sha256" / checksum[:2] / checksum
    blob.parent.mkdir(parents=True)
    blob.write_bytes(payload)
    (tmp_path / "cas-1/staging/uncommitted").write_bytes(b"not finalized")
    return {
        "schema": "roboguide.recovery-profile/v0.2",
        "profile_id": "explicit-original-replica-set",
        "revision": 1,
        "max_bytes": 32 * 1024 * 1024,
        "max_files": 100,
        "budget_seconds": 30,
        "artifact_replica_sets": [["cas-1", "cas-2", "cas-3"]],
        "components": components,
    }


def test_explicit_finalized_cas_recovery_preserves_sql_and_never_repeats_missing_placements(
    replica_files: dict[str, Any], tmp_path: Path
) -> None:
    """Only verified original finalized CAS bytes are copied; retries remain strictly readonly."""
    created = backup(replica_files, tmp_path / "bundle", "original-all-stores")
    receipt = restore(
        tmp_path / "bundle", created["manifest_digest"], tmp_path / "restored", "original-restore"
    )
    assert receipt["schema"] == "roboguide.restore-receipt/v0.2"
    assert len(receipt["artifact_replica_placements"]) == 2
    assert not (tmp_path / "restored/stores/cas-2/staging/uncommitted").exists()
    assert (
        restore(
            tmp_path / "bundle",
            created["manifest_digest"],
            tmp_path / "restored",
            "original-restore",
        )
        == receipt
    )
    missing = tmp_path / "restored" / receipt["artifact_replica_placements"][0]["target"]
    missing.unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        restore(
            tmp_path / "bundle",
            created["manifest_digest"],
            tmp_path / "restored",
            "original-restore",
        )
    assert not missing.exists()


def test_v3_keeps_original_replica_content_placement_and_receipt_rules(
    replica_files: dict[str, Any], tmp_path: Path
) -> None:
    """The new worker profile retains the complete original v0.2 replica/CAS contract."""
    replica_files["schema"] = "roboguide.recovery-profile/v0.3"
    created = backup(replica_files, tmp_path / "v3-bundle", "original-v3-all-stores")
    receipt = restore(
        tmp_path / "v3-bundle",
        created["manifest_digest"],
        tmp_path / "v3-restored",
        "original-v3-restore",
    )
    assert receipt["schema"] == "roboguide.restore-receipt/v0.3"
    assert len(receipt["artifact_replica_placements"]) == 2
    assert (
        restore(
            tmp_path / "v3-bundle",
            created["manifest_digest"],
            tmp_path / "v3-restored",
            "original-v3-restore",
        )
        == receipt
    )


def test_replica_content_recovery_rejects_cross_cluster_or_incomplete_original_universe(
    replica_files: dict[str, Any], tmp_path: Path
) -> None:
    """Explicit paths cannot authorize copying content between actual different SQL identities."""
    database_path = tmp_path / "replica-2/replica.sqlite3"
    with sqlite3.connect(database_path) as database:
        database.execute(
            "UPDATE replica_meta SET value=? WHERE key='identity'",
            (json.dumps(["foreign", "original-schema"]),),
        )
    with pytest.raises(ValueError, match="cluster/schema/bootstrap"):
        backup(replica_files, tmp_path / "foreign", "cross-cluster")
    assert not (tmp_path / "foreign.partial-cross-cluster").exists()
    with sqlite3.connect(database_path) as database:
        database.execute(
            "UPDATE replica_meta SET value=? WHERE key='identity'",
            (json.dumps(["original-cluster", "original-schema"]),),
        )
    replica_files["artifact_replica_sets"][0].pop()
    with pytest.raises(ValueError, match="3-9"):
        backup(replica_files, tmp_path / "partial", "incomplete-set")


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


def replica_v1_profile(tmp_path: Path) -> dict[str, Any]:
    """Create a complete SQL storage boundary for recovery corruption tests without Nodes."""
    root = tmp_path / "replica"
    root.mkdir()
    (root / "replica.sqlite3.writer.lock").touch()
    (root / "supervisor.writer.lock").touch()
    database = root / "replica.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            PRAGMA user_version=1;
            CREATE TABLE replica_storage_format(
                version INTEGER PRIMARY KEY CHECK(version=1), schema TEXT NOT NULL
            );
            INSERT INTO replica_storage_format VALUES (1,'roboguide.replica-storage/v0.1');
            CREATE TABLE replica_v1_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE replica_v1_commands(
                revision INTEGER PRIMARY KEY, command_id TEXT UNIQUE NOT NULL, data TEXT NOT NULL
            );
            CREATE TABLE replica_v1_log(log_index INTEGER PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE replica_v1_events(
                sequence INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL,
                data TEXT NOT NULL, digest TEXT NOT NULL
            );
            CREATE TABLE replica_v1_snapshot(
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                meta TEXT NOT NULL, data BLOB NOT NULL
            );
            CREATE VIEW replica_meta AS SELECT * FROM replica_v1_meta;
            CREATE VIEW replica_commands AS SELECT * FROM replica_v1_commands;
            CREATE VIEW replica_log AS SELECT * FROM replica_v1_log;
            CREATE VIEW replica_events AS SELECT * FROM replica_v1_events;
            CREATE VIEW replica_snapshot AS SELECT * FROM replica_v1_snapshot;
            INSERT INTO replica_v1_meta VALUES ('identity','["cluster","schema"]');
            """
        )
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("INSERT INTO replica_meta VALUES ('old','writer')")
    return {
        "schema": "roboguide.recovery-profile/v0.5",
        "profile_id": "replica-v1",
        "revision": 1,
        "max_bytes": 8 * 1024 * 1024,
        "max_files": 32,
        "budget_seconds": 30,
        "artifact_replica_sets": [],
        "components": [
            {
                "component_id": "replica",
                "kind": "replica",
                "root": str(root),
                "database": "replica.sqlite3",
                "trees": [],
                "depends_on": [],
            }
        ],
    }


def test_replica_v1_requires_explicit_profile_and_fences_legacy_writes(tmp_path: Path) -> None:
    """Replica storage v1 preserves complete rows while old names remain readonly views."""
    profile = replica_v1_profile(tmp_path)
    result = backup(profile, tmp_path / "bundle", "replica-v1")
    assert verify(tmp_path / "bundle", result["manifest_digest"])["profile"]["schema"] == (
        "roboguide.recovery-profile/v0.5"
    )
    legacy = copy.deepcopy(profile)
    legacy["schema"] = "roboguide.recovery-profile/v0.4"
    with pytest.raises(ValueError, match="explicit recovery profile v0.5"):
        backup(legacy, tmp_path / "legacy-bundle", "legacy")


@pytest.mark.parametrize(
    "corruption", ["missing-view", "redirected-view", "writer-trigger", "marker"]
)
def test_replica_recovery_revalidates_pinned_sql_fences(tmp_path: Path, corruption: str) -> None:
    """Even exact byte/row pins cannot authorize a damaged storage writer contract."""
    profile = replica_v1_profile(tmp_path)
    outcome = backup(profile, tmp_path / "bundle", "original")
    manifest = verify(tmp_path / "bundle", outcome["manifest_digest"])
    database = tmp_path / "bundle/stores/replica/replica.sqlite3"
    with sqlite3.connect(database) as connection:
        if corruption == "missing-view":
            connection.execute("DROP VIEW replica_meta")
        elif corruption == "redirected-view":
            connection.executescript(
                "DROP VIEW replica_meta; CREATE VIEW replica_meta AS SELECT * FROM replica_v1_log;"
            )
        elif corruption == "writer-trigger":
            connection.executescript(
                "CREATE TRIGGER old_writer INSTEAD OF INSERT ON replica_meta BEGIN "
                "INSERT INTO replica_v1_meta VALUES (NEW.key,NEW.value); END;"
            )
        else:
            connection.execute("UPDATE replica_storage_format SET schema='unrecognized'")
    row = manifest["files"][0]
    raw_database = database.read_bytes()
    row["byte_size"] = len(raw_database)
    row["content_digest"] = digest(raw_database)
    row["sqlite"] = _identity(database, Budget(profile))
    raw = canonical(manifest)
    (tmp_path / "bundle/manifest.json").write_bytes(raw)
    with pytest.raises(ValueError, match="Replica"):
        verify(tmp_path / "bundle", digest(raw))
    with pytest.raises(ValueError, match="Replica"):
        restore(tmp_path / "bundle", digest(raw), tmp_path / "restored", "refuse")
    assert not (tmp_path / "restored").exists()


def test_replica_v2_recovery_preserves_original_transfer_hold(tmp_path: Path) -> None:
    """Preserve the original uncertain ID and reject a forged completed proof."""
    profile = replica_v1_profile(tmp_path)
    profile["schema"] = "roboguide.recovery-profile/v0.6"
    database = tmp_path / "replica/replica.sqlite3"
    original = {
        "transfer_id": "original-uncertain",
        "source_epoch": {"term": 1, "replica_id": 1},
        "target": 2,
        "required_last_log_index": 5,
        "state": "unknown",
    }
    with sqlite3.connect(database) as connection:
        connection.executescript(
            "DROP TABLE replica_storage_format;"
            "CREATE TABLE replica_storage_format("
            "version INTEGER PRIMARY KEY CHECK(version=2),schema TEXT NOT NULL);"
            "INSERT INTO replica_storage_format VALUES (2,'roboguide.replica-storage/v0.2');"
            "PRAGMA user_version=2;"
        )
        connection.execute(
            "INSERT INTO replica_v1_meta VALUES ('leader_transfers_v2',?)",
            (json.dumps([original]),),
        )
    result = backup(profile, tmp_path / "bundle", "original")
    manifest = verify(tmp_path / "bundle", result["manifest_digest"])
    restore(tmp_path / "bundle", result["manifest_digest"], tmp_path / "restored", "restore")
    with sqlite3.connect(tmp_path / "restored/stores/replica/replica.sqlite3") as connection:
        assert json.loads(
            connection.execute(
                "SELECT value FROM replica_v1_meta WHERE key='leader_transfers_v2'"
            ).fetchone()[0]
        ) == [original]
    with pytest.raises(ValueError, match="original recovery profile"):
        backup(
            {**profile, "schema": "roboguide.recovery-profile/v0.5"}, tmp_path / "wrong", "wrong"
        )
    bundled = tmp_path / "bundle/stores/replica/replica.sqlite3"
    with sqlite3.connect(bundled) as connection:
        connection.execute(
            "UPDATE replica_v1_meta SET value=? WHERE key='leader_transfers_v2'",
            (json.dumps([{**original, "state": "completed"}]),),
        )
    row = manifest["files"][0]
    content = bundled.read_bytes()
    row.update(
        byte_size=len(content),
        content_digest=digest(content),
        sqlite=_identity(bundled, Budget(profile)),
    )
    raw = canonical(manifest)
    (tmp_path / "bundle/manifest.json").write_bytes(raw)
    with pytest.raises(ValueError, match="completed quorum proof"):
        verify(tmp_path / "bundle", digest(raw))
