"""Audit immutable metrics against actual SQL bodies, events and archive floors."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery.controller_metrics import SCHEMA, checksum, validate_metrics
from roboguide_recovery.controller_storage import (
    STORAGE_V3,
    VALUES_PROFILE,
    actual_format,
    validate,
)
from roboguide_recovery.controller_values import digest
from test_controller_values import database, encoded


def counters(connection: sqlite3.Connection) -> dict[str, Any]:
    """Read original test counters without altering their stored formatting or digest."""
    return dict(
        json.loads(
            connection.execute(
                "SELECT data_json FROM telemetry_storage_metrics WHERE singleton=1"
            ).fetchone()[0]
        )
    )


def save(connection: sqlite3.Connection, value: dict[str, Any]) -> None:
    """Recompute metadata checksum in counterexamples so full audit must verify real SQL too."""
    raw = encoded(value)
    connection.execute(
        "UPDATE telemetry_storage_metrics SET data_json=?,digest=? WHERE singleton=1",
        (raw, checksum(raw)),
    )


def test_metrics_digest_domain_and_full_original_fixture_are_readonly(tmp_path: Path) -> None:
    """Metrics preserve exact original bytes under the domain-separated Rust checksum contract."""
    with database(tmp_path / "controller.sqlite3", 3) as connection:
        raw = connection.execute("SELECT data_json FROM telemetry_storage_metrics").fetchone()[0]
        assert checksum(raw) == digest(SCHEMA + "\n" + raw)
        before = list(connection.iterdump())
        validate_metrics(connection)
        validate(connection, VALUES_PROFILE, STORAGE_V3)
        assert list(connection.iterdump()) == before


@pytest.mark.parametrize("damage", ["table", "head", "schema", "digest", "columns", "duplicate"])
def test_missing_or_mutated_metrics_authority_refuses_recovery(tmp_path: Path, damage: str) -> None:
    """SQLite integrity alone cannot replace the mandatory exact singleton and its closed schema."""
    with database(tmp_path / "controller.sqlite3", 3) as connection:
        if damage == "table":
            connection.execute("DROP TABLE telemetry_storage_metrics")
            with pytest.raises(ValueError, match="generation"):
                actual_format(connection)
            return
        if damage == "head":
            connection.execute("DELETE FROM telemetry_storage_metrics")
        elif damage == "schema":
            connection.execute("UPDATE telemetry_storage_metrics SET schema='future'")
        elif damage == "digest":
            connection.execute("UPDATE telemetry_storage_metrics SET digest='sha256:bad'")
        elif damage == "columns":
            connection.execute("ALTER TABLE telemetry_storage_metrics ADD COLUMN hidden INTEGER")
        else:
            connection.execute(
                "INSERT INTO telemetry_storage_metrics SELECT 2,schema,data_json,digest "
                "FROM telemetry_storage_metrics WHERE singleton=1"
            )
        with pytest.raises(ValueError, match="metrics"):
            validate_metrics(connection)


@pytest.mark.parametrize(
    "field,value",
    [
        ("hot_events", 0),
        ("hot_observations", 0),
        ("hot_bodies", 0),
        ("hot_body_value_bytes", 1),
        ("last_appended_event_sequence", 2),
        ("body_reuse_hits_total", 1),
        ("logical_value_bytes_total", 0),
        ("created_body_value_bytes_total", 0),
        ("accounting_start_event_sequence", 0),
        ("accounting_start_event_sequence", 2),
        ("appended_events_total", False),
        ("observation_events_total", -1),
        ("hot_events", 2**63),
    ],
)
def test_checksums_cannot_hide_counter_or_gauge_conflicts(
    tmp_path: Path, field: str, value: Any
) -> None:
    """Independent SQL recount catches repinned gauges while closed relations preserve totals."""
    with database(tmp_path / "controller.sqlite3", 3) as connection:
        changed = counters(connection)
        changed[field] = value
        save(connection, changed)
        with pytest.raises(ValueError, match="metrics"):
            validate_metrics(connection)


def test_actual_hot_growth_requires_counter_effect_in_same_transaction(tmp_path: Path) -> None:
    """A new hot event cannot hide behind unchanged metrics or valid body bytes."""
    with database(tmp_path / "controller.sqlite3", 3) as connection:
        connection.execute(
            "INSERT INTO controller_v3_events SELECT 2,'event-2',20,correlation_id,causation_id,"
            "payload_schema,payload_json,body_id,payload_prefix,payload_suffix,payload_digest "
            "FROM controller_v3_events WHERE sequence=1"
        )
        with pytest.raises(ValueError, match="hot gauges"):
            validate_metrics(connection)
        changed = counters(connection)
        changed.update(
            {
                "appended_events_total": 2,
                "observation_events_total": 2,
                "body_reuse_hits_total": 1,
                "hot_events": 2,
                "hot_observations": 2,
                "logical_value_bytes_total": changed["logical_value_bytes_total"] * 2,
                "last_appended_event_sequence": 2,
            }
        )
        save(connection, changed)
        validate_metrics(connection)


def test_archive_retirement_preserves_cumulative_totals_and_original_high_water(
    tmp_path: Path,
) -> None:
    """Zero hot gauges after real SQL retirement do not erase append totals or archived sequence."""
    with database(tmp_path / "controller.sqlite3", 3) as connection:
        changed = counters(connection)
        connection.execute("DELETE FROM controller_v3_events")
        connection.execute("DELETE FROM telemetry_bodies")
        connection.execute("INSERT INTO event_log_metadata VALUES('history_floor','1')")
        changed.update(
            {"hot_events": 0, "hot_observations": 0, "hot_bodies": 0, "hot_body_value_bytes": 0}
        )
        save(connection, changed)
        validate_metrics(connection)
        assert counters(connection)["appended_events_total"] == 1
        connection.execute("UPDATE event_log_metadata SET value='2' WHERE key='history_floor'")
        with pytest.raises(ValueError, match="high-water"):
            validate_metrics(connection)


def test_migration_with_retained_tail_has_no_claim_on_previous_archive_totals(
    tmp_path: Path,
) -> None:
    """Counters may start at migration while older archives precede the retained tail."""
    with database(tmp_path / "controller.sqlite3", 3) as connection:
        connection.execute("UPDATE controller_v3_events SET sequence=101 WHERE sequence=1")
        connection.execute("INSERT INTO event_log_metadata VALUES('history_floor','100')")
        connection.execute(
            "UPDATE event_log_metadata SET value='101' WHERE key='next_event_number'"
        )
        changed = counters(connection)
        changed["accounting_start_event_sequence"] = 101
        changed["last_appended_event_sequence"] = 101
        save(connection, changed)
        validate_metrics(connection)
        assert counters(connection)["appended_events_total"] == 1


@pytest.mark.parametrize("floor", [0, 100])
def test_empty_migration_tail_preserves_explicit_accounting_baseline(
    tmp_path: Path, floor: int
) -> None:
    """An already archived prefix sets coverage without pretending to be newly accounted events."""
    with database(tmp_path / "controller.sqlite3", 3) as connection:
        connection.execute("DELETE FROM controller_v3_events")
        connection.execute("DELETE FROM telemetry_bodies")
        connection.execute(
            "INSERT INTO event_log_metadata VALUES('history_floor',?)", (str(floor),)
        )
        connection.execute(
            "UPDATE event_log_metadata SET value=? WHERE key='next_event_number'", (str(floor),)
        )
        changed = dict.fromkeys(counters(connection), 0)
        changed["accounting_start_event_sequence"] = floor + 1
        changed["last_appended_event_sequence"] = floor
        save(connection, changed)
        validate_metrics(connection)
        assert counters(connection)["appended_events_total"] == 0
        changed["last_appended_event_sequence"] = 0 if floor else 1
        save(connection, changed)
        with pytest.raises(ValueError, match="metrics"):
            validate_metrics(connection)
