"""Read original source GC history without creating or completing effects."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import Any

from .profile import real_directory
from .snapshot_references import OperationBudget, canonical, digest

SCHEMA = "roboguide.recovery-profile/v0.16"
PROFILES = {
    SCHEMA,
    "roboguide.recovery-profile/v0.18",
    "roboguide.recovery-profile/v0.24",
    "roboguide.recovery-profile/v0.25",
    "roboguide.recovery-profile/v0.26",
    "roboguide.recovery-profile/v0.27",
    "roboguide.recovery-profile/v0.28",
    "roboguide.recovery-profile/v0.29",
    "roboguide.recovery-profile/v0.30",
}
STORAGE = "roboguide.artifact-storage/v5"
WRAPPER = "roboguide.controller-checkpoint/v52"
WRAPPERS = {
    WRAPPER,
    "roboguide.controller-checkpoint/v53",
    "roboguide.controller-checkpoint/v54",
    "roboguide.controller-checkpoint/v55",
    "roboguide.controller-checkpoint/v56",
    "roboguide.controller-checkpoint/v57",
    "roboguide.controller-checkpoint/v58",
    "roboguide.controller-checkpoint/v59",
    "roboguide.controller-checkpoint/v60",
}
SQL_SCHEMA = "roboguide.artifact-gc-sql/v0.1"
DATABASE = "reclamation-v1/journal.sqlite3"
MAX_RECORD = 512 * 1024
MAX_HISTORY = 64 * 1024 * 1024
MAX_COMMANDS = 4096
COMMAND = re.compile(r"[a-zA-Z0-9_-]{1,128}\Z")
CHECKSUM = re.compile(r"sha256:[0-9a-f]{64}\Z")
OWNER = re.compile(r"artifact-store:[0-9a-f]{64}\Z")
AUTHORITY = re.compile(r"controller-store:[0-9a-f]{64}\Z")


def _object(raw: Any) -> dict[str, Any]:
    """Require complete bounded canonical JSON before interpreting original authority."""
    if not isinstance(raw, str) or len(raw.encode()) > MAX_RECORD:
        raise ValueError("original Artifact GC record exceeds its bound")
    value = json.loads(raw)
    if not isinstance(value, dict) or canonical(value).decode() != raw:
        raise ValueError("original Artifact GC record is not canonical")
    return value


def _integer(value: Any, *, positive: bool = False) -> bool:
    """Keep booleans and oversized numeric declarations out of original OS evidence."""
    return type(value) is int and (0 < value if positive else 0 <= value) and value < 2**64


def _text(value: Any, maximum: int) -> bool:
    """Keep bounded original human identifiers without wildcard or control interpretation."""
    return (
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and bool(value.strip())
        and "*" not in value
        and not any(ord(character) < 32 for character in value)
    )


def validate_process(kernel: Any, fence: Any) -> None:
    """Validate retained actual kernel/process/writer evidence without creating a GC intent."""
    if (
        not isinstance(kernel, dict)
        or set(kernel) != {"boot_id", "time_namespace", "admitted_ns", "deadline_ns"}
        or not _text(kernel["boot_id"], 128)
        or not _text(kernel["time_namespace"], 128)
        or not all(_integer(kernel[key]) for key in ("admitted_ns", "deadline_ns"))
        or kernel["deadline_ns"] <= kernel["admitted_ns"]
    ):
        raise ValueError("original Artifact GC kernel budget is invalid")
    if (
        not isinstance(fence, dict)
        or set(fence) != {"pid", "start_ticks", "uid", "writers"}
        or not _integer(fence["pid"], positive=True)
        or not _integer(fence["start_ticks"], positive=True)
        or not _integer(fence["uid"])
        or not isinstance(fence["writers"], list)
        or not 1 <= len(fence["writers"]) <= 4096
    ):
        raise ValueError("original Artifact GC process/writer evidence is invalid")
    seen = set()
    for writer in fence["writers"]:
        if (
            not isinstance(writer, dict)
            or set(writer) != {"component_id", "path", "device", "inode"}
            or not _text(writer["component_id"], 128)
            or not _text(writer["path"], 4096)
            or not Path(writer["path"]).is_absolute()
            or not _integer(writer["device"])
            or not _integer(writer["inode"], positive=True)
            or writer["path"] in seen
        ):
            raise ValueError("original Artifact GC writer coverage changed")
        seen.add(writer["path"])


def validate_intent(value: dict[str, Any], owner: str) -> None:
    """Validate original command, actual writers, closure and kernel budget."""
    if (
        set(value)
        != {
            "schema",
            "command_id",
            "component_id",
            "store_owner_id",
            "profile_digest",
            "content_digest",
            "byte_size",
            "reason",
            "kernel",
            "fence",
            "content_inode",
            "known_authorities",
            "closure",
            "original_tombstone",
        }
        or value["schema"] != "roboguide.artifact-gc-intent/v0.1"
        or not isinstance(value["command_id"], str)
        or COMMAND.fullmatch(value["command_id"]) is None
        or value["store_owner_id"] != owner
        or OWNER.fullmatch(owner) is None
        or not _text(value["component_id"], 128)
        or not _text(value["reason"], 1024)
        or any(
            not isinstance(value[key], str) or CHECKSUM.fullmatch(value[key]) is None
            for key in ("profile_digest", "content_digest")
        )
        or not _integer(value["byte_size"])
        or value["byte_size"] > 4 * 1024**3
    ):
        raise ValueError("original Artifact GC intent/owner/input is invalid")
    validate_process(value["kernel"], value["fence"])
    inode = value["content_inode"]
    if inode is not None and (
        not isinstance(inode, dict)
        or set(inode) != {"device", "inode"}
        or not _integer(inode["device"])
        or not _integer(inode["inode"], positive=True)
    ):
        raise ValueError("original Artifact GC content inode is invalid")
    known, closure = value["known_authorities"], value["closure"]
    if (
        not isinstance(known, list)
        or len(known) > 1024
        or any(not isinstance(key, str) or AUTHORITY.fullmatch(key) is None for key in known)
        or known != sorted(set(known))
        or not isinstance(closure, list)
        or len(closure) != len(known)
    ):
        raise ValueError("original Artifact GC known authority coverage is incomplete")
    for key, proof in zip(known, closure, strict=True):
        if (
            not isinstance(proof, dict)
            or set(proof)
            != {
                "authority_id",
                "component_id",
                "event_sequence",
                "checkpoint_digest",
                "catalog_digest",
            }
            or proof["authority_id"] != key
            or not _text(proof["component_id"], 128)
            or not _integer(proof["event_sequence"])
            or any(
                not isinstance(proof[name], str) or CHECKSUM.fullmatch(proof[name]) is None
                for name in ("checkpoint_digest", "catalog_digest")
            )
        ):
            raise ValueError("original Artifact GC exact closure evidence changed")
    if value["original_tombstone"] is not None and (
        not isinstance(value["original_tombstone"], str)
        or COMMAND.fullmatch(value["original_tombstone"]) is None
    ):
        raise ValueError("original Artifact GC predecessor tombstone is invalid")


def validate(
    root: Path, owner: str, budget: OperationBudget | None = None
) -> tuple[dict[str, tuple[dict[str, Any], dict[str, Any] | None]], dict[str, dict[str, Any]]]:
    """Refuse missing original rows/heads or malformed SQL/receipt authority."""
    path = root / DATABASE
    real_directory(path.parent)
    if any(
        Path(str(path) + suffix).exists() or Path(str(path) + suffix).is_symlink()
        for suffix in ("-wal", "-shm")
    ):
        raise ValueError("original Artifact GC SQL has unsupported WAL authority")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 128 * 1024 * 1024:
            raise ValueError("original Artifact GC SQL is unavailable or exceeds its bound")
        header = stream.read(20)
        if len(header) != 20 or header[:16] != b"SQLite format 3\0" or header[18:] != b"\x01\x01":
            raise ValueError(
                "original Artifact GC SQL header requires explicit DELETE journal mode"
            )
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
            raise ValueError("original Artifact GC SQL journal mode changed")
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_RECORD + 1)
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if (
            tables != {"gc_metadata", "gc_commands", "gc_tombstones"}
            or connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type IN ('view','trigger') LIMIT 1"
            ).fetchone()
            is not None
        ):
            raise ValueError("original Artifact GC SQL tables/authority changed")
        markers = dict(connection.execute("SELECT key,value FROM gc_metadata"))
        if set(markers) != {"schema", "store_owner_id", "command_ids", "tombstone_digests"} or (
            markers["schema"] != SQL_SCHEMA or markers["store_owner_id"] != owner
        ):
            raise ValueError("original Artifact GC SQL marker/owner is missing or changed")
        known, retired = (
            json.loads(markers["command_ids"]),
            json.loads(markers["tombstone_digests"]),
        )
        for head, pattern in ((known, COMMAND), (retired, CHECKSUM)):
            if (
                not isinstance(head, list)
                or len(head) > MAX_COMMANDS
                or any(not isinstance(key, str) or pattern.fullmatch(key) is None for key in head)
                or head != sorted(set(head))
            ):
                raise ValueError("original Artifact GC mandatory inventory is invalid")
        commands, tombstones = {}, {}
        total = 0
        for index, (key, raw, result) in enumerate(
            connection.execute(
                "SELECT command_id,intent_json,receipt_json FROM gc_commands "
                "ORDER BY command_id LIMIT 4097"
            )
        ):
            if budget is not None:
                budget.check()
            total += len(raw.encode()) + (len(result.encode()) if result is not None else 0)
            if index >= MAX_COMMANDS or total > MAX_HISTORY:
                raise ValueError("original Artifact GC history exceeds its bounds")
            intent = _object(raw)
            validate_intent(intent, owner)
            if intent["command_id"] != key or key not in known:
                raise ValueError("original Artifact GC command inventory conflicts")
            commands[key] = intent, None if result is None else _object(result)
        for index, (checksum, first, raw) in enumerate(
            connection.execute(
                "SELECT content_digest,first_command_id,tombstone_json "
                "FROM gc_tombstones LIMIT 4097"
            )
        ):
            if budget is not None:
                budget.check()
            if index >= MAX_COMMANDS or first not in commands:
                raise ValueError("original Artifact GC tombstone command is absent")
            value, intent = _object(raw), commands[first][0]
            if (
                value
                != {
                    "schema": "roboguide.artifact-gc-tombstone/v0.1",
                    "store_owner_id": owner,
                    "content_digest": checksum,
                    "byte_size": intent["byte_size"],
                    "first_command_id": first,
                    "first_intent_digest": digest(canonical(intent)),
                }
                or intent["content_digest"] != checksum
                or checksum not in retired
            ):
                raise ValueError("original Artifact GC tombstone authority changed")
            tombstones[checksum] = value
        if set(commands) != set(known) or set(tombstones) != set(retired):
            raise ValueError("original Artifact GC mandatory command/tombstone row is missing")
        for key, (intent, receipt) in commands.items():
            if intent["content_digest"] not in tombstones:
                raise ValueError("original Artifact GC command lost its atomic tombstone")
            original = intent["original_tombstone"]
            if original is not None and (
                intent["content_digest"] not in tombstones
                or tombstones[intent["content_digest"]]["first_command_id"] != original
            ):
                raise ValueError("original Artifact GC predecessor tombstone changed")
            if original is None and tombstones[intent["content_digest"]]["first_command_id"] != key:
                raise ValueError("original Artifact GC first command changed its tombstone")
            if receipt is None:
                continue
            if intent["content_digest"] not in tombstones or (
                set(receipt)
                != {
                    "schema",
                    "command_id",
                    "intent_digest",
                    "tombstone_digest",
                    "result",
                    "completed_ns",
                    "deleted_inode",
                }
                or receipt["schema"] != "roboguide.artifact-gc-receipt/v0.1"
                or receipt["command_id"] != key
                or receipt["intent_digest"] != digest(canonical(intent))
                or receipt["tombstone_digest"]
                != digest(canonical(tombstones[intent["content_digest"]]))
                or receipt["result"] not in {"deleted", "already_missing"}
                or not _integer(receipt["completed_ns"])
                or not intent["kernel"]["admitted_ns"]
                <= receipt["completed_ns"]
                < intent["kernel"]["deadline_ns"]
                or receipt["deleted_inode"]
                != (intent["content_inode"] if receipt["result"] == "deleted" else None)
                or (receipt["result"] == "deleted" and intent["content_inode"] is None)
            ):
                raise ValueError("original Artifact GC receipt lacks exact original effect proof")
        return commands, tombstones
