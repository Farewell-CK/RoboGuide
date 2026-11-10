"""Recover explicit legacy Artifact bytes under real writer leases without invented owners."""

from __future__ import annotations

import copy
import fcntl
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery import artifact_layout, backup, restore, verify
from roboguide_recovery.artifact_source_references import validate_bundle as validate_inputs
from roboguide_recovery.bundle import Budget
from roboguide_recovery.controller_storage import VALUES_PROFILE
from roboguide_recovery.profile import validate as validate_profile
from roboguide_recovery.snapshot_references import digest
from test_capacity_recovery import wrapper
from test_catalog_references import catalog as catalog
from test_controller_values import database, encoded
from test_retained_artifacts import retained as retained


@pytest.fixture
def legacy(tmp_path: Path) -> tuple[dict[str, Any], Path, bytes]:
    """Create actual original bare blobs and SQL without a retained-owner adoption or service."""
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    (artifact / ".writer.lock").touch()
    (artifact / "staging").mkdir()
    payload = b"original legacy artifact bytes\x00\xff"
    checksum = digest(payload)
    blob = artifact / "blobs/sha256" / checksum[7:9] / checksum[7:]
    blob.parent.mkdir(parents=True)
    blob.write_bytes(payload)
    controller = tmp_path / "controller"
    controller.mkdir()
    (controller / "controller.sqlite3.writer.lock").touch()
    with database(controller / "controller.sqlite3", 3) as connection:
        checkpoint = wrapper()
        connection.execute(
            "INSERT INTO controller_checkpoint VALUES(1,1,?,?)",
            (checkpoint["schema"], encoded(checkpoint)),
        )
        connection.executescript(
            "CREATE TABLE event_archive_operations(command_id TEXT,intent_json TEXT,state TEXT,"
            "receipt_json TEXT);"
            "CREATE TABLE event_archive_aborts(command_id TEXT,original_command_id TEXT,"
            "intent_json TEXT,receipt_json TEXT);"
            "INSERT INTO event_log_metadata VALUES('archive_operations_schema',"
            "'roboguide.archive-operations/v0.2');"
        )
        connection.execute(
            "INSERT INTO event_log_metadata VALUES('archive_authority_id',?)",
            ("controller-store:" + "a" * 64,),
        )
    profile: dict[str, Any] = {
        "schema": VALUES_PROFILE,
        "profile_id": "actual-legacy-artifact",
        "revision": 1,
        "max_bytes": 16 * 1024 * 1024,
        "max_files": 128,
        "budget_seconds": 30,
        "artifact_replica_sets": [],
        "components": [
            {
                "component_id": "controller",
                "kind": "controller",
                "root": str(controller),
                "database": "controller.sqlite3",
                "trees": [],
                "depends_on": ["artifact"],
                "storage_schema": "roboguide.controller-storage/v3",
            },
            {
                "component_id": "artifact",
                "kind": "artifact",
                "root": str(artifact),
                "database": None,
                "trees": [],
                "depends_on": [],
                "storage_schema": artifact_layout.LEGACY_STORAGE,
            },
        ],
    }
    return profile, blob, payload


def test_legacy_bytes_keep_original_layout_without_fabricating_owner(
    legacy: tuple[dict[str, Any], Path, bytes], tmp_path: Path
) -> None:
    """Restore original bare bytes and an independent lease without creating owner authority."""
    profile, blob, payload = legacy
    original = {
        str(path.relative_to(blob.parents[3])): path.read_bytes()
        for path in blob.parents[3].rglob("*")
        if path.is_file()
    }
    receipt = backup(profile, tmp_path / "bundle", "original-backup")
    manifest = verify(tmp_path / "bundle", receipt["manifest_digest"])
    assert manifest["artifact_snapshot_operations"] == []
    restore(
        tmp_path / "bundle", receipt["manifest_digest"], tmp_path / "recovered", "original-restore"
    )
    recovered = tmp_path / "recovered/stores/artifact"
    assert (recovered / blob.relative_to(blob.parents[3])).read_bytes() == payload
    assert (recovered / ".writer.lock").is_file()
    assert not (recovered / ".retention-owner.json").exists()
    assert not (recovered / "objects-v1").exists()
    assert not (recovered / "maintenance-v1").exists()
    assert {
        str(path.relative_to(blob.parents[3])): path.read_bytes()
        for path in blob.parents[3].rglob("*")
        if path.is_file()
    } == original


def test_live_independent_legacy_writer_blocks_backup(
    legacy: tuple[dict[str, Any], Path, bytes], tmp_path: Path
) -> None:
    """Controller inactivity cannot substitute for owning the actual independent Artifact lease."""
    profile, blob, _ = legacy
    with (blob.parents[3] / ".writer.lock").open("rb") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="still active"):
            backup(profile, tmp_path / "refused", "locked")
        assert not (tmp_path / "refused").exists()


@pytest.mark.parametrize(
    "marker",
    [
        ".retention-owner.json",
        "objects-v1",
        "maintenance-v1",
        "reference-authorities-v1",
        "reclamation-v1",
        ".retention-intent.json",
    ],
)
def test_retained_generation_markers_cannot_hide_behind_legacy_declaration(
    legacy: tuple[dict[str, Any], Path, bytes], tmp_path: Path, marker: str
) -> None:
    """An incomplete retained upgrade never becomes a fresh legacy source through profile choice."""
    profile, blob, _ = legacy
    (blob.parents[3] / marker).write_bytes(b"{}")
    with pytest.raises(ValueError, match="generation marker"):
        backup(profile, tmp_path / "refused", "wrong-generation")


@pytest.mark.parametrize("damage", ["bytes", "filename", "shard", "symlink"])
def test_original_blob_hash_and_paths_are_checked(
    legacy: tuple[dict[str, Any], Path, bytes], tmp_path: Path, damage: str
) -> None:
    """Accept legacy CAS only with actual bytes and the exact original path structure."""
    profile, blob, _ = legacy
    if damage == "bytes":
        blob.write_bytes(b"altered")
    elif damage == "filename":
        blob.rename(blob.with_name("a" * 64))
    elif damage == "shard":
        destination = blob.parent.with_name("bad")
        blob.parent.rename(destination)
    else:
        actual = tmp_path / "elsewhere"
        blob.rename(actual)
        blob.symlink_to(actual)
    with pytest.raises((ValueError, OSError)):
        backup(profile, tmp_path / "refused", "wrong-blob")


@pytest.mark.parametrize("damage", ["old-profile", "missing-binding", "second-controller"])
def test_explicit_new_profile_and_unique_original_controller_binding_are_required(
    legacy: tuple[dict[str, Any], Path, bytes], tmp_path: Path, damage: str
) -> None:
    """Require the new profile and exactly one explicit original Controller binding."""
    profile, _, _ = legacy
    changed = copy.deepcopy(profile)
    if damage == "old-profile":
        changed["schema"] = "roboguide.recovery-profile/v0.31"
    elif damage == "missing-binding":
        changed["components"][0]["depends_on"] = []
    else:
        extra = copy.deepcopy(changed["components"][0])
        extra["component_id"] = "other-controller"
        extra["root"] = str(tmp_path / "other-controller")
        changed["components"].append(extra)
    with pytest.raises(ValueError):
        validate_profile(changed)


def test_missing_copied_blob_is_not_filled_from_original_source(
    legacy: tuple[dict[str, Any], Path, bytes], tmp_path: Path
) -> None:
    """Bundle verification requires its own original blob even when the live source still has it."""
    profile, blob, _ = legacy
    receipt = backup(profile, tmp_path / "bundle", "original")
    (tmp_path / "bundle/stores/artifact" / blob.relative_to(blob.parents[3])).unlink()
    assert blob.is_file()
    with pytest.raises((ValueError, OSError)):
        verify(tmp_path / "bundle", receipt["manifest_digest"])


def test_standalone_bare_verifier_reference_requires_its_explicit_legacy_blob(
    legacy: tuple[dict[str, Any], Path, bytes], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Standalone v0.32 cannot skip original bare-byte checks as if a Replica Root owned them."""
    profile, blob, payload = legacy
    root = tmp_path / "copied"
    target = root / "stores/controller"
    target.mkdir(parents=True)
    shutil.copyfile(
        Path(profile["components"][0]["root"]) / "controller.sqlite3", target / "controller.sqlite3"
    )
    evidence = {"artifact_digest": digest(payload), "artifact_size_bytes": len(payload)}
    raw = encoded({"SemanticVerifierEvidenceAdmitted": {"receipt": {"evidence": evidence}}})
    row = {
        "sequence": 2,
        "event_id": "verifier",
        "timestamp_ms": 20,
        "correlation_id": "test",
        "causation_id": None,
        "payload_schema": "domain.EventPayload.json/v1",
        "payload_json": raw,
    }
    with sqlite3.connect(target / "controller.sqlite3") as connection:
        connection.execute("INSERT INTO catalog_recovery_events VALUES(2,?)", (encoded(row),))
    from roboguide_recovery import artifact_source_references

    # The complete bundle path separately checks the original projection head.
    files = {
        "stores/artifact/" + str(blob.relative_to(blob.parents[3])).replace("\\", "/"): {
            "kind": "content",
            "content_digest": digest(payload),
            "byte_size": len(payload),
        }
    }
    monkeypatch.setattr(artifact_source_references, "catalog_rows", lambda *_args: [row])
    validate_inputs(root, profile["components"], files, Budget(profile), VALUES_PROFILE)
    with pytest.raises(ValueError, match="legacy Artifact input"):
        validate_inputs(root, profile["components"], {}, Budget(profile), VALUES_PROFILE)


@pytest.mark.parametrize("placement", ["prepared-blob", "prepared-staging", "completed-blob"])
def test_standalone_original_upload_retains_its_exact_legacy_input(
    legacy: tuple[dict[str, Any], Path, bytes],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    placement: str,
) -> None:
    """Require original upload bytes in the bound legacy store, including prepared staging."""
    from roboguide_recovery import artifact_source_references

    profile, blob, payload = legacy
    root = tmp_path / "upload-copy"
    target = root / "stores/controller"
    target.mkdir(parents=True)
    shutil.copyfile(
        Path(profile["components"][0]["root"]) / "controller.sqlite3", target / "controller.sqlite3"
    )
    upload = {
        "upload_id": "original-upload",
        "principal_id": "unit-uploader",
        "tenant_id": "unit-tenant",
        "original_certificate": "unit-loopback",
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
        "content_digest": digest(payload),
        "byte_size": len(payload),
        "prepared_at_ms": 2,
    }
    originals = [upload, prepared]
    if placement.startswith("completed"):
        originals.append(prepared | {"phase": "completed", "completed_at_ms": 3})
    rows = [
        {
            "payload_schema": "domain.EventPayload.json/v23",
            "payload_json": encoded({"ArtifactUploadTransition": {"record": value}}),
        }
        for value in originals
    ]
    name = (
        "staging/original-upload.partial"
        if placement.endswith("staging")
        else blob.relative_to(blob.parents[3]).as_posix()
    )
    record = {"kind": "content", "content_digest": digest(payload), "byte_size": len(payload)}
    files = {"stores/artifact/" + name: record}
    monkeypatch.setattr(artifact_source_references, "catalog_rows", lambda *_args: rows)
    validate_inputs(root, profile["components"], files, Budget(profile), VALUES_PROFILE)
    with pytest.raises(ValueError, match="legacy Artifact input"):
        validate_inputs(root, profile["components"], {}, Budget(profile), VALUES_PROFILE)
    record["byte_size"] = len(payload) + 1
    with pytest.raises(ValueError, match="legacy Artifact input"):
        validate_inputs(root, profile["components"], files, Budget(profile), VALUES_PROFILE)


def test_bare_equal_bytes_cannot_satisfy_an_original_qualified_catalog_owner(
    catalog: tuple[Path, Path, dict[str, Any], dict[str, Any]], tmp_path: Path
) -> None:
    """Keep an original owner-qualified reference unresolved despite equal legacy CAS bytes."""
    from roboguide_recovery import catalog_references

    artifact, controller, profile, reference = catalog
    profile = copy.deepcopy(profile)
    profile["schema"] = VALUES_PROFILE
    for component in profile["components"]:
        component["storage_schema"] = (
            artifact_layout.LEGACY_STORAGE
            if component["kind"] == "artifact"
            else "roboguide.controller-storage/legacy"
        )
    (artifact / "blobs").unlink()
    (artifact / ".retention-owner.json").unlink()
    (artifact / "objects-v1").rename(artifact / "blobs")
    with sqlite3.connect(controller) as connection:
        connection.executescript(
            "ALTER TABLE controller_checkpoint ADD COLUMN event_sequence INTEGER DEFAULT 0;"
            "CREATE TABLE controller_checkpoint_head(singleton INTEGER PRIMARY KEY,"
            "base_revision INTEGER,revision INTEGER,event_sequence INTEGER,schema TEXT,"
            "base_digest TEXT,digest TEXT);"
        )
    root = tmp_path / "qualified-bundle"
    shutil.copytree(artifact, root / "stores/artifact")
    (root / "stores/controller").mkdir()
    shutil.copyfile(controller, root / "stores/controller/events.sqlite3")
    dependency = reference["dependencies"][0]
    checksum = dependency["content_digest"]
    files = {
        "stores/controller/events.sqlite3": {"kind": "sqlite"},
        f"stores/artifact/blobs/sha256/{checksum[7:9]}/{checksum[7:]}": {
            "kind": "content",
            "content_digest": checksum,
            "byte_size": dependency["byte_size"],
        },
    }
    artifact_layout.validate_legacy(artifact)
    with pytest.raises(ValueError, match="actual Artifact owner is absent"):
        catalog_references.validate(
            root, profile["components"], files, Budget(profile), VALUES_PROFILE
        )
