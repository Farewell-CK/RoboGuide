"""Counterexamples over real SQL and exact bytes, without deployment claims."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery import coordinated_archives as archive
from roboguide_recovery.archive_references import _checkpoint_identity
from roboguide_recovery.bundle import Budget
from roboguide_recovery.replica_content_bundle import projection_authority
from roboguide_recovery.snapshot_references import canonical, digest


def evidence() -> tuple[dict[str, Any], bytes]:
    """Freeze original metadata and exact package bytes for independent damage."""
    event = {
        "sequence": 1,
        "event_id": "original-event",
        "timestamp_ms": 3,
        "correlation_id": "original-correlation",
        "causation_id": None,
        "payload_schema": "domain.EventPayload.json/v33",
        "payload_json": '{"original":1.00}',
    }
    checkpoint = {
        "event_sequence": 1,
        "schema": "roboguide.controller-checkpoint/v60",
        "checkpoint_json": '{"原始":1.00}',
    }
    segment = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
    manifest = {
        "schema": "roboguide.event-archive/v2",
        "start_sequence": 1,
        "end_sequence": 1,
        "event_count": 1,
        "segment_digest": digest(segment),
        "checkpoint_sequence": 1,
        "checkpoint_digest": _checkpoint_identity(checkpoint),
    }
    package = {
        "schema": "roboguide.coordinated-archive-package/v0.1",
        "authority_id": "controller-store:" + "a" * 64,
        "manifest": manifest,
        "checkpoint": checkpoint,
        "events": [event],
        "catalog": [],
        "recovery_digest": "sha256:" + "b" * 64,
        "original_inputs": [],
    }
    raw = canonical(package)
    key = {
        "source_owner": "artifact-store:" + "c" * 64,
        "content_digest": digest(raw),
        "byte_size": len(raw),
    }
    intent = {
        "schema": "roboguide.coordinated-archive-intent/v0.1",
        "request": {
            "command_id": "original-archive",
            "principal": "original-admin",
            "source_owner": key["source_owner"],
            "end_sequence": 1,
            "budget_ms": 1000,
        },
        "authority_id": package["authority_id"],
        "issuing_epoch": 'original-profile:{"term":1,"replica_id":7}',
        "previous_floor": 0,
        "manifest": manifest,
        "package": key,
        "catalog_digest": digest(canonical([])),
        "recovery_digest": package["recovery_digest"],
        "boot_id": "0" * 36,
        "time_namespace": "time:[original-test]",
        "issued_ms": 100,
        "deadline_ms": 1100,
    }
    return {"intent": intent, "completed_ms": 200, "abort": None}, raw


def database(row: dict[str, Any]) -> sqlite3.Connection:
    """Create only actual bounded test SQL; declarations establish no Node or physical evidence."""
    sql = sqlite3.connect(":memory:")
    sql.executescript(
        "CREATE TABLE event_log_metadata(key TEXT PRIMARY KEY,value TEXT); "
        "CREATE TABLE coordinated_archive_operations("
        "command_id TEXT PRIMARY KEY,record_json TEXT); "
        "CREATE TABLE event_archive_manifests("
        "start_sequence INTEGER,end_sequence INTEGER,manifest_json TEXT);"
    )
    sql.executemany(
        "INSERT INTO event_log_metadata VALUES (?,?)",
        [
            ("archive_operations_schema", archive.STORAGE),
            ("archive_authority_id", row["intent"]["authority_id"]),
        ],
    )
    sql.execute(
        "INSERT INTO coordinated_archive_operations VALUES (?,?)",
        (row["intent"]["request"]["command_id"], json.dumps(row)),
    )
    sql.execute(
        "INSERT INTO event_archive_manifests VALUES (1,1,?)",
        (json.dumps(row["intent"]["manifest"]),),
    )
    sql.commit()
    return sql


def test_exact_original_bytes_and_sql_are_readonly() -> None:
    """Verification preserves original number spelling, Unicode and retained original clocks."""
    row, raw = evidence()
    with closing(database(row)) as sql:
        before = list(sql.iterdump())
        assert archive.operations(sql, archive.PROFILE) == [row]
        archive.validate_package(raw, row)
        assert list(sql.iterdump()) == before


@pytest.mark.parametrize(
    "damage",
    ["missing-table", "missing-marker", "missing-manifest", "wrong-authority", "old-profile"],
)
def test_missing_original_authority_refuses(damage: str) -> None:
    """Reject missing original authority despite accepted schema or matching hashes."""
    row, _ = evidence()
    with closing(database(row)) as sql:
        profile = archive.PROFILE
        if damage == "missing-table":
            sql.execute("DROP TABLE coordinated_archive_operations")
        elif damage == "missing-marker":
            sql.execute("DELETE FROM event_log_metadata WHERE key='archive_operations_schema'")
        elif damage == "missing-manifest":
            sql.execute("DELETE FROM event_archive_manifests")
        elif damage == "wrong-authority":
            sql.execute(
                "UPDATE event_log_metadata SET value=? WHERE key='archive_authority_id'",
                ("controller-store:" + "d" * 64,),
            )
        else:
            profile = "roboguide.recovery-profile/v0.29"
        before = list(sql.iterdump())
        with pytest.raises(ValueError):
            archive.operations(sql, profile)
        assert list(sql.iterdump()) == before


@pytest.mark.parametrize(
    "damage",
    [
        "late-completion",
        "renewed-budget",
        "false-clock",
        "changed-owner",
        "changed-prefix",
        "aborted-completion",
    ],
)
def test_original_budget_owner_and_disposition_cannot_be_rewritten(damage: str) -> None:
    """Preserve the original owner, complete prefix and first immutable budget."""
    row, _ = evidence()
    if damage == "late-completion":
        row["completed_ms"] = row["intent"]["deadline_ms"]
    elif damage == "renewed-budget":
        row["intent"]["deadline_ms"] += 1
    elif damage == "false-clock":
        row["intent"]["issued_ms"] = True
    elif damage == "changed-owner":
        row["intent"]["package"]["source_owner"] = "artifact-store:" + "d" * 64
    elif damage == "changed-prefix":
        row["intent"]["manifest"]["start_sequence"] = 2
    else:
        row["abort"] = {"principal": "admin", "issuing_epoch": "next-epoch"}
    with pytest.raises(ValueError):
        archive.record(row)


@pytest.mark.parametrize(
    "damage",
    [
        "missing-event",
        "changed-catalog",
        "changed-checkpoint",
        "duplicate-owner",
        "raw-byte-corruption",
    ],
)
def test_rehashed_package_cannot_replace_original_evidence(damage: str) -> None:
    """Even matching outer CAS hashes cannot hide changed inner original joins or discontinuity."""
    row, raw = evidence()
    package = json.loads(raw)
    if damage == "missing-event":
        package["events"] = []
    elif damage == "changed-catalog":
        package["catalog"] = [copy.deepcopy(package["events"][0])]
    elif damage == "changed-checkpoint":
        package["checkpoint"]["checkpoint_json"] = '{"原始":1.0}'
    elif damage == "duplicate-owner":
        key = {
            "source_owner": "artifact-store:" + "a" * 64,
            "content_digest": "sha256:" + "b" * 64,
            "byte_size": 7,
        }
        package["original_inputs"] = [key, key]
    else:
        with pytest.raises(ValueError):
            archive.validate_package(raw + b" ", row)
        return
    raw = canonical(package)
    row["intent"]["package"].update(content_digest=digest(raw), byte_size=len(raw))
    with pytest.raises(ValueError):
        archive.validate_package(raw, row)


def projection_fixture(root: Path) -> list[dict[str, Any]]:
    """Retain actual test SQL/CAS for an old projection and a newer archived parent prefix."""
    row, raw = evidence()
    package = json.loads(raw)
    fields = (
        "sequence",
        "event_id",
        "timestamp_ms",
        "correlation_id",
        "causation_id",
        "payload_schema",
        "payload_json",
    )
    second = package["events"][0] | {"sequence": 2, "event_id": "protected-original-event"}
    package["events"].append(second)
    package["checkpoint"]["event_sequence"] = 2
    manifest = package["manifest"]
    manifest.update(
        end_sequence=2,
        event_count=2,
        checkpoint_sequence=2,
        checkpoint_digest=_checkpoint_identity(package["checkpoint"]),
        segment_digest=digest(
            b"".join(
                json.dumps(
                    {name: event[name] for name in fields},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
                + b"\n"
                for event in package["events"]
            )
        ),
    )
    row["intent"]["manifest"] = manifest
    row["intent"]["request"]["end_sequence"] = 2
    raw = canonical(package)
    row["intent"]["package"].update(content_digest=digest(raw), byte_size=len(raw))
    components: list[dict[str, Any]] = [
        {"component_id": "child", "kind": "controller", "database": "child.sqlite3"},
        {
            "component_id": "parent",
            "kind": "replica",
            "database": "parent.sqlite3",
            "depends_on": ["child"],
        },
        {"component_id": "original-root", "kind": "replica-content", "depends_on": ["parent"]},
    ]
    for component in components:
        (root / "stores" / component["component_id"]).mkdir(parents=True)
    recovery = {
        "schema": archive.RECOVERY,
        "metadata": {
            "archive_authority_id": row["intent"]["authority_id"],
            "archive_operations_schema": archive.STORAGE,
        },
        "catalog_archive_events": [second],
        "coordinated_archives": [row],
        "archive_manifests": [
            {"start_sequence": 1, "end_sequence": 2, "manifest_json": json.dumps(manifest)}
        ],
    }
    with closing(sqlite3.connect(root / "stores/parent/parent.sqlite3")) as sql:
        sql.executescript(
            "CREATE TABLE replica_v1_meta(key TEXT PRIMARY KEY,value TEXT);"
            "CREATE TABLE replica_v1_events(sequence INTEGER PRIMARY KEY,data TEXT);"
        )
        sql.execute(
            "INSERT INTO replica_v1_meta VALUES ('content_state',?)",
            (json.dumps({"candidate": {"profile_identity": "d" * 64, "recovery": recovery}}),),
        )
        sql.commit()
    with closing(sqlite3.connect(root / "stores/child/child.sqlite3")) as sql:
        sql.execute(
            "CREATE TABLE events(sequence INTEGER PRIMARY KEY,event_id TEXT,timestamp_ms INTEGER,"
            "correlation_id TEXT,causation_id TEXT,payload_schema TEXT,payload_json TEXT)"
        )
        sql.execute(
            "INSERT INTO events VALUES (?,?,?,?,?,?,?)",
            tuple(package["events"][0][name] for name in fields),
        )
        sql.commit()
    key = row["intent"]["package"]
    path = (
        root
        / "stores/original-root/content-v1"
        / key["source_owner"][15:]
        / key["content_digest"][7:]
    )
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    return components


def test_original_projection_accepts_exact_archived_prefix_without_writing(tmp_path: Path) -> None:
    """A retired child's exact prefix remains provable through its own parent's actual Archive."""
    components = projection_fixture(tmp_path)
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert (
        projection_authority(
            tmp_path,
            components[0],
            components,
            "controller-store:" + "a" * 64,
            Budget({"max_files": 100, "max_bytes": 1048576, "budget_seconds": 30}),
        )
        == "d" * 64
    )
    assert before == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before}


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "corrupt",
        "rehashed",
        "wrong-root",
        "ambiguous-root",
        "changed-local",
        "conflicting-protected",
        "wrong-authority",
        "pending",
    ],
)
def test_archived_projection_keeps_exact_member_prefix_and_package_fences(
    tmp_path: Path, damage: str
) -> None:
    """Another Root, outer hash, pending record or changed fact cannot lend prefix authority."""
    components = projection_fixture(tmp_path)
    path = next((tmp_path / "stores/original-root/content-v1").glob("*/*"))
    with closing(sqlite3.connect(tmp_path / "stores/parent/parent.sqlite3")) as sql:
        state = json.loads(sql.execute("SELECT value FROM replica_v1_meta").fetchone()[0])
        recovery = state["candidate"]["recovery"]
        if damage == "missing":
            path.unlink()
        elif damage == "corrupt":
            raw = path.read_bytes()
            path.write_bytes(b"!" + raw[1:])
        elif damage == "rehashed":
            package = json.loads(path.read_bytes())
            package["events"][0]["payload_json"] = '{"original":2}'
            raw = canonical(package)
            recovery["coordinated_archives"][0]["intent"]["package"].update(
                content_digest=digest(raw), byte_size=len(raw)
            )
            path.with_name(digest(raw)[7:]).write_bytes(raw)
            path.unlink()
        elif damage == "wrong-root":
            other = tmp_path / "stores/another-root/content-v1" / path.parent.name / path.name
            other.parent.mkdir(parents=True)
            path.rename(other)
            components.append(
                {
                    "component_id": "another-root",
                    "kind": "replica-content",
                    "depends_on": ["other-parent"],
                }
            )
        elif damage == "ambiguous-root":
            components.append(
                {
                    "component_id": "another-root",
                    "kind": "replica-content",
                    "depends_on": ["parent"],
                }
            )
        elif damage == "changed-local":
            with closing(sqlite3.connect(tmp_path / "stores/child/child.sqlite3")) as child:
                child.execute("UPDATE events SET payload_json='{}'")
                child.commit()
        elif damage == "conflicting-protected":
            recovery["catalog_archive_events"][0]["payload_json"] = "{}"
        elif damage == "wrong-authority":
            recovery["metadata"]["archive_authority_id"] = "controller-store:" + "e" * 64
        else:
            recovery["coordinated_archives"][0]["completed_ms"] = None
        sql.execute("UPDATE replica_v1_meta SET value=?", (json.dumps(state),))
        sql.commit()
    with pytest.raises((ValueError, OSError)):
        projection_authority(
            tmp_path,
            components[0],
            components,
            "controller-store:" + "a" * 64,
            Budget({"max_files": 100, "max_bytes": 1048576, "budget_seconds": 30}),
        )
