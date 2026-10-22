"""Retirement parser counterexamples only; synthetic owners and leases prove no OS authority."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery import archive_retirement as retirement
from roboguide_recovery.bundle import Budget
from roboguide_recovery.snapshot_references import canonical, digest


def put(path: Path, value: object) -> None:
    """Encode explicit contract-fixture metadata without creating deployment facts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value))


@pytest.fixture
def sealed(tmp_path: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    """Create a fully synthetic terminal owner for negative parser and history checks."""
    root = tmp_path / "content"
    owner = {
        "schema": "roboguide.artifact-storage/v3",
        "owner_id": "artifact-store:" + "a" * 64,
        "source_device": 1,
        "source_inode": 2,
    }
    copy_intent: dict[str, Any] = {
        "request": {
            "authority_id": "controller-store:" + "b" * 64,
            "source_owner": "artifact-store:" + "c" * 64,
        },
        "inputs": [{"content_digest": "sha256:" + "d" * 64, "byte_size": 4}],
    }
    publication = {"intent": copy_intent, "target_owner": owner["owner_id"]}
    intent = {
        "schema": "roboguide.archive-recipient-retirement/v0.1",
        "command_id": "unit-retire",
        "original_intent_digest": digest(canonical(copy_intent)),
        "publication_digest": digest(canonical(publication)),
        "source_receipt": digest(canonical(publication)),
        "source_authority": copy_intent["request"]["authority_id"],
        "source_owner": copy_intent["request"]["source_owner"],
        "target_owner": owner["owner_id"],
        "original_owner": owner,
        "inputs": copy_intent["inputs"],
        "principal_uid": 1,
        "reason": "unit parser contract only",
        "profile_digest": "sha256:" + "e" * 64,
        "kernel": {
            "boot_id": "0" * 36,
            "time_namespace": "unit-time-namespace",
            "admitted_ns": 1,
            "deadline_ns": 1000,
        },
        "fence": {
            "pid": 1,
            "start_ticks": 1,
            **{
                key: {"device": 1, "inode": 2}
                for key in (
                    "source_writer",
                    "controller_writer",
                    "archive_writer",
                    "recipient_writer",
                )
            },
        },
    }
    for name in retirement.HEADERS:
        put(root / name, owner | {"schema": retirement.STORAGE})
    put(
        root / "reference-authorities-v1/inventory.json",
        {
            "schema": "roboguide.artifact-reference-inventory/v0.1",
            "store_owner_id": owner["owner_id"],
            "authority_ids": [],
        },
    )
    (root / "reference-authorities-v1/controllers").mkdir()
    (root / "objects-v1").mkdir()
    (root / "staging").mkdir()
    put(
        root
        / "maintenance-v1/operations"
        / digest(canonical(copy_intent))[7:]
        / "archive-reference.json",
        {
            "schema": "roboguide.archive-recipient-reference/v0.1",
            "intent": copy_intent,
            "target_owner": owner["owner_id"],
        },
    )
    put(root / "retirement-v1/intent.json", intent)
    put(root / "retirement-v1/receipt.json", retirement.receipt(intent))
    put(
        root / "retirement-v1/gc-commands.json",
        {"schema": "roboguide.archive-gc-command-head/v0.1", "command_ids": []},
    )
    (root / "retirement-v1/operations").mkdir()
    return root, publication, intent


def budget() -> Budget:
    """Keep synthetic parser scans bounded; this clock grants no command or device authority."""
    return Budget({"budget_seconds": 30, "max_bytes": 1024 * 1024, "max_files": 256})


def test_retired_history_needs_metadata_but_not_deleted_content(
    sealed: tuple[Path, dict[str, Any], dict[str, Any]],
) -> None:
    """A sealed original history may omit bytes, but deleting its tombstone must still refuse."""
    root, publication, original = sealed
    assert retirement.validate_seal(root, publication, budget()) == original
    with pytest.raises(ValueError, match="recovery v0.14"):
        retirement.retired(root, publication, "roboguide.recovery-profile/v0.13", budget())
    (root / "retirement-v1/intent.json").unlink()
    with pytest.raises(FileNotFoundError):
        retirement.validate_seal(root, publication, budget())


@pytest.mark.parametrize(
    "field", ["source_receipt", "target_owner", "inputs", "principal_uid", "kernel", "fence"]
)
def test_changed_original_input_never_grants_retirement(
    sealed: tuple[Path, dict[str, Any], dict[str, Any]], field: str
) -> None:
    """Rewriting the complete receipt hash cannot legitimize a contradictory original input."""
    root, publication, original = sealed
    changed = copy.deepcopy(original)
    changed[field] = None
    put(root / "retirement-v1/intent.json", changed)
    put(root / "retirement-v1/receipt.json", retirement.receipt(changed))
    with pytest.raises((ValueError, TypeError)):
        retirement.validate_seal(root, publication, budget())


def test_another_actual_producer_or_known_consumer_blocks_retired_owner(
    sealed: tuple[Path, dict[str, Any], dict[str, Any]],
) -> None:
    """Require the complete consumer inventory and sole original producer."""
    root, publication, original = sealed
    other = root / "maintenance-v1/operations" / ("f" * 64)
    other.mkdir()
    with pytest.raises(ValueError, match="another or unknown"):
        retirement.validate_seal(root, publication, budget())
    other.rmdir()
    key = "1" * 64
    authority = "controller-store:" + key
    put(
        root / "reference-authorities-v1/controllers" / (key + ".json"),
        {
            "schema": "roboguide.artifact-reference-authority/v0.1",
            "authority_id": authority,
            "store_owner_id": original["target_owner"],
        },
    )
    put(
        root / "reference-authorities-v1/inventory.json",
        {
            "schema": "roboguide.artifact-reference-inventory/v0.1",
            "store_owner_id": original["target_owner"],
            "authority_ids": [authority],
        },
    )
    with pytest.raises(ValueError, match="known Controller consumers"):
        retirement.validate_seal(root, publication, budget())


def test_gc_head_cannot_hide_a_missing_original_command(
    sealed: tuple[Path, dict[str, Any], dict[str, Any]],
) -> None:
    """An empty operations directory is not empty authority after a durable head recorded its ID."""
    root, publication, _ = sealed
    put(
        root / "retirement-v1/gc-commands.json",
        {"schema": "roboguide.archive-gc-command-head/v0.1", "command_ids": ["0" * 64]},
    )
    with pytest.raises(ValueError, match="missing or unaccounted"):
        retirement.validate_seal(root, publication, budget())


def test_partial_owner_seal_cannot_release_missing_content(
    sealed: tuple[Path, dict[str, Any], dict[str, Any]],
) -> None:
    """The tombstone alone does not replace all original terminal version fences."""
    root, publication, original = sealed
    put(root / "maintenance-v1/identity.json", original["original_owner"])
    with pytest.raises(ValueError, match="seal is incomplete"):
        retirement.validate_seal(root, publication, budget())


def test_unknown_gc_journal_remains_separate_from_completed_deletion_claims(
    sealed: tuple[Path, dict[str, Any], dict[str, Any]],
) -> None:
    """Missing a receipt preserves Unknown; a rewritten receipt cannot invent partial deletion."""
    root, publication, original = sealed
    command = {
        "schema": "roboguide.archive-recipient-gc-intent/v0.1",
        "command_id": "unit-gc",
        "retirement_digest": digest(canonical(original)),
        "profile_digest": "sha256:" + "e" * 64,
        "principal_uid": 1,
        "reason": "unit parser only",
        "kernel": original["kernel"],
        "fence": original["fence"],
        "inputs": [
            item | {"present": True, "inode": {"device": 1, "inode": 2}}
            for item in original["inputs"]
        ],
    }
    key = digest(b"unit-gc")[7:]
    directory = root / "retirement-v1/operations" / key
    put(directory / "intent.json", command)
    put(
        root / "retirement-v1/gc-commands.json",
        {"schema": "roboguide.archive-gc-command-head/v0.1", "command_ids": [key]},
    )
    assert retirement.validate_seal(root, publication, budget()) == original
    put(
        directory / "receipt.json",
        {
            "schema": "roboguide.archive-recipient-gc-receipt/v0.1",
            "intent_digest": digest(canonical(command)),
            "deleted": [],
            "already_missing": [],
        },
    )
    with pytest.raises(ValueError, match="actual deletion evidence"):
        retirement.validate_seal(root, publication, budget())


def test_unpublished_gc_head_requires_new_explicit_reconciliation(
    sealed: tuple[Path, dict[str, Any], dict[str, Any]],
) -> None:
    """An orphan original intent remains Unknown and cannot silently become an empty journal."""
    root, publication, original = sealed
    command = {
        "schema": "roboguide.archive-recipient-gc-intent/v0.1",
        "command_id": "unit-gc",
        "retirement_digest": digest(canonical(original)),
        "profile_digest": "sha256:" + "e" * 64,
        "principal_uid": 1,
        "reason": "unit parser only",
        "kernel": original["kernel"],
        "fence": original["fence"],
        "inputs": [item | {"present": False, "inode": None} for item in original["inputs"]],
    }
    put(root / "retirement-v1/operations" / digest(b"unit-gc")[7:] / "intent.json", command)
    with pytest.raises(ValueError, match="missing or unaccounted"):
        retirement.validate_seal(root, publication, budget())
    retirement.validate_gc_history(root, original, budget(), allow_unaccounted=True)
