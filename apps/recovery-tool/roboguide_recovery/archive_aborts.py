"""Verify original unpublished Archive cleanup without generating copier or physical-stop facts."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .snapshot_references import OperationBudget, canonical, digest

SCHEMA = "roboguide.recovery-profile/v0.13"
WRAPPER = "roboguide.controller-checkpoint/v50"
MARKER = "roboguide.archive-operations/v0.2"
CHECKSUM = re.compile(r"sha256:[0-9a-f]{64}\Z")
MAX_RECORD = 16 * 1024


def _text(value: Any, maximum: int) -> bool:
    """Validate a bounded original software identifier without granting permission."""
    return (
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and bool(value.strip())
        and not any(ord(character) < 32 for character in value)
    )


def _intent(value: Any, original: dict[str, Any], command: str) -> dict[str, Any]:
    """Retain first cleanup attribution beside the unchanged original copy intent."""
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schema",
            "request",
            "source_owner",
            "authority_id",
            "destination",
            "boot_id",
            "time_namespace",
            "issued_ms",
            "deadline_ms",
        }
        or value["schema"] != "roboguide.archive-abort-intent/v0.1"
    ):
        raise ValueError("original Archive cleanup intent is not closed/versioned")
    request = value["request"]
    source = original["request"]
    if (
        not isinstance(request, dict)
        or set(request)
        != {
            "command_id",
            "original_command_id",
            "original_intent_digest",
            "principal",
            "tenant",
            "budget_ms",
        }
        or request["command_id"] != command
        or not _text(command, 128)
        or request["command_id"] == request["original_command_id"]
        or request["original_command_id"] != source["command_id"]
        or request["original_intent_digest"] != digest(canonical(original))
        or not _text(request["principal"], 256)
        or request["tenant"] != source["tenant"]
        or type(request["budget_ms"]) is not int
        or not 0 < request["budget_ms"] <= 30_000
        or any(
            value[key] != source[source_key]
            for key, source_key in (
                ("source_owner", "source_owner"),
                ("authority_id", "authority_id"),
                ("destination", "destination"),
            )
        )
        or not _text(value["boot_id"], 36)
        or len(value["boot_id"]) != 36
        or not _text(value["time_namespace"], 128)
        or any(
            type(value[key]) is not int or not 0 <= value[key] < 2**64
            for key in ("issued_ms", "deadline_ms")
        )
        or value["deadline_ms"] - value["issued_ms"] != request["budget_ms"]
    ):
        raise ValueError("original Archive cleanup attribution/input/kernel budget changed")
    return dict(value)


def receipt(value: Any, intent: dict[str, Any]) -> dict[str, Any]:
    """Validate original fence history without asserting current lease ownership."""
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "abort_intent_digest", "tombstone_digest", "fence"}
        or value["schema"] != "roboguide.archive-abort-receipt/v0.1"
        or value["abort_intent_digest"] != digest(canonical(intent))
        or not isinstance(value["tombstone_digest"], str)
        or CHECKSUM.fullmatch(value["tombstone_digest"]) is None
    ):
        raise ValueError("original Archive cleanup receipt identity changed")
    fence = value["fence"]
    if (
        not isinstance(fence, dict)
        or set(fence)
        != {
            "schema",
            "source_owner",
            "source_writer",
            "archive_writer",
            "process_id",
            "process_start_ticks",
            "boot_id",
            "time_namespace",
            "observed_ms",
        }
        or fence["schema"] != "roboguide.original-local-archive-copier-fence/v0.1"
        or fence["source_owner"] != intent["source_owner"]
        or fence["boot_id"] != intent["boot_id"]
        or fence["time_namespace"] != intent["time_namespace"]
        or type(fence["observed_ms"]) is not int
        or not intent["issued_ms"] <= fence["observed_ms"]
        or fence["observed_ms"] + 10 >= intent["deadline_ms"]
        or type(fence["process_id"]) is not int
        or not 0 < fence["process_id"] < 2**32
        or type(fence["process_start_ticks"]) is not int
        or not 0 < fence["process_start_ticks"] < 2**64
    ):
        raise ValueError(
            "original Archive cleanup lacks exact original process/kernel fence evidence"
        )
    for key in ("source_writer", "archive_writer"):
        writer = fence[key]
        if (
            not isinstance(writer, dict)
            or set(writer) != {"device", "inode"}
            or any(
                type(writer[name]) is not int or not 0 <= writer[name] < 2**64
                for name in ("device", "inode")
            )
            or writer["inode"] == 0
        ):
            raise ValueError("original Archive cleanup writer descriptor identity is invalid")
    return dict(value)


def operations(
    connection: sqlite3.Connection,
    profile_schema: str,
    budget: OperationBudget,
    originals: list[tuple[dict[str, Any], str, str | None]],
    *,
    current: bool,
) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    """Preserve exact cleanup authority and Unknown beside copy history."""
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    marker = (
        connection.execute(
            "SELECT value FROM event_log_metadata WHERE key='archive_operations_schema'"
        ).fetchone()
        if "event_log_metadata" in tables
        else None
    )
    new = marker == (MARKER,)
    if current and (
        not new
        or profile_schema
        not in {
            SCHEMA,
            "roboguide.recovery-profile/v0.14",
            "roboguide.recovery-profile/v0.15",
            "roboguide.recovery-profile/v0.16",
            "roboguide.recovery-profile/v0.17",
            "roboguide.recovery-profile/v0.18",
            "roboguide.recovery-profile/v0.19",
            "roboguide.recovery-profile/v0.20",
            "roboguide.recovery-profile/v0.21",
            "roboguide.recovery-profile/v0.22",
            "roboguide.recovery-profile/v0.23",
            "roboguide.recovery-profile/v0.24",
            "roboguide.recovery-profile/v0.25",
            "roboguide.recovery-profile/v0.26",
        }
        or "event_archive_aborts" not in tables
    ):
        raise ValueError("original Archive cleanup requires wrapper v50 and recovery v0.13")
    if not new:
        if "event_archive_aborts" in tables:
            raise ValueError("original Archive cleanup journal is hidden under an old marker")
        return []
    if (
        profile_schema
        not in {
            SCHEMA,
            "roboguide.recovery-profile/v0.14",
            "roboguide.recovery-profile/v0.15",
            "roboguide.recovery-profile/v0.16",
            "roboguide.recovery-profile/v0.17",
            "roboguide.recovery-profile/v0.18",
            "roboguide.recovery-profile/v0.19",
            "roboguide.recovery-profile/v0.20",
            "roboguide.recovery-profile/v0.21",
            "roboguide.recovery-profile/v0.22",
            "roboguide.recovery-profile/v0.23",
            "roboguide.recovery-profile/v0.24",
            "roboguide.recovery-profile/v0.25",
            "roboguide.recovery-profile/v0.26",
        }
        or not current
        or "event_archive_aborts" not in tables
    ):
        raise ValueError("original Archive cleanup authority cannot be omitted or downgraded")
    by_id = {
        original["request"]["command_id"]: (original, state, published)
        for original, state, published in originals
    }
    result: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    total = 0
    completed: set[str] = set()
    for command, source, raw, proof in connection.execute(
        "SELECT command_id,original_command_id,intent_json,receipt_json FROM event_archive_aborts "
        "ORDER BY command_id"
    ):
        budget.check()
        total += len(raw.encode()) + (0 if proof is None else len(proof.encode()))
        if (
            len(result) >= 4096
            or len(raw.encode()) > MAX_RECORD
            or (proof is not None and len(proof.encode()) > MAX_RECORD)
            or total > 64 * 1024 * 1024
        ):
            raise ValueError("original Archive cleanup history exceeds bounded evidence")
        original = by_id.get(source)
        if original is None or original[1] != "pending" or original[2] is not None:
            raise ValueError("original Archive cleanup lost its unpublished copy intent")
        intent = _intent(json.loads(raw), original[0], command)
        if intent["request"]["original_command_id"] != source:
            raise ValueError("original Archive cleanup source row key changed")
        retained = None if proof is None else receipt(json.loads(proof), intent)
        if retained is not None:
            if source in completed:
                raise ValueError("original Archive pin has conflicting complete cleanup commands")
            completed.add(source)
        result.append((intent, retained))
    if (
        sum(
            state == "pending" and original["request"]["command_id"] not in completed
            for original, state, _ in originals
        )
        > 1
    ):
        raise ValueError("original Archive pending ownership overlaps")
    return result


def validate_tombstone(
    root: Path,
    prefix: str,
    intent: dict[str, Any],
    retained: dict[str, Any] | None,
    files: dict[str, dict[str, Any]],
    budget: OperationBudget,
) -> bool:
    """Check exact tombstones; only a source SQL receipt retires its pin."""
    name = prefix + "/abort-" + hashlib.sha256(intent["request"]["command_id"].encode()).hexdigest()
    final, pending = name + ".json", name + ".pending"
    selected = final if final in files else pending if pending in files else None
    if selected is None:
        if retained is not None:
            raise ValueError("original Archive cleanup receipt lost its durable tombstone")
        return False
    budget.check()
    record = files[selected]
    if record["kind"] != "content" or record["byte_size"] > MAX_RECORD:
        raise ValueError("original Archive cleanup tombstone exceeds bounded regular bytes")
    raw = (root / selected).read_bytes()
    value = json.loads(raw)
    if (
        len(raw) > MAX_RECORD
        or not isinstance(value, dict)
        or set(value) != {"schema", "intent", "fence"}
        or value["schema"] != "roboguide.archive-abort-tombstone/v0.1"
        or value["intent"] != intent
        or canonical(value) != raw
        or digest(raw) != record["content_digest"]
    ):
        raise ValueError("original Archive cleanup tombstone changed")
    original_receipt = receipt(
        {
            "schema": "roboguide.archive-abort-receipt/v0.1",
            "abort_intent_digest": digest(canonical(intent)),
            "tombstone_digest": digest(raw),
            "fence": value["fence"],
        },
        intent,
    )
    if retained is not None and retained != original_receipt:
        raise ValueError("original Archive cleanup SQL receipt differs from its tombstone")
    if prefix + "/publication.json" in files:
        raise ValueError("original Archive has both final publication and abort tombstone")
    return retained is not None
