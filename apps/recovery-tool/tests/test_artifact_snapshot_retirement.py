"""Test metadata retirement recovery with explicit contract fixtures."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery import artifact_snapshot_retirement as retirement
from roboguide_recovery import artifact_snapshots
from roboguide_recovery.snapshot_references import canonical, digest, digest_profile
from test_artifact_gc import retained_journal


def snapshot(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Path]:
    """Declare an expired copy and actual temporary writer files for recovery checks."""
    owner, _ = retained_journal(root)
    header = {
        "schema": "roboguide.artifact-storage/v5",
        "owner_id": owner,
        "source_device": 1,
        "source_inode": 2,
    }
    for name in (
        "staging",
        "objects-v1",
        "maintenance-v1/operations",
        "reference-authorities-v1/controllers",
    ):
        (root / name).mkdir(parents=True, exist_ok=True)
    for name in (
        "blobs",
        ".retention-owner.json",
        "maintenance-v1/identity.json",
        "reference-authorities-v1/identity.json",
        "reclamation-v1/identity.json",
    ):
        (root / name).write_bytes(canonical(header))
    (root / ".writer.lock").touch()
    (root / "reference-authorities-v1/inventory.json").write_bytes(
        canonical(
            {
                "schema": "roboguide.artifact-reference-inventory/v0.1",
                "store_owner_id": owner,
                "authority_ids": [],
            }
        )
    )
    original: dict[str, Any] = {
        "schema": "roboguide.recovery-profile/v0.14",
        "profile_id": "unit-original",
        "revision": 1,
        "max_bytes": 4 * 1024 * 1024,
        "max_files": 128,
        "budget_seconds": 20,
        "artifact_replica_sets": [],
        "components": [
            {
                "component_id": "artifact",
                "kind": "artifact",
                "root": str(root),
                "database": None,
                "trees": [],
                "depends_on": [],
            }
        ],
    }
    current = copy.deepcopy(original)
    current["schema"] = "roboguide.recovery-profile/v0.16"
    current["components"][0]["storage_schema"] = "roboguide.artifact-storage/v5"
    purpose: dict[str, Any] = {
        "command_id": "original-copy",
        "profile_digest": digest_profile(original),
        "component_id": "artifact",
        "destination": str(root.parent / "unpublished-copy"),
        "store_owner_id": owner,
        "kind": "snapshot",
    }
    request: dict[str, Any] = purpose | {
        "schema": "roboguide.artifact-snapshot-intent/v0.1",
        "operation_id": digest(canonical(purpose)),
        "generation": 1,
        "inputs": [],
        "original_uid": os.getuid(),
        "kernel": {
            "boot_id": "unit-original-kernel",
            "time_namespace": "unit-original-namespace",
            "admitted_ns": 10,
            "deadline_ns": 11,
        },
    }
    directory = root / "maintenance-v1/operations" / request["operation_id"][7:]
    directory.mkdir()
    (directory / "intent.json").write_bytes(canonical(request))
    return current, original, request, directory


def test_pending_retirement_observes_then_explicitly_reconciles_original_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retain the first pending fence without inventing a new budget or receipt."""
    current, original, request, directory = snapshot(tmp_path)
    arguments = (
        current,
        original,
        "artifact",
        request["operation_id"],
        digest(canonical(request)),
        "unit-retirement",
        "unit-original-copy-drained",
    )
    original_link = os.link

    def interrupt(*_args: Any, **_kwargs: Any) -> None:
        """Inject a unit publication failure after pending bytes have been fsynced."""
        raise OSError("unit original receipt publication fault")

    monkeypatch.setattr(os, "link", interrupt)
    with pytest.raises(OSError, match="original receipt"):
        retirement.run(*arguments)
    pending = artifact_snapshots._read(directory / "receipt.json.pending")
    monkeypatch.setattr(os, "link", original_link)
    observed = retirement.run(*arguments)
    assert observed["phase"] == "unknown" and observed["receipt"] == pending
    assert not (directory / "receipt.json").exists()
    reconciled = retirement.run(*arguments, reconcile=True)
    assert reconciled["reconciled_metadata_only"] and reconciled["copied_files"] == 0
    assert artifact_snapshots._read(directory / "receipt.json") == pending
    assert artifact_snapshots._read(directory / "intent.json") == request
    assert retirement.run(*arguments)["receipt"] == pending


def test_recomputed_receipt_cannot_omit_original_profile_writer(tmp_path: Path) -> None:
    """Hashing a replacement fence cannot turn an unrelated writer into original drain coverage."""
    current, original, request, _ = snapshot(tmp_path)
    result = retirement.run(
        current,
        original,
        "artifact",
        request["operation_id"],
        digest(canonical(request)),
        "unit-retirement",
        "unit-original-copy-drained",
    )
    receipt = copy.deepcopy(result["receipt"])
    receipt["fence"]["writers"][0]["path"] = str(tmp_path / "unrelated-writer.lock")
    receipt["proof_digest"] = digest(
        canonical(
            {
                "original": request,
                "fence": receipt["fence"],
                "release_command_id": receipt["release_command_id"],
                "reason": receipt["reason"],
            }
        )
    )
    with pytest.raises(ValueError, match="omitted"):
        retirement.validate_receipt(receipt, request)
