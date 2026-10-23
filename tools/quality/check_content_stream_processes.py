"""Check actual original storage streams, native death/deadlines/SQL and preserved Unknown.

This is a local backend gate with explicitly declared retention targets. It does not start a
consensus cluster, authenticate a network stream, create embodied Nodes or prove physical facts.
"""

from __future__ import annotations

import argparse
import copy
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
from check_state_verifier_processes import address
from content_stream_upgrade_processes import run as run_upgrade
from replica_parent_adoption_processes import PayloadReadBarrier, stop_owned_child

ROOT = Path(__file__).resolve().parents[2]


def original(root: Path, command: str) -> tuple[str, dict[str, Any]] | None:
    """Independently read exact original raw SQL, closing the actual connection before restart."""
    with closing(
        sqlite3.connect((root / "content.sqlite3").as_uri() + "?mode=ro", uri=True, timeout=0.1)
    ) as sql:
        rows = sql.execute("SELECT record FROM content_streams").fetchall()
    for (raw,) in rows:
        value = json.loads(raw)
        if value["intent"]["command_id"] == command:
            return raw, value
    return None


def admitted(
    process: subprocess.Popen[bytes], root: Path, command: str, barrier: PayloadReadBarrier
) -> tuple[str, dict[str, Any]]:
    """Require original durable intent plus actual kernel source-open blocking before any kill."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ValueError("actual stream ended before the original kernel barrier")
        try:
            row = original(root, command)
        except sqlite3.OperationalError as error:
            if error.sqlite_errorcode not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise
            time.sleep(0.002)
            continue
        if row is not None and barrier.signals:
            assert row[1]["receipt"] is None
            assert row[1]["worker_closed_ms"] is None
            return row
        time.sleep(0.002)
    raise ValueError("actual stream intent/SIGIO unavailable within the original bound")


def target(
    store: dict[str, Any], identity: dict[str, Any], content: dict[str, Any]
) -> dict[str, Any]:
    """Declare retention context only; no election, real membership or network ACK is claimed."""
    member = str(store["member"])
    return {
        "profile_identity": store["profile"],
        "epoch": {"term": 1, "replica_id": store["member"]},
        "revision": 1,
        "checkpoint_digest": "b" * 64,
        "batch_digest": "c" * 64,
        "membership": {
            "log_id": {"leader_id": {"term": 1, "node_id": store["member"]}, "index": 1},
            "membership": {
                "configs": [[store["member"]]],
                "nodes": {
                    member: {"addr": address(), "content_store_owner": identity["store_owner"]}
                },
            },
        },
        "member_stores": {member: identity["store_owner"]},
        "inputs": [content],
    }


def run(
    binary: Path,
    source_probe: Path,
    output: Path,
    old_probe: Path | None,
    producer_probe: Path | None = None,
    original_controller_result: Path | None = None,
) -> dict[str, Any]:
    """Retain actual binary/source identities, native waits and every failed observation."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    output = output.resolve()
    paths = [binary, source_probe, Path(__file__)]
    paths += sorted((ROOT / "core/controller-replication/src").rglob("*.rs"))
    paths += sorted((ROOT / "core/controller-replication/examples").glob("*.rs"))
    paths += [
        Path(__file__).with_name(name)
        for name in (
            "replica_parent_adoption_processes.py",
            "content_stream_upgrade_processes.py",
            "check_replica_content_processes.py",
            "check_state_verifier_processes.py",
        )
    ]
    if (producer_probe is None) != (original_controller_result is None):
        raise ValueError(
            "original producer upgrade requires both actual binary and Controller result"
        )
    if producer_probe is not None:
        paths.append(producer_probe)
    if old_probe is not None:
        paths.append(old_probe)
    identities = {str(path): digest(path) for path in paths}
    save(output / "source-identity.json", identities)
    workers = Processes(binary, output)
    result: dict[str, Any] = {
        "passed": False,
        "embodied_nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "declared_retention_target_is_consensus": False,
    }
    try:
        payload = output / "actual-payload.bin"
        payload.write_bytes(os.urandom(2 * 1024 * 1024 + 7))
        code, source = workers.wait(
            workers.start(
                {"action": "source", "root": str(output / "source"), "payload": str(payload)},
                "actual-source",
                source_probe,
            )
        )
        assert code == 0, source
        key = source["result"]["content"]
        assert key["content_digest"] == "sha256:" + digest(payload)
        files = [
            path for path in (output / "source").rglob(key["content_digest"][7:]) if path.is_file()
        ]
        assert len(files) == 1
        source_file = files[0]
        store: dict[str, Any] = {"root": str(output / "receiver"), "profile": "a" * 64, "member": 1}
        receiver = workers.run({"action": "create", "store": store}, "actual-receiver")["identity"]
        context = target(store, receiver, key)
        base = {
            "action": "copy",
            "store": store,
            "source": str(output / "source"),
            "content": key,
            "target": context,
            "budget_ms": 30000,
            "hold_after": False,
        }

        def inspect(commands: list[str], label: str) -> dict[str, Any]:
            """Read the actual original receiver SQL and independently verify full bytes."""
            return workers.run(
                {"action": "inspect", "store": store, "content": key, "commands": commands}, label
            )

        killed = dict(base, command_id="actual-original-killed")
        with PayloadReadBarrier(source_file) as barrier:
            child = workers.start(killed, "actual-killed-original")
            raw, pending = admitted(child, Path(store["root"]), killed["command_id"], barrier)
            assert not list((Path(store["root"]) / "content-v1").rglob(key["content_digest"][7:]))
            result["actual_killed_source_open_signals"] = list(barrier.signals)
            child.kill()
            assert workers.wait(child)[0] == -signal.SIGKILL
        after = inspect([killed["command_id"]], "actual-killed-inspection")
        assert original(Path(store["root"]), killed["command_id"]) == (raw, pending)
        assert after["actual_bytes"] is False and len(after["references"]) == 1
        hidden = source_file.with_suffix(".actual-original-held")
        source_file.rename(hidden)
        try:
            duplicate = workers.run(killed, "actual-killed-readonly-duplicate")["stream"]
            assert duplicate["original"] == pending and duplicate["receipt"] is None
        finally:
            hidden.rename(source_file)
        repaired = workers.run(
            dict(base, command_id="actual-distinct-repair"), "actual-distinct-repair"
        )["stream"]
        assert repaired["receipt"] is not None
        assert original(Path(store["root"]), killed["command_id"]) == (raw, pending)
        result["killed_unknown_unchanged_beside_distinct_repair"] = True

        expiring = dict(base, command_id="actual-expiring-original", budget_ms=3000)
        with PayloadReadBarrier(source_file) as barrier:
            child = workers.start(expiring, "actual-expiring-original")
            _, frozen = admitted(child, Path(store["root"]), expiring["command_id"], barrier)
            result["actual_expiry_stop_wait_signal"] = stop_owned_child(child)
            assert result["actual_expiry_stop_wait_signal"] == signal.SIGSTOP
            barrier.close()
            budget = frozen["intent"]["budget"]
            deadline = time.monotonic() + 5
            while int(float(Path("/proc/uptime").read_text().split()[0]) * 1000) <= (
                budget["admitted_ms"] + budget["duration_ms"] + 20
            ):
                if time.monotonic() > deadline:
                    raise ValueError("original actual kernel deadline did not elapse")
                time.sleep(0.02)
            os.kill(child.pid, signal.SIGCONT)
            code, expired = workers.wait(child)
            assert code != 0 and "expired" in expired.get("error", ""), expired
        expired_row = original(Path(store["root"]), expiring["command_id"])
        assert expired_row is not None
        assert expired_row[1]["intent"] == frozen["intent"]
        assert expired_row[1]["receipt"] is None
        assert expired_row[1]["worker_closed_ms"] is not None
        assert (
            workers.run(expiring, "actual-expired-readonly-duplicate")["stream"]["original"]
            == (expired_row[1])
        )
        result["original_expiry_did_not_renew_budget_or_publish"] = True

        fault_store = dict(store, root=str(output / "sql-fault-receiver"))
        fault_identity = workers.run(
            {"action": "create", "store": fault_store}, "actual-sql-fault-receiver"
        )["identity"]
        fault_root = Path(fault_store["root"])
        sql_fault = dict(
            base,
            store=fault_store,
            target=target(fault_store, fault_identity, key),
            command_id="actual-original-sql-fault",
        )
        with PayloadReadBarrier(source_file) as barrier:
            child = workers.start(sql_fault, "actual-original-sql-fault")
            raw_fault, frozen_fault = admitted(child, fault_root, sql_fault["command_id"], barrier)
            assert stop_owned_child(child) == signal.SIGSTOP
            with closing(sqlite3.connect(fault_root / "content.sqlite3")) as sql:
                sql.execute("BEGIN IMMEDIATE")
                barrier.close()
                os.kill(child.pid, signal.SIGCONT)
                code, refused = workers.wait(child)
                assert code != 0 and "locked" in refused.get("error", ""), refused
                sql.rollback()
        assert original(fault_root, sql_fault["command_id"]) == (raw_fault, frozen_fault)
        assert frozen_fault["receipt"] is None and frozen_fault["worker_closed_ms"] is None
        observed_fault = workers.run(sql_fault, "actual-sql-fault-readonly-duplicate")["stream"]
        assert observed_fault["original"] == frozen_fault
        actual_files = list((fault_root / "content-v1").rglob(key["content_digest"][7:]))
        assert len(actual_files) == 1 and digest(actual_files[0]) == key["content_digest"][7:]
        unavailable = workers.run(
            {
                "action": "inspect",
                "store": fault_store,
                "content": key,
                "commands": [sql_fault["command_id"]],
            },
            "actual-unreceipted-file-is-unavailable",
        )
        assert unavailable["actual_bytes"] is False and len(unavailable["references"]) == 1
        assert (
            workers.run(
                dict(sql_fault, command_id="actual-sql-fault-distinct-repair"),
                "actual-sql-fault-distinct-repair",
            )["stream"]["receipt"]
            is not None
        )
        assert original(fault_root, sql_fault["command_id"]) == (raw_fault, frozen_fault)
        result["actual_sql_failure_after_new_file_publication_kept_unknown"] = True

        committed = dict(base, command_id="actual-committed-unread-response", hold_after=True)
        child = workers.start(committed, "actual-committed-unread-response")
        actual_ready = workers.ready(child)
        committed_original = actual_ready["original"]
        assert committed_original["receipt"] is not None
        child.kill()
        assert workers.wait(child)[0] == -signal.SIGKILL
        source_file.rename(hidden)
        try:
            same = dict(committed, hold_after=False)
            assert workers.run(same, "actual-committed-readonly-duplicate")["stream"] == (
                committed_original
            )
        finally:
            hidden.rename(source_file)
        result["unread_response_kept_original_receipt_without_source_read"] = True

        wrong = copy.deepcopy(base)
        wrong["target"]["member_stores"]["1"] = "replica-content:" + "f" * 64
        wrong["command_id"] = "actual-wrong-committed-owner"
        workers.refuse(wrong, "actual-wrong-committed-owner", "committed member")
        assert original(Path(store["root"]), wrong["command_id"]) is None
        holder = workers.start({"action": "hold", "store": store}, "actual-root-holder")
        workers.ready(holder)
        code, refused = workers.wait(
            workers.start(
                {"action": "inspect", "store": store, "content": key, "commands": []},
                "actual-live-writer-fenced",
            )
        )
        assert code != 0 and "active" in refused.get("error", ""), refused
        assert workers.wait(holder, finish=True)[0] == 0
        result["actual_root_clone_writer_fenced"] = True
        if old_probe is not None:
            code, refused = workers.wait(
                workers.start(
                    {"action": "hold", "store": store}, "actual-old-binary-refused", old_probe
                )
            )
            assert code != 0 and "SQL format" in refused.get("error", ""), refused
            result["actual_old_binary_fenced"] = True
        else:
            result["actual_old_binary_fenced"] = None
        database = Path(store["root"]) / "content.sqlite3"
        with closing(sqlite3.connect(database)) as sql, sql:
            head = sql.execute("SELECT value FROM metadata WHERE key='stream_ids'").fetchone()[0]
            sql.execute("DELETE FROM metadata WHERE key='stream_ids'")
        workers.refuse(
            {"action": "inspect", "store": store, "content": key, "commands": []},
            "actual-lost-mandatory-head",
            "mandatory",
        )
        with closing(sqlite3.connect(database)) as sql, sql:
            sql.execute("INSERT INTO metadata VALUES ('stream_ids',?)", (head,))
        final = inspect(
            [killed["command_id"], committed["command_id"]],
            "actual-final-inspection",
        )
        assert final["identity"] == receiver and final["actual_bytes"] is True
        if producer_probe is not None and original_controller_result is not None:
            result["original_producer_upgrade"] = run_upgrade(
                workers,
                producer_probe,
                original_controller_result.resolve(strict=True),
                output,
                store["profile"],
                store["member"],
            )
        assert all(digest(Path(path)) == value for path, value in identities.items())
        result.update(
            passed=True,
            actual_original_identity=receiver,
            actual_payload_size=key["byte_size"],
            actual_payload_digest=key["content_digest"],
            actual_retained_stream_pins=len(final["references"]),
            original_sql_inode_preserved=database.stat().st_ino == receiver["database_inode"],
        )
        assert result["original_sql_inode_preserved"]
        return result
    finally:
        workers.cleanup()
        save(output / "processes.json", [record for _, record in workers.owned])
        save(output / "result.json", result)


def main() -> None:
    """Require exact maintained/frozen executables and retain a new native evidence directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--source-probe", required=True, type=Path)
    parser.add_argument("--old-probe", type=Path)
    parser.add_argument("--producer-probe", type=Path)
    parser.add_argument("--original-controller-result", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = run(
        args.binary.resolve(strict=True),
        args.source_probe.resolve(strict=True),
        args.output,
        args.old_probe.resolve(strict=True) if args.old_probe else None,
        args.producer_probe.resolve(strict=True) if args.producer_probe else None,
        args.original_controller_result,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
