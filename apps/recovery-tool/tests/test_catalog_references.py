"""Check reference closure with explicit parser fixtures; native gates prove real producers."""

from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery.bundle import Budget, backup, canonical, digest, restore, verify
from test_retained_artifacts import retained as retained


def test_prepared_wrapper_requires_explicit_profile_and_refuses_unverified_history(
    catalog: tuple[Path, Path, dict[str, Any], dict[str, Any]],
) -> None:
    """Do not publish a bundle by ignoring new original recovery authority or an old profile."""
    from contextlib import closing

    from roboguide_recovery import catalog_references

    _, database, profile, _ = catalog
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "UPDATE controller_checkpoint SET schema='roboguide.controller-checkpoint/v53'"
        )
        connection.commit()
    budget = Budget(profile)
    with pytest.raises(ValueError, match="explicit recovery v0.18"):
        catalog_references._controller(database, profile["schema"], budget)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO events(payload_schema,payload_json) VALUES(?,?)",
            ("domain.EventPayload.json/v32", '{"ArtifactUploadRecoveryTransition":{}}'),
        )
        connection.commit()
    with pytest.raises(ValueError, match="parent8/Root4"):
        catalog_references._controller(database, "roboguide.recovery-profile/v0.18", budget)


@pytest.fixture
def catalog(
    retained: tuple[Path, dict[str, Any], str],
    tmp_path: Path,
) -> tuple[Path, Path, dict[str, Any], dict[str, Any]]:
    """Add closed authority-contract SQL around actual bytes, without Node or device records."""
    artifact_root, profile, checksum = retained
    profile = copy.deepcopy(profile)
    profile["schema"] = "roboguide.recovery-profile/v0.10"
    controller = tmp_path / "controller"
    controller.mkdir()
    (controller / "events.sqlite3.writer.lock").touch()
    database = controller / "events.sqlite3"
    manifest: dict[str, Any] = {
        "schema": "roboguide.memory-manifest/v0.1",
        "selector": {"memory_id": "source", "revision_id": "one"},
        "kind": "artifact",
        "provider_id": "parser-provider",
        "owner": {"owner": "robo_guide", "component": "parser-fixture"},
        "scope": {"kind": "global"},
        "visibility": "exchangeable",
        "payload_schema": "opaque/parser-fixture/v1",
        "media_type": "application/octet-stream",
        "artifact": {
            "content_digest": checksum,
            "byte_size": (artifact_root / "objects-v1/sha256" / checksum[7:9] / checksum[7:])
            .stat()
            .st_size,
        },
        "source_mission_id": None,
        "source_execution_id": None,
        "source_task_ref": None,
        "created_at": 1,
    }
    reference = {
        "schema": "roboguide.artifact-catalog-reference/v0.1",
        "selector": manifest["selector"],
        "tenant_id": "parser-tenant",
        "principal_id": "parser-publisher",
        "authority_digest": digest(b"explicit parser authority fixture"),
        "generation": 1,
        "admitted_at_ms": 1,
        "dependencies": [
            {
                "kind": "catalog",
                "store_owner_id": "artifact-store:" + "a" * 64,
                "provider_id": manifest["provider_id"],
                "selector": manifest["selector"],
                "manifest_digest": digest(canonical(manifest)),
                "typed_map": None,
                "content_digest": checksum,
                "byte_size": manifest["artifact"]["byte_size"],
            }
        ],
        "retirement": None,
    }
    with sqlite3.connect(database) as connection:
        connection.executescript(
            "CREATE TABLE events(sequence INTEGER PRIMARY KEY, "
            "payload_schema TEXT, payload_json TEXT);"
            "CREATE TABLE controller_checkpoint(singleton INTEGER PRIMARY KEY, "
            "schema TEXT, checkpoint_json TEXT);"
        )
        connection.execute(
            "INSERT INTO controller_checkpoint VALUES(1,?,?)",
            ("roboguide.controller-checkpoint/v48", "{}"),
        )
        for payload_schema, payload in [
            (
                "domain.EventPayload.json/v29",
                {"ArtifactCatalogReferenceTransition": {"record": reference}},
            ),
            ("domain.EventPayload.json/v17", {"MemoryManifestPublished": {"manifest": manifest}}),
        ]:
            connection.execute(
                "INSERT INTO events(payload_schema,payload_json) VALUES(?,?)",
                (payload_schema, canonical(payload).decode()),
            )
    profile["components"].append(
        {
            "component_id": "controller",
            "kind": "controller",
            "root": str(controller),
            "database": database.name,
            "trees": [],
            "depends_on": ["artifact"],
        }
    )
    return artifact_root, database, profile, reference


def test_original_catalog_owner_and_bytes_survive_complete_restore(
    catalog: tuple[Path, Path, dict[str, Any], dict[str, Any]],
    tmp_path: Path,
) -> None:
    """Actual hashable bytes and exact original SQL remain linked across complete cold restore."""
    _, _, profile, reference = catalog
    bundle = tmp_path / "bundle"
    result = backup(profile, bundle, "original")
    verify(bundle, result["manifest_digest"])
    receipt = restore(bundle, result["manifest_digest"], tmp_path / "restored", "restore")
    assert receipt["schema"] == "roboguide.restore-receipt/v0.10"
    restored = tmp_path / "restored/stores/controller/events.sqlite3"
    with sqlite3.connect(restored) as connection:
        raw = connection.execute(
            "SELECT payload_json FROM events WHERE payload_schema='domain.EventPayload.json/v29'"
        ).fetchone()[0]
    assert json.loads(raw)["ArtifactCatalogReferenceTransition"]["record"] == reference
    assert restore(bundle, result["manifest_digest"], tmp_path / "restored", "restore") == receipt


def test_missing_original_content_refuses_before_publication(
    catalog: tuple[Path, Path, dict[str, Any], dict[str, Any]],
    tmp_path: Path,
) -> None:
    """Active original references require content, independently of metadata or success flags."""
    artifact_root, _, profile, reference = catalog
    checksum = reference["dependencies"][0]["content_digest"][7:]
    (artifact_root / "objects-v1/sha256" / checksum[:2] / checksum).unlink()
    with pytest.raises(ValueError, match="exact bundled CAS bytes"):
        backup(profile, tmp_path / "incomplete", "incomplete")
    assert not (tmp_path / "incomplete").exists()


@pytest.mark.parametrize("fault", ["events", "reference", "codec", "owner"])
def test_missing_authority_and_equal_hash_foreign_owner_refuse(
    catalog: tuple[Path, Path, dict[str, Any], dict[str, Any]],
    tmp_path: Path,
    fault: str,
) -> None:
    """Complete bytes cannot replace absent authority, an older codec or another owner's claim."""
    artifact_root, database, profile, _ = catalog
    with sqlite3.connect(database) as connection:
        if fault == "events":
            connection.execute("DROP TABLE events")
        elif fault == "reference":
            connection.execute(
                "DELETE FROM events WHERE payload_schema='domain.EventPayload.json/v29'"
            )
        elif fault == "codec":
            connection.execute("UPDATE events SET payload_schema='domain.EventPayload.json/v28'")
    if fault == "owner":
        for name in ("blobs", ".retention-owner.json"):
            owner = json.loads((artifact_root / name).read_bytes())
            owner["owner_id"] = "artifact-store:" + "b" * 64
            (artifact_root / name).write_bytes(canonical(owner))
    with pytest.raises(ValueError, match="Catalog|authority|owner"):
        backup(profile, tmp_path / "rejected", "rejected")
    assert not (tmp_path / "rejected").exists()


def test_repinned_bundle_cannot_omit_active_content(
    catalog: tuple[Path, Path, dict[str, Any], dict[str, Any]],
    tmp_path: Path,
) -> None:
    """An altered outer manifest pin cannot erase an original active dependency inside SQL."""
    _, _, profile, reference = catalog
    target = tmp_path / "bundle"
    backup(profile, target, "original")
    manifest = json.loads((target / "manifest.json").read_bytes())
    checksum = reference["dependencies"][0]["content_digest"]
    removed = next(row for row in manifest["files"] if row["content_digest"] == checksum)
    (target / removed["path"]).unlink()
    manifest["files"].remove(removed)
    raw = canonical(manifest)
    (target / "manifest.json").write_bytes(raw)
    with pytest.raises(ValueError, match="exact bundled CAS bytes"):
        verify(target, digest(raw))


def test_child_lineage_retains_parent_after_parent_catalog_retirement(
    catalog: tuple[Path, Path, dict[str, Any], dict[str, Any]],
    tmp_path: Path,
) -> None:
    """Only retiring both independent publications permits absent historical parent bytes."""
    artifact_root, database, profile, parent = catalog
    with sqlite3.connect(database) as connection:
        manifest = json.loads(
            connection.execute("SELECT payload_json FROM events WHERE sequence=2").fetchone()[0]
        )["MemoryManifestPublished"]["manifest"]
        child = copy.deepcopy(parent)
        child["selector"] = {"memory_id": "child", "revision_id": "one"}
        child_manifest = copy.deepcopy(manifest)
        child_manifest["selector"] = child["selector"]
        child["dependencies"][0]["selector"] = child["selector"]
        child["dependencies"][0]["manifest_digest"] = digest(canonical(child_manifest))
        lineage = copy.deepcopy(parent["dependencies"][0])
        lineage["kind"] = "lineage"
        child["dependencies"].append(lineage)
        released = copy.deepcopy(parent)
        released["retirement"] = {
            "command_id": "retire-parent",
            "principal_id": "parser-admin",
            "proof_digest": digest(b"explicit parser revoke fixture"),
            "retired_at_ms": 2,
        }
        for payload in [
            {"ArtifactCatalogReferenceTransition": {"record": child}},
            {"MemoryManifestPublished": {"manifest": child_manifest}},
            {"ArtifactCatalogReferenceTransition": {"record": released}},
        ]:
            schema = (
                "domain.EventPayload.json/v29"
                if "ArtifactCatalogReferenceTransition" in payload
                else "domain.EventPayload.json/v17"
            )
            connection.execute(
                "INSERT INTO events(payload_schema,payload_json) VALUES(?,?)",
                (schema, canonical(payload).decode()),
            )
    checksum = parent["dependencies"][0]["content_digest"][7:]
    (artifact_root / "objects-v1/sha256" / checksum[:2] / checksum).unlink()
    with pytest.raises(ValueError, match="exact bundled CAS bytes"):
        backup(profile, tmp_path / "missing-parent", "missing-parent")
    child["retirement"] = {**released["retirement"], "command_id": "retire-child"}
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO events(payload_schema,payload_json) VALUES(?,?)",
            (
                "domain.EventPayload.json/v29",
                canonical({"ArtifactCatalogReferenceTransition": {"record": child}}).decode(),
            ),
        )
    result = backup(profile, tmp_path / "released-history", "released-history")
    verify(tmp_path / "released-history", result["manifest_digest"])
