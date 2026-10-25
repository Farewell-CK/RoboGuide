"""Validate declared Controller generations without creating authority or repairing original SQL."""

from __future__ import annotations

import sqlite3

PROFILE = "roboguide.recovery-profile/v0.17"
STORAGE = "roboguide.controller-storage/v1"
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
        or "controller_v1_events" in objects
        or ("events" in objects and objects["events"][0] != "table")
    )
    if not fenced:
        return LEGACY
    if (
        version != 1
        or any(objects.get(name, (None,))[0] != "table" for name in TABLES)
        or objects.get("events") != ("view", VIEW)
        or OLD_INDEXES.intersection(objects)
        or any(kind == "trigger" for kind, _ in objects.values())
        or connection.execute("SELECT version,schema FROM controller_storage_format").fetchall()
        != [(1, STORAGE)]
    ):
        raise ValueError("mandatory Controller storage v1 generation/view/marker differs")
    columns = {row[1] for row in connection.execute("PRAGMA table_info(controller_v1_events)")}
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
        raise ValueError("original Controller v1 event columns are incomplete")
    return STORAGE


def validate(
    connection: sqlite3.Connection, profile_schema: str, expected: str | None = None
) -> None:
    """Require an explicit compatible profile; neither a declaration nor a view grants authority."""
    actual = actual_format(connection)
    from .capacity_recovery import _checkpoint

    retained = (
        _checkpoint(
            connection,
            schemas=frozenset(
                f"roboguide.controller-checkpoint/v{version}" for version in range(49, 61)
            ),
        )
        if actual == STORAGE
        else None
    )
    if (
        retained is not None
        and retained["schema"] == "roboguide.controller-checkpoint/v60"
        and profile_schema != "roboguide.recovery-profile/v0.29"
    ):
        raise ValueError("resource-bearing Federation v60 requires explicit recovery v0.29")
    if actual == STORAGE and profile_schema not in {
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
    }:
        raise ValueError("Controller storage v1 requires explicit recovery v0.17")
    objects = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for current_table in ("controller_checkpoint", "controller_checkpoint_head"):
        if (
            current_table in objects
            and connection.execute(
                f"SELECT 1 FROM {current_table} "
                "WHERE schema='roboguide.controller-checkpoint/v60' LIMIT 1"
            ).fetchone()
        ):
            if profile_schema != "roboguide.recovery-profile/v0.29":
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
            not in {"roboguide.recovery-profile/v0.28", "roboguide.recovery-profile/v0.29"}
        ):
            raise ValueError("cohort/hazard Controller v59 requires explicit recovery v0.28")
        if (
            table in objects
            and connection.execute(
                f"SELECT 1 FROM {table} WHERE schema='roboguide.controller-checkpoint/v58' LIMIT 1"
            ).fetchone()
            is not None
            and profile_schema
            not in {"roboguide.recovery-profile/v0.27", "roboguide.recovery-profile/v0.28"}
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
            }
        ):
            raise ValueError("quantitative Controller v56 requires explicit recovery v0.24")
    if retained is not None:
        from .federation_recovery import validate as validate_federation

        validate_federation(connection)
    if profile_schema in {"roboguide.recovery-profile/v0.28", "roboguide.recovery-profile/v0.29"}:
        from .coordination_recovery import validate as validate_coordination

        validate_coordination(connection)
    if profile_schema in {
        "roboguide.recovery-profile/v0.24",
        "roboguide.recovery-profile/v0.25",
        "roboguide.recovery-profile/v0.26",
        "roboguide.recovery-profile/v0.27",
        "roboguide.recovery-profile/v0.28",
        "roboguide.recovery-profile/v0.29",
    }:
        from .capacity_recovery import validate as validate_capacity

        validate_capacity(connection)
    if profile_schema in {
        "roboguide.recovery-profile/v0.26",
        "roboguide.recovery-profile/v0.27",
        "roboguide.recovery-profile/v0.28",
        "roboguide.recovery-profile/v0.29",
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
    if actual_format(connection) == STORAGE:
        names.add("events")
    return names
