"""Counterexamples for retained source GC contracts; fixtures confer no deployment authority."""

from __future__ import annotations

import copy
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery import artifact_gc_journal as gc
from roboguide_recovery.artifact_source_references import _application, _use_revoked, upload_history
from roboguide_recovery.snapshot_references import canonical, digest


def closed_revision() -> tuple[dict[str, Any], str, dict[str, Any], dict[str, Any]]:
    """Build labelled original-goal parser inputs, never execution or physical evidence."""
    checksum = digest(b"unit-released-history")
    goal = {"predicate": "unit-independent-original-goal"}
    context = {"content_digest": checksum}
    contract = {
        "mission_id": "unit-mission",
        "tenant_id": "unit-tenant",
        "spec": goal,
        "context_digest": digest(canonical(context)),
        "source": {
            "source_id": "unit-source",
            "revision": 1,
            "producer_principals": ["unit-independent"],
        },
        "digest": "",
    }
    contract["digest"] = digest(canonical(contract))
    evidence = {
        "mission_id": "unit-mission",
        "tenant_id": "unit-tenant",
        "contract_digest": contract["digest"],
        "source_id": "unit-source",
        "source_revision": 1,
        "group_id": "unit-original-group",
        "valid_for_ms": 1000,
        "verdict": "satisfied",
        "finality": "final",
    }
    key = digest(canonical(evidence))
    clock = {
        "boot_id": "00000000-0000-0000-0000-000000000000",
        "namespace": [4, 1],
        "elapsed_ms": 10,
        "resolution_ms": 10,
    }
    progress = {
        "budget": {"issued": clock, "duration_ms": 1000},
        "completed_time": clock | {"elapsed_ms": 20},
    }
    state = {
        "revoked": True,
        "authority": "unit-authority",
        "switches": [{"phase": "applied"}],
        "policy": {"goal_context": context, "goal_verifier": goal, "proposer": "unit-proposer"},
        "goal_contract_digest": contract["digest"],
        "goal_evidence": {key: progress},
    }
    mission = {
        "plan": {"mission": {"id": "unit-mission"}},
        "lifecycle": "Completed",
        "group_id": "unit-original-group",
        "goal_gate": contract["digest"],
        "goal_completed_at": 30,
        "goal_receipt": key,
    }
    control = {
        "mission_goal_contracts": {"unit-mission": contract},
        "mission_goal_receipts": {
            key: {
                "input_digest": key,
                "evidence": evidence,
                "authority_subject": "unit-independent",
                "received_at": 10,
            }
        },
    }
    checkpoint: dict[str, Any] = {
        "schema": gc.WRAPPER,
        "replication_profile": None,
        "memory_preparation": {"records": {}},
        "memory_use": {"records": {}},
        "memory_imports": {},
        "memory_handoffs": {},
        "orchestration_json": json.dumps([mission]),
        "integration_json": json.dumps({"control": control}),
        "runtime_revisions": {"unit-mission": state},
    }
    return checkpoint, checksum, state, control


@pytest.mark.parametrize(
    "loss", ["policy", "manual-switch", "goal-progress", "receipt", "kernel", "deadline", "source"]
)
def test_immutable_revision_history_requires_actual_closure_before_release(loss: str) -> None:
    """Refuse missing independent closure even when the Mission's terminal string says Completed."""
    checkpoint, checksum, state, control = closed_revision()
    _application(checkpoint, checksum)
    progress = next(iter(state["goal_evidence"].values()))
    if loss == "policy":
        state["revoked"] = False
    elif loss == "manual-switch":
        state["switches"][0]["phase"] = "manual-required"
    elif loss == "goal-progress":
        progress["completed_time"] = None
    elif loss == "receipt":
        control["mission_goal_receipts"].clear()
    elif loss == "kernel":
        progress["completed_time"]["namespace"] = [4, 2]
    elif loss == "deadline":
        progress["completed_time"]["elapsed_ms"] += progress["budget"]["duration_ms"]
    else:
        next(iter(control["mission_goal_receipts"].values()))["authority_subject"] = "unit-proposer"
    checkpoint["integration_json"] = json.dumps({"control": control})
    with pytest.raises(ValueError):
        _application(checkpoint, checksum)


def retained_journal(root: Path) -> tuple[str, dict[str, Any]]:
    """Write one labelled parser fixture with immutable Unknown and a complete independent head."""
    directory = root / "reclamation-v1"
    directory.mkdir()
    owner = "artifact-store:" + "a" * 64
    intent: dict[str, Any] = {
        "schema": "roboguide.artifact-gc-intent/v0.1",
        "command_id": "unit-original",
        "component_id": "unit-artifact",
        "store_owner_id": owner,
        "profile_digest": "sha256:" + "b" * 64,
        "content_digest": digest(b"opaque parser bytes"),
        "byte_size": 19,
        "reason": "unit-parser-contract",
        "kernel": {
            "boot_id": "unit-unavailable-kernel",
            "time_namespace": "unit-unavailable-namespace",
            "admitted_ns": 10,
            "deadline_ns": 20,
        },
        "fence": {
            "pid": 1,
            "start_ticks": 1,
            "uid": 1,
            "writers": [
                {
                    "component_id": "unit-artifact",
                    "path": str(root / ".writer.lock"),
                    "device": 1,
                    "inode": 1,
                }
            ],
        },
        "content_inode": {"device": 1, "inode": 2},
        "known_authorities": [],
        "closure": [],
        "original_tombstone": None,
    }
    tombstone = {
        "schema": "roboguide.artifact-gc-tombstone/v0.1",
        "store_owner_id": owner,
        "content_digest": intent["content_digest"],
        "byte_size": intent["byte_size"],
        "first_command_id": intent["command_id"],
        "first_intent_digest": digest(canonical(intent)),
    }
    with sqlite3.connect(root / gc.DATABASE) as connection:
        connection.executescript(
            "CREATE TABLE gc_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);"
            "CREATE TABLE gc_commands(command_id TEXT PRIMARY KEY,"
            "intent_json TEXT NOT NULL,receipt_json TEXT);"
            "CREATE TABLE gc_tombstones(content_digest TEXT PRIMARY KEY,"
            "first_command_id TEXT NOT NULL,tombstone_json TEXT NOT NULL);"
        )
        connection.executemany(
            "INSERT INTO gc_metadata VALUES(?,?)",
            [
                ("schema", gc.SQL_SCHEMA),
                ("store_owner_id", owner),
                ("command_ids", canonical([intent["command_id"]]).decode()),
                ("tombstone_digests", canonical([intent["content_digest"]]).decode()),
            ],
        )
        connection.execute(
            "INSERT INTO gc_commands VALUES(?,?,NULL)",
            (intent["command_id"], canonical(intent).decode()),
        )
        connection.execute(
            "INSERT INTO gc_tombstones VALUES(?,?,?)",
            (intent["content_digest"], intent["command_id"], canonical(tombstone).decode()),
        )
    return owner, intent


@pytest.mark.parametrize(
    "loss", ["command", "tombstone", "command_head", "tombstone_head", "schema"]
)
def test_unknown_history_never_becomes_empty_authority(tmp_path: Path, loss: str) -> None:
    """Reject lost original rows/heads even when content remains."""
    owner, _ = retained_journal(tmp_path)
    commands, tombstones = gc.validate(tmp_path, owner)
    assert commands["unit-original"][1] is None and len(tombstones) == 1
    with sqlite3.connect(tmp_path / gc.DATABASE) as connection:
        if loss == "command":
            connection.execute("DELETE FROM gc_commands")
        elif loss == "tombstone":
            connection.execute("DELETE FROM gc_tombstones")
        else:
            key = {
                "command_head": "command_ids",
                "tombstone_head": "tombstone_digests",
                "schema": "schema",
            }[loss]
            connection.execute("DELETE FROM gc_metadata WHERE key=?", (key,))
    with pytest.raises(ValueError):
        gc.validate(tmp_path, owner)


def test_wal_header_is_refused_before_read_can_create_new_sidecars(tmp_path: Path) -> None:
    """Check the actual header before a readonly SQL query can create WAL/SHM sidecars."""
    owner, _ = retained_journal(tmp_path)
    with closing(sqlite3.connect(tmp_path / gc.DATABASE)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    directory = tmp_path / "reclamation-v1"
    before = sorted(path.name for path in directory.iterdir())
    with pytest.raises(ValueError, match="DELETE journal mode"):
        gc.validate(tmp_path, owner)
    assert sorted(path.name for path in directory.iterdir()) == before


@pytest.mark.parametrize(
    "mutation", ["late", "no_inode", "different_tombstone", "different_command"]
)
def test_completion_needs_exact_original_effect_proof(tmp_path: Path, mutation: str) -> None:
    """Reject claimed success without timely original unlink and tombstone proof."""
    owner, intent = retained_journal(tmp_path)
    tombstone = gc.validate(tmp_path, owner)[1][intent["content_digest"]]
    receipt = {
        "schema": "roboguide.artifact-gc-receipt/v0.1",
        "command_id": intent["command_id"],
        "intent_digest": digest(canonical(intent)),
        "tombstone_digest": digest(canonical(tombstone)),
        "result": "deleted",
        "completed_ns": 15,
        "deleted_inode": intent["content_inode"],
    }
    if mutation == "late":
        receipt["completed_ns"] = 20
    elif mutation == "no_inode":
        receipt["deleted_inode"] = None
    elif mutation == "different_tombstone":
        receipt["tombstone_digest"] = "sha256:" + "0" * 64
    else:
        receipt["command_id"] = "another-command"
    with sqlite3.connect(tmp_path / gc.DATABASE) as connection:
        connection.execute("UPDATE gc_commands SET receipt_json=?", (canonical(receipt).decode(),))
    with pytest.raises(ValueError):
        gc.validate(tmp_path, owner)


def original_use() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Declare unit provider receipts without creating deployment facts."""
    configuration, preparation_digest = "sha256:" + "1" * 64, "sha256:" + "2" * 64
    command = {
        "command_id": "unit-revoke",
        "operation": "revoke",
        "lease_id": "unit-use",
        "configuration_digest": configuration,
        "preparation_digest": preparation_digest,
    }
    proofs = []
    inputs, observations = [], []
    for name in ("unit-provider-a", "unit-provider-b"):
        original: dict[str, Any] = {
            "schema": "roboguide.memory-use-provider-receipt/v0.1",
            "command_id": command["command_id"],
            "configuration_digest": configuration,
            "preparation_digest": preparation_digest,
            "provider_id": name,
            "provider_instance_id": name + "-original-instance",
            "lease_id": "unit-use",
            "generation": 0,
            "import_ids": [],
            "status": "revoked",
        }
        proof = {
            "provider_id": name,
            "provider_instance_id": original["provider_instance_id"],
            "provider_contract_digest": "sha256:" + "3" * 64,
            "import_ids": [],
            "receipt_digest": digest(canonical(original)),
            "original_receipt": original,
        }
        proofs.append(proof)
        inputs.append({"import_id": name + "-import", "consumer": {"provider_id": name}})
        observations.append(
            {
                "import_id": name + "-import",
                "provider_instance_id": proof["provider_instance_id"],
                "provider_contract_digest": proof["provider_contract_digest"],
            }
        )
    delivery = {
        "command": command,
        "received_at_ms": 10,
        "receipt": {
            "command_id": command["command_id"],
            "generation": 0,
            "status": "unknown",
            "providers": [proofs[0]],
        },
        "reconciliation": {
            "queries": {
                "1": {
                    "current_authority_at_receipt": True,
                    "receipt": {"observations": [{"status": "confirmed", "proof": proofs[1]}]},
                }
            }
        },
    }
    use = {"revoked": True, "controls": {"unit-revoke": delivery}}
    preparation = {
        "preparation": {"inputs": inputs},
        "controls": {
            "unit-probe": {"receipt": {"status": "available", "observations": observations}}
        },
    }
    return use, preparation, delivery


@pytest.mark.parametrize(
    "mutation", ["missing_provider", "late_authority", "other_instance", "duplicate_provider"]
)
def test_aggregate_revoke_never_discharges_independent_original_providers(mutation: str) -> None:
    """Require each confirmed provider to match original availability."""
    use, preparation, delivery = original_use()
    assert _use_revoked(use, preparation)
    assert delivery["receipt"]["status"] == "unknown"
    if mutation == "missing_provider":
        delivery["reconciliation"] = None
    elif mutation == "late_authority":
        delivery["reconciliation"]["queries"]["1"]["current_authority_at_receipt"] = False
    elif mutation == "duplicate_provider":
        delivery["receipt"]["providers"].append(copy.deepcopy(delivery["receipt"]["providers"][0]))
    else:
        proof = delivery["receipt"]["providers"][0]
        proof["provider_instance_id"] = proof["original_receipt"]["provider_instance_id"] = (
            "unit-other-instance"
        )
        proof["receipt_digest"] = digest(canonical(proof["original_receipt"]))
    assert not _use_revoked(use, preparation)


def test_upload_retirement_cannot_replace_original_completed_receipt() -> None:
    """Reject recomputed retirement that changes its original byte/source receipt."""
    upload = {
        "upload_id": "unit-upload",
        "principal_id": "unit-uploader",
        "tenant_id": "unit-tenant",
        "original_certificate": "explicit-unit-loopback",
        "admitted_at_ms": 1,
        "deadline_ms": 10,
        "phase": "started",
        "content_digest": None,
        "byte_size": None,
        "prepared_at_ms": None,
        "completed_at_ms": None,
    }
    prepared = upload | {
        "phase": "prepared",
        "content_digest": digest(b"unit-bytes"),
        "byte_size": 10,
        "prepared_at_ms": 2,
    }
    completed = prepared | {"phase": "completed", "completed_at_ms": 3}
    receipt: dict[str, Any] = {
        "schema": "roboguide.artifact-upload-retirement/v0.1",
        "command_id": "unit-retire",
        "store_owner_id": "artifact-store:" + "4" * 64,
        "upload": completed,
        "principal_id": "unit-admin",
        "tenant_id": "unit-tenant",
        "certificate_digest": None,
        "canonical_grant_json": "{}",
        "admitted_at_ms": 4,
        "reason": "unit-release",
    }
    rows = [
        {
            "payload_schema": "domain.EventPayload.json/v23",
            "payload_json": json.dumps({"ArtifactUploadTransition": {"record": value}}),
        }
        for value in (upload, prepared, completed)
    ]
    rows.append(
        {
            "payload_schema": "domain.EventPayload.json/v31",
            "payload_json": json.dumps({"ArtifactUploadRetired": {"receipt": receipt}}),
        }
    )
    originals, retired = upload_history(rows)
    assert originals["unit-upload"] == completed and retired["unit-upload"] == receipt
    changed = copy.deepcopy(receipt)
    changed["upload"]["prepared_at_ms"] = 1
    rows[-1]["payload_json"] = json.dumps({"ArtifactUploadRetired": {"receipt": changed}})
    with pytest.raises(ValueError, match="complete original"):
        upload_history(rows)
