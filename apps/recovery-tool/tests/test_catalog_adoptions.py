"""Synthetic adoption parser faults; actual old producers have their own native gate."""

from __future__ import annotations

import copy
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery import catalog_adoptions, catalog_references
from roboguide_recovery.bundle import Budget
from roboguide_recovery.snapshot_references import canonical, digest
from test_catalog_references import catalog as catalog
from test_retained_artifacts import retained as retained


@pytest.fixture
def adopted(
    catalog: tuple[Path, Path, dict[str, Any], dict[str, Any]],
) -> tuple[Path, dict[str, Any]]:
    """Build explicit bounded SQL parser evidence without a real publisher, grant or OS identity."""
    _, database, _, reference = catalog
    reference = copy.deepcopy(reference)
    with sqlite3.connect(database) as connection:
        raw = connection.execute(
            "SELECT payload_json FROM events WHERE payload_schema='domain.EventPayload.json/v17'"
        ).fetchone()[0]
        publication = {
            "sequence": 1,
            "event_id": "unit-publication",
            "timestamp_ms": 1,
            "correlation_id": "unit-catalog",
            "causation_id": None,
            "payload_schema": "domain.EventPayload.json/v17",
            "payload_json": raw,
        }
        authority = {
            "principal_id": reference["principal_id"],
            "tenant_id": reference["tenant_id"],
            "certificate_digest": None,
            "canonical_grant_json": canonical(
                {"scope": "synthetic-current-administrator"}
            ).decode(),
        }
        reference["authority_digest"] = digest(canonical(["unit-adopt", publication, authority]))
        receipt = {
            "schema": "roboguide.artifact-catalog-adoption/v0.1",
            "command_id": "unit-adopt",
            "publication": publication,
            "authority": authority,
            "historical_publisher": None,
            "historical_tenant": None,
            "record": reference,
        }
        connection.executescript(
            "DROP TABLE events; CREATE TABLE events(sequence INTEGER PRIMARY KEY,event_id TEXT,"
            "timestamp_ms INTEGER,correlation_id TEXT,causation_id TEXT,payload_schema TEXT,"
            "payload_json TEXT); CREATE TABLE event_log_metadata(key TEXT PRIMARY KEY,value TEXT);"
            "CREATE TABLE event_archive_operations(command_id TEXT PRIMARY KEY,intent_json TEXT,"
            "state TEXT,receipt_json TEXT); CREATE TABLE event_archive_aborts("
            "command_id TEXT PRIMARY KEY,original_command_id TEXT,"
            "intent_json TEXT,receipt_json TEXT);"
        )
        connection.execute(
            "UPDATE controller_checkpoint SET schema=?", (catalog_adoptions.WRAPPER,)
        )
        connection.executemany(
            "INSERT INTO event_log_metadata VALUES(?,?)",
            [
                ("archive_operations_schema", "roboguide.archive-operations/v0.2"),
                ("archive_authority_id", "controller-store:" + "b" * 64),
            ],
        )
        for sequence, schema, payload in [
            (1, publication["payload_schema"], raw),
            (
                2,
                "domain.EventPayload.json/v29",
                canonical({"ArtifactCatalogReferenceTransition": {"record": reference}}).decode(),
            ),
            (
                3,
                catalog_adoptions.CODEC,
                canonical({"ArtifactCatalogLegacyAdopted": {"receipt": receipt}}).decode(),
            ),
        ]:
            connection.execute(
                "INSERT INTO events VALUES(?,?,?,?,?,?,?)",
                (
                    sequence,
                    "unit-publication" if sequence == 1 else "unit-event-" + str(sequence),
                    1,
                    "unit-catalog",
                    None,
                    schema,
                    payload,
                ),
            )
    return database, receipt


def scan(database: Path, schema: str = catalog_adoptions.SCHEMA) -> None:
    """Invoke the real bounded recovery reducer; parsing alone proves no administrator authority."""
    catalog_references._controller(
        database, schema, Budget({"budget_seconds": 30, "max_bytes": 1024 * 1024, "max_files": 256})
    )


def test_current_adoption_retains_original_raw_envelope_and_old_profile_refuses(
    adopted: tuple[Path, dict[str, Any]],
) -> None:
    """Complete rows succeed; an old profile cannot ignore adoption authority."""
    database, _ = adopted
    scan(database)
    with pytest.raises(ValueError, match="recovery v0.15"):
        scan(database, "roboguide.recovery-profile/v0.14")
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM events WHERE payload_schema=?", (catalog_adoptions.CODEC,))
    with pytest.raises(ValueError, match="mandatory original adoption receipt"):
        scan(database)


@pytest.mark.parametrize("field", ["event_id", "sequence", "payload_json", "timestamp_ms"])
def test_rehashed_receipt_cannot_replace_the_actual_original_publication(
    adopted: tuple[Path, dict[str, Any]], field: str
) -> None:
    """Even a new canonical identity cannot substitute a different source envelope or raw string."""
    database, receipt = adopted
    receipt = copy.deepcopy(receipt)
    receipt["publication"][field] = (
        receipt["publication"][field] + 1
        if field in {"sequence", "timestamp_ms"}
        else receipt["publication"][field] + " "
    )
    receipt["record"]["authority_digest"] = digest(
        canonical([receipt["command_id"], receipt["publication"], receipt["authority"]])
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE events SET payload_json=? WHERE sequence=2",
            (
                canonical(
                    {"ArtifactCatalogReferenceTransition": {"record": receipt["record"]}}
                ).decode(),
            ),
        )
        connection.execute(
            "UPDATE events SET payload_json=? WHERE sequence=3",
            (canonical({"ArtifactCatalogLegacyAdopted": {"receipt": receipt}}).decode(),),
        )
    with pytest.raises(ValueError, match="original legacy Catalog adoption input/history changed"):
        scan(database)


@pytest.mark.parametrize("field", ["historical_publisher", "historical_tenant"])
def test_missing_historical_attribution_cannot_be_filled_by_the_current_admin(
    adopted: tuple[Path, dict[str, Any]], field: str
) -> None:
    """A current retention decision never manufactures the unavailable old publisher or tenant."""
    database, receipt = adopted
    receipt = copy.deepcopy(receipt)
    receipt[field] = "invented-historical-identity"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE events SET payload_json=? WHERE sequence=3",
            (canonical({"ArtifactCatalogLegacyAdopted": {"receipt": receipt}}).decode(),),
        )
    with pytest.raises(ValueError, match="input/history changed"):
        scan(database)
