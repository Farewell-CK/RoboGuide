"""SQL upgrade counterexamples; synthetic records establish no deployed process facts."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery.application_upgrades import (
    AUTHORITY,
    BASE,
    OPERATIONS,
    ORIGINAL_POLICY,
    PROFILE,
    validate_parent,
)
from roboguide_recovery.snapshot_references import canonical


def original_metadata() -> dict[str, Any]:
    """Build synthetic original member, clock and wait links independently of Rust code."""
    epoch = {"term": 2, "replica_id": 1}
    membership = {
        "log_id": {"leader_id": {"term": 2, "node_id": 1}, "index": 1},
        "membership": {
            "configs": [[1, 2, 3]],
            "nodes": {
                str(member): {
                    "addr": f"127.0.0.1:{28000 + member}",
                    "content_store_owner": f"replica-content:{member:064x}",
                }
                for member in [1, 2, 3]
            },
        },
    }
    target = {
        "application_schema": "roboguide.controller-checkpoint/v56",
        "binary_digest": "sha256:" + "b" * 64,
        "policy_digest": "c" * 64,
    }
    predecessor = {"revision": 1, "event_sequence": 0, "checkpoint_digest": "a" * 64}
    intent = {
        "request": {
            "schema": "roboguide.application-upgrade/v0.1",
            "command_id": "unit-original-upgrade",
            "expected_revision": 0,
            "from_schema": "roboguide.controller-checkpoint/v55",
            "target": target,
            "budget_ms": 10000,
        },
        "epoch": epoch,
        "membership": membership,
        "predecessor": predecessor,
        "supports": [
            {
                "member": member,
                "leaf": f"sha256:{member:064x}",
                "infrastructure_binary_digest": "sha256:" + "d" * 64,
                "target": target,
                "membership": membership,
                "predecessor": predecessor,
            }
            for member in [1, 2, 3]
        ],
    }
    drains = [
        {
            "member": member,
            "leaf": f"sha256:{member:064x}",
            "intent_digest": "sha256:" + hashlib.sha256(canonical(intent)).hexdigest(),
            "proof": {
                "kind": "no-application-process",
                "supervisor_instance": f"00000000-0000-4000-8000-{member:012x}",
                "epoch": epoch,
            },
        }
        for member in [1, 2, 3]
    ]
    return {
        "identity": ["e" * 64, "roboguide.controller-checkpoint/v55"],
        BASE: 8,
        AUTHORITY: {
            "schema": "roboguide.application-upgrade-authority/v0.1",
            "original_schema": "roboguide.controller-checkpoint/v55",
            "receipts": [{"intent": intent, "drains": drains, "revision": 1}],
        },
        OPERATIONS: [
            {
                "intent": intent,
                "clock": {
                    "boot_id": "00000000-0000-4000-8000-000000000001",
                    "namespace": [4, 7],
                    "pid_namespace": [4, 8],
                    "admitted_ms": 500,
                    "duration_ms": 9000,
                    "pid": 1234,
                    "start_ticks": 20,
                },
                "process": None,
                "process_history_head": 0,
                "supervisor_instance": "00000000-0000-4000-8000-000000000001",
                "drain": drains[0],
            }
        ],
        "controller_processes_v1": [],
        "content_root_identity": {"member": 1},
        ORIGINAL_POLICY: "e" * 64,
    }


def database(path: Path, values: dict[str, Any]) -> sqlite3.Connection:
    """Create actual SQLite fixture rows without starting any Controller, Node or device."""
    sql = sqlite3.connect(path)
    sql.executescript(
        "PRAGMA user_version=10; "
        "CREATE TABLE replica_v1_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);"
    )
    sql.executemany(
        "INSERT INTO replica_v1_meta VALUES (?,?)",
        [(key, json.dumps(value)) for key, value in values.items()],
    )
    sql.commit()
    return sql


def test_original_upgrade_preserves_complete_original_rows_and_requires_new_profile(
    tmp_path: Path,
) -> None:
    """Current explicit validation reads original clocks and waits without modifying SQL."""
    with closing(database(tmp_path / "source.sqlite3", original_metadata())) as sql:
        before = list(sql.iterdump())
        validate_parent(sql, PROFILE)
        assert list(sql.iterdump()) == before
        with pytest.raises(ValueError, match="v0.25"):
            validate_parent(sql, "roboguide.recovery-profile/v0.24")


@pytest.mark.parametrize(
    "damage", ["authority", "support", "drain", "clock", "prefix", "instance", "downgrade"]
)
def test_upgrade_missing_or_changed_original_authority_refuses(tmp_path: Path, damage: str) -> None:
    """Omission, borrowed stop proofs and renewed clocks cannot become original evidence."""
    values = copy.deepcopy(original_metadata())
    if damage == "authority":
        del values[AUTHORITY]
    elif damage == "support":
        values[AUTHORITY]["receipts"][0]["intent"]["supports"].pop()
    elif damage == "drain":
        values[AUTHORITY]["receipts"][0]["drains"][0]["intent_digest"] = "sha256:" + "f" * 64
    elif damage == "clock":
        values[OPERATIONS][0]["clock"]["duration_ms"] = 11000
    elif damage == "prefix":
        values[OPERATIONS][0]["process_history_head"] = 1
    elif damage == "instance":
        values[OPERATIONS][0]["supervisor_instance"] = "00000000-0000-4000-8000-000000000002"
    else:
        values[AUTHORITY]["receipts"][0]["intent"]["request"]["target"]["application_schema"] = (
            "roboguide.controller-checkpoint/v54"
        )
    with closing(database(tmp_path / "source.sqlite3", values)) as sql:
        with pytest.raises(ValueError):
            validate_parent(sql, PROFILE)


def patch_metadata() -> dict[str, Any]:
    """Build a distinct same-schema repair retaining the original unmigrated checkpoint identity."""
    values = original_metadata()
    patch = copy.deepcopy(values[AUTHORITY]["receipts"][0])
    patch["revision"] = 2
    intent = patch["intent"]
    request = intent["request"]
    request.update(
        command_id="unit-original-patch",
        expected_revision=1,
        from_schema="roboguide.controller-checkpoint/v56",
    )
    request["target"].update(binary_digest="sha256:" + "f" * 64, policy_digest="d" * 64)
    for support in intent["supports"]:
        support["target"] = copy.deepcopy(request["target"])
    for drain in patch["drains"]:
        drain["intent_digest"] = "sha256:" + hashlib.sha256(canonical(intent)).hexdigest()
    values[AUTHORITY]["receipts"].append(patch)
    return values


def test_same_schema_patch_keeps_original_predecessor_and_code_history(tmp_path: Path) -> None:
    """Repair an activated failed binary while retaining its original checkpoint."""
    values = patch_metadata()
    with closing(database(tmp_path / "patch.sqlite3", values)) as sql:
        before = list(sql.iterdump())
        validate_parent(sql, PROFILE)
        assert before == list(sql.iterdump())


@pytest.mark.parametrize("damage", ["policy", "local-wait", "foreign-member", "retired-code"])
def test_patch_cannot_borrow_local_wait_or_restore_retired_code(
    tmp_path: Path, damage: str
) -> None:
    """Refuse retired code and borrowed member drains for a distinct patch intent."""
    values = patch_metadata()
    if damage == "policy":
        del values[ORIGINAL_POLICY]
    elif damage == "local-wait":
        values[OPERATIONS][0]["drain"] = None
    elif damage == "foreign-member":
        values["content_root_identity"]["member"] = 2
    else:
        target = values[AUTHORITY]["receipts"][0]["intent"]["request"]["target"]
        intent = values[AUTHORITY]["receipts"][1]["intent"]
        intent["request"]["target"] = copy.deepcopy(target)
        for support in intent["supports"]:
            support["target"] = copy.deepcopy(target)
        for drain in values[AUTHORITY]["receipts"][1]["drains"]:
            drain["intent_digest"] = "sha256:" + hashlib.sha256(canonical(intent)).hexdigest()
    with closing(database(tmp_path / "damaged.sqlite3", values)) as sql:
        with pytest.raises(ValueError):
            validate_parent(sql, PROFILE)


@pytest.mark.parametrize("damage", ["predecessor", "boolean-revision"])
def test_original_predecessor_cannot_disappear_under_valid_member_hashes(
    tmp_path: Path, damage: str
) -> None:
    """Rehashed member proofs cannot authorize an absent checkpoint or a Boolean revision."""
    values = original_metadata()
    receipt = values[AUTHORITY]["receipts"][0]
    if damage == "predecessor":
        receipt["intent"]["predecessor"]["checkpoint_digest"] = None
        for drain in receipt["drains"]:
            drain["intent_digest"] = (
                "sha256:" + hashlib.sha256(canonical(receipt["intent"])).hexdigest()
            )
    else:
        receipt["revision"] = True
    with closing(database(tmp_path / "missing-checkpoint.sqlite3", values)) as sql:
        with pytest.raises(ValueError):
            validate_parent(sql, PROFILE)
