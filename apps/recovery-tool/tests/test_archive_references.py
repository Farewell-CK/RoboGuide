"""Original directory and reference-authority parser faults; actual producers have a native gate."""

from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery import archive_references, bundle
from roboguide_recovery.snapshot_references import canonical, digest
from test_artifact_snapshots import journal as journal
from test_retained_artifacts import retained as retained


@pytest.fixture
def inventory(journal: tuple[Path, dict[str, Any], str]) -> tuple[Path, dict[str, Any], str]:
    """Extend parser fixtures; their identities do not describe live Controllers or devices."""
    root, profile, checksum = journal
    profile = copy.deepcopy(profile)
    profile["schema"] = archive_references.SCHEMA
    owner = json.loads((root / ".retention-owner.json").read_bytes())
    owner["schema"] = "roboguide.artifact-storage/v3"
    (root / "reference-authorities-v1/controllers").mkdir(parents=True)
    for name in (
        ".retention-owner.json",
        "blobs",
        "maintenance-v1/identity.json",
        "reference-authorities-v1/identity.json",
    ):
        (root / name).write_bytes(canonical(owner))
    (root / "reference-authorities-v1/inventory.json").write_bytes(
        canonical(
            {
                "schema": "roboguide.artifact-reference-inventory/v0.1",
                "store_owner_id": owner["owner_id"],
                "authority_ids": [],
            }
        )
    )
    return root, profile, checksum


def test_original_nested_empty_directories_survive_backup_verify_and_restore(
    inventory: tuple[Path, dict[str, Any], str], tmp_path: Path
) -> None:
    """Preserve empty structures and reject lost original directory evidence."""
    _, profile, _ = inventory
    target = tmp_path / "bundle"
    result = bundle.backup(profile, target, "original")
    manifest = bundle.verify(target, result["manifest_digest"])
    assert manifest["schema"] == "roboguide.recovery-bundle/v0.4"
    required = "stores/artifact/reference-authorities-v1/controllers"
    assert required in manifest["directories"]
    recovered = tmp_path / "restored"
    receipt = bundle.restore(target, result["manifest_digest"], recovered, "restore")
    assert receipt["schema"] == "roboguide.restore-receipt/v0.12"
    assert (recovered / required).is_dir()
    (target / required).rmdir()
    with pytest.raises((ValueError, OSError)):
        bundle.verify(target, result["manifest_digest"])
    assert not (target / required).exists()


def test_known_original_consumer_cannot_disappear_from_the_declared_bundle(
    inventory: tuple[Path, dict[str, Any], str], tmp_path: Path
) -> None:
    """Refuse a complete CAS when an original consumer is absent."""
    root, profile, _ = inventory
    owner = json.loads((root / ".retention-owner.json").read_bytes())
    authority = "controller-store:" + "a" * 64
    original = {
        "schema": "roboguide.artifact-reference-authority/v0.1",
        "authority_id": authority,
        "store_owner_id": owner["owner_id"],
    }
    path = root / "reference-authorities-v1/controllers" / ("a" * 64 + ".json")
    path.write_bytes(canonical(original))
    (root / "reference-authorities-v1/inventory.json").write_bytes(
        canonical(
            {
                "schema": "roboguide.artifact-reference-inventory/v0.1",
                "store_owner_id": owner["owner_id"],
                "authority_ids": [authority],
            }
        )
    )
    target = tmp_path / "refused"
    with pytest.raises(ValueError, match="reference authority is absent"):
        bundle.backup(profile, target, "unknown-original-consumer")
    assert not target.exists() and json.loads(path.read_bytes()) == original
    old = profile | {"schema": "roboguide.recovery-profile/v0.11"}
    with pytest.raises(ValueError, match="owner metadata"):
        bundle.backup(old, tmp_path / "old-refused", "old")


def test_catalog_archive_keeps_complete_original_envelopes_and_refuses_missing_rows(
    tmp_path: Path,
) -> None:
    """Preserve ordered original envelopes and detect missing rows."""
    connection = sqlite3.connect(tmp_path / "fixture.sqlite3")
    connection.executescript(
        "CREATE TABLE event_log_metadata(key TEXT PRIMARY KEY,value TEXT); "
        "CREATE TABLE catalog_archive_events(sequence INTEGER PRIMARY KEY,data TEXT)"
    )
    row = {
        "sequence": 7,
        "event_id": "original-event",
        "timestamp_ms": 3,
        "correlation_id": "original-command",
        "causation_id": None,
        "payload_schema": "domain.EventPayload.json/v29",
        "payload_json": json.dumps({"MemoryManifestPublished": {"manifest": {}}}),
    }
    head = {
        "schema": "roboguide.catalog-archive-checkpoint/v0.1",
        "through_sequence": 9,
        "event_count": 1,
        "digest": digest(canonical([row])),
    }
    connection.executemany(
        "INSERT INTO event_log_metadata VALUES (?,?)",
        [("history_floor", "9"), ("catalog_archive_checkpoint", json.dumps(head))],
    )
    connection.execute("INSERT INTO catalog_archive_events VALUES (?,?)", (7, json.dumps(row)))
    budget = bundle.Budget({"max_bytes": 1024 * 1024, "max_files": 128, "budget_seconds": 30})
    assert archive_references.catalog_history(connection, archive_references.SCHEMA, budget) == [
        row
    ]
    with pytest.raises(ValueError, match="v0.12"):
        archive_references.catalog_history(connection, "roboguide.recovery-profile/v0.11", budget)
    connection.execute("DELETE FROM catalog_archive_events")
    with pytest.raises(ValueError, match="incomplete or changed"):
        archive_references.catalog_history(connection, archive_references.SCHEMA, budget)
    connection.close()


def test_directory_manifest_repin_cannot_hide_a_missing_actual_directory(
    inventory: tuple[Path, dict[str, Any], str], tmp_path: Path
) -> None:
    """Re-pinning file hashes never makes a mandatory original backend directory optional."""
    _, profile, _ = inventory
    target = tmp_path / "bundle"
    result = bundle.backup(profile, target, "original")
    manifest = bundle.verify(target, result["manifest_digest"])
    missing = "stores/artifact/reference-authorities-v1/controllers"
    (target / missing).rmdir()
    manifest["directories"].remove(missing)
    raw = canonical(manifest)
    (target / "manifest.json").write_bytes(raw)
    with pytest.raises((ValueError, OSError)):
        bundle.verify(target, digest(raw))
