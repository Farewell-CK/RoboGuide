"""Verify lossless Controller value reconstruction and bounded original Catalog coverage."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .archive_references import PROTECTED, catalog_history

SCHEMA = "roboguide.catalog-recovery-checkpoint/v0.1"
FIELDS = (
    "sequence",
    "event_id",
    "timestamp_ms",
    "correlation_id",
    "causation_id",
    "payload_schema",
    "payload_json",
)
VIEW = (
    "CREATE VIEW events AS SELECT "
    "e.sequence,e.event_id,e.timestamp_ms,e.correlation_id,e.causation_id,e.payload_schema"
    ",CASE WHEN e.body_id IS NULL THEN rg_verified_event(e.payload_json,e.payload_digest) "
    "ELSE rg_verified_event(e.payload_prefix || "
    "rg_verified_body(e.body_id,b.scope_hash,b.value_digest,b.byte_size,b.value_json) || "
    "e.payload_suffix,e.payload_digest) END AS payload_json FROM controller_v3_events e LEFT "
    "JOIN telemetry_bodies b ON e.body_id=b.body_id"
)


def digest(value: str) -> str:
    """Hash original UTF-8 bytes without normalizing JSON text or floating point encodings."""
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _body(identity: Any, scope: Any, expected: Any, size: Any, value: Any) -> str:
    """Refuse absent, cross-digest or altered immutable bodies before returning original bytes."""
    if (
        not all(isinstance(item, str) for item in (identity, scope, expected, value))
        or type(size) is not int
        or size < 0
        or len(value.encode()) != size
        or digest(value) != expected
        or digest(scope + "\n" + expected) != identity
    ):
        raise ValueError("required original telemetry body is missing or corrupt")
    return str(value)


def _event(value: Any, expected: Any) -> str:
    """Check complete reconstructed bytes, retaining clocks and native source identity."""
    if not isinstance(value, str) or not isinstance(expected, str) or digest(value) != expected:
        raise ValueError("original event reconstruction differs")
    return value


def install_functions(connection: sqlite3.Connection) -> None:
    """Install the same deterministic verification functions on each actual readonly connection."""
    connection.create_function("rg_verified_body", 5, _body, deterministic=True)
    connection.create_function("rg_verified_event", 2, _event, deterministic=True)


def validate_bodies(connection: sqlite3.Connection) -> None:
    """Stream every immutable body and observation reference during cold recovery and backup."""
    install_functions(connection)
    try:
        for row in connection.execute(
            "SELECT body_id,scope_hash,value_digest,byte_size,value_json FROM telemetry_bodies"
        ):
            _body(*row)
        for (value,) in connection.execute("SELECT payload_json FROM events ORDER BY sequence"):
            if not isinstance(value, str):
                raise ValueError("original reconstructed event is missing")
        for identity, kind, original in connection.execute(
            "SELECT c.command_id,c.kind,j.command_id FROM command_journal_classes c "
            "LEFT JOIN control_command_journal j ON j.command_id=c.command_id"
        ):
            if original != identity or kind not in {"external", "node-facts", "timer"}:
                raise ValueError("typed journal classification lacks its original command")
    except sqlite3.DatabaseError as error:
        raise ValueError("Controller v3 original body or event verification failed") from error


def _same_json(left: Any, right: Any) -> bool:
    """Compare complete native facts without equating booleans with numeric measurements."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return set(left) == set(right) and all(
            _same_json(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_json(a, b) for a, b in zip(left, right, strict=True)
        )
    return bool(left == right)


def _chain(previous: str, value: Any) -> str:
    """Reproduce the original length-delimited metadata chain without floating point values."""
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    return (
        "sha256:"
        + hashlib.sha256(previous.encode() + len(raw).to_bytes(8, "little") + raw).hexdigest()
    )


def _event_record(
    connection: sqlite3.Connection, sequence: int, kind: str, archived: Any = None
) -> Any:
    """Resolve the actual original event through verified body reconstruction."""
    source = connection.execute(
        "SELECT payload_json FROM events WHERE sequence=?", (sequence,)
    ).fetchone()
    if source is None:
        floor = _metadata_uint(connection, "history_floor", 0) or 0
        if archived is not None and 0 < sequence <= floor:
            return archived(sequence, kind)
        raise ValueError("retention reference requires missing original event or archive proof")
    value = json.loads(source[0])
    if not isinstance(value, dict) or set(value) != {kind} or not isinstance(value[kind], dict):
        raise ValueError("retention reference original event kind differs")
    return value[kind]


def validate_references(
    connection: sqlite3.Connection, archive_roots: Mapping[str, Path] | None = None
) -> None:
    """Preserve original dependencies and unresolved history without duplicating values."""
    install_functions(connection)
    from .reference_archives import ReferenceArchiveResolver

    archived = ReferenceArchiveResolver(connection, archive_roots)
    heads = connection.execute(
        "SELECT singleton,through_sequence,records_count,edges_count,gaps_count,edges_digest,"
        "gaps_digest,archived_prefix_unresolved,head_digest FROM retention_reference_head"
    ).fetchall()
    if len(heads) != 1 or heads[0][0] != 1:
        raise ValueError("original retention reference coverage is missing or duplicated")
    _, through, records, edges, gaps, edge_hash, gap_hash, incomplete, checksum = heads[0]
    if (
        not all(_uint(value) for value in (through, records, edges, gaps))
        or type(incomplete) is not int
        or incomplete not in {0, 1}
    ):
        raise ValueError("original retention reference counters are invalid")
    head = [through, records, edges, gaps, edge_hash, gap_hash, bool(incomplete)]
    if digest(json.dumps(head, ensure_ascii=False, separators=(",", ":"))) != checksum:
        raise ValueError("original retention reference head checksum differs")
    maximum = connection.execute("SELECT COALESCE(MAX(sequence),0) FROM events").fetchone()[0]
    if through != max(maximum, _metadata_uint(connection, "history_floor", 0) or 0):
        raise ValueError("original retention reference coverage differs")
    actual_records = 0
    for sequence, record_digest in connection.execute(
        "SELECT source_sequence,record_digest FROM retention_reference_records "
        "ORDER BY source_sequence"
    ):
        actual_records += 1
        if (
            not _uint(sequence)
            or not 0 < sequence <= through
            or not isinstance(record_digest, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", record_digest) is None
        ):
            raise ValueError("original retention source identity is invalid")
        source = _event_record(connection, sequence, "StateRecordObserved", archived)
        if not isinstance(source.get("record"), dict):
            raise ValueError("original retention source lacks its complete State record")
    edge_chain, actual_edges = digest("roboguide.retention-decision-edges/v0.1"), 0
    for decision, source, record_digest, retained_digest in connection.execute(
        "SELECT e.decision_sequence,e.source_sequence,e.record_digest,r.record_digest "
        "FROM retention_reference_edges e LEFT JOIN retention_reference_records r "
        "ON r.source_sequence=e.source_sequence ORDER BY e.decision_sequence,e.source_sequence"
    ):
        actual_edges += 1
        if (
            not _uint(decision)
            or not _uint(source)
            or not 0 < source < decision <= through
            or record_digest != retained_digest
        ):
            raise ValueError("original retention decision edge is detached from its source")
        record = _event_record(connection, source, "StateRecordObserved", archived)["record"]
        owner = _event_record(connection, decision, "StatePolicyDecision", archived)
        receipts = owner.get("receipts")
        if not isinstance(receipts, list) or not any(
            isinstance(receipt, dict) and _same_json(record, receipt.get("record"))
            for receipt in receipts
        ):
            raise ValueError(
                "retention decision edge does not name its complete original State input"
            )
        edge_chain = _chain(edge_chain, [decision, source, record_digest])
    gap_chain, actual_gaps = digest("roboguide.retention-decision-gaps/v0.1"), 0
    for decision, record_digest in connection.execute(
        "SELECT decision_sequence,record_digest FROM retention_reference_gaps "
        "ORDER BY decision_sequence,record_digest"
    ):
        actual_gaps += 1
        if (
            not _uint(decision)
            or not 0 < decision <= through
            or not isinstance(record_digest, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", record_digest) is None
        ):
            raise ValueError("original unresolved retention decision identity differs")
        if not any(
            isinstance(receipt, dict) and isinstance(receipt.get("record"), dict)
            for receipt in _event_record(connection, decision, "StatePolicyDecision", archived).get(
                "receipts", []
            )
        ):
            raise ValueError("original unresolved retention dependency has no State input")
        gap_chain = _chain(gap_chain, [decision, record_digest])
    if (actual_records, actual_edges, actual_gaps, edge_chain, gap_chain) != (
        records,
        edges,
        gaps,
        edge_hash,
        gap_hash,
    ):
        raise ValueError("original retention reference counts or ordered checksum differ")


def _policy(value: Any) -> None:
    """Keep policy interpretation and hard maintenance limits equal to the Controller contract."""
    if (
        not isinstance(value, dict)
        or set(value)
        != {"schema", "mode", "hot_window_ms", "archive_window_ms", "max_events", "max_input_bytes"}
        or value["schema"] != "roboguide.retention-policy/v0.1"
        or value["mode"] not in {"disabled", "evaluate", "enabled"}
        or not all(
            type(value[key]) is int and 0 < value[key] <= maximum
            for key, maximum in (
                ("hot_window_ms", 2**63 - 1),
                ("archive_window_ms", 2**63 - 1),
                ("max_events", 8192),
                ("max_input_bytes", 4 * 1024 * 1024),
            )
        )
    ):
        raise ValueError("original retention policy is unknown or exceeds hard bounds")


def validate_retention(connection: sqlite3.Connection) -> None:
    """Retain every accepted policy command, original receipt and persisted Controller clock."""
    rows = connection.execute(
        "SELECT singleton,revision,policy_json,policy_digest,clock_high_water_ms "
        "FROM retention_policy"
    ).fetchall()
    if len(rows) != 1 or rows[0][0] != 1:
        raise ValueError("original retention policy authority is missing or duplicated")
    _, revision, raw, expected, clock = rows[0]
    if (
        not _uint(revision)
        or revision > 2**63 - 1
        or not _uint(clock)
        or clock > 2**63 - 1
        or not isinstance(raw, str)
        or digest(raw) != expected
    ):
        raise ValueError("original retention policy checksum or clock differs")
    policy = json.loads(raw)
    _policy(policy)
    last_policy, last_digest, previous_clock, count = None, None, 0, 0
    for (
        identity,
        accepted_revision,
        command_raw,
        command_digest,
        receipt_raw,
        receipt_digest,
    ) in connection.execute(
        "SELECT command_id,revision,command_json,command_digest,receipt_json,receipt_digest "
        "FROM retention_policy_commands ORDER BY revision"
    ):
        count += 1
        if (
            not isinstance(command_raw, str)
            or not isinstance(receipt_raw, str)
            or digest(command_raw) != command_digest
            or digest(receipt_raw) != receipt_digest
        ):
            raise ValueError("original retention command or receipt checksum differs")
        command, receipt = json.loads(command_raw), json.loads(receipt_raw)
        if (
            not isinstance(command, dict)
            or set(command) != {"command_id", "expected_revision", "policy"}
            or not isinstance(identity, str)
            or not identity.strip()
            or len(identity.encode()) > 256
            or command["command_id"] != identity
            or not _uint(command["expected_revision"])
            or not isinstance(receipt, dict)
            or set(receipt)
            != {
                "schema",
                "command_id",
                "previous_revision",
                "revision",
                "accepted_at_ms",
                "policy_digest",
            }
            or receipt["schema"] != "roboguide.retention-policy-receipt/v0.1"
            or receipt["command_id"] != identity
            or not all(_uint(receipt[key]) for key in ("previous_revision", "revision"))
            or accepted_revision != count
            or command["expected_revision"] != count - 1
            or receipt["previous_revision"] != count - 1
            or receipt["revision"] != count
            or not _uint(receipt["accepted_at_ms"])
            or not previous_clock <= receipt["accepted_at_ms"] <= clock
        ):
            raise ValueError("original retention command is detached from its acceptance")
        _policy(command["policy"])
        encoded = json.dumps(command["policy"], ensure_ascii=False, separators=(",", ":"))
        if digest(encoded) != receipt["policy_digest"]:
            raise ValueError("original retention accepted policy differs from its digest")
        last_policy, last_digest = command["policy"], receipt["policy_digest"]
        previous_clock = receipt["accepted_at_ms"]
    initial = {
        "schema": "roboguide.retention-policy/v0.1",
        "mode": "evaluate",
        "hot_window_ms": 86_400_000,
        "archive_window_ms": 2_592_000_000,
        "max_events": 8192,
        "max_input_bytes": 4 * 1024 * 1024,
    }
    if (
        count != revision
        or (revision == 0 and policy != initial)
        or (revision > 0 and (policy != last_policy or expected != last_digest))
    ):
        raise ValueError("current retention policy lacks its complete original acceptance history")


class _ProjectionBudget:
    """Bound independent Catalog validation while respecting outer SQL progress deadlines."""

    def __init__(self) -> None:
        """Keep a single nonrenewable deadline for this bounded projection inspection."""
        self.deadline_ns = time.monotonic_ns() + 60 * 1_000_000_000
        self.max_files = 16_384
        self.max_bytes = 64 * 1024 * 1024

    def check(self, byte_count: int = 0, file_count: int = 0) -> None:
        """Refuse deadline exhaustion rather than silently truncating recovery evidence."""
        if (
            time.monotonic_ns() > self.deadline_ns
            or byte_count > self.max_bytes
            or file_count > self.max_files
        ):
            raise ValueError("Catalog recovery checkpoint validation budget exhausted")


def _uint(value: Any) -> bool:
    """Reject booleans, negative values and overflows in original sequence and clock fields."""
    return type(value) is int and 0 <= value < 2**64


def validate_verifier_authorization(wrapper: dict[str, Any] | None, profile: str) -> None:
    """Fence restricted verifier grants without interpreting them as ordinary operator authority."""
    if wrapper is None or not isinstance(wrapper.get("integration_json"), str):
        return
    control = json.loads(wrapper["integration_json"]).get("control", {})
    authorization = control.get("operator_authorization")
    if authorization is None:
        return
    if not isinstance(authorization, dict):
        raise ValueError("original operator authorization is malformed")
    grants = authorization.get("grants", {})
    restricted = authorization.get("schema_version") == "roboguide.operator-authorization/v0.3" or (
        isinstance(grants, dict)
        and any(
            isinstance(grant, dict) and grant.get("role") == "verifier" for grant in grants.values()
        )
    )
    if not restricted:
        return
    if (
        profile != "roboguide.recovery-profile/v0.32"
        or wrapper.get("schema") != "roboguide.controller-checkpoint/v60"
        or set(authorization) != {"schema_version", "registry_id", "revision", "grants"}
        or authorization["schema_version"] != "roboguide.operator-authorization/v0.3"
        or not isinstance(authorization["registry_id"], str)
        or not authorization["registry_id"].strip()
        or len(authorization["registry_id"].encode()) > 128
        or not _uint(authorization["revision"])
        or authorization["revision"] == 0
        or not isinstance(grants, dict)
        or not 0 < len(grants) <= 4096
    ):
        raise ValueError("restricted verifier authorization requires v60 and recovery v0.32")
    for identity, grant in grants.items():
        if (
            not isinstance(identity, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", identity) is None
            or not isinstance(grant, dict)
            or not {"principal_id", "role", "tenant_ids"} <= set(grant)
            or set(grant)
            - {"principal_id", "role", "tenant_ids", "not_before_ms", "expires_at_ms", "revoked"}
            or not isinstance(grant["principal_id"], str)
            or not grant["principal_id"].strip()
            or len(grant["principal_id"].encode()) > 128
            or grant["role"]
            not in {"viewer", "operator", "recovery-admin", "metrics-reader", "verifier"}
            or not isinstance(grant["tenant_ids"], list)
            or not 0 < len(grant["tenant_ids"]) <= 64
            or any(
                not isinstance(tenant, str) or not tenant.strip() or len(tenant.encode()) > 128
                for tenant in grant["tenant_ids"]
            )
            or not _uint(grant.get("not_before_ms", 0))
            or not _uint(grant.get("expires_at_ms", 0))
            or type(grant.get("revoked", False)) is not bool
        ):
            raise ValueError("original restricted verifier certificate grant differs")
        tenants = set(grant["tenant_ids"])
        start, end = grant.get("not_before_ms", 0), grant.get("expires_at_ms", 0)
        if (
            ("*" in tenants and grant["role"] not in {"recovery-admin", "metrics-reader"})
            or (grant["role"] == "metrics-reader" and tenants != {"*"})
            or (end != 0 and end <= start)
        ):
            raise ValueError("restricted verifier grant exceeds its original tenant or time scope")


def _metadata_uint(connection: sqlite3.Connection, key: str, default: int | None) -> int | None:
    """Read the original counter without inventing a replacement for malformed metadata."""
    row = connection.execute("SELECT value FROM event_log_metadata WHERE key=?", (key,)).fetchone()
    if row is None:
        return default
    raw = row[0]
    if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal():
        raise ValueError("Catalog recovery original counter is malformed")
    value = int(raw)
    if not _uint(value):
        raise ValueError("Catalog recovery original counter overflows")
    return value


def validate_catalog(connection: sqlite3.Connection, profile_schema: str) -> None:
    """Verify required v2/v3 coverage, chain and every protected original source envelope."""
    install_functions(connection)
    budget = _ProjectionBudget()
    heads = connection.execute("SELECT singleton,data,digest FROM catalog_recovery_head").fetchall()
    if len(heads) != 1 or heads[0][0] != 1:
        raise ValueError("Catalog recovery checkpoint head is missing or duplicated")
    _, raw, checksum = heads[0]
    if not isinstance(raw, str) or len(raw.encode()) > 4096 or digest(raw) != checksum:
        raise ValueError("Catalog recovery checkpoint head checksum differs")
    head = json.loads(raw)
    if (
        not isinstance(head, dict)
        or set(head)
        != {"schema", "through_sequence", "timestamp_high_water", "records", "bytes", "digest"}
        or head["schema"] != SCHEMA
        or not all(
            _uint(head[key])
            for key in ("through_sequence", "timestamp_high_water", "records", "bytes")
        )
        or head["records"] > budget.max_files
        or head["bytes"] > budget.max_bytes
    ):
        raise ValueError("Catalog recovery checkpoint head is invalid or unbounded")
    floor = _metadata_uint(connection, "history_floor", 0)
    maximum = connection.execute("SELECT COALESCE(MAX(sequence),0) FROM events").fetchone()[0]
    high_water = max(maximum, floor or 0)
    counter = _metadata_uint(connection, "next_event_number", None)
    if head["through_sequence"] != high_water or (counter is not None and counter != high_water):
        raise ValueError("Catalog recovery coverage differs from original history")
    archived = {row["sequence"]: row for row in catalog_history(connection, profile_schema, budget)}
    chain, count, size = digest(SCHEMA), 0, 0
    for sequence, data in connection.execute(
        "SELECT sequence,data FROM catalog_recovery_events ORDER BY sequence"
    ):
        budget.check()
        if not isinstance(data, str):
            raise ValueError("Catalog recovery protected envelope is missing")
        size += len(data.encode())
        count += 1
        budget.check(size, count)
        row = json.loads(data)
        source = connection.execute(
            "SELECT " + ",".join(FIELDS) + " FROM events WHERE sequence=?", (sequence,)
        ).fetchone()
        original = (
            dict(zip(FIELDS, source, strict=True)) if source is not None else archived.get(sequence)
        )
        if (
            not isinstance(row, dict)
            or set(row) != set(FIELDS)
            or row != original
            or row["sequence"] != sequence
            or sequence > high_water
            or not _uint(row["timestamp_ms"])
            or row["timestamp_ms"] > head["timestamp_high_water"]
        ):
            raise ValueError("Catalog recovery protected original source differs")
        payload = json.loads(row["payload_json"])
        if (
            not isinstance(payload, dict)
            or len(payload) != 1
            or next(iter(payload)) not in PROTECTED
        ):
            raise ValueError("Catalog recovery checkpoint contains unrelated evidence")
        chain = (
            "sha256:"
            + hashlib.sha256(
                chain.encode() + len(data.encode()).to_bytes(8, "little") + data.encode()
            ).hexdigest()
        )
    if (count, size, chain) != (head["records"], head["bytes"], head["digest"]):
        raise ValueError("Catalog recovery checkpoint rows or ordered checksum differ")
