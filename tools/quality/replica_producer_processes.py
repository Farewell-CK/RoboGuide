"""Verify original producer interruption with owned processes, SQLite locks and CAS bytes."""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from check_replica_content_processes import Processes, digest, save


def rows(member: dict[str, Any], table: str = "content_producers") -> list[dict[str, Any]]:
    """Read complete actual managed SQL rows independently of HTTP/JSON responses."""
    if table not in {"content_producers", "content_readers"}:
        raise ValueError("actual producer checker table is not in its closed readonly set")
    path = Path(member["root"]) / "content/content.sqlite3"
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.1)) as sql:
        return [json.loads(raw) for (raw,) in sql.execute(f"SELECT record FROM {table}")]


def operate(member: dict[str, Any], kind: str, **arguments: Any) -> dict[str, Any]:
    """Declare one explicit local storage invocation without Task, device or quorum authority."""
    return {"action": "operate", "member": member, "operation": {"kind": kind, **arguments}}


def stop_after_original(
    process: subprocess.Popen[bytes], member: dict[str, Any], kind: str
) -> dict[str, Any]:
    """Suspend the owned incarnation after its actual intent/pins and before publication."""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ValueError("actual original producer exited before its retained SQL fault window")
        try:
            candidates = rows(member)
        except sqlite3.OperationalError as error:
            if error.sqlite_errorcode not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise
            continue
        for original in candidates:
            intent = original["intent"]
            if intent["budget"]["pid"] != process.pid or intent["source"]["kind"] != kind:
                continue
            if original["publication"] is not None or original["worker_closed_ms"] is not None:
                raise ValueError("actual original producer already passed its native fault window")
            os.kill(process.pid, signal.SIGSTOP)
            exact = next(
                row
                for row in rows(member)
                if row["intent"]["operation_id"] == intent["operation_id"]
            )
            if exact["publication"] is not None or exact["worker_closed_ms"] is not None:
                raise ValueError(
                    "actual producer completed before the original suspend took effect"
                )
            source = os.stat(Path(member["root"]) / "replica.sqlite3")
            if (intent["sql_device"], intent["sql_inode"]) != (source.st_dev, source.st_ino):
                raise ValueError("original producer did not retain its actual parent SQL inode")
            return exact
        time.sleep(0.001)
    raise ValueError("actual producer original intent deadline expired")


def record_by_id(storage: dict[str, Any], operation_id: str) -> dict[str, Any]:
    """Select exactly the original retained operation, without replacing it by a current head."""
    return next(row for row in storage["producers"] if row["operation_id"] == operation_id)


def sql_count(member: dict[str, Any]) -> int:
    """Count actual durable parent log rows independently of product publication responses."""
    path = Path(member["root"]) / "replica.sqlite3"
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as sql:
        return int(sql.execute("SELECT COUNT(*) FROM replica_v1_log").fetchone()[0])


def legacy_upgrade(
    output: Path,
    workers: Processes,
    probe: Path,
    source_sql: Path,
    source_cas: Path,
    profile: str,
    old_probe: Path | None,
) -> dict[str, Any]:
    """Preserve actual v2 raw pins/history; original absent budget/source stays unavailable."""
    members: list[dict[str, Any]] = [
        {"root": str(output / f"legacy-{member}"), "profile": profile, "member": member}
        for member in (53, 59)
    ]
    binary = old_probe or probe
    frozen = digest(binary)
    child = workers.start(
        {
            "action": "exercise",
            "source_sql": str(source_sql),
            "source_cas": str(source_cas),
            "first": members[0],
            "second": members[1],
            "hold": True,
        },
        "original-reader-v2-live-writer",
        binary,
    )
    original = workers.ready(child)
    for member in members:
        workers.refuse(
            {"action": "upgrade-root", "member": member},
            "original-live-writer-refuses-producer-upgrade",
            "still active",
        )
    native_wait, _ = workers.wait(child, finish=True)
    if native_wait != 0:
        raise ValueError(
            "actual original v2 managed handles did not close with their native zero wait"
        )
    observations = []
    for member in members:
        database = Path(member["root"]) / "content/content.sqlite3"
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as sql:
            before = {
                table: sql.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                for table in (
                    "content_references",
                    "content_readers",
                    "content_operations",
                    "content_objects",
                )
            }
            identity = sql.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()[0]
        upgraded = workers.run(
            {"action": "upgrade-root", "member": member}, "actual-explicit-producer-upgrade"
        )
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as sql:
            after = {
                table: sql.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                for table in before
            }
            current_identity = sql.execute(
                "SELECT value FROM metadata WHERE key='identity'"
            ).fetchone()[0]
            version = sql.execute("PRAGMA user_version").fetchone()[0]
            legacy = json.loads(
                sql.execute(
                    "SELECT value FROM metadata WHERE key='legacy_producer_ids'"
                ).fetchone()[0]
            )
            originals = sql.execute("SELECT COUNT(*) FROM content_producers").fetchone()[0]
        if (
            before != after
            or identity != current_identity
            or version != 3
            or originals != 0
            or not legacy
        ):
            raise ValueError(
                "producer upgrade rewrote legacy history or invented original process authority"
            )
        new_member = {**member, "producer_journal": True}
        workers.refuse(
            {"action": "inspect", "member": new_member},
            "original-parent-v4-not-implicitly-upgraded",
            "explicit original upgrade",
        )
        code, old = workers.wait(
            workers.start({"action": "inspect", "member": member}, "old-reader-v2-fenced", binary)
        )
        if code == 0 or "format" not in old.get("error", ""):
            raise ValueError("actual old reader profile acquired new producer storage")
        observations.append(
            {
                "original": member,
                "upgrade": upgraded,
                "legacy_ids": legacy,
                "raw_history_identical": True,
            }
        )
    if digest(binary) != frozen:
        raise ValueError("original legacy executable changed during the native upgrade gate")
    return {
        "historical_binary": old_probe is not None,
        "binary_sha256": frozen,
        "native_wait": native_wait,
        "original": original,
        "members": observations,
        "legacy_original_source_budget": "unavailable; raw pins retained; parent not upgraded",
    }


def run(
    output: Path,
    probe: Path,
    source_sql: Path,
    source_cas: Path,
    profile: str,
    old_probe: Path | None = None,
) -> dict[str, Any]:
    """Prove actual Unknown/publication/release boundaries without online/physical claims."""
    output.mkdir(mode=0o700)
    workers = Processes(probe, output)
    binary = digest(probe)
    checker = digest(Path(__file__))
    result: dict[str, Any] = {
        "schema": "roboguide.original-content-producer-process-check/v0.1",
        "passed": False,
        "scope": "direct storage/owned processes; no network quorum, Nodes or physical continuity",
        "probe_sha256": binary,
        "checker_sha256": checker,
        "nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
    }
    members: list[dict[str, Any]] = [
        {
            "root": str(output / f"member-{member}"),
            "profile": profile,
            "member": member,
            "producer_journal": True,
        }
        for member in (37, 41, 47)
    ]
    entries = [output / f"original-entry-{member['member']}.json" for member in members]
    try:
        prepared = []
        for member, entry in zip(members[:2], entries[:2], strict=True):
            prepared.append(
                workers.run(
                    {
                        "action": "prepare",
                        "member": member,
                        "source_sql": str(source_sql),
                        "source_cas": str(source_cas),
                        "entry_output": str(entry),
                    },
                    "actual-original-source-capture-and-copy",
                )
            )
        frozen_entries = {str(entry): digest(entry) for entry in entries[:2]}

        first = members[0]
        with closing(sqlite3.connect(Path(first["root"]) / "replica.sqlite3")) as parent:
            parent.execute("BEGIN IMMEDIATE")
            child = workers.start(
                operate(first, "append", entry=str(entries[0])),
                "actual-original-log-before-sql-kill",
            )
            original = stop_after_original(child, first, "log")
            save(output / "original-killed-log.json", original)
            child.kill()
            native_wait, _ = workers.wait(child)
            if native_wait != -signal.SIGKILL:
                raise ValueError("actual original log worker lacks its own SIGKILL wait")
            parent.rollback()
        if sql_count(first) != 0:
            raise ValueError("original killed log already published parent SQL")
        before = workers.run(
            {"action": "inspect", "member": first}, "actual-killed-original-reopen"
        )
        retained = record_by_id(before, original["intent"]["operation_id"])
        if retained["disposition"] != "unknown" or retained["original"] != original:
            raise ValueError("actual killed original intent/process/budget was replaced")
        workers.refuse(
            operate(first, "append", entry=str(entries[0])),
            "original-killed-log-id-cannot-reexecute",
            "cannot execute again",
        )
        retired = workers.run(
            operate(first, "reconcile"), "actual-killed-original-readonly-reconciliation"
        )["storage"]
        closed = record_by_id(retired, retained["operation_id"])
        if (
            closed["disposition"] != "retired-unknown"
            or closed["original"]["publication"] is not None
            or closed["original"]["resolution"]["process_end"] is None
        ):
            raise ValueError(
                "original process-end retirement invented a publication/managed-close receipt"
            )
        if closed["original"]["intent"] != original["intent"]:
            raise ValueError("actual original killed budget/source changed")

        second = members[1]
        root_database = Path(second["root"]) / "content/content.sqlite3"
        with (
            closing(sqlite3.connect(Path(second["root"]) / "replica.sqlite3")) as parent,
            closing(sqlite3.connect(root_database)) as root,
        ):
            parent.execute("BEGIN IMMEDIATE")
            child = workers.start(
                operate(second, "append", entry=str(entries[1])),
                "actual-parent-published-root-receipt-lost",
            )
            published_original = stop_after_original(child, second, "log")
            save(output / "original-parent-publication-before-receipt.json", published_original)
            root.execute("BEGIN IMMEDIATE")
            parent.rollback()
            os.kill(child.pid, signal.SIGCONT)
            failed_wait, response = workers.wait(child)
            if failed_wait == 0 or "locked" not in response.get("error", ""):
                raise ValueError(
                    f"actual root SQL failure did not retain its native error: {response}"
                )
            if sql_count(second) != 1:
                raise ValueError(
                    "actual original parent SQL did not publish before its lost root receipt"
                )
            root.rollback()
        uncertain = workers.run(
            operate(second, "reconcile"), "actual-published-parent-unknown-observation"
        )["storage"]
        uncertain_record = record_by_id(uncertain, published_original["intent"]["operation_id"])
        if (
            uncertain_record["disposition"] != "unknown"
            or uncertain_record["original"]["publication"] is not None
            or not uncertain_record["original"]["observation"]["still_referenced"]
        ):
            raise ValueError(
                "readonly source observation invented original within-budget publication"
            )
        workers.run(
            operate(second, "append", entry=str(entries[1])), "actual-original-log-readonly-retry"
        )
        if sql_count(second) != 1:
            raise ValueError("original readonly log retry wrote another row")
        original_entry: dict[str, Any] = json.loads(entries[1].read_text())
        released = workers.run(
            operate(second, "truncate", log_id=original_entry["log_id"]),
            "actual-original-parent-removal",
        )["storage"]
        released_record = record_by_id(released, uncertain_record["operation_id"])
        if (
            released_record["disposition"] != "retired-unknown"
            or released_record["original"]["publication"] is not None
            or released_record["original"]["resolution"]["process_end"] is None
        ):
            raise ValueError(
                "actual removed original log lost its uncertain outcome or original process proof"
            )
        workers.refuse(
            operate(second, "append", entry=str(entries[1])),
            "original-removed-log-id-cannot-resurrect",
            "cannot execute again",
        )
        repair_entry = output / "explicit-storage-repair-entry.json"
        original_entry["log_id"]["index"] = 2
        save(repair_entry, original_entry)
        workers.run(
            operate(second, "append", entry=str(repair_entry)),
            "actual-independent-original-storage-repair-log",
        )
        workers.run(
            operate(second, "apply", entry=str(repair_entry)),
            "actual-independent-original-view-commit",
        )

        with closing(sqlite3.connect(Path(second["root"]) / "replica.sqlite3")) as parent:
            parent.execute("BEGIN IMMEDIATE")
            child = workers.start(
                operate(
                    second,
                    "build",
                    operation_id="original-killed-snapshot",
                    output=str(output / "never-published-snapshot.json"),
                ),
                "actual-original-snapshot-before-sql-kill",
            )
            snapshot_original = stop_after_original(child, second, "snapshot-build")
            save(output / "original-killed-snapshot.json", snapshot_original)
            child.kill()
            snapshot_wait, _ = workers.wait(child)
            if snapshot_wait != -signal.SIGKILL:
                raise ValueError("actual Snapshot worker lacks its original killed native wait")
            parent.rollback()
        workers.refuse(
            operate(
                second,
                "build",
                operation_id="original-killed-snapshot",
                output=str(output / "no-original-repeat.json"),
            ),
            "original-killed-snapshot-id-refused",
            "cannot execute again",
        )
        workers.run(operate(second, "reconcile"), "actual-original-snapshot-process-end")
        snapshot_file = output / "actual-published-snapshot.json"
        snapshot = workers.run(
            operate(
                second,
                "build",
                operation_id="explicit-independent-snapshot-repair",
                output=str(snapshot_file),
            ),
            "actual-independent-snapshot-repair",
        )

        third = members[2]
        workers.run({"action": "create", "member": third}, "actual-independent-empty-receiver")
        meta = snapshot["effect"]["meta"]
        workers.refuse(
            operate(
                third,
                "install",
                operation_id="original-missing-content",
                meta=meta,
                input=str(snapshot_file),
            ),
            "original-target-missing-bytes-refused",
            "content",
        )
        before_missing = workers.run(
            {"action": "inspect", "member": third}, "original-missing-target-history"
        )
        workers.run(
            operate(third, "copy-original", source_cas=str(source_cas), entry=str(entries[1])),
            "actual-independent-receiver-byte-copy",
        )
        workers.refuse(
            operate(
                third,
                "install",
                operation_id="original-missing-content",
                meta=meta,
                input=str(snapshot_file),
            ),
            "original-target-id-cannot-repeat",
            "cannot execute again",
        )
        installed = workers.run(
            operate(
                third,
                "install",
                operation_id="explicit-independent-target-repair",
                meta=meta,
                input=str(snapshot_file),
            ),
            "actual-target-original-publication",
        )["storage"]
        if (
            not installed["actual_bytes"]
            or installed["checkpoint_digest"] != snapshot["storage"]["checkpoint_digest"]
        ):
            raise ValueError(
                "actual receiver lost complete original metadata or independently verified bytes"
            )
        for old in before_missing["producers"]:
            current = record_by_id(installed, old["operation_id"])
            if (
                current["disposition"] != "retired-unknown"
                or current["original"]["intent"] != old["original"]["intent"]
                or current["original"]["publication"] is not None
            ):
                raise ValueError(
                    "receiving repair replaced its original missing-content uncertainty"
                )
        source_ids = {record["operation_id"] for record in snapshot["storage"]["producers"]}
        if source_ids.intersection(record["operation_id"] for record in installed["producers"]):
            raise ValueError("receiving Snapshot imported sender operational producer authority")

        readers = workers.start(
            operate(second, "hold-snapshots", prefix="actual-independent-reader"),
            "actual-replaced-snapshot-independent-readers",
        )
        workers.ready(readers)
        workers.refuse(
            {"action": "inspect", "member": second},
            "actual-reader-scope-retains-writers",
            "still active",
        )
        readers.kill()
        reader_wait, _ = workers.wait(readers)
        if reader_wait != -signal.SIGKILL:
            raise ValueError("actual original Snapshot readers lack their native killed wait")
        retained_readers = [
            row
            for row in rows(second, "content_readers")
            if row["receipt"] is None and row["process_end"] is None
        ]
        if len(retained_readers) != 2:
            raise ValueError(
                "old/current actual reader scopes lost their independent original journals"
            )
        retired_readers = []
        for reader in retained_readers:
            if reader["intent"].get("retention_only") is not True:
                raise ValueError("actual Snapshot scope acquired invented payload-read authority")
            closed_reader = workers.run(
                operate(second, "retire-reader", reader_id=reader["intent"]["reader_id"]),
                "actual-original-reader-process-end",
            )["effect"]
            if closed_reader["receipt"] is not None or closed_reader["process_end"] is None:
                raise ValueError("actual reader process absence invented a byte-read receipt")
            retired_readers.append(closed_reader)
        after_readers = workers.run(
            {"action": "inspect", "member": second}, "actual-final-original-producer-pins"
        )
        for item in after_readers["pins"]:
            kinds = [pin["kind"] for pin in item["references"]]
            if (
                "reader" in kinds
                or kinds.count("snapshot") != 1
                or kinds.count("view") != 1
                or kinds.count("log") != 1
                or kinds.count("receive") != 1
            ):
                raise ValueError(
                    f"actual independent producer release changed other original owners: {kinds}"
                )
        legacy = legacy_upgrade(output, workers, probe, source_sql, source_cas, profile, old_probe)

        with closing(sqlite3.connect(Path(second["root"]) / "replica.sqlite3")) as parent:
            parent.execute("DELETE FROM replica_v1_meta WHERE key='content_producer_heads'")
            parent.commit()
        workers.refuse(
            {"action": "inspect", "member": second},
            "missing-original-parent-head-is-not-empty",
            "heads are missing",
        )
        with closing(sqlite3.connect(Path(third["root"]) / "content/content.sqlite3")) as root:
            root.execute(
                "DELETE FROM content_producers WHERE operation_id="
                "(SELECT operation_id FROM content_producers LIMIT 1)"
            )
            root.commit()
        workers.refuse(
            {"action": "inspect", "member": third}, "missing-original-producer-row-refused", "head"
        )
        if (
            digest(probe) != binary
            or digest(Path(__file__)) != checker
            or any(digest(Path(path)) != value for path, value in frozen_entries.items())
        ):
            raise ValueError(
                "actual maintained native binary/checker/original entry changed during observation"
            )
        result.update(
            passed=True,
            original_source_captures=prepared,
            original_killed_log=original,
            original_log_native_wait=native_wait,
            original_lost_receipt=published_original,
            actual_root_failure_wait=failed_wait,
            independently_observed_unknown=uncertain_record,
            original_removed_unknown=released_record,
            original_killed_snapshot=snapshot_original,
            snapshot_native_wait=snapshot_wait,
            actual_target=installed,
            actual_final_pins=after_readers,
            original_reader_native_wait=reader_wait,
            independently_retired_readers=retired_readers,
            legacy_upgrade=legacy,
            original_entries=frozen_entries,
            missing_parent_head_refused=True,
            missing_original_row_refused=True,
        )
    finally:
        workers.cleanup()
        result["processes"] = [record for _, record in workers.owned]
        save(output / "result.json", result)
    return result
