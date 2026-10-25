"""Independently verify original online Archive journals, parent fences and exact package bytes."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any

from .snapshot_references import OperationBudget, canonical, digest

PROFILE = "roboguide.recovery-profile/v0.30"
STORAGE = "roboguide.archive-operations/v0.3"
RECOVERY = "roboguide.application-recovery/v0.2"
ENVELOPE = "roboguide.application-commit/v4"
MAX_BYTES = 128 * 1024 * 1024
SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")
OWNER = re.compile(r"artifact-store:[0-9a-f]{64}\Z")
AUTHORITY = re.compile(r"controller-store:[0-9a-f]{64}\Z")


def _closed(value: Any, fields: set[str]) -> dict[str, Any]:
    """Reject hidden or absent original evidence rather than normalizing it into permission."""
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("coordinated Archive record is not closed")
    return value


def _uint(value: Any) -> bool:
    """Keep booleans and overflow out of original sequence, length and clock identities."""
    return type(value) is int and 0 <= value <= 2**64 - 1


def _text(value: Any, bound: int) -> bool:
    """Retain bounded actual subjects and command IDs without accepting invisible control text."""
    return (
        isinstance(value, str)
        and 0 < len(value.encode()) <= bound
        and not any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)
    )


def content_key(value: Any) -> dict[str, Any]:
    """Validate qualified bytes without borrowing another owner's identity."""
    key = _closed(value, {"source_owner", "content_digest", "byte_size"})
    if (
        not isinstance(key["source_owner"], str)
        or OWNER.fullmatch(key["source_owner"]) is None
        or not isinstance(key["content_digest"], str)
        or SHA.fullmatch(key["content_digest"]) is None
        or not _uint(key["byte_size"])
        or key["byte_size"] > 4 * 1024**3
    ):
        raise ValueError("coordinated Archive original qualified content differs")
    return key


def record(value: Any) -> dict[str, Any]:
    """Check the original first clock and independent outcome without reading the current clock."""
    row = _closed(value, {"intent", "completed_ms", "abort"})
    intent = _closed(
        row["intent"],
        {
            "schema",
            "request",
            "authority_id",
            "issuing_epoch",
            "previous_floor",
            "manifest",
            "package",
            "catalog_digest",
            "recovery_digest",
            "boot_id",
            "time_namespace",
            "issued_ms",
            "deadline_ms",
        },
    )
    request = _closed(
        intent["request"],
        {"command_id", "principal", "source_owner", "end_sequence", "budget_ms"},
    )
    manifest = _closed(
        intent["manifest"],
        {
            "schema",
            "start_sequence",
            "end_sequence",
            "event_count",
            "segment_digest",
            "checkpoint_sequence",
            "checkpoint_digest",
        },
    )
    key = content_key(intent["package"])
    numbers = [
        intent["previous_floor"],
        intent["issued_ms"],
        intent["deadline_ms"],
        request["end_sequence"],
        request["budget_ms"],
        manifest["start_sequence"],
        manifest["end_sequence"],
        manifest["event_count"],
        manifest["checkpoint_sequence"],
    ]
    if (
        intent["schema"] != "roboguide.coordinated-archive-intent/v0.1"
        or not all(_uint(value) for value in numbers)
        or not _text(request["command_id"], 128)
        or not _text(request["principal"], 256)
        or not _text(intent["issuing_epoch"], 512)
        or not isinstance(intent["authority_id"], str)
        or AUTHORITY.fullmatch(intent["authority_id"]) is None
        or request["source_owner"] != key["source_owner"]
        or not 100 <= request["budget_ms"] <= 300_000
        or intent["deadline_ms"] - intent["issued_ms"] != request["budget_ms"]
        or not isinstance(intent["boot_id"], str)
        or len(intent["boot_id"]) != 36
        or not _text(intent["time_namespace"], 128)
        or manifest["schema"] != "roboguide.event-archive/v2"
        or manifest["start_sequence"] != intent["previous_floor"] + 1
        or request["end_sequence"] != manifest["end_sequence"]
        or not 1 <= manifest["event_count"] <= 8192
        or manifest["end_sequence"] - manifest["start_sequence"] + 1 != manifest["event_count"]
        or manifest["checkpoint_sequence"] < manifest["end_sequence"]
        or not isinstance(manifest["checkpoint_digest"], str)
        or re.fullmatch(r"[0-9a-f]{64}", manifest["checkpoint_digest"]) is None
        or any(
            not isinstance(value, str) or SHA.fullmatch(value) is None
            for value in (
                manifest["segment_digest"],
                intent["catalog_digest"],
                intent["recovery_digest"],
            )
        )
        or key["byte_size"] > MAX_BYTES
    ):
        raise ValueError("coordinated Archive original identity, prefix or budget differs")
    completed = row["completed_ms"]
    if completed is not None and (
        not _uint(completed)
        or not intent["issued_ms"] <= completed < intent["deadline_ms"]
        or row["abort"] is not None
    ):
        raise ValueError("coordinated Archive original completion budget differs")
    if row["abort"] is not None:
        abort = _closed(row["abort"], {"principal", "issuing_epoch"})
        if not _text(abort["principal"], 256) or not _text(abort["issuing_epoch"], 512):
            raise ValueError("coordinated Archive explicit abandonment lacks its actual subject")
    return row


def parent_base(connection: sqlite3.Connection, profile_schema: str | None = None) -> int:
    """Verify the exact outer parent11 fence and expose only its retained base interpretation."""
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    names = {
        value[0]
        for value in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    metadata = (
        dict(connection.execute("SELECT key,value FROM replica_v1_meta"))
        if "replica_v1_meta" in names
        else {}
    )
    keys = {"coordinated_archive_parent_base", "coordinated_archive_contract"}
    if version != 11:
        if keys & metadata.keys():
            raise ValueError("old Replica parent contains hidden coordinated Archive authority")
        return version
    if profile_schema is not None and profile_schema != PROFILE:
        raise ValueError("coordinated Archive parent11 requires explicit recovery v0.30")
    if not keys <= metadata.keys() or connection.execute(
        "SELECT version,schema FROM replica_storage_format"
    ).fetchall() != [(11, "roboguide.replica-storage/v0.11")]:
        raise ValueError("coordinated Archive original parent marker is absent or differs")
    base = json.loads(metadata["coordinated_archive_parent_base"])
    if (
        type(base) is not int
        or base not in {8, 9, 10}
        or json.loads(metadata["coordinated_archive_contract"])
        != "roboguide.coordinated-content-archive/v0.1"
    ):
        raise ValueError("coordinated Archive original parent base/contract differs")
    return base


def operations(
    connection: sqlite3.Connection, profile_schema: str, budget: OperationBudget | None = None
) -> list[dict[str, Any]]:
    """Read the mandatory entire bounded original journal before backup, verify or source GC."""
    names = {
        value[0]
        for value in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    marker = None
    if "event_log_metadata" in names:
        row = connection.execute(
            "SELECT value FROM event_log_metadata WHERE key='archive_operations_schema'"
        ).fetchone()
        marker = None if row is None else row[0]
    present = "coordinated_archive_operations" in names
    if marker != STORAGE:
        if present:
            raise ValueError("coordinated Archive table lacks its original generation")
        return []
    if profile_schema != PROFILE or not present:
        raise ValueError("mandatory coordinated Archive journal requires explicit recovery v0.30")
    if [
        row[1] for row in connection.execute("PRAGMA table_info(coordinated_archive_operations)")
    ] != ["command_id", "record_json"]:
        raise ValueError("coordinated Archive original journal columns differ")
    authority = connection.execute(
        "SELECT value FROM event_log_metadata WHERE key='archive_authority_id'"
    ).fetchone()
    rows: list[dict[str, Any]] = []
    total = 0
    for key, raw in connection.execute(
        "SELECT command_id,record_json FROM coordinated_archive_operations ORDER BY command_id"
    ):
        if budget is not None:
            budget.check()
        total += len(raw.encode())
        if len(rows) >= 256 or total > MAX_BYTES:
            raise ValueError("coordinated Archive retained journal exceeds original bounds")
        value = record(json.loads(raw))
        if key != value["intent"]["request"]["command_id"] or authority != (
            value["intent"]["authority_id"],
        ):
            raise ValueError("coordinated Archive original row or authority differs")
        if value["completed_ms"] is not None:
            manifest = value["intent"]["manifest"]
            original = connection.execute(
                "SELECT manifest_json FROM event_archive_manifests "
                "WHERE start_sequence=?1 AND end_sequence=?2",
                (manifest["start_sequence"], manifest["end_sequence"]),
            ).fetchone()
            if original is None or json.loads(original[0]) != manifest:
                raise ValueError("coordinated Archive original completion lost its manifest")
        rows.append(value)
    return rows


def validate_projection(recovery: dict[str, Any]) -> list[dict[str, Any]]:
    """Require a complete original v0.2 journal beside replicated current metadata."""
    rows = recovery.get("coordinated_archives")
    if rows is None:
        if (
            recovery.get("schema") == RECOVERY
            or recovery.get("metadata", {}).get("archive_operations_schema") == STORAGE
        ):
            raise ValueError("replicated coordinated Archive journal is missing")
        return []
    if (
        recovery.get("schema") != RECOVERY
        or recovery.get("metadata", {}).get("archive_operations_schema") != STORAGE
        or not isinstance(rows, list)
        or len(rows) > 256
    ):
        raise ValueError("replicated coordinated Archive generation differs")
    result = [record(row) for row in rows]
    ids = [row["intent"]["request"]["command_id"] for row in result]
    if ids != sorted(set(ids)):
        raise ValueError("replicated coordinated Archive original IDs conflict")
    for row in result:
        if row["intent"]["authority_id"] != recovery["metadata"]["archive_authority_id"]:
            raise ValueError("replicated coordinated Archive original authority differs")
        if row["completed_ms"] is not None and not any(
            json.loads(manifest["manifest_json"]) == row["intent"]["manifest"]
            for manifest in recovery["archive_manifests"] or []
        ):
            raise ValueError("replicated original Archive completion lacks manifest coverage")
    return result


def validate_package(raw: bytes, row: dict[str, Any]) -> None:
    """Verify actual CAS bytes against original prefix, checkpoint and Catalog."""
    from .archive_references import _checkpoint_identity

    intent = row["intent"]
    key = content_key(intent["package"])
    if len(raw) != key["byte_size"] or len(raw) > MAX_BYTES or digest(raw) != key["content_digest"]:
        raise ValueError("coordinated Archive actual package bytes differ")
    package = _closed(
        json.loads(raw),
        {
            "schema",
            "authority_id",
            "manifest",
            "checkpoint",
            "events",
            "catalog",
            "recovery_digest",
            "original_inputs",
        },
    )
    manifest = intent["manifest"]
    if (
        package["schema"] != "roboguide.coordinated-archive-package/v0.1"
        or package["authority_id"] != intent["authority_id"]
        or package["manifest"] != manifest
        or package["recovery_digest"] != intent["recovery_digest"]
        or _checkpoint_identity(package["checkpoint"]) != manifest["checkpoint_digest"]
        or package["checkpoint"]["event_sequence"] != manifest["checkpoint_sequence"]
        or digest(canonical(package["catalog"])) != intent["catalog_digest"]
        or not isinstance(package["events"], list)
        or len(package["events"]) != manifest["event_count"]
        or [value["sequence"] for value in package["events"]]
        != list(range(manifest["start_sequence"], manifest["end_sequence"] + 1))
        or not isinstance(package["original_inputs"], list)
        or len(package["original_inputs"]) > 16_384
    ):
        raise ValueError("coordinated Archive original complete package evidence differs")
    # Rust's original segment is typed struct JSON, whose field order is deliberately retained here.
    fields = (
        "sequence",
        "event_id",
        "timestamp_ms",
        "correlation_id",
        "causation_id",
        "payload_schema",
        "payload_json",
    )
    segment = b"".join(
        json.dumps(
            {name: value[name] for name in fields},
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        + b"\n"
        for value in package["events"]
    )
    if digest(segment) != manifest["segment_digest"]:
        raise ValueError("coordinated Archive original event segment bytes differ")
    keys = [content_key(value) for value in package["original_inputs"]]
    identities = [(key["source_owner"], key["content_digest"], key["byte_size"]) for key in keys]
    if identities != sorted(set(identities)):
        raise ValueError("coordinated Archive original qualified requirements conflict")
    if hashlib.sha256(raw).hexdigest() != key["content_digest"][7:]:
        raise ValueError("coordinated Archive complete package identity changed")


def snapshot_publication(
    raw: bytes,
    meta: dict[str, Any],
    profile: str,
    parent_version: int,
    identity: dict[str, Any],
    tables: dict[str, dict[str, Any]],
) -> None:
    """Validate actual source-publication lineage without importing source-local authority."""
    body = json.loads(raw)
    publication = body.get("source_publication")
    if publication is None:
        return
    row = _closed(
        publication, {"schema", "source_member", "source_store_owner", "producer_operation_id"}
    )
    operation = row["producer_operation_id"]
    owner = row["source_store_owner"]
    member = row["source_member"]
    if (
        profile != PROFILE
        or parent_version != 11
        or row["schema"] != "roboguide.snapshot-publication/v0.1"
        or not _uint(member)
        or member == 0
        or not isinstance(owner, str)
        or re.fullmatch(r"replica-content:[0-9a-f]{64}", owner) is None
        or not isinstance(operation, str)
        or re.fullmatch(r"producer:[0-9a-f]{64}", operation) is None
        or body["membership"]["membership"]["nodes"].get(str(member), {}).get("content_store_owner")
        != owner
    ):
        raise ValueError("Snapshot source publication differs from its original explicit scope")
    if owner == identity["store_owner"]:
        original = tables["content_producers"].get(hashlib.sha256(canonical(operation)).hexdigest())
        actual = hashlib.sha256(raw).hexdigest()
        parent = hashlib.sha256(
            json.dumps([meta, actual], ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        if (
            original is None
            or original["intent"]["operation_id"] != operation
            or original["intent"]["source"]["kind"] != "snapshot-build"
            or original["intent"]["source"]["authority_digest"] != actual
            or original["intent"]["source"]["parent_record_digest"] != parent
        ):
            raise ValueError("Snapshot source publication lost its original local producer")
