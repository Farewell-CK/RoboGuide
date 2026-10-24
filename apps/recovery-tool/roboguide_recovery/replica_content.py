"""Verify complete paired parent8/Root journals and explicitly bind restored physical storage."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import Any

from .snapshot_references import OperationBudget, canonical

PROFILE = "roboguide.recovery-profile/v0.19"
SNAPSHOT_PROFILE = "roboguide.recovery-profile/v0.20"
GC_PROFILE = "roboguide.recovery-profile/v0.21"
STAGING_PROFILE = "roboguide.recovery-profile/v0.22"
INITIALIZATION_PROFILE = "roboguide.recovery-profile/v0.23"
PROFILES = frozenset(
    {PROFILE, SNAPSHOT_PROFILE, GC_PROFILE, STAGING_PROFILE, INITIALIZATION_PROFILE}
)
LAYOUT4 = "roboguide.replica-content-layout/v0.4"
LAYOUT5 = "roboguide.replica-content-layout/v0.5"
LAYOUT6 = "roboguide.replica-content-layout/v0.6"
LAYOUT7 = "roboguide.replica-content-layout/v0.7"
LAYOUT8 = "roboguide.replica-content-layout/v0.8"
BINDING_FILE = ".restore-binding.json"
HEADS = {
    "reference_ids": ("content_references", "reference_id"),
    "operation_ids": ("content_operations", "operation_id"),
    "content_keys": ("content_objects", "content_key"),
    "reader_ids": ("content_readers", "reader_id"),
    "producer_ids": ("content_producers", "operation_id"),
    "stream_ids": ("content_streams", "stream_id"),
}
HEX = re.compile(r"[0-9a-f]{64}\Z")
SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _closed(value: Any, fields: set[str]) -> dict[str, Any]:
    """Reject incomplete or hidden authority fields without normalizing original evidence."""
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Replica content original record is not closed")
    return value


def _raw(path: Path, maximum: int = 16 * 1024) -> str:
    """Read one bounded actual regular leaf without following a symlink."""
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
            raise ValueError("Replica content metadata is not a bounded regular file")
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("Replica content metadata grew beyond its original bound")
    return raw.decode("utf-8")


def _digest(value: object) -> str:
    """Match original Rust canonical object ordering without changing original evidence."""
    return hashlib.sha256(canonical(value)).hexdigest()


def _sha(raw: str) -> str:
    """Retain the exact raw immutable binding identity across Python and Rust."""
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def metadata_files(component: dict[str, Any]) -> list[str]:
    """Select original identity fences and the explicit current backing head."""
    result = [".content-creation.json", ".retention-owner.json", "blobs"]
    if component["storage_schema"] == LAYOUT5 or (
        component["storage_schema"] in {LAYOUT6, LAYOUT7, LAYOUT8}
        and component.get("restore_binding_digest") is not None
    ):
        result.append(BINDING_FILE)
    return result


def validate_pairing(components: list[dict[str, Any]]) -> None:
    """Require one complete Root per declared parent, with no inferred or repeated member roots."""
    parents = {item["component_id"] for item in components if item["kind"] == "replica"}
    paired: set[str] = set()
    for item in components:
        if item["kind"] != "replica-content":
            continue
        if (
            item["database"] is not None
            or len(item["depends_on"]) != 1
            or item["depends_on"][0] not in parents
            or item["depends_on"][0] in paired
        ):
            raise ValueError("Replica content requires one exact independent parent dependency")
        paired.add(item["depends_on"][0])
    if not parents or paired != parents:
        raise ValueError("complete parent8 recovery requires every original paired Root")
    by_id = {item["component_id"]: item for item in components}
    controllers: set[str] = set()
    for parent in parents:
        selected = [by_id[key] for key in by_id[parent]["depends_on"] if key in by_id]
        declared = [item["component_id"] for item in selected if item["kind"] == "controller"]
        if (
            len(declared) > 1
            or (declared and declared[0] in controllers)
            or len([item for item in selected if item["kind"] == "artifact"]) != len(declared)
        ):
            raise ValueError(
                "parent8 requires its exact original Controller and Artifact components"
            )
        if declared:
            controllers.add(declared[0])
    if controllers != {item["component_id"] for item in components if item["kind"] == "controller"}:
        raise ValueError("recovery v0.19 requires complete paired Controller projection coverage")


def _identity(value: Any) -> dict[str, Any]:
    """Validate original software provenance separately from current backing."""
    value = _closed(
        value,
        {
            "schema",
            "profile_identity",
            "member",
            "store_owner",
            "root_device",
            "root_inode",
            "database_device",
            "database_inode",
        },
    )
    if (
        value["schema"] != "roboguide.replica-content-storage/v0.1"
        or not isinstance(value["profile_identity"], str)
        or HEX.fullmatch(value["profile_identity"]) is None
        or not isinstance(value["store_owner"], str)
        or re.fullmatch(r"replica-content:[0-9a-f]{64}", value["store_owner"]) is None
        or any(
            type(value[key]) is not int or not 0 <= value[key] < 2**64
            for key in (
                "member",
                "root_device",
                "root_inode",
                "database_device",
                "database_inode",
            )
        )
        or any(value[key] == 0 for key in ("member", "root_inode", "database_inode"))
    ):
        raise ValueError("Replica content original software identity differs")
    return dict(value)


def _content(value: Any) -> tuple[str, str, int]:
    """Keep the complete Artifact source owner, digest and size independently of Root ownership."""
    value = _closed(value, {"source_owner", "content_digest", "byte_size"})
    if (
        not isinstance(value["source_owner"], str)
        or re.fullmatch(r"artifact-store:[0-9a-f]{64}", value["source_owner"]) is None
        or not isinstance(value["content_digest"], str)
        or SHA.fullmatch(value["content_digest"]) is None
        or type(value["byte_size"]) is not int
        or not 0 <= value["byte_size"] <= 1024**3
    ):
        raise ValueError("Replica content qualified byte identity differs")
    return value["source_owner"], value["content_digest"], value["byte_size"]


def _budget(value: Any) -> None:
    """Check original kernel/process budgets without sampling or renewing them."""
    value = _closed(
        value,
        {
            "boot_id",
            "namespace",
            "pid_namespace",
            "admitted_ms",
            "duration_ms",
            "pid",
            "start_ticks",
        },
    )
    if (
        not isinstance(value["boot_id"], str)
        or re.fullmatch(r"[0-9a-fA-F-]{36}", value["boot_id"]) is None
        or any(
            type(value[key]) is not int or not 0 <= value[key] < 2**64
            for key in (
                "admitted_ms",
                "duration_ms",
                "pid",
                "start_ticks",
            )
        )
        or not 100 <= value["duration_ms"] <= 300000
        or value["admitted_ms"] + value["duration_ms"] >= 2**64
        or not 0 < value["pid"] < 2**32
        or value["start_ticks"] == 0
        or any(
            not isinstance(value[key], list)
            or len(value[key]) != 2
            or any(type(part) is not int or not 0 <= part < 2**64 for part in value[key])
            for key in ("namespace", "pid_namespace")
        )
        or value["namespace"][1] == 0
        or value["pid_namespace"][1] == 0
    ):
        raise ValueError("Replica content original process/kernel budget differs")


def _process_end(original: dict[str, Any], raw: Any) -> dict[str, Any]:
    """Validate exact retained negative process evidence without sampling the restored machine."""
    value = _closed(
        raw,
        {
            "original_boot_id",
            "original_pid",
            "original_start_ticks",
            "observer",
            "reason",
            "replacement_start_ticks",
        },
    )
    observer = value["observer"]
    _budget(observer)
    if (
        type(value["original_pid"]) is not int
        or type(value["original_start_ticks"]) is not int
        or not isinstance(value["original_boot_id"], str)
        or not isinstance(value["reason"], str)
    ):
        raise ValueError("Replica process end original incarnation has invalid types")
    if (value["original_boot_id"], value["original_pid"], value["original_start_ticks"]) != (
        original["boot_id"],
        original["pid"],
        original["start_ticks"],
    ):
        raise ValueError("Replica process end changed its original incarnation")
    replacement = value["replacement_start_ticks"]
    if value["reason"] == "new-kernel":
        valid = observer["boot_id"] != original["boot_id"] and replacement is None
    elif value["reason"] in {"original-process-absent", "pid-reused"}:
        valid = (observer["boot_id"], observer["pid_namespace"]) == (
            original["boot_id"],
            original["pid_namespace"],
        ) and (
            replacement is None
            if value["reason"] == "original-process-absent"
            else (
                type(replacement) is int
                and 0 < replacement < 2**64
                and replacement != original["start_ticks"]
            )
        )
    else:
        valid = False
    if not valid:
        raise ValueError("Replica process-end observation lacks original negative evidence")
    return value


def bindings(raw: str, identity: dict[str, Any]) -> list[str]:
    """Verify the bounded backing chain and retain exact raw prior bindings."""
    if len(raw.encode()) > 64 * 16384 + 32768:
        raise ValueError("Replica content restore history exceeds its original bound")
    history = json.loads(raw)
    if not isinstance(history, list) or not 1 <= len(history) <= 64:
        raise ValueError("Replica content restore history is absent or unbounded")
    previous = None
    snapshot_history = 0
    for index, encoded in enumerate(history):
        if not isinstance(encoded, str) or len(encoded.encode()) > 16384:
            raise ValueError("Replica content original restore binding exceeds bound")
        binding = _closed(
            json.loads(encoded),
            {
                "schema",
                "command_id",
                "manifest_digest",
                "source_database_digest",
                "source_root_version",
                "source_parent_device",
                "source_parent_inode",
                "original_identity",
                "ordinal",
                "previous_binding_digest",
                "backing",
            },
        )
        backing = _closed(
            binding["backing"],
            {
                "root_device",
                "root_inode",
                "database_device",
                "database_inode",
                "parent_device",
                "parent_inode",
            },
        )
        if (
            (binding["schema"], binding["source_root_version"])
            not in {
                ("roboguide.replica-content-restore-binding/v0.1", 4 if index == 0 else 5),
                ("roboguide.replica-content-restore-binding/v0.2", 6),
                ("roboguide.replica-content-restore-binding/v0.3", 7),
                ("roboguide.replica-content-restore-binding/v0.4", 8),
            }
            or not isinstance(binding["command_id"], str)
            or not 0 < len(binding["command_id"]) <= 128
            or binding["original_identity"] != identity
            or type(binding["ordinal"]) is not int
            or binding["ordinal"] != index + 1
            or binding["previous_binding_digest"] != previous
            or any(
                not isinstance(binding[key], str) or SHA.fullmatch(binding[key]) is None
                for key in ("manifest_digest", "source_database_digest")
            )
            or any(type(value) is not int or not 0 <= value < 2**64 for value in backing.values())
            or backing["root_inode"] == 0
            or backing["database_inode"] == 0
            or backing["parent_inode"] == 0
            or type(binding["source_parent_device"]) is not int
            or not 0 <= binding["source_parent_device"] < 2**64
            or type(binding["source_parent_inode"]) is not int
            or not 0 < binding["source_parent_inode"] < 2**64
        ):
            raise ValueError("Replica content original restore source/chain/backing differs")
        if binding["source_root_version"] < snapshot_history:
            raise ValueError("Snapshot restore history regressed its backing generation")
        if binding["source_root_version"] >= 6:
            snapshot_history = binding["source_root_version"]
        if index > 0:
            prior = json.loads(history[index - 1])["backing"]
            if (binding["source_parent_device"], binding["source_parent_inode"]) != (
                prior["parent_device"],
                prior["parent_inode"],
            ):
                raise ValueError("Replica content original restore parent chain differs")
        previous = _sha(encoded)
    return list(history)


def _backing(root: Path) -> dict[str, int]:
    """Observe actual destination backing independently of copied provenance."""
    directory, database = root.lstat(), (root / "content.sqlite3").lstat()
    if not stat.S_ISDIR(directory.st_mode) or not stat.S_ISREG(database.st_mode):
        raise ValueError("Replica content backing is not an actual directory/database")
    return {
        "root_device": directory.st_dev,
        "root_inode": directory.st_ino,
        "database_device": database.st_dev,
        "database_inode": database.st_ino,
    }


def inventory(
    root: Path, component: dict[str, Any], budget: OperationBudget, *, live: bool = False
) -> dict[str, Any]:
    """Read complete Root inventories under caller-owned leases or immutable bundle."""
    identity = _identity(json.loads(_raw(root / ".retention-owner.json")))
    if any(
        json.loads(_raw(root / name)) != identity for name in (".content-creation.json", "blobs")
    ):
        raise ValueError("Replica content original identity fence is missing or changed")
    path = root / "content.sqlite3"
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        header = stream.read(20)
    if (
        len(header) != 20
        or header[:16] != b"SQLite format 3\0"
        or header[18:20] != b"\x01\x01"
        or (root / "content.sqlite3-wal").exists()
        or (root / "content.sqlite3-shm").exists()
    ):
        raise ValueError("Replica content requires original native DELETE journal without sidecars")
    if path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError("Replica content original SQL exceeds its fixed bound")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as connection:
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 32 * 1024 * 1024 + 4096)
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if (version, component["storage_schema"]) not in {
            (4, LAYOUT4),
            (5, LAYOUT5),
            (6, LAYOUT6),
            (7, LAYOUT7),
            (8, LAYOUT8),
        }:
            raise ValueError("Replica content declared/actual original generation differs")
        heads = HEADS | (
            {"snapshot_receive_ids": ("content_snapshot_receives", "operation_id")}
            if version >= 6
            else {}
        )
        if version >= 7:
            heads |= {
                "gc_ids": ("content_gc_commands", "operation_id"),
                "tombstone_keys": ("content_tombstones", "content_key"),
                "stream_retirement_ids": ("content_stream_retirements", "stream_id"),
            }
        if version == 8:
            heads |= {
                "staging_gc_ids": ("content_staging_gc_commands", "operation_id"),
                "staging_gc_item_ids": ("content_staging_gc_items", "item_id"),
            }
        expected = set(table for table, _ in heads.values()) | {"metadata"}
        objects = dict(
            connection.execute("SELECT name,type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
        )
        if objects != {name: "table" for name in expected}:
            raise ValueError("Replica content mandatory tables/triggers differ")
        tables: dict[str, Any] = {}
        metadata_columns = [
            (row[1], row[2], row[3], row[5])
            for row in connection.execute("PRAGMA table_info(metadata)")
        ]
        if metadata_columns != [("key", "TEXT", 0, 1), ("value", "TEXT", 1, 0)]:
            raise ValueError("Replica content original metadata columns/keys differ")
        metadata = dict(connection.execute("SELECT key,value FROM metadata LIMIT 18"))
        required = set(heads) | {"identity", "layout", "legacy_producer_ids"}
        restored = version == 5 or (version >= 6 and metadata.get("backing_mode") == "restored")
        if version >= 6:
            required.add("backing_mode")
            if metadata.get("backing_mode") not in {"original", "restored"} or restored != (
                component.get("restore_binding_digest") is not None
            ):
                raise ValueError("Snapshot Root6 declared/actual backing mode differs")
        if restored:
            required.add("restore_bindings")
        if set(metadata) != required or metadata["layout"] != component["storage_schema"]:
            raise ValueError("Replica content mandatory inventory/layout is missing")
        if json.loads(metadata["identity"]) != identity:
            raise ValueError("Replica content SQL/header original identity differs")
        for head, (table, column) in heads.items():
            budget.check()
            columns = [
                (row[1], row[2], row[3], row[5])
                for row in connection.execute(f"PRAGMA table_info({table})")
            ]
            expected_columns = [(column, "TEXT", 0, 1), ("record", "TEXT", 1, 0)]
            if table == "content_streams":
                expected_columns.insert(1, ("request_digest", "TEXT", 1, 0))
            if columns != expected_columns:
                raise ValueError("Replica content original inventory columns differ")
            ids = json.loads(metadata[head])
            if (
                not isinstance(ids, list)
                or len(ids) > 65536
                or any(not isinstance(value, str) or not 0 < len(value) <= 512 for value in ids)
                or ids != sorted(set(ids))
            ):
                raise ValueError("Replica content original inventory exceeds its fixed bound")
            rows: dict[str, Any] = {}
            for index, row in enumerate(connection.execute(f"SELECT * FROM {table} LIMIT 65537")):
                budget.check()
                if index >= 65536 or len(row[-1].encode()) > (
                    16 * 1024 * 1024
                    if table == "content_stream_retirements"
                    else 32 * 1024 * 1024
                    if table
                    in {
                        "content_snapshot_receives",
                        "content_gc_commands",
                        "content_staging_gc_commands",
                    }
                    else 8 * 1024 * 1024
                    if table in {"content_producers", "content_streams"}
                    else 16384
                ):
                    raise ValueError("Replica content original record exceeds fixed bound")
                value = json.loads(row[-1])
                if table == "content_streams" and value["request_digest"] != row[1]:
                    raise ValueError("Replica content original stream SQL digest differs")
                rows[row[0]] = value
            if sorted(rows) != ids:
                raise ValueError("Replica content original mandatory head lost journal rows")
            tables[table] = rows
        history = bindings(metadata["restore_bindings"], identity) if restored else []
    if restored:
        if _raw(root / BINDING_FILE) != history[-1]:
            raise ValueError("Replica content current binding differs from original SQL head")
        if version >= 6 and component.get("restore_binding_digest") != _sha(history[-1]):
            raise ValueError("Snapshot Root6 binding differs from its exact explicit declaration")
        if live and {
            key: json.loads(history[-1])["backing"][key] for key in _backing(root)
        } != _backing(root):
            raise ValueError("Replica content actual restored backing differs")
    elif live and {key: identity[key] for key in _backing(root)} != _backing(root):
        raise ValueError("Replica content original backing inode differs")
    validate_records(identity, tables, json.loads(metadata["legacy_producer_ids"]), budget)
    if version >= 7:
        from .replica_gc import validate as validate_gc

        validate_gc(identity, tables, budget)
        from .replica_stream_retirements import validate as validate_stream_retirements

        validate_stream_retirements(identity, tables, budget)
    if version == 8:
        from .replica_staging_gc import validate as validate_staging_gc

        validate_staging_gc(identity, tables, budget)
    return {"identity": identity, "tables": tables, "history": history, "version": version}


def _pin(
    tables: dict[str, Any],
    reference_id: str,
    kind: str,
    content: dict[str, Any],
    authority: str,
    *,
    active: bool | None = None,
    release: str | None = None,
) -> None:
    """Require one exact independently owned original pin rather than a matching hash elsewhere."""
    actual = tables["content_references"].get(reference_id)
    expected = {
        "reference_id": reference_id,
        "kind": kind,
        "content": content,
        "authority_digest": authority,
        "generation": 1,
    }
    if (
        actual is None
        or actual["reference"] != expected
        or (active is not None and actual["active"] != active)
        or (release is not None and actual["release_digest"] != release)
    ):
        raise ValueError("Replica content original operation lost its exact independent pin")


def _completed(budget: dict[str, Any], sample: Any) -> bool:
    """Match the original ten-millisecond conservative completion fence without a new clock."""
    return (
        type(sample) is int
        and budget["admitted_ms"] <= sample
        and sample - budget["admitted_ms"] + 10 < budget["duration_ms"]
    )


def _receipt(value: Any, intent: dict[str, Any], identity: dict[str, Any], request: str) -> None:
    """Check original byte completion separately from current backing."""
    value = _closed(
        value,
        {
            "schema",
            "request_digest",
            "command_id",
            "content",
            "store_owner",
            "member",
            "profile_identity",
            "file_device",
            "file_inode",
            "completed_ms",
        },
    )
    budget = intent["budget"]
    if (
        value["schema"]
        not in {
            "roboguide.replica-content-receipt/v0.1",
            "roboguide.replica-content-stream-receipt/v0.1",
        }
        or value["request_digest"] != request
        or value["command_id"] != intent["command_id"]
        or value["content"] != intent["content"]
        or value["store_owner"] != identity["store_owner"]
        or value["member"] != identity["member"]
        or value["profile_identity"] != identity["profile_identity"]
        or type(value["file_inode"]) is not int
        or value["file_inode"] <= 0
        or type(value["completed_ms"]) is not int
        or not _completed(budget, value["completed_ms"])
    ):
        raise ValueError("Replica content original byte receipt/source/budget differs")


def validate_records(
    identity: dict[str, Any], tables: dict[str, Any], legacy: Any, budget: OperationBudget
) -> None:
    """Bind operations to original identity, budgets and independent pins."""
    pins = tables["content_references"]
    owned: set[str] = set()
    if not isinstance(legacy, list) or len(legacy) > 65536 or legacy != sorted(set(legacy)):
        raise ValueError("Replica content original legacy pin inventory differs")
    for key in legacy:
        if (
            key not in pins
            or pins[key]["reference"]["kind"] not in {"log", "view", "snapshot"}
            or key.startswith("producer:")
        ):
            raise ValueError("Replica content original legacy pin lost its retained responsibility")
        owned.add(key)
    for key, value in pins.items():
        budget.check()
        value = _closed(value, {"reference", "active", "release_digest"})
        reference = _closed(
            value["reference"],
            {"reference_id", "kind", "content", "authority_digest", "generation"},
        )
        _content(reference["content"])
        if (
            reference["reference_id"] != key
            or reference["kind"]
            not in {"receive", "stream", "reader", "log", "view", "snapshot", "snapshot-receive"}
            or (
                reference["kind"] == "snapshot-receive"
                and "content_snapshot_receives" not in tables
            )
            or reference["generation"] != 1
            or type(value["active"]) is not bool
            or not isinstance(reference["authority_digest"], str)
            or HEX.fullmatch(reference["authority_digest"]) is None
            or (value["active"] and value["release_digest"] is not None)
            or (
                not value["active"]
                and (
                    not isinstance(value["release_digest"], str)
                    or HEX.fullmatch(value["release_digest"]) is None
                )
            )
        ):
            raise ValueError("Replica content original reference/release differs")
    for table, kind, command in (
        ("content_operations", "receive", "command_id"),
        ("content_readers", "reader", "reader_id"),
        ("content_streams", "stream", "command_id"),
        ("content_producers", "producer", "operation_id"),
    ):
        if (
            table in {"content_readers", "content_streams", "content_producers"}
            and len(tables[table]) > 4096
        ):
            raise ValueError("Replica content original operation inventory exceeds fixed bound")
        for key, record in tables[table].items():
            budget.check()
            record_fields = {"intent", "receipt", "process_end"}
            if kind == "stream":
                record_fields = {"intent", "request_digest", "receipt", "worker_closed_ms"}
            elif kind == "producer":
                record_fields = {
                    "intent",
                    "publication",
                    "worker_closed_ms",
                    "observation",
                    "resolution",
                }
            _closed(record, record_fields)
            intent = record["intent"]
            intent_fields = {"schema", command, "receiver", "content", "authority_digest", "budget"}
            if kind == "receive":
                intent_fields.add("source")
            elif kind == "stream":
                intent_fields = {
                    "schema",
                    command,
                    "receiver",
                    "content",
                    "target",
                    "origin",
                    "budget",
                }
            elif kind == "producer":
                intent_fields = {
                    "schema",
                    command,
                    "receiver",
                    "source",
                    "sql_device",
                    "sql_inode",
                    "inputs",
                    "budget",
                }
            elif "retention_only" in intent:
                intent_fields.add("retention_only")
            _closed(intent, intent_fields)
            schema = (
                "roboguide.content-producer-intent/v0.1"
                if kind == "producer"
                else (
                    "roboguide.replica-content-"
                    + {"receive": "copy", "stream": "stream", "reader": "reader"}[kind]
                    + "/v0.1"
                )
            )
            if (
                intent["schema"] != schema
                or intent["receiver"] != identity
                or not isinstance(intent[command], str)
                or not 0 < len(intent[command]) <= 128
                or _digest(intent[command]) != key
            ):
                raise ValueError("Replica content original receiver/operation identity differs")
            _budget(intent["budget"])
            if kind == "producer":
                inputs = intent["inputs"]
                if not isinstance(inputs, list) or len(inputs) > 16384 or intent["sql_inode"] <= 0:
                    raise ValueError("Replica content original producer inputs/SQL differs")
                keys = [_content(value) for value in inputs]
                if keys != sorted(set(keys)) or len({key[:2] for key in keys}) != len(keys):
                    raise ValueError("Replica content original producer input identities conflict")
                source_kind = {
                    "log": "log",
                    "view": "view",
                    "snapshot-build": "snapshot",
                    "snapshot-install": "snapshot",
                }.get(intent["source"]["kind"])
                if source_kind is None:
                    raise ValueError("Replica content original producer source kind differs")
                for content in inputs:
                    pin = "producer:" + key + ":" + _digest(content)
                    _pin(
                        tables,
                        pin,
                        source_kind,
                        content,
                        _digest(intent),
                        active=record["resolution"] is None,
                        release=_digest(record) if record["resolution"] is not None else None,
                    )
                    owned.add(pin)
                continue
            _content(intent["content"])
            pin = kind + ":" + key
            authority = (
                intent["target"]["batch_digest"] if kind == "stream" else intent["authority_digest"]
            )
            ended = record.get("receipt") is not None or record.get("process_end") is not None
            if kind == "stream":
                if (
                    record["request_digest"] != _digest(intent)
                    or intent["content"] not in intent["target"]["inputs"]
                ):
                    raise ValueError("Replica content original stream target/digest differs")
                retired = tables.get("content_stream_retirements", {}).get(key)
                _pin(
                    tables,
                    pin,
                    kind,
                    intent["content"],
                    authority,
                    active=retired is None,
                    release=_digest(retired) if retired is not None else None,
                )
            else:
                if record.get("receipt") is not None and record.get("process_end") is not None:
                    raise ValueError(
                        "Replica content original completion conflicts with process-end"
                    )
                if record.get("process_end") is not None:
                    _process_end(intent["budget"], record["process_end"])
                _pin(
                    tables,
                    pin,
                    kind,
                    intent["content"],
                    authority,
                    active=not ended if kind == "reader" or record.get("receipt") is None else None,
                    release=_digest(record)
                    if ended and (kind == "reader" or record.get("process_end") is not None)
                    else None,
                )
            owned.add(pin)
            if kind != "reader" and record.get("receipt") is not None:
                _receipt(record["receipt"], intent, identity, _digest(intent))
            if kind == "reader" and record["receipt"] is not None:
                receipt = record["receipt"]
                fields = {
                    "schema",
                    "request_digest",
                    "reader_id",
                    "content",
                    "store_owner",
                    "file_device",
                    "file_inode",
                    "bytes_read",
                    "observed_digest",
                    "complete",
                    "closed_ms",
                }
                if "retention_only" in receipt:
                    fields.add("retention_only")
                _closed(receipt, fields)
                retention = intent.get("retention_only", False)
                if (
                    receipt["schema"] != "roboguide.replica-content-reader-receipt/v0.1"
                    or receipt["request_digest"] != _digest(intent)
                    or receipt["reader_id"] != intent["reader_id"]
                    or receipt["content"] != intent["content"]
                    or receipt["store_owner"] != identity["store_owner"]
                    or receipt.get("retention_only", False) != intent.get("retention_only", False)
                    or type(receipt["bytes_read"]) is not int
                    or not 0 <= receipt["bytes_read"] <= intent["content"]["byte_size"]
                    or type(receipt["complete"]) is not bool
                    or not isinstance(receipt["observed_digest"], str)
                    or SHA.fullmatch(receipt["observed_digest"]) is None
                    or type(receipt["closed_ms"]) is not int
                    or receipt["closed_ms"] < intent["budget"]["admitted_ms"]
                    or (not retention and receipt["file_inode"] == 0)
                    or (
                        retention
                        and (
                            receipt["file_inode"] != 0
                            or receipt["file_device"] != 0
                            or receipt["bytes_read"] != 0
                            or receipt["complete"]
                            or receipt["observed_digest"]
                            != "sha256:" + hashlib.sha256(b"").hexdigest()
                        )
                    )
                    or (
                        receipt["complete"]
                        and (
                            receipt["bytes_read"] != intent["content"]["byte_size"]
                            or receipt["observed_digest"] != intent["content"]["content_digest"]
                            or not _completed(intent["budget"], receipt["closed_ms"])
                        )
                    )
                ):
                    raise ValueError("Replica content original reader close differs")
    from .snapshot_content_receives import validate as validate_snapshot_receives

    owned.update(validate_snapshot_receives(identity, tables, budget))
    if owned != set(pins):
        raise ValueError("Replica content original retained pin lost its mandatory owner journal")
    for key, receipt in tables["content_objects"].items():
        _content(receipt["content"])
        if _digest(receipt["content"]) != key:
            raise ValueError("Replica content original object key changed qualified identity")
        table = (
            "content_streams"
            if receipt["schema"] == "roboguide.replica-content-stream-receipt/v0.1"
            else "content_operations"
        )
        source = tables[table].get(_digest(receipt["command_id"]))
        if source is None or source["receipt"] != receipt:
            raise ValueError("Replica content original object lost its exact original receipt")
