"""Observe actual original v0.1 content, drained upgrade and old-binary refusal."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from check_replica_content_processes import Processes


def run(
    workers: Processes,
    old: Path,
    output: Path,
    store: dict[str, Any],
    copy: dict[str, Any],
    inspect: dict[str, Any],
) -> dict[str, Any]:
    """Keep actual original owner/raw rows/receipts unchanged after the old writer's native wait."""
    if not old.is_file() or not os.access(old, os.X_OK):
        raise ValueError("the explicit original v0.1 probe must be an actual retained executable")
    root = output / "actual-original-v1"
    legacy = dict(store, root=str(root))
    code, created = workers.wait(
        workers.start({"action": "create", "store": legacy}, "old-create", old)
    )
    if code != 0:
        raise ValueError(f"actual original old creation failed: {created}")
    original = created["result"]["identity"]
    code, copied = workers.wait(
        workers.start(dict(copy, store=legacy, command_id="actual-old-copy"), "old-copy", old)
    )
    if code != 0 or copied["result"]["copy"]["receipt"] is None:
        raise ValueError(f"actual original old copy failed: {copied}")
    query = dict(inspect, store=legacy, copies=["actual-old-copy"], readers=[])
    code, before = workers.wait(workers.start(query, "old-inspect", old))
    if code != 0 or before["result"]["actual_bytes"] is not True:
        raise ValueError("actual original old copy has no independently readable bytes")
    with closing(sqlite3.connect(root / "content.sqlite3")) as database:
        if database.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("retained original binary did not produce actual SQL v0.1")
        original_rows = database.execute(
            "SELECT operation_id,record FROM content_operations ORDER BY operation_id"
        ).fetchall()
    live = workers.start({"action": "hold", "store": legacy}, "old-live", old)
    workers.ready(live)
    workers.refuse(
        {"action": "upgrade", "store": legacy}, "old-live-upgrade-refused", "still active"
    )
    if workers.wait(live, finish=True)[0] != 0:
        raise ValueError("actual old writer lacks its successful native close/wait")
    workers.refuse(query, "old-no-implicit-upgrade", "explicit reader-scope upgrade")
    upgraded = workers.run({"action": "upgrade", "store": legacy}, "original-reader-scope-upgrade")
    if upgraded["identity"] != original:
        raise ValueError("actual upgrade changed original store identity")
    with closing(sqlite3.connect(root / "content.sqlite3")) as database:
        if (
            database.execute("PRAGMA user_version").fetchone()[0] != 2
            or database.execute(
                "SELECT operation_id,record FROM content_operations ORDER BY operation_id"
            ).fetchall()
            != original_rows
        ):
            raise ValueError("actual upgrade changed original raw copy intents or receipts")
    if workers.run(query, "original-upgraded-byte-read") != before["result"]:
        raise ValueError("actual upgrade changed original availability/receipt/pin history")
    code, refused = workers.wait(workers.start(query, "old-binary-fenced", old))
    if code == 0 or "SQL format" not in refused.get("error", ""):
        raise ValueError(f"original old binary acquired new layout authority: {refused}")
    return {
        "old_probe_sha256": hashlib.sha256(old.read_bytes()).hexdigest(),
        "original_identity": original,
        "original_raw_rows_unchanged": True,
        "live_old_writer_refused": True,
        "actual_old_binary_fenced": True,
        "original_receipts_and_budgets_unchanged": True,
    }
