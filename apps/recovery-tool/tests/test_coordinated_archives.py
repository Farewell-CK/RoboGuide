"""Counterexamples over real SQL and exact bytes, without deployment claims."""

from __future__ import annotations

import copy
import json
import sqlite3
from contextlib import closing
from typing import Any

import pytest
from roboguide_recovery import coordinated_archives as archive
from roboguide_recovery.archive_references import _checkpoint_identity
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
