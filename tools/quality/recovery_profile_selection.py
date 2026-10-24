"""Select explicit native recovery declarations from actual retained Controller headers."""

from __future__ import annotations

import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path
from typing import Any


def declare_controller_storage(profile: dict[str, Any]) -> None:
    """Declare observed legacy/v1 storage explicitly; the CLI independently validates full SQL."""
    if profile["schema"] != "roboguide.recovery-profile/v0.18":
        return
    for component in profile["components"]:
        if component["kind"] != "controller":
            continue
        database = Path(component["root"]) / component["database"]
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as sql:
            version = sql.execute("PRAGMA user_version").fetchone()[0]
            marker = sql.execute(
                "SELECT type FROM sqlite_master WHERE name='controller_storage_format'"
            ).fetchone()
        if version == 0 and marker is None:
            component["storage_schema"] = "roboguide.controller-storage/legacy"
        elif version == 1 and marker == ("table",):
            component["storage_schema"] = "roboguide.controller-storage/v1"
        else:
            raise ValueError("actual Controller storage header is unavailable or inconsistent")


def standalone_binary_schema(binary: Path) -> str:
    """Read only the actual binary's metadata before declaring a new maintained process profile."""
    schema = subprocess.check_output(
        [str(binary), "--application-schema"], text=True, timeout=5
    ).strip()
    if not schema.startswith("roboguide.controller-checkpoint/v") or "\n" in schema:
        raise ValueError("actual Controller binary returned an invalid schema header")
    return (
        "roboguide.recovery-profile/v0.18"
        if schema in {"roboguide.controller-checkpoint/v53", "roboguide.controller-checkpoint/v54"}
        else "roboguide.recovery-profile/v0.16"
    )


def standalone_schema(database: Path) -> str:
    """Observe the actual wrapper; the recovery CLI still validates all original authority."""
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as sql:
        row = sql.execute("SELECT schema FROM controller_checkpoint WHERE singleton=1").fetchone()
    if row is None or not isinstance(row[0], str):
        raise ValueError("actual Controller checkpoint header is unavailable")
    return (
        "roboguide.recovery-profile/v0.18"
        if row[0] in {"roboguide.controller-checkpoint/v53", "roboguide.controller-checkpoint/v54"}
        else "roboguide.recovery-profile/v0.16"
    )


def restore_schema(profile_schema: str) -> str:
    """Expect the actual selected profile's receipt, preserving old versions unchanged."""
    if profile_schema not in {
        "roboguide.recovery-profile/v0.16",
        "roboguide.recovery-profile/v0.17",
        "roboguide.recovery-profile/v0.18",
    }:
        raise ValueError("unsupported explicit standalone recovery declaration")
    return profile_schema.replace("recovery-profile", "restore-receipt")
