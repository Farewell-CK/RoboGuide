"""Audit original cumulative storage counters and independently count current hot SQL gauges."""

from __future__ import annotations

import hashlib
import json
import sqlite3

from .controller_values import _metadata_uint, install_functions

SCHEMA = "roboguide.telemetry-storage-metrics/v0.1"
FIELDS = {
    "accounting_start_event_sequence",
    "appended_events_total",
    "observation_events_total",
    "logical_value_bytes_total",
    "body_creation_misses_total",
    "body_reuse_hits_total",
    "created_body_value_bytes_total",
    "hot_events",
    "hot_observations",
    "hot_bodies",
    "hot_body_value_bytes",
    "last_appended_event_sequence",
}


def checksum(data: str) -> str:
    """Hash the exact persisted bytes behind the same explicit Rust metrics digest domain."""
    return "sha256:" + hashlib.sha256(SCHEMA.encode() + b"\n" + data.encode()).hexdigest()


def validate_metrics(connection: sqlite3.Connection) -> None:
    """Verify the required head without claiming to reconstruct pre-migration cumulative history."""
    install_functions(connection)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(telemetry_storage_metrics)")}
    if columns != {"singleton", "schema", "data_json", "digest"}:
        raise ValueError("mandatory telemetry metrics table layout differs")
    heads = connection.execute(
        "SELECT singleton,schema,data_json,digest FROM telemetry_storage_metrics"
    ).fetchall()
    if len(heads) != 1 or heads[0][0] != 1:
        raise ValueError("mandatory telemetry metrics head is missing or duplicated")
    _, schema, raw, expected = heads[0]
    if (
        schema != SCHEMA
        or not isinstance(raw, str)
        or len(raw.encode()) > 4096
        or checksum(raw) != expected
    ):
        raise ValueError("original telemetry metrics generation, bound or checksum differs")
    counters = json.loads(raw)
    if (
        not isinstance(counters, dict)
        or set(counters) != FIELDS
        or not all(type(value) is int and 0 <= value <= 2**63 - 1 for value in counters.values())
    ):
        raise ValueError("original telemetry metrics counters are not closed unsigned values")
    c = counters
    if (
        c["accounting_start_event_sequence"] == 0
        or c["last_appended_event_sequence"]
        != c["accounting_start_event_sequence"] - 1 + c["appended_events_total"]
        or c["observation_events_total"] > c["appended_events_total"]
        or c["body_creation_misses_total"] + c["body_reuse_hits_total"]
        != c["observation_events_total"]
        or c["hot_events"] > c["appended_events_total"]
        or c["hot_observations"] > c["hot_events"]
        or c["hot_observations"] > c["observation_events_total"]
        or c["hot_bodies"] > c["hot_observations"]
        or c["hot_bodies"] > c["body_creation_misses_total"]
        or c["hot_body_value_bytes"] > c["created_body_value_bytes_total"]
        or c["created_body_value_bytes_total"] > c["logical_value_bytes_total"]
        or c["logical_value_bytes_total"] < c["observation_events_total"]
        or (c["observation_events_total"] == 0) != (c["logical_value_bytes_total"] == 0)
        or (c["body_creation_misses_total"] == 0) != (c["created_body_value_bytes_total"] == 0)
        or (c["hot_bodies"] == 0) != (c["hot_body_value_bytes"] == 0)
        or (c["hot_observations"] > 0 and c["hot_bodies"] == 0)
        or c["last_appended_event_sequence"] < c["appended_events_total"]
    ):
        raise ValueError("original telemetry metrics counter identities differ")
    events, observations, logical_hot_bytes = 0, 0, 0
    for raw_payload, body_id, body_bytes in connection.execute(
        "SELECT e.payload_json,r.body_id,b.byte_size FROM events e "
        "JOIN controller_v3_events r ON r.sequence=e.sequence "
        "LEFT JOIN telemetry_bodies b ON b.body_id=r.body_id ORDER BY e.sequence"
    ):
        events += 1
        payload = json.loads(raw_payload)
        if isinstance(payload, dict) and set(payload) == {"StateRecordObserved"}:
            if body_id is None or type(body_bytes) is not int or body_bytes <= 0:
                raise ValueError("original telemetry observation lacks its counted body reference")
            observations += 1
            logical_hot_bytes += body_bytes
        elif body_id is not None:
            raise ValueError("original non-observation event contains a telemetry body reference")
    body_count, body_bytes = connection.execute(
        "SELECT COUNT(*),COALESCE(SUM(length(CAST(value_json AS BLOB))),0) FROM telemetry_bodies"
    ).fetchone()
    maximum = connection.execute("SELECT COALESCE(MAX(sequence),0) FROM events").fetchone()[0]
    floor = _metadata_uint(connection, "history_floor", 0) or 0
    high_water = max(maximum, floor)
    if (
        (events, observations, body_count, body_bytes)
        != (
            c["hot_events"],
            c["hot_observations"],
            c["hot_bodies"],
            c["hot_body_value_bytes"],
        )
        or c["last_appended_event_sequence"] != high_water
        or c["accounting_start_event_sequence"] > floor + 1
    ):
        raise ValueError("original telemetry metrics hot gauges or append high-water differ")
    if logical_hot_bytes > c["logical_value_bytes_total"]:
        raise ValueError(
            "original telemetry logical byte total does not cover the hot observations"
        )
