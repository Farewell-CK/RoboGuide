"""Preserve bare historical Memory publications without manufacturing retained-owner authority."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery import backup, restore, verify
from roboguide_recovery.controller_metrics import checksum as metrics_checksum
from roboguide_recovery.controller_values import digest, install_functions
from test_controller_values import encoded
from test_legacy_artifact_v32 import legacy as legacy


def append_event(database: Path, payload: dict[str, Any], schema: str) -> None:
    """Append an actual inline source and rebuild the exact protected projection test contract."""
    with sqlite3.connect(database) as connection:
        install_functions(connection)
        sequence = connection.execute(
            "SELECT max(sequence)+1 FROM controller_v3_events"
        ).fetchone()[0]
        raw = encoded(payload)
        envelope = {
            "sequence": sequence,
            "event_id": f"original-{sequence}",
            "timestamp_ms": 10 + sequence,
            "correlation_id": "original-publisher",
            "causation_id": None,
            "payload_schema": schema,
            "payload_json": raw,
        }
        connection.execute(
            "INSERT INTO controller_v3_events VALUES(?,?,?,?,?,?,?,NULL,NULL,NULL,?)",
            (*envelope.values(), digest(raw)),
        )
        data = encoded(envelope)
        connection.execute("INSERT INTO catalog_recovery_events VALUES(?,?)", (sequence, data))
        head_raw = connection.execute("SELECT data FROM catalog_recovery_head").fetchone()[0]
        head = json.loads(head_raw)
        chain = hashlib.sha256()
        chain.update(head["digest"].encode())
        chain.update(len(data.encode()).to_bytes(8, "little"))
        chain.update(data.encode())
        head.update(
            through_sequence=sequence,
            timestamp_high_water=10 + sequence,
            records=head["records"] + 1,
            bytes=head["bytes"] + len(data.encode()),
            digest="sha256:" + chain.hexdigest(),
        )
        encoded_head = encoded(head)
        connection.execute(
            "UPDATE catalog_recovery_head SET data=?,digest=?", (encoded_head, digest(encoded_head))
        )
        connection.execute(
            "UPDATE event_log_metadata SET value=? WHERE key='next_event_number'", (str(sequence),)
        )
        metrics = json.loads(
            connection.execute("SELECT data_json FROM telemetry_storage_metrics").fetchone()[0]
        )
        metrics.update(
            appended_events_total=sequence,
            hot_events=sequence,
            last_appended_event_sequence=sequence,
        )
        metrics_raw = encoded(metrics)
        connection.execute(
            "UPDATE telemetry_storage_metrics SET data_json=?,digest=?",
            (metrics_raw, metrics_checksum(metrics_raw)),
        )
        through, records, edges, gaps, edge_hash, gap_hash, unresolved = connection.execute(
            "SELECT through_sequence,records_count,edges_count,gaps_count,edges_digest,"
            "gaps_digest,archived_prefix_unresolved FROM retention_reference_head"
        ).fetchone()
        require_head = [sequence, records, edges, gaps, edge_hash, gap_hash, bool(unresolved)]
        assert through == sequence - 1
        connection.execute(
            "UPDATE retention_reference_head SET through_sequence=?,head_digest=?",
            (sequence, digest(encoded(require_head))),
        )


@pytest.fixture
def published(
    legacy: tuple[dict[str, Any], Path, bytes],
) -> tuple[dict[str, Any], Path, bytes, dict[str, Any]]:
    """Model the actual field's v17 bare Memory manifest, with independently hashable CAS bytes."""
    profile, blob, _ = legacy
    blob.unlink()
    payload = b"synthetic origin fixture; no field coordinates or credentials".ljust(1302, b".")
    checksum = "sha256:" + hashlib.sha256(payload).hexdigest()
    blob = blob.parents[1] / checksum[7:9] / checksum[7:]
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(payload)
    database = Path(profile["components"][0]["root"]) / "controller.sqlite3"
    for index in range(8):
        content = payload if index == 0 else f"synthetic original upload {index}".encode()
        checksum = "sha256:" + hashlib.sha256(content).hexdigest()
        target = blob.parents[1] / checksum[7:9] / checksum[7:]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        upload = {
            "upload_id": f"original-upload-{index}",
            "principal_id": "unit-original-publisher",
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
            "content_digest": checksum,
            "byte_size": len(content),
            "prepared_at_ms": 2,
        }
        for state in (upload, prepared, prepared | {"phase": "completed", "completed_at_ms": 3}):
            append_event(
                database,
                {"ArtifactUploadTransition": {"record": state}},
                "domain.EventPayload.json/v23",
            )
    manifest = {
        "schema": "roboguide.memory-manifest/v0.1",
        "selector": {"memory_id": "dog2-origin-field-20261106", "revision_id": "r1"},
        "kind": "spatial",
        "provider_id": "reported-origins",
        "owner": {"owner": "robo_guide", "component": "original-memory-recorder"},
        "scope": {"kind": "global"},
        "visibility": "exchangeable",
        "payload_schema": "original.origin/v0.1",
        "media_type": "application/json",
        "artifact": {
            "content_digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
            "byte_size": len(payload),
        },
        "source_mission_id": None,
        "source_execution_id": None,
        "source_task_ref": None,
        "created_at": 12,
    }
    append_event(
        database,
        {"MemoryManifestPublished": {"manifest": manifest}},
        "domain.EventPayload.json/v17",
    )
    return profile, blob, payload, manifest


def replace_publication(database: Path, manifest: dict[str, Any]) -> None:
    """Change a test source and its protected digest together to exercise semantic rejection."""
    with sqlite3.connect(database) as connection:
        raw = encoded({"MemoryManifestPublished": {"manifest": manifest}})
        connection.execute(
            "UPDATE controller_v3_events SET payload_json=?,payload_digest=? WHERE sequence=26",
            (raw, digest(raw)),
        )
        envelope = json.loads(
            connection.execute(
                "SELECT data FROM catalog_recovery_events WHERE sequence=26"
            ).fetchone()[0]
        )
        envelope["payload_json"] = raw
        connection.execute(
            "UPDATE catalog_recovery_events SET data=? WHERE sequence=26", (encoded(envelope),)
        )
        head = json.loads(
            connection.execute("SELECT data FROM catalog_recovery_head").fetchone()[0]
        )
        chain, size = digest("roboguide.catalog-recovery-checkpoint/v0.1"), 0
        for (data,) in connection.execute(
            "SELECT data FROM catalog_recovery_events ORDER BY sequence"
        ):
            chain = (
                "sha256:"
                + hashlib.sha256(
                    chain.encode() + len(data.encode()).to_bytes(8, "little") + data.encode()
                ).hexdigest()
            )
            size += len(data.encode())
        head.update(digest=chain, bytes=size)
        encoded_head = encoded(head)
        connection.execute(
            "UPDATE catalog_recovery_head SET data=?,digest=?", (encoded_head, digest(encoded_head))
        )


def test_complete_bare_catalog_backup_restore_retains_original_bytes_without_owner(
    published: tuple[dict[str, Any], Path, bytes, dict[str, Any]], tmp_path: Path
) -> None:
    """A current wrapper may preserve an unadopted historical publication and its exact content."""
    profile, blob, payload, manifest = published
    original = Path(profile["components"][0]["root"]) / "controller.sqlite3"
    with sqlite3.connect(original) as connection:
        row = connection.execute(
            "SELECT * FROM catalog_recovery_events WHERE sequence=26"
        ).fetchone()
    receipt = backup(profile, tmp_path / "bundle", "bare-catalog-backup")
    verify(tmp_path / "bundle", receipt["manifest_digest"])
    restore(
        tmp_path / "bundle",
        receipt["manifest_digest"],
        tmp_path / "restored",
        "bare-catalog-restore",
    )
    recovered = tmp_path / "restored/stores"
    with sqlite3.connect(recovered / "controller/controller.sqlite3") as connection:
        assert (
            connection.execute("SELECT * FROM catalog_recovery_events WHERE sequence=26").fetchone()
            == row
        )
        assert (
            json.loads(json.loads(row[1])["payload_json"])["MemoryManifestPublished"]["manifest"]
            == manifest
        )
        assert connection.execute("SELECT count(*) FROM catalog_recovery_events").fetchone() == (
            25,
        )
        assert connection.execute(
            "SELECT count(*) FROM catalog_recovery_events WHERE "
            "json_extract(json_extract(data,'$.payload_json'),"
            "'$.ArtifactUploadTransition') IS NOT NULL"
        ).fetchone() == (24,)
    assert (recovered / "artifact" / blob.relative_to(blob.parents[3])).read_bytes() == payload
    assert not (recovered / "artifact/.retention-owner.json").exists()
    assert not (recovered / "artifact/maintenance-v1").exists()


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "bytes",
        "size",
        "qualified",
        "semantic-owner",
        "bare-map",
        "mixed-reference",
        "upload-retirement",
        "republication",
        "projection-source",
    ],
)
def test_legacy_declaration_never_replaces_missing_original_catalog_proof(
    published: tuple[dict[str, Any], Path, bytes, dict[str, Any]], tmp_path: Path, fault: str
) -> None:
    """Refuse absent bytes, new ownership, mixed missing references and altered original history."""
    profile, blob, _, manifest = published
    database = Path(profile["components"][0]["root"]) / "controller.sqlite3"
    if fault == "missing":
        blob.unlink()
    elif fault == "bytes":
        blob.write_bytes(b"different bytes")
    elif fault in {"size", "qualified", "semantic-owner", "republication"}:
        changed = copy.deepcopy(manifest)
        if fault == "size":
            changed["artifact"]["byte_size"] += 1
        elif fault == "qualified":
            changed["artifact"]["store_owner_id"] = "artifact-store:" + "a" * 64
        elif fault == "semantic-owner":
            changed["owner"]["store_owner_id"] = "artifact-store:" + "a" * 64
        else:
            changed["provider_id"] = "different-original-provider"
        if fault == "republication":
            append_event(
                database,
                {"MemoryManifestPublished": {"manifest": changed}},
                "domain.EventPayload.json/v17",
            )
        else:
            replace_publication(database, changed)
    elif fault == "mixed-reference":
        reference = {
            "schema": "roboguide.artifact-catalog-reference/v0.1",
            "selector": {"memory_id": "other", "revision_id": "r1"},
            "tenant_id": "unit",
            "principal_id": "unit",
            "authority_digest": digest("unit"),
            "generation": 1,
            "admitted_at_ms": 13,
            "dependencies": [],
            "retirement": None,
        }
        append_event(
            database,
            {"ArtifactCatalogReferenceTransition": {"record": reference}},
            "domain.EventPayload.json/v29",
        )
    elif fault == "upload-retirement":
        append_event(
            database, {"ArtifactUploadRetired": {"receipt": {}}}, "domain.EventPayload.json/v31"
        )
    elif fault == "bare-map":
        append_event(
            database,
            {"MapArtifactPublished": {"manifest": {"map_id": "legacy-map", "revision_id": "r1"}}},
            "domain.EventPayload.json/v11",
        )
    else:
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE controller_v3_events SET event_id='changed-original' WHERE sequence=26"
            )
    with pytest.raises((ValueError, OSError)):
        backup(profile, tmp_path / "refused", "legacy-refused")
    assert not (tmp_path / "refused").exists()
