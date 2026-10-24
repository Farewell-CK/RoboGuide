"""Validate parent9 original initialization declarations, immutable source captures and Unknown."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import PurePosixPath
from typing import Any, cast

from . import replica_content as content

INITIAL = "roboguide.application-initialization/v0.1"
ORDINARY = "roboguide.application-commit/v3"


def _policy(value: Any) -> dict[str, Any]:
    """Require the bounded original policy without granting access or renewing budgets."""
    value = content._closed(
        value,
        {
            "schema",
            "source_state_digest",
            "source_owner",
            "source_member",
            "command_id",
            "budget_ms",
        },
    )
    if (
        value["schema"] != "roboguide.content-initialization-policy/v0.1"
        or not isinstance(value["source_state_digest"], str)
        or content.SHA.fullmatch(value["source_state_digest"]) is None
        or not isinstance(value["source_owner"], str)
        or not value["source_owner"].startswith("artifact-store:")
        or content.HEX.fullmatch(value["source_owner"][15:]) is None
        or type(value["source_member"]) is not int
        or not 0 < value["source_member"] < 2**64
        or not isinstance(value["command_id"], str)
        or not 0 < len(value["command_id"].encode()) <= 128
        or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in value["command_id"])
        or type(value["budget_ms"]) is not int
        or not 100 <= value["budget_ms"] <= 300000
    ):
        raise ValueError("original initialization policy is invalid")
    return cast(dict[str, Any], value)


def _checkpoint(value: dict[str, Any]) -> str:
    """Match the immutable original Rust checkpoint hash domain without rewriting raw authority."""
    value = content._closed(value, {"schema", "event_sequence", "checkpoint_json"})
    schema, raw = value["schema"].encode(), value["checkpoint_json"].encode()
    return hashlib.sha256(
        b"roboguide.application-commit/v1"
        + len(schema).to_bytes(8, "little")
        + schema
        + value["event_sequence"].to_bytes(8, "little")
        + len(raw).to_bytes(8, "little")
        + raw
    ).hexdigest()


def _anchor(value: Any, policy: dict[str, Any]) -> dict[str, Any]:
    """Validate historical source lineage separately from current availability."""
    value = content._closed(
        value,
        {
            "schema",
            "source_state_digest",
            "candidate_digest",
            "source_writer",
            "source_epoch",
            "history_floor",
            "checkpoint_digest",
            "event_sequence",
            "authority_revision",
        },
    )
    writer = content._closed(value["source_writer"], {"owner", "device", "inode"})
    epoch = content._closed(value["source_epoch"], {"term", "replica_id"})
    if (
        value["schema"] != "roboguide.content-initialization-anchor/v0.1"
        or value["source_state_digest"] != policy["source_state_digest"]
        or writer["owner"] != policy["source_owner"]
        or type(writer["device"]) is not int
        or writer["device"] < 0
        or type(writer["inode"]) is not int
        or writer["inode"] <= 0
        or epoch["replica_id"] != policy["source_member"]
        or type(epoch["term"]) is not int
        or epoch["term"] <= 0
        or any(
            not isinstance(value[key], str) or content.HEX.fullmatch(value[key]) is None
            for key in ("candidate_digest", "checkpoint_digest")
        )
        or any(
            type(value[key]) is not int or not 0 < value[key] < 2**64
            for key in ("history_floor", "event_sequence", "authority_revision")
        )
        or value["history_floor"] > value["event_sequence"]
    ):
        raise ValueError("original initialization lineage differs from its immutable source")
    return cast(dict[str, Any], value)


def _initial_candidate(candidate: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Recompute complete original source and anchor, preserving the real archived prefix."""
    mandatory = {
        "schema",
        "profile_identity",
        "epoch",
        "source_writer",
        "batch",
        "recovery",
        "origins",
        "inputs",
        "initialization_digest",
    }
    if (
        not mandatory <= set(candidate) <= mandatory | {"source_initialization"}
        or candidate["schema"] != INITIAL
        or candidate.get("source_initialization") is not None
    ):
        raise ValueError("original initialization envelope is not closed")
    batch = content._closed(
        candidate["batch"],
        {
            "schema",
            "previous_event_sequence",
            "previous_checkpoint_digest",
            "checkpoint",
            "events",
            "commands",
        },
    )
    recovery = content._closed(
        candidate["recovery"],
        {
            "schema",
            "metadata",
            "archive_operations",
            "archive_aborts",
            "catalog_archive_events",
            "archive_manifests",
        },
    )
    floor = int(recovery["metadata"].get("history_floor", "0"))
    events, commands, checkpoint = batch["events"], batch["commands"], batch["checkpoint"]
    if (
        batch["schema"] != "roboguide.application-snapshot/v0.1"
        or batch["previous_event_sequence"] != 0
        or batch["previous_checkpoint_digest"] is not None
        or not isinstance(events, list)
        or len(events) > 8192
        or not isinstance(commands, list)
        or not 0 < len(commands) <= 8192
        or floor <= 0
        or floor + len(events) != checkpoint["event_sequence"]
        or [row["sequence"] for row in events]
        != list(range(floor + 1, checkpoint["event_sequence"] + 1))
        or [row["authority_revision"] for row in commands] != list(range(1, len(commands) + 1))
        or len({row["command_id"] for row in commands}) != len(commands)
        or len(checkpoint["checkpoint_json"].encode()) > 64 * 1024 * 1024
        or recovery["schema"] != "roboguide.application-recovery/v0.1"
        or not isinstance(recovery["catalog_archive_events"], list)
        or not isinstance(recovery["archive_manifests"], list)
        or not recovery["archive_operations"]
    ):
        raise ValueError("original initialization lost complete bounded source rows/floor")
    last = commands[-1]
    if (
        last["checkpoint_event_sequence"] != checkpoint["event_sequence"]
        or last["checkpoint_schema"] != checkpoint["schema"]
        or last["resulting_state_digest"]
        != "sha256:" + hashlib.sha256(checkpoint["checkpoint_json"].encode()).hexdigest()
    ):
        raise ValueError("original initialization lost its exact last command/checkpoint")
    through = 0
    for manifest in recovery["archive_manifests"]:
        if (
            manifest["start_sequence"] != through + 1
            or manifest["end_sequence"] < manifest["start_sequence"]
        ):
            raise ValueError("original initialization Archive prefix is discontinuous")
        through = manifest["end_sequence"]
    if through != floor:
        raise ValueError("original initialization Archive coverage differs from its source floor")
    for operation in recovery["archive_operations"]:
        if (
            json.loads(operation["intent_json"])["request"]["source_owner"]
            != candidate["source_writer"]["owner"]
        ):
            raise ValueError("original Archive owner differs from initialization source")
    complete = {
        "schema": "roboguide.complete-replica-state/v0.1",
        "checkpoint": checkpoint,
        "events": events,
        "commands": commands,
        "recovery": recovery,
    }
    checksum = "sha256:" + content._digest(complete)
    if candidate["initialization_digest"] != checksum or policy["source_state_digest"] != checksum:
        raise ValueError("original initialization complete source digest changed")
    return _anchor(
        {
            "schema": "roboguide.content-initialization-anchor/v0.1",
            "source_state_digest": checksum,
            "candidate_digest": content._digest(candidate),
            "source_writer": candidate["source_writer"],
            "source_epoch": candidate["epoch"],
            "history_floor": floor,
            "checkpoint_digest": _checkpoint(checkpoint),
            "event_sequence": checkpoint["event_sequence"],
            "authority_revision": last["authority_revision"],
        },
        policy,
    )


def validate_content(state: Any, policy: dict[str, Any] | None) -> None:
    """Require the selected candidate codec and its exact retained initialization lineage."""
    if state is None:
        return
    candidate = state["candidate"]
    if candidate["schema"] == INITIAL:
        if policy is None or state.get("source_initialization") != _initial_candidate(
            candidate, policy
        ):
            raise ValueError("accepted initialization lacks its exact immutable source anchor")
    elif candidate["schema"] == ORDINARY:
        anchor = candidate.get("source_initialization")
        if (
            candidate.get("initialization_digest") is not None
            or state.get("source_initialization") != anchor
        ):
            raise ValueError("ordinary candidate changed its historical initialization lineage")
        if anchor is not None:
            if policy is None:
                raise ValueError("initialization lineage lacks its original deployment policy")
            _anchor(anchor, policy)
        elif policy is not None:
            raise ValueError("declared initialization was replaced by an empty ordinary source")
    else:
        raise ValueError("unsupported original complete-content envelope")
    if state["batch_digest"] != content._digest(candidate):
        raise ValueError("original accepted candidate digest changed")


def validate_parent(connection: sqlite3.Connection) -> None:
    """Verify mandatory parent9 authority and every original operation in the same SQL view."""
    metadata = dict(connection.execute("SELECT key,value FROM replica_v1_meta LIMIT 4097"))
    if len(metadata) > 4096 or not {
        "content_initialization_policy",
        "content_initialization_ids",
        "content_root_identity",
        "identity",
    } <= set(metadata):
        raise ValueError("parent9 original initialization policy/inventory is missing")
    policy = json.loads(metadata["content_initialization_policy"])
    if policy is not None:
        policy = _policy(policy)
    columns = [
        (row[1], row[2], row[3], row[5])
        for row in connection.execute("PRAGMA table_info(content_initializations)")
    ]
    if columns != [("command_id", "TEXT", 0, 1), ("record", "TEXT", 1, 0)]:
        raise ValueError("parent9 original initialization table differs")
    ids = json.loads(metadata["content_initialization_ids"])
    if not isinstance(ids, list) or len(ids) > 1 or ids != sorted(set(ids)):
        raise ValueError("parent9 original initialization inventory is unbounded or repeated")
    root = json.loads(metadata["content_root_identity"])
    profile = json.loads(metadata["identity"])[0]
    actual = []
    for key, raw in connection.execute(
        "SELECT command_id,record FROM content_initializations LIMIT 2"
    ):
        if len(raw.encode()) > 256 * 1024 * 1024:
            raise ValueError("original initialization record exceeds its bound")
        record = content._closed(
            json.loads(raw), {"intent", "intent_digest", "captured", "completion"}
        )
        intent = content._closed(
            record["intent"],
            {"schema", "profile_identity", "root", "policy", "input", "epoch", "budget"},
        )
        source_input = content._closed(intent["input"], {"source_sql", "source_cas"})
        if (
            policy is None
            or intent["schema"] != "roboguide.content-initialization-intent/v0.1"
            or intent["policy"] != policy
            or intent["root"] != root
            or intent["profile_identity"] != profile
            or root["member"] != policy["source_member"]
            or key != policy["command_id"]
            or record["intent_digest"] != "sha256:" + content._digest(intent)
            or intent["epoch"]["replica_id"] != root["member"]
            or intent["epoch"]["term"] <= 0
            or any(
                not isinstance(path, str)
                or len(path.encode()) > 4096
                or not PurePosixPath(path).is_absolute()
                or ".." in PurePosixPath(path).parts
                for path in source_input.values()
            )
        ):
            raise ValueError("original initialization declaration/input identity changed")
        content._budget(intent["budget"])
        if intent["budget"]["duration_ms"] != policy["budget_ms"]:
            raise ValueError("original initialization budget differs from its declaration")
        captured = record["captured"]
        anchor = None
        if captured is not None:
            captured = content._closed(captured, {"candidate", "captured_ms"})
            anchor = _initial_candidate(captured["candidate"], policy)
            if (
                captured["candidate"]["profile_identity"] != profile
                or captured["candidate"]["epoch"] != intent["epoch"]
                or not content._completed(intent["budget"], captured["captured_ms"])
            ):
                raise ValueError("original initialization capture changed its first epoch/budget")
        if record["completion"] is not None:
            completion = content._closed(
                record["completion"], {"receipt", "complete_members", "completed_ms"}
            )
            receipt = completion["receipt"]
            if (
                anchor is None
                or captured is None
                or receipt["accepted"] is not True
                or receipt["revision"] <= 0
                or receipt["checkpoint_digest"] != anchor["checkpoint_digest"]
                or not completion["complete_members"]
                or not content._completed(intent["budget"], completion["completed_ms"])
                or completion["completed_ms"] < captured["captured_ms"]
            ):
                raise ValueError(
                    "original initialization completion lost its original actual proof/budget"
                )
        actual.append(key)
    if sorted(actual) != ids:
        raise ValueError("parent9 original initialization inventory lost its operation")
    validate_content(json.loads(metadata.get("content_state", "null")), policy)
