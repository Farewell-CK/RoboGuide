"""Verify archived inputs through original immutable receipts and explicit relocation."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery.archive_references import _checkpoint_identity
from roboguide_recovery.controller_values import FIELDS, install_functions, validate_references
from roboguide_recovery.reference_archives import ReferenceArchiveResolver
from roboguide_recovery.snapshot_references import canonical, digest
from test_controller_values import database


def published_input(tmp_path: Path) -> tuple[sqlite3.Connection, Path, Path, dict[str, Any]]:
    """Archive an original SQLite observation and retain its independent publication receipt."""
    connection = database(tmp_path / "controller.sqlite3", 3)
    install_functions(connection)
    row = dict(zip(FIELDS, connection.execute("SELECT * FROM events").fetchone(), strict=True))
    segment = json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
    root = tmp_path / "archives"
    checkpoint = {
        "schema": "roboguide.controller-checkpoint/v60",
        "event_sequence": 1,
        "checkpoint_json": "{}",
    }
    manifest = {
        "schema": "roboguide.event-archive/v2",
        "start_sequence": 1,
        "end_sequence": 1,
        "event_count": 1,
        "segment_digest": digest(segment),
        "checkpoint_sequence": 1,
        "checkpoint_digest": _checkpoint_identity(checkpoint),
    }
    request: dict[str, Any] = {
        "command_id": "original-archive",
        "principal": "admin",
        "tenant": "*",
        "source_owner": "artifact-store:" + "a" * 64,
        "authority_id": "controller-store:" + "b" * 64,
        "destination": str(root),
        "budget_ms": 1000,
    }
    intent = {
        "schema": "roboguide.archive-intent/v0.1",
        "request": request,
        "manifest": manifest,
        "checkpoint": checkpoint,
        "catalog_digest": digest(b"[]"),
        "inputs": [],
        "boot_id": "b" * 36,
        "time_namespace": "time:[1]",
        "issued_ms": 1,
        "deadline_ms": 1001,
    }
    publication = {
        "schema": "roboguide.content-archive/v0.1",
        "intent": intent,
        "target_owner": "artifact-store:" + "c" * 64,
        "checkpoint_digest": digest(canonical(checkpoint)),
    }
    directory = (
        root
        / "archives-v1"
        / hashlib.sha256(request["authority_id"].encode()).hexdigest()
        / ("archive-" + hashlib.sha256(request["command_id"].encode()).hexdigest())
    )
    directory.mkdir(parents=True)
    for name, value in (
        ("intent.json", intent),
        ("checkpoint.json", checkpoint),
        ("target-owner.json", publication["target_owner"]),
        ("publication.json", publication),
    ):
        (directory / name).write_bytes(canonical(value))
    (directory / "events.jsonl").write_bytes(segment)
    connection.executescript(
        "CREATE TABLE event_archive_manifests(start_sequence INTEGER PRIMARY KEY,"
        "end_sequence INTEGER,manifest_json TEXT);"
        "CREATE TABLE event_archive_operations(command_id TEXT PRIMARY KEY,intent_json TEXT,"
        "state TEXT,receipt_json TEXT);"
        "DELETE FROM controller_v3_events; DELETE FROM telemetry_bodies;"
        "INSERT INTO event_log_metadata VALUES('history_floor','1');"
    )
    connection.execute(
        "INSERT INTO event_log_metadata VALUES('archive_authority_id',?)",
        (request["authority_id"],),
    )
    connection.execute("INSERT INTO event_archive_manifests VALUES(1,1,?)", (json.dumps(manifest),))
    connection.execute(
        "INSERT INTO event_archive_operations VALUES(?,?,'compacted',?)",
        (request["command_id"], json.dumps(intent), digest(canonical(publication))),
    )
    connection.commit()
    return connection, root, directory, row


def test_archived_source_remains_recoverable_after_hot_body_removal(tmp_path: Path) -> None:
    """References retain their original complete observation even after hot row/body compaction."""
    connection, _, _, _ = published_input(tmp_path)
    try:
        validate_references(connection)
        assert connection.execute("SELECT COUNT(*) FROM telemetry_bodies").fetchone() == (0,)
    finally:
        connection.close()


def test_pruned_ordinary_source_index_is_not_recreated_during_recovery(tmp_path: Path) -> None:
    """Safe expendable source-index pruning keeps the complete original archive and its receipt."""
    connection, _, _, _ = published_input(tmp_path)
    try:
        edge_hash = digest(b"roboguide.retention-decision-edges/v0.1")
        gap_hash = digest(b"roboguide.retention-decision-gaps/v0.1")
        head = json.dumps([1, 0, 0, 0, edge_hash, gap_hash, False], separators=(",", ":")).encode()
        connection.execute("DELETE FROM retention_reference_records WHERE source_sequence=1")
        connection.execute(
            "UPDATE retention_reference_head SET records_count=0,head_digest=?", (digest(head),)
        )
        before = list(connection.iterdump())
        validate_references(connection)
        assert list(connection.iterdump()) == before
        assert connection.execute("SELECT COUNT(*) FROM telemetry_bodies").fetchone() == (0,)
        assert ReferenceArchiveResolver(connection)(1, "StateRecordObserved")["record"]
        connection.execute(
            "INSERT INTO retention_reference_records VALUES(1,?)",
            (digest(b"forged-returning-record"),),
        )
        with pytest.raises(ValueError, match="counts"):
            validate_references(connection)
    finally:
        connection.close()


def test_bundle_reference_resolution_uses_only_explicit_relocated_archive_root(
    tmp_path: Path,
) -> None:
    """A recovered bundle cannot resolve references through a surviving live deployment."""
    connection, original, directory, _ = published_input(tmp_path)
    try:
        relocated = tmp_path / "bundle/stores/archive"
        shutil.copytree(original, relocated)
        (directory / "events.jsonl").unlink()
        validate_references(connection, {str(original): relocated})
        with pytest.raises(ValueError, match="absent from the complete bundle"):
            validate_references(connection, {})
    finally:
        connection.close()


def test_archived_source_stays_bound_to_late_complete_decision_input(tmp_path: Path) -> None:
    """A late hot decision resolves its exact archived source after the hot value is gone."""
    connection, _, _, row = published_input(tmp_path)
    try:
        source_record = json.loads(row["payload_json"])["StateRecordObserved"]["record"]
        decision = json.dumps({"StatePolicyDecision": {"receipts": [{"record": source_record}]}})
        connection.execute(
            "INSERT INTO controller_v3_events VALUES(2,'decision',20,'mission',NULL,"
            "'domain.EventPayload.json/v1',?,NULL,NULL,NULL,?)",
            (decision, digest(decision.encode())),
        )
        connection.execute("UPDATE event_log_metadata SET value='2' WHERE key='next_event_number'")
        source_digest = connection.execute(
            "SELECT record_digest FROM retention_reference_records WHERE source_sequence=1"
        ).fetchone()[0]
        connection.execute("INSERT INTO retention_reference_edges VALUES(2,1,?)", (source_digest,))
        seed = digest(b"roboguide.retention-decision-edges/v0.1")
        encoded = json.dumps([2, 1, source_digest], separators=(",", ":")).encode()
        edges = digest(seed.encode() + len(encoded).to_bytes(8, "little") + encoded)
        gaps = digest(b"roboguide.retention-decision-gaps/v0.1")
        head = json.dumps([2, 1, 1, 0, edges, gaps, False], separators=(",", ":")).encode()
        connection.execute(
            "UPDATE retention_reference_head SET through_sequence=2,edges_count=1,"
            "edges_digest=?,head_digest=?",
            (edges, digest(head)),
        )
        validate_references(connection)
        connection.execute("DELETE FROM retention_reference_records WHERE source_sequence=1")
        detached_head = json.dumps([2, 0, 1, 0, edges, gaps, False], separators=(",", ":")).encode()
        connection.execute(
            "UPDATE retention_reference_head SET records_count=0,head_digest=?",
            (digest(detached_head),),
        )
        with pytest.raises(ValueError, match="detached from its source"):
            validate_references(connection)
        connection.execute("INSERT INTO retention_reference_records VALUES(1,?)", (source_digest,))
        connection.execute(
            "UPDATE retention_reference_head SET records_count=1,head_digest=?", (digest(head),)
        )
        source_record["value"] = {"different": True}
        changed = json.dumps({"StatePolicyDecision": {"receipts": [{"record": source_record}]}})
        connection.execute(
            "UPDATE controller_v3_events SET payload_json=?,payload_digest=? WHERE sequence=2",
            (changed, digest(changed.encode())),
        )
        with pytest.raises(ValueError, match="complete original State input"):
            validate_references(connection)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "damage",
    [
        "pending",
        "receipt",
        "missing",
        "segment",
        "publication",
        "authority",
        "uncovered",
        "symlink",
    ],
)
def test_archived_input_never_uses_missing_or_unpublished_evidence(
    tmp_path: Path, damage: str
) -> None:
    """Failed publication and modified original evidence remain explicit errors."""
    connection, _, directory, _ = published_input(tmp_path)
    try:
        if damage == "pending":
            connection.execute(
                "UPDATE event_archive_operations SET state='pending',receipt_json=NULL"
            )
        elif damage == "receipt":
            connection.execute(
                "UPDATE event_archive_operations SET receipt_json=?", (digest(b"foreign"),)
            )
        elif damage == "authority":
            connection.execute(
                "UPDATE event_log_metadata SET value=? WHERE key='archive_authority_id'",
                ("controller-store:" + "d" * 64,),
            )
        elif damage == "uncovered":
            connection.execute("UPDATE event_log_metadata SET value='0' WHERE key='history_floor'")
        elif damage == "missing":
            (directory / "events.jsonl").unlink()
        elif damage == "segment":
            (directory / "events.jsonl").write_bytes(b"{}\n")
        elif damage == "publication":
            (directory / "publication.json").write_bytes(b"{}")
        else:
            renamed = directory / "saved.jsonl"
            (directory / "events.jsonl").rename(renamed)
            (directory / "events.jsonl").symlink_to(renamed)
        with pytest.raises((ValueError, OSError)):
            ReferenceArchiveResolver(connection)(1, "StateRecordObserved")
    finally:
        connection.close()
