"""Check actual Unix peers and State file commits, without claiming a full Controller deployment."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path
from typing import Any, Protocol

from check_prepared_source_processes import ask as source_ask
from check_replica_content_processes import Processes, digest, save


class ActorPort(Protocol):
    """Exact original infrastructure control pipe supplied by the owning checker."""

    process: subprocess.Popen[bytes]

    def ask(self, request: dict[str, Any], *, required: bool = True) -> dict[str, Any]:
        """Send once and retain the actual original infrastructure response."""
        ...


def journal(path: Path) -> dict[str, tuple[str, dict[str, Any]]]:
    """Read exact original raw rows/head and close the actual reader before restart."""
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as sql:
        version = sql.execute("PRAGMA user_version").fetchone()[0]
        assert version == 8
        head = json.loads(
            sql.execute(
                "SELECT value FROM replica_v1_meta WHERE key=?", ("controller_content_ingress_ids",)
            ).fetchone()[0]
        )
        rows = sql.execute(
            "SELECT command_id,request_digest,record FROM controller_content_ingress"
        ).fetchall()
    assert set(head) == {row[0] for row in rows}
    result: dict[str, tuple[str, dict[str, Any]]] = {}
    for command, expected, raw in rows:
        record = json.loads(raw)
        actual = hashlib.sha256(
            json.dumps(record["intent"], ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        assert actual == expected == record["request_digest"]
        assert record["intent"]["command_id"] == command
        result[command] = (raw, record)
    return result


def run_source(
    actor: ActorPort,
    configuration: dict[str, Any],
    binary: Path,
    output: Path,
    calls: Processes,
    members: list[int],
) -> dict[str, Any]:
    """Check actual owned children, source guards, SQL loss and original native waits."""
    directory = output / "owned-source"
    directory.mkdir(mode=0o700)
    config_path = directory / "original-configuration.json"
    parent = Path(configuration["parent_sql"])
    started = actor.ask(
        {
            "action": "spawn-owned",
            "binary": str(binary),
            "configuration": str(config_path),
            "source_cas": str(directory / "source-cas"),
            "source_sql": str(directory / "source.sqlite3"),
            "budget_ms": 45000,
        }
    )
    original_process = started["process"]
    assert started["child_ready"]["pid"] == original_process["pid"]
    assert started["child_ready"]["ready"] is True

    def child(request: dict[str, Any]) -> dict[str, Any]:
        """Address only the actual original owned pipe and require its real response PID."""
        value = actor.ask({"action": "owned-command", "request": request})
        assert value["pid"] == original_process["pid"], value
        if "result" not in value:
            raise ValueError(f"actual original owned source refused: {value}")
        result: dict[str, Any] = value["result"]
        return result

    assert child({"action": "fence"})["process"] == original_process
    assert journal(parent) == {}
    foreign_config = json.loads(config_path.read_bytes())
    foreign_config["source_cas"] = str(directory / "foreign-cas")
    foreign_config["source_sql"] = str(directory / "foreign.sqlite3")
    intruder = calls.start(foreign_config, "same-certificate-other-pid", binary)
    ready = calls.ready(intruder)
    assert ready["pid"] != original_process["pid"]
    source_ask(calls, intruder, {"action": "fence"}, "actual Unix peer differs")
    source_ask(calls, intruder, {"action": "shutdown"})
    code, _ = calls.wait(intruder)
    assert code == 0
    assert journal(parent) == {}

    start = child({"action": "start", "upload_id": "actual-owned-file"})["record"]
    originals = journal(parent)
    assert len(originals) == 1
    zero_command, (_, zero) = next(iter(originals.items()))
    assert zero["intent"]["target"]["inputs"] == []
    assert zero["receipt"]["commit"]["accepted"] is True
    assert zero["intent"]["process"] == original_process
    assert zero["intent"]["budget"]["pid"] == actor.process.pid
    assert set(zero["receipt"]["complete_members"]) == set(members)
    payload = directory / "original-payload.bin"
    payload.write_bytes(os.urandom(2 * 1024 * 1024 + 23))
    prepared = child({"action": "prepare", "payload": str(payload)})
    assert prepared["public_cas"] is False
    record = prepared["record"]
    assert record["content_digest"] == "sha256:" + digest(payload)
    assert record["admitted_at_ms"] == start["admitted_at_ms"]
    assert record["deadline_ms"] == start["deadline_ms"]
    current = journal(parent)
    assert len(current) == 2 and current[zero_command] == originals[zero_command]
    prepared_command = next(command for command in current if command not in originals)
    prepared_raw = current[prepared_command]
    assert prepared_raw[1]["intent"]["candidate"]["batch"]["checkpoint"]["event_sequence"] == 2
    prepared_view = child({"action": "inspect"})["view"]
    assert prepared_view["revision"] == prepared_raw[1]["intent"]["target"]["revision"]
    assert prepared_view["content"]["candidate"] == prepared_raw[1]["intent"]["candidate"]
    assert set(actor.ask({"action": "quorum"})["complete_members"]) == set(members)
    source = directory / "source-cas"
    public_file = (
        source
        / "objects-v1"
        / "sha256"
        / record["content_digest"][7:9]
        / record["content_digest"][7:]
    )
    assert not public_file.exists()
    source_inode = (directory / "source.sqlite3").stat().st_ino
    writer_inode = (source / ".writer.lock").stat().st_ino
    child({"action": "revoke"})
    replay = child({"action": "replay", "command_id": prepared_command})["original"]
    assert replay["original"] == prepared_raw[1]
    assert journal(parent) == current

    with closing(sqlite3.connect(parent, timeout=0.1)) as sql, sql:
        sql.execute(
            "CREATE TRIGGER fail_original_ingress_receipt BEFORE UPDATE ON "
            "controller_content_ingress BEGIN SELECT RAISE(ABORT,"
            "'actual original ingress receipt failure'); END"
        )
    completed = child({"action": "complete"})
    assert completed["record"]["phase"] == "completed" and completed["public_cas"] is True
    assert completed["record"]["admitted_at_ms"] == start["admitted_at_ms"]
    assert completed["record"]["deadline_ms"] == start["deadline_ms"]
    after = journal(parent)
    assert len(after) == 3
    unknown_command = next(command for command in after if command not in current)
    unknown = after[unknown_command]
    assert unknown[1]["receipt"] is None
    assert digest(public_file) == digest(payload)
    with closing(sqlite3.connect(parent, timeout=0.1)) as sql, sql:
        sql.execute("DROP TRIGGER fail_original_ingress_receipt")
    hidden = public_file.with_suffix(".actual-original-withheld")
    public_file.rename(hidden)
    replay = child({"action": "replay", "command_id": unknown_command})["original"]
    assert replay["original"] == unknown[1]
    assert journal(parent) == after
    hidden.rename(public_file)
    inspected = child({"action": "inspect"})
    assert inspected["record"] == completed["record"] and inspected["events"] == 3
    assert inspected["view"]["revision"] == 3
    assert set(actor.ask({"action": "quorum"})["complete_members"]) == set(members)
    stopped = actor.ask({"action": "stop-owned"})["original_exit"]
    assert stopped["process"] == original_process and stopped["signal"] == 9
    reopened = actor.ask(
        {
            "action": "spawn-owned",
            "binary": str(binary),
            "configuration": str(directory / "reopened-configuration.json"),
            "source_cas": str(source),
            "source_sql": str(directory / "source.sqlite3"),
            "budget_ms": 45000,
        }
    )
    assert reopened["process"]["instance_id"] != original_process["instance_id"]
    original_process = reopened["process"]
    assert child({"action": "fence"})["process"] == original_process
    restored = child({"action": "inspect"})
    assert restored["record"] == completed["record"] and restored["events"] == 3
    assert (directory / "source.sqlite3").stat().st_ino == source_inode
    assert (source / ".writer.lock").stat().st_ino == writer_inode
    assert journal(parent) == after
    second_exit = actor.ask({"action": "stop-owned"})["original_exit"]
    assert second_exit["process"] == original_process and second_exit["signal"] == 9
    with closing(sqlite3.connect(parent.as_uri() + "?mode=ro", uri=True)) as sql:
        history = json.loads(
            sql.execute(
                "SELECT value FROM replica_v1_meta WHERE key=?", ("controller_processes_v1",)
            ).fetchone()[0]
        )
    assert len(history) == 2
    assert all(row["outcome"] is not None and row["outcome"]["signal"] == 9 for row in history)
    result = {
        "actual_spawn": started,
        "actual_reopen": reopened,
        "actual_first_exit": stopped,
        "actual_second_exit": second_exit,
        "source_sql_inode": source_inode,
        "source_writer_inode": writer_inode,
        "actual_complete": inspected,
        "retained_unknown": unknown[1],
        "original_operations": after,
        "source_contract_is_full_controller_kernel": False,
    }
    save(directory / "result.json", result)
    return result
