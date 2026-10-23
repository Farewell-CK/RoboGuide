"""Upgrade actual old producer/reader history without inventing historical stream evidence."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from check_replica_content_processes import Processes, digest


def rows(path: Path) -> dict[str, Any]:
    """Read exact original producer records/heads and close the real SQL before any upgrade."""
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as sql:
        tables = [
            name
            for (name,) in sql.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if name != "metadata"
        ]
        result: dict[str, Any] = {}
        for table in tables:
            if not table.replace("_", "").isalnum():
                raise ValueError("unexpected original table name")
            result[table] = sql.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()
        result["metadata"] = sql.execute(
            "SELECT * FROM metadata WHERE key!='layout' ORDER BY key"
        ).fetchall()
    return result


def run(
    workers: Processes, old: Path, source: Path, output: Path, profile: str, member_id: int
) -> dict[str, Any]:
    """Create full history with the frozen old binary, wait its actual handles, then upgrade."""
    assert json.loads((source / "result.json").read_bytes())["passed"] is True
    assert (
        json.loads((source / "owned-process-exits.json").read_bytes())["all_owned_processes_reaped"]
        is True
    )
    member: dict[str, Any] = {
        "root": str(output / "actual-original-producer"),
        "profile": profile,
        "member": member_id,
        "producer_journal": True,
    }
    entry = output / "actual-original-entry.json"

    def old_call(request: dict[str, Any], label: str) -> dict[str, Any]:
        """Require historical executable success and preserve its actual native outputs."""
        code, value = workers.wait(workers.start(request, label, old))
        assert code == 0, value
        response: dict[str, Any] = value["result"]
        return response

    prepared = old_call(
        {
            "action": "prepare",
            "source_sql": str(source / "controller.sqlite3"),
            "source_cas": str(source / "cas"),
            "member": member,
            "entry_output": str(entry),
        },
        "actual-old-producer-prepare",
    )
    for kind in ("append", "apply"):
        old_call(
            {
                "action": "operate",
                "member": member,
                "operation": {"kind": kind, "entry": str(entry)},
            },
            "actual-old-producer-" + kind,
        )
    old_call(
        {
            "action": "operate",
            "member": member,
            "operation": {
                "kind": "build",
                "operation_id": "actual-old-snapshot-before-streams",
                "output": str(output / "actual-old-snapshot.json"),
            },
        },
        "actual-old-producer-snapshot",
    )
    root = Path(member["root"]) / "content"
    store = {"root": str(root), "profile": profile, "member": member_id}
    holder = workers.start(
        {
            "action": "operate",
            "member": member,
            "operation": {"kind": "hold-snapshots", "prefix": "actual-old-stream-upgrade"},
        },
        "actual-old-reader-holder",
        old,
    )
    workers.ready(holder)
    workers.refuse(
        {"action": "upgrade", "store": store}, "actual-old-live-upgrade-refused", "active"
    )
    assert workers.wait(holder, finish=True)[0] == 0
    database = root / "content.sqlite3"
    before = rows(database)
    assert before["content_producers"] and before["content_readers"]
    with closing(sqlite3.connect(database)) as sql:
        assert sql.execute("PRAGMA user_version").fetchone()[0] == 3
        identity = json.loads(
            sql.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()[0]
        )
    actual_inode = database.stat().st_ino
    upgraded = workers.run({"action": "upgrade", "store": store}, "actual-original-stream-upgrade")
    assert upgraded["identity"] == identity and database.stat().st_ino == actual_inode
    after = rows(database)
    assert after.pop("content_streams") == []
    after["metadata"] = [row for row in after["metadata"] if row[0] != "stream_ids"]
    assert after == before
    code, refusal = workers.wait(
        workers.start({"action": "inspect", "member": member}, "actual-old-producer-refused", old)
    )
    assert code != 0 and "format" in refusal.get("error", ""), refusal
    key = prepared["inputs"][0]
    observed = workers.run(
        {"action": "inspect", "store": store, "content": key, "commands": []},
        "actual-upgraded-original-byte-read",
    )
    assert observed["identity"] == identity and observed["actual_bytes"] is True
    return {
        "old_binary_sha256": digest(old),
        "original_sql_inode": actual_inode,
        "original_identity": identity,
        "original_raw_rows_preserved": True,
        "actual_old_log_view_snapshot_reader_history": True,
        "old_live_readers_blocked_upgrade": True,
        "old_binary_fenced_after_upgrade": True,
        "new_stream_history_empty": True,
    }
