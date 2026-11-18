"""Validate declared Controller generations without creating authority or repairing original SQL."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from pathlib import Path

PROFILE = "roboguide.recovery-profile/v0.17"
STORAGE = "roboguide.controller-storage/v1"
STORAGE_V2 = "roboguide.controller-storage/v2"
STORAGE_V3 = "roboguide.controller-storage/v3"
VALUES_PROFILE = "roboguide.recovery-profile/v0.32"
LEGACY = "roboguide.controller-storage/legacy"
VIEW = "CREATE VIEW events AS SELECT * FROM controller_v1_events"
TABLES = {
    "controller_storage_format",
    "controller_v1_events",
    "event_log_metadata",
    "controller_checkpoint",
    "controller_checkpoint_head",
    "controller_checkpoint_deltas",
    "control_command_journal",
    "service_access_audit",
}
OLD_INDEXES = {
    "idx_events_timestamp",
    "idx_events_correlation",
    "idx_events_sequence",
    "idx_artifact_reference_selector",
}


def actual_format(connection: sqlite3.Connection) -> str:
    """Refuse incomplete generations before treating missing original tables as empty history."""
    objects = {
        row[0]: (row[1], row[2])
        for row in connection.execute("SELECT name,type,sql FROM sqlite_master")
    }
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    fenced = (
        version != 0
        or "controller_storage_format" in objects
        or bool(
            {
                "controller_v1_events",
                "controller_v3_events",
                "telemetry_bodies",
                "telemetry_storage_metrics",
                "catalog_recovery_head",
                "retention_reference_head",
                "retention_policy",
            }
            & objects.keys()
        )
        or ("events" in objects and objects["events"][0] != "table")
    )
    if not fenced:
        return LEGACY
    from .controller_values import VIEW as VALUES_VIEW
    from .controller_values import install_functions

    if version not in {1, 2, 3}:
        raise ValueError("unknown Controller storage generation")
    actual = f"roboguide.controller-storage/v{version}"
    required = set(TABLES)
    table, view = "controller_v1_events", VIEW
    if version >= 2:
        required |= {"catalog_recovery_head", "catalog_recovery_events"}
    if version == 3:
        table, view = "controller_v3_events", VALUES_VIEW
        required.remove("controller_v1_events")
        required |= {
            "controller_v3_events",
            "telemetry_bodies",
            "telemetry_storage_metrics",
            "command_journal_classes",
            "retention_policy",
            "retention_policy_commands",
            "retention_reference_head",
            "retention_reference_records",
            "retention_reference_edges",
            "retention_reference_gaps",
        }
        install_functions(connection)
    if (
        any(objects.get(name, (None,))[0] != "table" for name in required)
        or objects.get("events") != ("view", view)
        or OLD_INDEXES.intersection(objects)
        or any(kind == "trigger" for kind, _ in objects.values())
        or connection.execute("SELECT version,schema FROM controller_storage_format").fetchall()
        != [(version, actual)]
    ):
        raise ValueError("mandatory Controller storage generation/view/marker differs")
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    if (
        not {
            "sequence",
            "event_id",
            "timestamp_ms",
            "correlation_id",
            "causation_id",
            "payload_schema",
            "payload_json",
        }
        <= columns
    ):
        raise ValueError("original Controller event columns are incomplete")
    if (
        version == 3
        and not {"body_id", "payload_prefix", "payload_suffix", "payload_digest"} <= columns
    ):
        raise ValueError("original Controller v3 reference columns are incomplete")
    return actual


def validate(
    connection: sqlite3.Connection,
    profile_schema: str,
    expected: str | None = None,
    *,
    archive_roots: Mapping[str, Path] | None = None,
) -> None:
    """Require an explicit compatible profile; neither a declaration nor a view grants authority."""
    from .coordinated_archives import operations as archive_operations

    archive_operations(connection, profile_schema)
    actual = actual_format(connection)
    if actual in {STORAGE_V2, STORAGE_V3}:
        if profile_schema != VALUES_PROFILE:
            raise ValueError("Controller storage v2/v3 requires explicit recovery v0.32")
        from .controller_values import validate_catalog

        if actual == STORAGE_V3:
            from .controller_metrics import validate_metrics
            from .controller_values import validate_bodies, validate_references, validate_retention

            validate_bodies(connection)
            validate_metrics(connection)
            validate_retention(connection)
            validate_references(connection, archive_roots)
        validate_catalog(connection, profile_schema)
    from .capacity_recovery import _checkpoint

    retained = (
        _checkpoint(
            connection,
            schemas=frozenset(
                f"roboguide.controller-checkpoint/v{version}" for version in range(49, 61)
            ),
        )
        if actual != LEGACY
        else None
    )
    if (
        retained is not None
        and retained["schema"] == "roboguide.controller-checkpoint/v60"
        and profile_schema
        not in {
            "roboguide.recovery-profile/v0.29",
            "roboguide.recovery-profile/v0.30",
            "roboguide.recovery-profile/v0.31",
            "roboguide.recovery-profile/v0.32",
        }
    ):
        raise ValueError("resource-bearing Federation v60 requires explicit recovery v0.29")
    if actual != LEGACY and profile_schema not in {
        PROFILE,
        "roboguide.recovery-profile/v0.18",
        "roboguide.recovery-profile/v0.19",
        "roboguide.recovery-profile/v0.20",
        "roboguide.recovery-profile/v0.21",
        "roboguide.recovery-profile/v0.22",
        "roboguide.recovery-profile/v0.23",
        "roboguide.recovery-profile/v0.24",
        "roboguide.recovery-profile/v0.25",
        "roboguide.recovery-profile/v0.26",
        "roboguide.recovery-profile/v0.27",
        "roboguide.recovery-profile/v0.28",
        "roboguide.recovery-profile/v0.29",
        "roboguide.recovery-profile/v0.30",
        "roboguide.recovery-profile/v0.31",
        "roboguide.recovery-profile/v0.32",
    }:
        raise ValueError("Controller storage v1 requires explicit recovery v0.17")
    objects = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if {
        "controller_checkpoint",
        "controller_checkpoint_head",
        "controller_checkpoint_deltas",
    } <= objects:
        from .controller_values import validate_verifier_authorization

        authority = retained or _checkpoint(
            connection,
            schemas=frozenset(
                f"roboguide.controller-checkpoint/v{version}" for version in range(49, 61)
            ),
        )
        validate_verifier_authorization(authority, profile_schema)
    for current_table in ("controller_checkpoint", "controller_checkpoint_head"):
        if (
            current_table in objects
            and connection.execute(
                f"SELECT 1 FROM {current_table} "
                "WHERE schema='roboguide.controller-checkpoint/v60' LIMIT 1"
            ).fetchone()
        ):
            if profile_schema not in {
                "roboguide.recovery-profile/v0.29",
                "roboguide.recovery-profile/v0.30",
                "roboguide.recovery-profile/v0.31",
                "roboguide.recovery-profile/v0.32",
            }:
                raise ValueError("resource-bearing Federation v60 requires explicit recovery v0.29")
            if retained is None:
                from .federation_recovery import validate as validate_federation

                validate_federation(connection)
    for table in ("controller_checkpoint", "controller_checkpoint_head"):
        if (
            table in objects
            and connection.execute(
                f"SELECT 1 FROM {table} WHERE schema='roboguide.controller-checkpoint/v59' LIMIT 1"
            ).fetchone()
            is not None
            and profile_schema
            not in {
                "roboguide.recovery-profile/v0.28",
                "roboguide.recovery-profile/v0.29",
                "roboguide.recovery-profile/v0.30",
                "roboguide.recovery-profile/v0.31",
                "roboguide.recovery-profile/v0.32",
            }
        ):
            raise ValueError("cohort/hazard Controller v59 requires explicit recovery v0.28")
        if (
            table in objects
            and connection.execute(
                f"SELECT 1 FROM {table} WHERE schema='roboguide.controller-checkpoint/v58' LIMIT 1"
            ).fetchone()
            is not None
            and profile_schema
            not in {
                "roboguide.recovery-profile/v0.27",
                "roboguide.recovery-profile/v0.28",
                "roboguide.recovery-profile/v0.29",
                "roboguide.recovery-profile/v0.30",
                "roboguide.recovery-profile/v0.31",
                "roboguide.recovery-profile/v0.32",
            }
        ):
            raise ValueError("spatial/calendar Controller v58 requires explicit recovery v0.27")
        if (
            table in objects
            and connection.execute(
                f"SELECT 1 FROM {table} WHERE schema='roboguide.controller-checkpoint/v57' LIMIT 1"
            ).fetchone()
            is not None
            and profile_schema
            not in {
                "roboguide.recovery-profile/v0.26",
                "roboguide.recovery-profile/v0.27",
                "roboguide.recovery-profile/v0.28",
                "roboguide.recovery-profile/v0.29",
                "roboguide.recovery-profile/v0.30",
                "roboguide.recovery-profile/v0.31",
                "roboguide.recovery-profile/v0.32",
            }
        ):
            raise ValueError("scoped typed-map Controller v57 requires explicit recovery v0.26")
        if (
            table in objects
            and connection.execute(
                f"SELECT 1 FROM {table} WHERE schema='roboguide.controller-checkpoint/v56' LIMIT 1"
            ).fetchone()
            is not None
            and profile_schema
            not in {
                "roboguide.recovery-profile/v0.24",
                "roboguide.recovery-profile/v0.25",
                "roboguide.recovery-profile/v0.26",
                "roboguide.recovery-profile/v0.27",
                "roboguide.recovery-profile/v0.28",
                "roboguide.recovery-profile/v0.29",
                "roboguide.recovery-profile/v0.30",
                "roboguide.recovery-profile/v0.31",
                "roboguide.recovery-profile/v0.32",
            }
        ):
            raise ValueError("quantitative Controller v56 requires explicit recovery v0.24")
    if retained is not None:
        from .federation_recovery import validate as validate_federation

        validate_federation(connection)
    if profile_schema in {
        "roboguide.recovery-profile/v0.28",
        "roboguide.recovery-profile/v0.29",
        "roboguide.recovery-profile/v0.30",
        "roboguide.recovery-profile/v0.31",
        "roboguide.recovery-profile/v0.32",
    }:
        from .coordination_recovery import validate as validate_coordination

        validate_coordination(connection)
    if profile_schema in {
        "roboguide.recovery-profile/v0.24",
        "roboguide.recovery-profile/v0.25",
        "roboguide.recovery-profile/v0.26",
        "roboguide.recovery-profile/v0.27",
        "roboguide.recovery-profile/v0.28",
        "roboguide.recovery-profile/v0.29",
        "roboguide.recovery-profile/v0.30",
        "roboguide.recovery-profile/v0.31",
        "roboguide.recovery-profile/v0.32",
    }:
        from .capacity_recovery import validate as validate_capacity

        validate_capacity(connection)
    if profile_schema in {
        "roboguide.recovery-profile/v0.26",
        "roboguide.recovery-profile/v0.27",
        "roboguide.recovery-profile/v0.28",
        "roboguide.recovery-profile/v0.29",
        "roboguide.recovery-profile/v0.30",
        "roboguide.recovery-profile/v0.31",
        "roboguide.recovery-profile/v0.32",
    }:
        from .scoped_map_recovery import validate as validate_scoped_maps

        validate_scoped_maps(connection)
    if profile_schema in {
        "roboguide.recovery-profile/v0.24",
        "roboguide.recovery-profile/v0.25",
        "roboguide.recovery-profile/v0.26",
        "roboguide.recovery-profile/v0.27",
        "roboguide.recovery-profile/v0.28",
        "roboguide.recovery-profile/v0.29",
        "roboguide.recovery-profile/v0.30",
        "roboguide.recovery-profile/v0.31",
        "roboguide.recovery-profile/v0.32",
    }:
        from .topology_recovery import validate as validate_topology

        validate_topology(connection)
    if expected is not None and actual != expected:
        raise ValueError("declared Controller storage format differs from actual original SQL")


def table_names(connection: sqlite3.Connection) -> set[str]:
    """Expose the exact validated read-only original-event view to existing evidence consumers."""
    names = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if actual_format(connection) != LEGACY:
        names.add("events")
    return names
