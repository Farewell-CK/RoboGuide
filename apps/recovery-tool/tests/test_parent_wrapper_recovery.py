"""Exercise actual SQL parent wrapper validation; synthetic empty rows grant no quorum authority."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery.bundle import Budget
from roboguide_recovery.replica_content_bundle import _parent


def parent(path: Path, encoded_identity: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Write a complete empty parent8 contract with one exact declared member and no content."""
    identity = {"profile_identity": "a" * 64, "member": 1}
    metadata = {
        "identity": encoded_identity,
        "content_root_identity": identity,
        "membership": {"membership": {"nodes": {"1": {"content_store_owner": "unit-root"}}}},
        "network_content_membership_schema": "roboguide.replica-storage/v0.7",
        "controller_content_ingress_schema": "roboguide.controller-content-ingress/v0.1",
        "controller_content_ingress_ids": [],
        "content_state": None,
        "content_producer_heads": {
            "schema": "roboguide.content-producer-heads/v0.1",
            "view": None,
            "snapshot": None,
        },
    }
    with closing(sqlite3.connect(path)) as sql, sql:
        sql.execute("PRAGMA user_version=8")
        sql.execute("CREATE TABLE replica_v1_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        sql.executemany(
            "INSERT INTO replica_v1_meta VALUES (?,?)",
            [(key, json.dumps(value)) for key, value in metadata.items()],
        )
        sql.execute("CREATE TABLE replica_v1_snapshot(singleton INTEGER,meta TEXT,data BLOB)")
        sql.execute(
            "CREATE TABLE controller_content_ingress("
            "command_id TEXT,request_digest TEXT,record TEXT)"
        )
        sql.execute(
            "CREATE TABLE replica_v1_events(sequence INTEGER,event_id TEXT,data TEXT,digest TEXT)"
        )
    return {"identity": identity, "history": [], "tables": {"content_producers": {}}}, {
        "source_backing": {"parent_device": path.stat().st_dev, "parent_inode": path.stat().st_ino}
    }


@pytest.mark.parametrize("version", ["v53", "v54", "v55"])
def test_complete_empty_original_parent_accepts_each_supported_wrapper(
    tmp_path: Path, version: str
) -> None:
    """Each generation is a separate original two-element identity, never a combined vector."""
    path = tmp_path / "parent.sqlite3"
    original, source = parent(path, ["a" * 64, "roboguide.controller-checkpoint/" + version])
    budget = Budget({"max_files": 100, "max_bytes": 1024**2, "budget_seconds": 10})
    assert _parent(path, original, source, budget, {1: "unit-root"}) == (None, [])


@pytest.mark.parametrize(
    "identity",
    [
        ["a" * 64, "roboguide.controller-checkpoint/v54", "roboguide.controller-checkpoint/v55"],
        ["b" * 64, "roboguide.controller-checkpoint/v55"],
        ["a" * 64, "roboguide.controller-checkpoint/v56"],
        ["a" * 64],
    ],
)
def test_changed_original_profile_or_wrapper_shape_refuses(
    tmp_path: Path, identity: list[str]
) -> None:
    """A correctly encoded SQL marker cannot borrow another profile or invented generation."""
    path = tmp_path / "parent.sqlite3"
    original, source = parent(path, identity)
    budget = Budget({"max_files": 100, "max_bytes": 1024**2, "budget_seconds": 10})
    with pytest.raises(ValueError, match="mandatory marker differs"):
        _parent(path, original, source, budget, {1: "unit-root"})
