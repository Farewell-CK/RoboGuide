"""Retained cleanup parser contracts; actual OS writer and Controller evidence has a native gate."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery import archive_aborts, archive_references, bundle
from roboguide_recovery.snapshot_references import canonical, digest


def original() -> dict[str, Any]:
    """Construct a closed parser-only copy intent without claiming a deployment or device owner."""
    checkpoint = {"schema": archive_aborts.WRAPPER, "event_sequence": 1, "checkpoint_json": "{}"}
    return {
        "schema": "roboguide.archive-intent/v0.1",
        "request": {
            "command_id": "original-copy",
            "principal": "fixture-admin",
            "tenant": "local",
            "source_owner": "artifact-store:" + "a" * 64,
            "authority_id": "controller-store:" + "b" * 64,
            "destination": "/declared/fixture-archive",
            "budget_ms": 1000,
        },
        "manifest": {
            "schema": "roboguide.event-archive/v2",
            "start_sequence": 1,
            "end_sequence": 1,
            "event_count": 1,
            "segment_digest": digest(b"fixture\n"),
            "checkpoint_sequence": 1,
            "checkpoint_digest": archive_references._checkpoint_identity(checkpoint),
        },
        "checkpoint": checkpoint,
        "catalog_digest": digest(canonical([])),
        "inputs": [],
        "boot_id": "0" * 36,
        "time_namespace": "time:[fixture]",
        "issued_ms": 1,
        "deadline_ms": 1001,
    }


def cleanup(copy_intent: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    """Build synthetic parser evidence that never asserts real OS leases."""
    intent = {
        "schema": "roboguide.archive-abort-intent/v0.1",
        "request": {
            "command_id": "original-cleanup",
            "original_command_id": "original-copy",
            "original_intent_digest": digest(canonical(copy_intent)),
            "principal": "fixture-admin",
            "tenant": "local",
            "budget_ms": 1000,
        },
        "source_owner": copy_intent["request"]["source_owner"],
        "authority_id": copy_intent["request"]["authority_id"],
        "destination": copy_intent["request"]["destination"],
        "boot_id": "0" * 36,
        "time_namespace": "time:[fixture]",
        "issued_ms": 3,
        "deadline_ms": 1003,
    }
    fence = {
        "schema": "roboguide.original-local-archive-copier-fence/v0.1",
        "source_owner": intent["source_owner"],
        "source_writer": {"device": 1, "inode": 2},
        "archive_writer": {"device": 1, "inode": 3},
        "process_id": 1,
        "process_start_ticks": 1,
        "boot_id": intent["boot_id"],
        "time_namespace": intent["time_namespace"],
        "observed_ms": 4,
    }
    raw = canonical(
        {"schema": "roboguide.archive-abort-tombstone/v0.1", "intent": intent, "fence": fence}
    )
    receipt = {
        "schema": "roboguide.archive-abort-receipt/v0.1",
        "abort_intent_digest": digest(canonical(intent)),
        "tombstone_digest": digest(raw),
        "fence": fence,
    }
    return intent, receipt, raw


def storage(
    tmp_path: Path, proof: bool = True
) -> tuple[sqlite3.Connection, dict[str, Any], dict[str, Any], dict[str, Any], bytes]:
    """Create actual temporary SQL carrying parser fixtures and a mandatory original marker."""
    copy_intent = original()
    intent, receipt, raw = cleanup(copy_intent)
    connection = sqlite3.connect(tmp_path / "fixture.sqlite3")
    connection.executescript(
        "CREATE TABLE event_log_metadata(key TEXT PRIMARY KEY,value TEXT); "
        "CREATE TABLE event_archive_aborts(command_id TEXT PRIMARY KEY,"
        "original_command_id TEXT,intent_json TEXT,receipt_json TEXT)"
    )
    connection.execute(
        "INSERT INTO event_log_metadata VALUES ('archive_operations_schema',?)",
        (archive_aborts.MARKER,),
    )
    connection.execute(
        "INSERT INTO event_archive_aborts VALUES (?,?,?,?)",
        (
            "original-cleanup",
            "original-copy",
            json.dumps(intent),
            json.dumps(receipt) if proof else None,
        ),
    )
    return connection, copy_intent, intent, receipt, raw


def test_unknown_cleanup_keeps_original_pin_and_complete_receipt_has_exact_tombstone(
    tmp_path: Path,
) -> None:
    """Keep a tombstone without its source SQL receipt explicitly Unknown."""
    connection, copy_intent, intent, receipt, raw = storage(tmp_path, proof=False)
    budget = bundle.Budget({"budget_seconds": 30, "max_files": 64, "max_bytes": 1024 * 1024})
    originals: list[tuple[dict[str, Any], str, str | None]] = [(copy_intent, "pending", None)]
    assert archive_aborts.operations(
        connection, archive_aborts.SCHEMA, budget, originals, current=True
    ) == [(intent, None)]
    prefix = "stores/archive/original"
    name = prefix + "/abort-" + hashlib.sha256(b"original-cleanup").hexdigest() + ".json"
    (tmp_path / name).parent.mkdir(parents=True)
    (tmp_path / name).write_bytes(raw)
    files = {name: {"kind": "content", "content_digest": digest(raw), "byte_size": len(raw)}}
    assert not archive_aborts.validate_tombstone(tmp_path, prefix, intent, None, files, budget)
    assert archive_aborts.validate_tombstone(tmp_path, prefix, intent, receipt, files, budget)
    (tmp_path / name).unlink()
    with pytest.raises(ValueError, match="lost its durable tombstone"):
        archive_aborts.validate_tombstone(tmp_path, prefix, intent, receipt, {}, budget)
    connection.close()


@pytest.mark.parametrize("part", ["source", "input", "budget", "lease", "observation", "version"])
def test_changed_original_cleanup_authority_or_os_evidence_refuses(
    tmp_path: Path, part: str
) -> None:
    """Refuse changed original source, budget or required fence fields."""
    connection, copy_intent, intent, receipt, _ = storage(tmp_path)
    if part == "source":
        intent["source_owner"] = "artifact-store:" + "c" * 64
    elif part == "input":
        intent["request"]["original_intent_digest"] = digest(b"replacement")
    elif part == "budget":
        intent["deadline_ms"] += 1
    elif part == "lease":
        receipt["fence"]["source_writer"]["inode"] = 0
    elif part == "observation":
        receipt["fence"]["observed_ms"] = intent["deadline_ms"]
    else:
        connection.execute("DELETE FROM event_log_metadata")
    connection.execute(
        "UPDATE event_archive_aborts SET intent_json=?,receipt_json=?",
        (json.dumps(intent), json.dumps(receipt)),
    )
    budget = bundle.Budget({"budget_seconds": 30, "max_files": 64, "max_bytes": 1024 * 1024})
    with pytest.raises(ValueError):
        archive_aborts.operations(
            connection,
            archive_aborts.SCHEMA,
            budget,
            [(copy_intent, "pending", None)],
            current=True,
        )
    connection.close()


def test_new_cleanup_authority_refuses_old_profile_even_with_empty_history(tmp_path: Path) -> None:
    """Refuse old recovery semantics even with zero original cleanup rows."""
    connection, copy_intent, _, _, _ = storage(tmp_path)
    connection.execute("DELETE FROM event_archive_aborts")
    budget = bundle.Budget({"budget_seconds": 30, "max_files": 64, "max_bytes": 1024 * 1024})
    with pytest.raises(ValueError, match="v0.13"):
        archive_aborts.operations(
            connection,
            "roboguide.recovery-profile/v0.12",
            budget,
            [(copy_intent, "pending", None)],
            current=True,
        )
    connection.execute("DROP TABLE event_archive_aborts")
    with pytest.raises(ValueError):
        archive_aborts.operations(
            connection,
            archive_aborts.SCHEMA,
            budget,
            [(copy_intent, "pending", None)],
            current=True,
        )
    connection.close()


def test_complete_publication_cannot_coexist_with_an_original_abort_tombstone(
    tmp_path: Path,
) -> None:
    """Refuse conflicting final publication and cleanup evidence."""
    copy_intent = original()
    intent, receipt, raw = cleanup(copy_intent)
    budget = bundle.Budget({"budget_seconds": 30, "max_files": 64, "max_bytes": 1024 * 1024})
    prefix = "stores/archive/original"

    name = prefix + "/abort-" + hashlib.sha256(b"original-cleanup").hexdigest() + ".json"
    (tmp_path / name).parent.mkdir(parents=True)
    (tmp_path / name).write_bytes(raw)
    files = {
        name: {"kind": "content", "content_digest": digest(raw), "byte_size": len(raw)},
        prefix + "/publication.json": {
            "kind": "content",
            "content_digest": digest(b"fixture"),
            "byte_size": 7,
        },
    }
    with pytest.raises(ValueError, match="both final publication"):
        archive_aborts.validate_tombstone(tmp_path, prefix, intent, receipt, files, budget)
