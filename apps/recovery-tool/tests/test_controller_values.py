"""Exercise actual v2/v3 SQLite reconstruction, corruption refusal and complete cold bundles."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery import backup, restore, verify
from roboguide_recovery.controller_metrics import SCHEMA as METRICS_SCHEMA
from roboguide_recovery.controller_metrics import checksum as metrics_checksum
from roboguide_recovery.controller_storage import (
    STORAGE_V2,
    STORAGE_V3,
    VALUES_PROFILE,
    actual_format,
    validate,
)
from roboguide_recovery.controller_values import (
    VIEW,
    digest,
    validate_catalog,
    validate_references,
    validate_retention,
    validate_verifier_authorization,
)
from roboguide_recovery.profile import validate as validate_profile
from test_capacity_recovery import wrapper


def encoded(value: Any) -> str:
    """Preserve the declared struct field order and original Unicode in test SQL metadata."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def database(path: Path, version: int) -> sqlite3.Connection:
    """Build actual SQL with empty application authority and one original observation."""
    c = sqlite3.connect(path)
    c.executescript(
        "CREATE TABLE controller_storage_format(version INTEGER PRIMARY KEY,schema TEXT NOT NULL);"
        "CREATE TABLE event_log_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);"
        "CREATE TABLE controller_checkpoint(singleton INTEGER PRIMARY KEY,event_sequence INTEGER,"
        "schema TEXT,checkpoint_json TEXT);"
        "CREATE TABLE controller_checkpoint_head(singleton INTEGER PRIMARY "
        "KEY,base_revision INTEGER,"
        "revision INTEGER,event_sequence INTEGER,schema TEXT,base_digest TEXT,digest TEXT);"
        "CREATE TABLE controller_checkpoint_deltas(revision INTEGER PRIMARY KEY,"
        "previous_revision INTEGER,event_sequence INTEGER,schema TEXT,base_digest TEXT,digest TEXT,"
        "delta_json TEXT);"
        "CREATE TABLE control_command_journal(command_id TEXT PRIMARY KEY,causation_id TEXT,"
        "authority_revision INTEGER,controller_epoch TEXT,plan_digest TEXT,input_digest TEXT,"
        "ownership_digest TEXT,resulting_state_digest TEXT,checkpoint_event_sequence INTEGER,"
        "checkpoint_schema TEXT);"
        "CREATE TABLE service_access_audit(sequence INTEGER PRIMARY KEY,service "
        "TEXT,principal TEXT,"
        "method TEXT,path TEXT,decision TEXT);"
        "CREATE TABLE catalog_recovery_head(singleton INTEGER PRIMARY KEY,data TEXT,digest TEXT);"
        "CREATE TABLE catalog_recovery_events(sequence INTEGER PRIMARY KEY,data TEXT);"
    )
    c.execute("PRAGMA user_version=" + str(version))
    c.execute(
        "INSERT INTO controller_storage_format VALUES(?,?)",
        (version, f"roboguide.controller-storage/v{version}"),
    )
    c.execute("INSERT INTO event_log_metadata VALUES('next_event_number','1')")
    body = '{"温度":1.2500e0,"capture":"原始"}'
    prefix, suffix = '{"StateRecordObserved":{"record":{"value":', "}}}"
    payload = prefix + body + suffix
    if version == 2:
        c.execute(
            "CREATE TABLE controller_v1_events(sequence INTEGER PRIMARY KEY,event_id "
            "TEXT,timestamp_ms INTEGER,correlation_id TEXT,causation_id "
            "TEXT,payload_schema TEXT,payload_json TEXT)"
        )
        c.execute("CREATE VIEW events AS SELECT * FROM controller_v1_events")
        c.execute(
            "INSERT INTO controller_v1_events "
            "VALUES(1,'event',10,'mission',NULL,'domain.EventPayload.json/v1',?)",
            (payload,),
        )
    else:
        c.executescript(
            "CREATE TABLE telemetry_bodies(body_id TEXT PRIMARY KEY,scope_hash "
            "TEXT,value_digest TEXT,byte_size INTEGER,value_json TEXT);"
            "CREATE TABLE controller_v3_events(sequence INTEGER PRIMARY KEY,event_id "
            "TEXT,timestamp_ms INTEGER,correlation_id TEXT,causation_id "
            "TEXT,payload_schema TEXT,payload_json TEXT,body_id TEXT,payload_prefix "
            "TEXT,payload_suffix TEXT,payload_digest TEXT);"
            "CREATE TABLE command_journal_classes(command_id TEXT PRIMARY KEY,kind TEXT);"
            "CREATE TABLE retention_policy(singleton INTEGER PRIMARY KEY,revision "
            "INTEGER,policy_json TEXT,policy_digest TEXT,clock_high_water_ms INTEGER);"
            "CREATE TABLE retention_policy_commands(command_id TEXT PRIMARY KEY,revision "
            "INTEGER UNIQUE,command_json TEXT,command_digest TEXT,receipt_json "
            "TEXT,receipt_digest TEXT);"
        )
        scope, checksum = digest("source-key"), digest(body)
        identity = digest(scope + "\n" + checksum)
        c.execute(
            "INSERT INTO telemetry_bodies VALUES(?,?,?,?,?)",
            (identity, scope, checksum, len(body.encode()), body),
        )
        c.execute(
            "INSERT INTO controller_v3_events "
            "VALUES(1,'event',10,'mission',NULL,'domain.EventPayload.json/v1',NULL,?,?,?,?)",
            (identity, prefix, suffix, digest(payload)),
        )
        c.executescript(
            "CREATE TABLE retention_reference_head(singleton INTEGER PRIMARY KEY,"
            "through_sequence INTEGER,records_count INTEGER,edges_count INTEGER,gaps_count INTEGER,"
            "edges_digest TEXT,gaps_digest TEXT,archived_prefix_unresolved INTEGER,"
            "head_digest TEXT);"
            "CREATE TABLE retention_reference_records(source_sequence INTEGER PRIMARY KEY,"
            "record_digest TEXT);"
            "CREATE TABLE retention_reference_edges(decision_sequence INTEGER,"
            "source_sequence INTEGER,record_digest TEXT);"
            "CREATE TABLE retention_reference_gaps(decision_sequence INTEGER,record_digest TEXT);"
        )
        edges, gaps = (
            digest("roboguide.retention-decision-edges/v0.1"),
            digest("roboguide.retention-decision-gaps/v0.1"),
        )
        reference_head = [1, 1, 0, 0, edges, gaps, False]
        c.execute(
            "INSERT INTO retention_reference_head VALUES(1,1,1,0,0,?,?,0,?)",
            (edges, gaps, digest(encoded(reference_head))),
        )
        c.execute(
            "INSERT INTO retention_reference_records VALUES(1,?)", (digest("original-record"),)
        )
        c.execute(
            "CREATE TABLE telemetry_storage_metrics(singleton INTEGER PRIMARY KEY,"
            "schema TEXT,data_json TEXT,digest TEXT)"
        )
        metrics = encoded(
            {
                "accounting_start_event_sequence": 1,
                "appended_events_total": 1,
                "observation_events_total": 1,
                "logical_value_bytes_total": len(body.encode()),
                "body_creation_misses_total": 1,
                "body_reuse_hits_total": 0,
                "created_body_value_bytes_total": len(body.encode()),
                "hot_events": 1,
                "hot_observations": 1,
                "hot_bodies": 1,
                "hot_body_value_bytes": len(body.encode()),
                "last_appended_event_sequence": 1,
            }
        )
        c.execute(
            "INSERT INTO telemetry_storage_metrics VALUES(1,?,?,?)",
            (METRICS_SCHEMA, metrics, metrics_checksum(metrics)),
        )
        c.execute(VIEW)
        policy = encoded(
            {
                "schema": "roboguide.retention-policy/v0.1",
                "mode": "evaluate",
                "hot_window_ms": 86_400_000,
                "archive_window_ms": 2_592_000_000,
                "max_events": 8192,
                "max_input_bytes": 4 * 1024 * 1024,
            }
        )
        c.execute("INSERT INTO retention_policy VALUES(1,0,?,?,0)", (policy, digest(policy)))
    head = encoded(
        {
            "schema": "roboguide.catalog-recovery-checkpoint/v0.1",
            "through_sequence": 1,
            "timestamp_high_water": 10,
            "records": 0,
            "bytes": 0,
            "digest": digest("roboguide.catalog-recovery-checkpoint/v0.1"),
        }
    )
    c.execute("INSERT INTO catalog_recovery_head VALUES(1,?,?)", (head, digest(head)))
    c.commit()
    return c


@pytest.mark.parametrize("version", [2, 3])
def test_generations_require_explicit_profile_and_complete_projection(
    tmp_path: Path, version: int
) -> None:
    """Old profiles cannot adopt v2/v3 through familiar public event columns."""
    with database(tmp_path / "controller.sqlite3", version) as c:
        expected = STORAGE_V2 if version == 2 else STORAGE_V3
        assert actual_format(c) == expected
        validate(c, VALUES_PROFILE, expected)
        assert (
            c.execute("SELECT payload_json FROM events")
            .fetchone()[0]
            .endswith('1.2500e0,"capture":"原始"}}}}')
        )
        with pytest.raises(ValueError, match="v0.32"):
            validate(c, "roboguide.recovery-profile/v0.31", expected)
        c.execute("DELETE FROM catalog_recovery_head")
        with pytest.raises(ValueError, match="head"):
            validate(c, VALUES_PROFILE, expected)


@pytest.mark.parametrize(
    "mutation",
    [
        "DELETE FROM telemetry_bodies",
        "UPDATE telemetry_bodies SET value_json='{}'",
        "UPDATE telemetry_bodies SET byte_size=1",
        "UPDATE telemetry_bodies SET scope_hash='foreign'",
        "UPDATE controller_v3_events SET payload_suffix='}'",
        "UPDATE controller_v3_events SET body_id='missing'",
        "UPDATE controller_v3_events SET payload_digest='sha256:bad'",
    ],
)
def test_missing_or_changed_body_and_envelope_are_refused(tmp_path: Path, mutation: str) -> None:
    """Cold validation checks reconstructed bytes rather than SQLite integrity_check alone."""
    with database(tmp_path / "controller.sqlite3", 3) as c:
        c.execute(mutation)
        assert c.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        with pytest.raises(ValueError, match="body|checksum"):
            validate(c, VALUES_PROFILE, STORAGE_V3)


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE event_log_metadata SET value='2' WHERE key='next_event_number'",
        "UPDATE catalog_recovery_head SET digest='sha256:bad'",
        "DELETE FROM events",
    ],
)
def test_projection_cannot_be_rebuilt_from_a_declaration(tmp_path: Path, mutation: str) -> None:
    """Coverage and exact ordered source identity remain mandatory after migration."""
    with database(tmp_path / "controller.sqlite3", 2) as c:
        c.execute(mutation.replace("DELETE FROM events", "DELETE FROM controller_v1_events"))
        with pytest.raises(ValueError, match="coverage|checksum"):
            validate_catalog(c, VALUES_PROFILE)


def test_typed_journal_requires_original_command(tmp_path: Path) -> None:
    """Classification never creates cleanup permission for an absent original authority command."""
    with database(tmp_path / "controller.sqlite3", 3) as c:
        c.execute("INSERT INTO command_journal_classes VALUES('node-facts-forged','timer')")
        with pytest.raises(ValueError, match="original command"):
            validate(c, VALUES_PROFILE, STORAGE_V3)


def test_policy_tampering_cannot_be_silently_reset_to_defaults(tmp_path: Path) -> None:
    """An internally valid nondefault policy still requires its original acceptance receipt."""
    with database(tmp_path / "controller.sqlite3", 3) as c:
        raw = c.execute("SELECT policy_json FROM retention_policy").fetchone()[0]
        policy = json.loads(raw)
        policy["mode"] = "enabled"
        changed = encoded(policy)
        c.execute(
            "UPDATE retention_policy SET policy_json=?,policy_digest=?", (changed, digest(changed))
        )
        with pytest.raises(ValueError, match="acceptance"):
            validate_retention(c)


@pytest.mark.parametrize("version", [2, 3])
def test_complete_backup_restore_keeps_exact_event_and_raw_tables(
    tmp_path: Path, version: int
) -> None:
    """Immutable cold bundles recover byte-exact events into a new directory without a service."""
    root = tmp_path / "controller"
    root.mkdir()
    (root / "controller.sqlite3.writer.lock").touch()
    c = database(root / "controller.sqlite3", version)
    checkpoint = wrapper()
    c.execute(
        "INSERT INTO controller_checkpoint VALUES(1,1,?,?)",
        (checkpoint["schema"], encoded(checkpoint)),
    )
    c.executescript(
        "CREATE TABLE event_archive_operations(command_id TEXT,intent_json TEXT,"
        "state TEXT,receipt_json TEXT);"
        "CREATE TABLE event_archive_aborts(command_id TEXT,original_command_id TEXT,"
        "intent_json TEXT,receipt_json TEXT);"
        "INSERT INTO event_log_metadata VALUES("
        "'archive_operations_schema','roboguide.archive-operations/v0.2');"
    )
    c.execute(
        "INSERT INTO event_log_metadata VALUES('archive_authority_id',?)",
        ("controller-store:" + "a" * 64,),
    )
    c.commit()
    c.close()
    storage = STORAGE_V2 if version == 2 else STORAGE_V3
    profile: dict[str, Any] = {
        "schema": VALUES_PROFILE,
        "profile_id": "controller-values",
        "revision": 1,
        "max_bytes": 16 * 1024 * 1024,
        "max_files": 100,
        "budget_seconds": 30,
        "artifact_replica_sets": [],
        "components": [
            {
                "component_id": "controller",
                "kind": "controller",
                "root": str(root),
                "database": "controller.sqlite3",
                "trees": [],
                "depends_on": [],
                "storage_schema": storage,
            }
        ],
    }
    validate_profile(profile)
    replicated = copy.deepcopy(profile)
    replica_root = tmp_path / "unsupported-replica"
    replica_root.mkdir()
    replicated["components"].append(
        {
            "component_id": "replica",
            "kind": "replica",
            "root": str(replica_root),
            "database": "raft.sqlite3",
            "trees": [],
            "depends_on": [],
        }
    )
    with pytest.raises(ValueError, match="standalone"):
        validate_profile(replicated)
    old = copy.deepcopy(profile)
    old["schema"] = "roboguide.recovery-profile/v0.31"
    with pytest.raises(ValueError, match="storage format"):
        validate_profile(old)
    receipt = backup(profile, tmp_path / "bundle", "backup-values")
    manifest = verify(tmp_path / "bundle", receipt["manifest_digest"])
    assert manifest["profile"]["schema"] == VALUES_PROFILE
    restore(
        tmp_path / "bundle", receipt["manifest_digest"], tmp_path / "restored", "restore-values"
    )
    restored_path = tmp_path / "restored/stores/controller/controller.sqlite3"
    with sqlite3.connect(restored_path) as restored:
        validate(restored, VALUES_PROFILE, storage)
        assert "1.2500e0" in restored.execute("SELECT payload_json FROM events").fetchone()[0]
    files = [entry for entry in manifest["files"] if entry["path"].endswith("controller.sqlite3")]
    assert files


def test_unknown_generation_and_modified_public_view_are_refused(tmp_path: Path) -> None:
    """Familiar tables never substitute for the explicit generation and verified view."""
    with database(tmp_path / "controller.sqlite3", 3) as c:
        c.execute("PRAGMA user_version=4")
        with pytest.raises(ValueError, match="unknown"):
            actual_format(c)
        c.execute("PRAGMA user_version=3")
        c.execute("DROP VIEW events")
        c.execute("CREATE VIEW events AS SELECT * FROM controller_v3_events")
        with pytest.raises(ValueError, match="view"):
            actual_format(c)


@pytest.mark.parametrize(
    "table",
    [
        "controller_v3_events",
        "telemetry_bodies",
        "catalog_recovery_head",
        "retention_reference_head",
        "retention_policy",
    ],
)
def test_orphaned_new_generation_table_is_not_legacy(tmp_path: Path, table: str) -> None:
    """Removing a marker never turns reserved new-generation authority into fresh legacy storage."""
    with sqlite3.connect(tmp_path / "partial.sqlite3") as c:
        c.execute(f"CREATE TABLE {table}(value TEXT)")
        with pytest.raises(ValueError, match="unknown"):
            actual_format(c)


def test_protected_projection_requires_exact_original_source(tmp_path: Path) -> None:
    """A checksum-consistent copied envelope cannot replace its different original source row."""
    with database(tmp_path / "controller.sqlite3", 2) as c:
        payload = encoded({"MapArtifactDeclared": {"synthetic_fixture": True}})
        c.execute("UPDATE controller_v1_events SET payload_json=?", (payload,))
        fields = (
            "sequence",
            "event_id",
            "timestamp_ms",
            "correlation_id",
            "causation_id",
            "payload_schema",
            "payload_json",
        )
        row = dict(zip(fields, c.execute("SELECT * FROM events").fetchone(), strict=True))
        data = encoded(row)
        c.execute("INSERT INTO catalog_recovery_events VALUES(1,?)", (data,))
        seed = digest("roboguide.catalog-recovery-checkpoint/v0.1")
        chain = (
            "sha256:"
            + hashlib.sha256(
                seed.encode() + len(data.encode()).to_bytes(8, "little") + data.encode()
            ).hexdigest()
        )
        head = encoded(
            {
                "schema": "roboguide.catalog-recovery-checkpoint/v0.1",
                "through_sequence": 1,
                "timestamp_high_water": 10,
                "records": 1,
                "bytes": len(data.encode()),
                "digest": chain,
            }
        )
        c.execute("UPDATE catalog_recovery_head SET data=?,digest=?", (head, digest(head)))
        validate_catalog(c, VALUES_PROFILE)
        c.execute("UPDATE controller_v1_events SET event_id='changed-original'")
        with pytest.raises(ValueError, match="source"):
            validate_catalog(c, VALUES_PROFILE)


@pytest.mark.parametrize(
    "damage",
    ["old-profile", "old-wrapper", "old-grant-schema", "global", "expiry", "revocation-type"],
)
def test_restricted_verifier_authorization_retains_its_own_recovery_fence(damage: str) -> None:
    """A restricted service grant cannot be recovered through an older or wider permission tier."""
    grant: dict[str, Any] = {
        "principal_id": "independent-posture-verifier",
        "role": "verifier",
        "tenant_ids": ["lab"],
        "not_before_ms": 10,
        "expires_at_ms": 20,
        "revoked": False,
    }
    authorization = {
        "schema_version": "roboguide.operator-authorization/v0.3",
        "registry_id": "original-registry",
        "revision": 1,
        "grants": {"sha256:" + "a" * 64: grant},
    }
    value = {
        "schema": "roboguide.controller-checkpoint/v60",
        "integration_json": encoded({"control": {"operator_authorization": authorization}}),
    }
    validate_verifier_authorization(value, VALUES_PROFILE)
    profile = VALUES_PROFILE
    if damage == "old-profile":
        profile = "roboguide.recovery-profile/v0.31"
    elif damage == "old-wrapper":
        value["schema"] = "roboguide.controller-checkpoint/v59"
    elif damage == "old-grant-schema":
        authorization["schema_version"] = "roboguide.operator-authorization/v0.2"
    elif damage == "global":
        grant["tenant_ids"] = ["*"]
    elif damage == "expiry":
        grant["expires_at_ms"] = 10
    else:
        grant["revoked"] = 1
    value["integration_json"] = encoded({"control": {"operator_authorization": authorization}})
    with pytest.raises(ValueError, match="verifier"):
        validate_verifier_authorization(value, profile)


@pytest.mark.parametrize(
    "damage", ["missing-edge", "head-checksum", "different-input", "source-digest"]
)
def test_reference_proof_checks_complete_original_decision_input(
    tmp_path: Path, damage: str
) -> None:
    """Retained digests cannot substitute for the actual original source and decision."""
    with database(tmp_path / "controller.sqlite3", 3) as c:
        record: dict[str, Any] = {"value": {"flag": True}}
        source_raw = encoded({"StateRecordObserved": {"record": record}})
        c.execute(
            "UPDATE controller_v3_events SET body_id=NULL,payload_prefix=NULL,"
            "payload_suffix=NULL,payload_json=?,payload_digest=? WHERE sequence=1",
            (source_raw, digest(source_raw)),
        )
        decision_raw = encoded({"StatePolicyDecision": {"receipts": [{"record": record}]}})
        c.execute(
            "INSERT INTO controller_v3_events VALUES(2,'decision',20,'mission',NULL,"
            "'domain.EventPayload.json/v1',?,NULL,NULL,NULL,?)",
            (decision_raw, digest(decision_raw)),
        )
        c.execute("UPDATE event_log_metadata SET value='2' WHERE key='next_event_number'")
        original_digest = digest("original-record")
        c.execute("INSERT INTO retention_reference_edges VALUES(2,1,?)", (original_digest,))
        seed = digest("roboguide.retention-decision-edges/v0.1")
        data = encoded([2, 1, original_digest]).encode()
        edge_hash = (
            "sha256:"
            + hashlib.sha256(seed.encode() + len(data).to_bytes(8, "little") + data).hexdigest()
        )
        gap_hash = digest("roboguide.retention-decision-gaps/v0.1")
        head = [2, 1, 1, 0, edge_hash, gap_hash, False]
        c.execute(
            "UPDATE retention_reference_head SET through_sequence=2,edges_count=1,"
            "edges_digest=?,head_digest=?",
            (edge_hash, digest(encoded(head))),
        )
        validate_references(c)
        if damage == "missing-edge":
            c.execute("DELETE FROM retention_reference_edges")
        elif damage == "head-checksum":
            c.execute("UPDATE retention_reference_head SET archived_prefix_unresolved=1")
        elif damage == "source-digest":
            c.execute(
                "UPDATE retention_reference_records SET record_digest=?", (digest("foreign"),)
            )
        else:
            record["value"]["flag"] = 1
            changed = encoded({"StatePolicyDecision": {"receipts": [{"record": record}]}})
            c.execute(
                "UPDATE controller_v3_events SET payload_json=?,payload_digest=? WHERE sequence=2",
                (changed, digest(changed)),
            )
        with pytest.raises(ValueError, match="reference|State input|edge"):
            validate_references(c)
