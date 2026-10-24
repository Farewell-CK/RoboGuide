"""Reject lost parent9 authority using explicit DTO fixtures; native gates prove real processes."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery import replica_content as content
from roboguide_recovery.replica_initialization import validate_parent


def parent(path: Path, *, operation: bool) -> None:
    """Create contract-only original Unknown SQL, with no source, process or quorum claim."""
    policy = {
        "schema": "roboguide.content-initialization-policy/v0.1",
        "source_state_digest": "sha256:" + "a" * 64,
        "source_owner": "artifact-store:" + "b" * 64,
        "source_member": 1,
        "command_id": "original-contract-source",
        "budget_ms": 1000,
    }
    root = {
        "schema": "roboguide.replica-content-storage/v0.1",
        "profile_identity": "a" * 64,
        "member": 1,
        "store_owner": "replica-content:" + "c" * 64,
        "root_device": 1,
        "root_inode": 1,
        "database_device": 1,
        "database_inode": 2,
    }
    intent = {
        "schema": "roboguide.content-initialization-intent/v0.1",
        "profile_identity": "a" * 64,
        "root": root,
        "policy": policy,
        "input": {"source_sql": "/declared/source.sqlite3", "source_cas": "/declared/source-cas"},
        "epoch": {"term": 1, "replica_id": 1},
        "budget": {
            "boot_id": "00000000-0000-0000-0000-000000000000",
            "namespace": [1, 1],
            "pid_namespace": [1, 2],
            "admitted_ms": 100,
            "duration_ms": 1000,
            "pid": 1,
            "start_ticks": 1,
        },
    }
    record = {
        "intent": intent,
        "intent_digest": "sha256:" + content._digest(intent),
        "captured": None,
        "completion": None,
    }
    metadata: dict[str, Any] = {
        "identity": ["a" * 64, "roboguide.controller-checkpoint/v53"],
        "content_initialization_policy": policy,
        "content_initialization_ids": [policy["command_id"]] if operation else [],
        "content_root_identity": root,
        "content_state": None,
    }
    with closing(sqlite3.connect(path)) as sql, sql:
        sql.execute("CREATE TABLE replica_v1_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        sql.execute(
            "CREATE TABLE content_initializations(command_id TEXT PRIMARY KEY,record TEXT NOT NULL)"
        )
        sql.executemany(
            "INSERT INTO replica_v1_meta VALUES (?,?)",
            [(key, json.dumps(value)) for key, value in metadata.items()],
        )
        if operation:
            sql.execute(
                "INSERT INTO content_initializations VALUES (?,?)",
                (policy["command_id"], json.dumps(record)),
            )


@pytest.mark.parametrize(
    "missing",
    ["content_initialization_policy", "content_initialization_ids", "content_root_identity"],
)
def test_missing_initialization_authority_never_defaults_to_empty(
    tmp_path: Path, missing: str
) -> None:
    """Loss of a mandatory original declaration refuses even with zero operations."""
    path = tmp_path / "parent.sqlite3"
    parent(path, operation=False)
    with closing(sqlite3.connect(path)) as sql, sql:
        validate_parent(sql)
        sql.execute("DELETE FROM replica_v1_meta WHERE key=?", (missing,))
        with pytest.raises(ValueError, match="missing"):
            validate_parent(sql)
        assert (
            sql.execute("SELECT 1 FROM replica_v1_meta WHERE key=?", (missing,)).fetchone() is None
        )


def test_unknown_keeps_original_budget_and_missing_row_cannot_be_empty(tmp_path: Path) -> None:
    """Repeated offline verification leaves exact raw Unknown; a removed original row refuses."""
    path = tmp_path / "parent.sqlite3"
    parent(path, operation=True)
    with closing(sqlite3.connect(path)) as sql, sql:
        before = sql.execute("SELECT record FROM content_initializations").fetchone()[0]
        validate_parent(sql)
        validate_parent(sql)
        assert sql.execute("SELECT record FROM content_initializations").fetchone()[0] == before
        assert json.loads(before)["completion"] is None
        sql.execute("DELETE FROM content_initializations")
        with pytest.raises(ValueError, match="lost its operation"):
            validate_parent(sql)


def test_changed_policy_or_synthetic_completion_cannot_replace_original_unknown(
    tmp_path: Path,
) -> None:
    """Retained intent disagreement and invented success without original capture both refuse."""
    path = tmp_path / "parent.sqlite3"
    parent(path, operation=True)
    with closing(sqlite3.connect(path)) as sql, sql:
        raw = sql.execute("SELECT record FROM content_initializations").fetchone()[0]
        record = json.loads(raw)
        record["intent"]["policy"]["budget_ms"] += 1
        record["intent_digest"] = "sha256:" + content._digest(record["intent"])
        sql.execute("UPDATE content_initializations SET record=?", (json.dumps(record),))
        with pytest.raises(ValueError, match="declaration/input identity changed"):
            validate_parent(sql)
        record = json.loads(raw)
        record["completion"] = {
            "receipt": {"accepted": True, "revision": 1, "checkpoint_digest": "d" * 64},
            "complete_members": [1, 2],
            "completed_ms": 200,
        }
        sql.execute("UPDATE content_initializations SET record=?", (json.dumps(record),))
        with pytest.raises(ValueError, match="lost its original actual proof"):
            validate_parent(sql)
