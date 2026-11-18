"""Offline administrator cancellation closure without native requests or device proof."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import socket
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from .journal import MANUAL_SETTLEMENT_SCHEMA, Conflict, Journal, canonical

FIELDS = {
    "schema_version",
    "command_id",
    "handle",
    "request_digest",
    "expected_sequence",
    "expected_audit_sha256",
    "confirmed",
    "disposition",
    "attestation",
    "evidence",
}


def audit_digest(
    journal: Journal, handle: str, *, original_row: dict[str, Any] | None = None
) -> str:
    """Bind the complete original row and every retained event including its wire bytes."""
    with journal.lock:
        events = []
        for item in journal.events(handle):
            item["raw"] = item["raw"].hex()
            events.append(item)
        value = {
            "run": journal.get(handle) if original_row is None else original_row,
            "events": events,
        }
        return hashlib.sha256(canonical(value).encode()).hexdigest()


def validate_restored(journal: Journal) -> None:
    """Reject missing, changed or detached operator receipts before accepting new work."""
    try:
        receipts = journal.db.execute("SELECT * FROM operator_settlements").fetchall()
        if not receipts:
            raise Conflict("manual journal has no retained operator settlement")
        handles = set()
        for entry in receipts:
            row = journal.get(entry["handle"])
            receipt = json.loads(entry["receipt"])
            command = json.loads(entry["command"])
            marker = row["progress"].get("manual_cancel_settlement", {})
            if (
                receipt["schema_version"] != "robonix.local-cancel-receipt/v0.1"
                or receipt["command"] != command
                or receipt["principal"] != entry["principal"]
                or command["command_id"] != entry["command_id"]
                or command["handle"] != row["handle"]
                or command["request_digest"] != row["digest"]
                or receipt["attempt_id"] != row["attempt"]
                or receipt["native_session_id"] != row["session_id"]
                or receipt["native_task_id"] != row["task_id"]
                or receipt["previous_state"] != "UNKNOWN"
                or receipt["previous_sequence"] != command["expected_sequence"]
                or row["sequence"] != command["expected_sequence"] + 1
                or row["state"] != "CANCELED"
                or row["detail"] != entry["receipt"]
                or marker
                != {
                    "command_id": entry["command_id"],
                    "principal": entry["principal"],
                    "receipt_sha256": hashlib.sha256(entry["receipt"].encode()).hexdigest(),
                }
                or receipt["physical_stop_confirmed"] is not False
                or receipt["automatic_continuation_authorized"] is not False
            ):
                raise Conflict("operator settlement identity or receipt changed")
            original = dict(row)
            original["progress"] = dict(row["progress"])
            original["progress"].pop("manual_cancel_settlement")
            original.update(
                state=receipt["previous_state"],
                detail=receipt["previous_detail"],
                sequence=receipt["previous_sequence"],
            )
            if (
                audit_digest(journal, row["handle"], original_row=original)
                != command["expected_audit_sha256"]
            ):
                raise Conflict("original settled task evidence changed")
            handles.add(row["handle"])
        for value in journal.db.execute("SELECT * FROM runs"):
            row = journal.decode(value)
            if bool(row["progress"].get("manual_cancel_settlement")) != (row["handle"] in handles):
                raise Conflict("operator settlement marker is detached")
    except (KeyError, TypeError, ValueError, sqlite3.Error) as exc:
        raise Conflict("invalid retained operator settlement: " + str(exc)) from exc


def settle(journal: Journal, command: dict[str, Any], principal: str) -> dict[str, Any]:
    """Record an exact administrator decision, preserving native evidence and original clocks."""
    encoded = canonical(command)
    if (
        set(command) != FIELDS
        or len(encoded.encode()) > 16384
        or command["schema_version"] != "robonix.local-cancel-settlement/v0.1"
        or command["confirmed"] is not True
        or command["disposition"] != "stopped"
        or type(command["expected_sequence"]) is not int
        or command["expected_sequence"] < 1
        or not isinstance(command["evidence"], dict)
        or not command["evidence"]
        or not isinstance(principal, str)
        or not 1 <= len(principal.strip()) <= 256
    ):
        raise ValueError("invalid explicit local cancellation settlement")
    for key, lower, upper in (
        ("command_id", 1, 128),
        ("handle", 1, 128),
        ("request_digest", 64, 64),
        ("expected_audit_sha256", 64, 64),
        ("attestation", 16, 4096),
    ):
        value = command[key]
        if not isinstance(value, str) or not lower <= len(value.strip()) <= upper:
            raise ValueError("invalid settlement field: " + key)
    for key in ("request_digest", "expected_audit_sha256"):
        if any(char not in "0123456789abcdef" for char in command[key]):
            raise ValueError("invalid SHA256: " + key)
    with journal.lock, journal.db:
        exists = journal.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='operator_settlements'"
        ).fetchone()
        prior = None
        if exists:
            prior = journal.db.execute(
                "SELECT * FROM operator_settlements WHERE command_id=? OR handle=?",
                (command["command_id"], command["handle"]),
            ).fetchone()
        if prior is not None:
            if prior["principal"] == principal and prior["command"] == encoded:
                return dict(json.loads(prior["receipt"]))
            raise Conflict("original cancellation has a different administrator settlement")
        row = journal.get(command["handle"])
        if (
            row["state"] != "UNKNOWN"
            or not row["cancel_requested"]
            or not row["abort_sent"]
            or not row["dispatched"]
            or row["digest"] != command["request_digest"]
            or row["sequence"] != command["expected_sequence"]
            or audit_digest(journal, row["handle"]) != command["expected_audit_sha256"]
        ):
            raise Conflict("original cancelled Unknown identity, sequence or evidence changed")
        receipt = {
            "schema_version": "robonix.local-cancel-receipt/v0.1",
            "principal": principal,
            "command": command,
            "settled_at_ns": time.time_ns(),
            "attempt_id": row["attempt"],
            "native_session_id": row["session_id"],
            "native_task_id": row["task_id"],
            "previous_state": row["state"],
            "previous_detail": row["detail"],
            "previous_sequence": row["sequence"],
            "physical_stop_confirmed": False,
            "automatic_continuation_authorized": False,
        }
        receipt_text = canonical(receipt)
        progress = row["progress"]
        progress["manual_cancel_settlement"] = {
            "command_id": command["command_id"],
            "principal": principal,
            "receipt_sha256": hashlib.sha256(receipt_text.encode()).hexdigest(),
        }
        profile = json.loads(
            journal.db.execute("SELECT value FROM meta WHERE key='profile'").fetchone()[0]
        )
        profile["schema"] = MANUAL_SETTLEMENT_SCHEMA
        journal.db.execute(
            "CREATE TABLE IF NOT EXISTS operator_settlements "
            "(command_id TEXT PRIMARY KEY, handle TEXT UNIQUE NOT NULL, "
            "principal TEXT NOT NULL, command TEXT NOT NULL, receipt TEXT NOT NULL)"
        )
        journal.db.execute("UPDATE meta SET value=? WHERE key='profile'", (canonical(profile),))
        journal.db.execute(
            "INSERT INTO operator_settlements VALUES(?,?,?,?,?)",
            (command["command_id"], row["handle"], principal, encoded, receipt_text),
        )
        journal.db.execute(
            "UPDATE runs SET state='CANCELED',detail=?,progress=?,sequence=sequence+1 "
            "WHERE handle=?",
            (receipt_text, canonical(progress), row["handle"]),
        )
        return receipt


def main() -> None:
    """Require an offline single-owner journal and a reviewed exact command file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--command", required=True, type=Path)
    args = parser.parse_args()
    raw = args.command.read_bytes()
    if len(raw) > 16384:
        raise ValueError("settlement command exceeds 16 KiB")
    command = json.loads(raw)
    if not isinstance(command, dict):
        raise ValueError("settlement command must be an object")
    with closing(
        sqlite3.connect(
            (args.directory / "pilot.sqlite3").resolve().as_uri() + "?mode=ro", uri=True
        )
    ) as db:
        metadata = json.loads(
            db.execute("SELECT value FROM meta WHERE key='profile'").fetchone()[0]
        )
    counts, events, size = metadata["limits"]
    principal = "local-os:" + socket.gethostname() + ":" + getpass.getuser()
    if hasattr(os, "getuid"):
        principal += ":" + str(os.getuid())
    journal = Journal(
        args.directory, metadata["profile"], max_records=counts, max_events=events, max_bytes=size
    )
    try:
        print(json.dumps(settle(journal, command, principal), ensure_ascii=False))
    finally:
        journal.close()


if __name__ == "__main__":
    main()
