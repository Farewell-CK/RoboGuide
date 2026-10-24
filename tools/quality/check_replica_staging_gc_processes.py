"""Verify Root8 private-name reclamation with actual processes, SQL and native file quotas.

Declared stream targets are storage contract fixtures, never consensus or embodied Node facts.
An optional already-reaped runnable source is upgraded only for a separate complete recovery gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import signal
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from check_content_stream_processes import original as stream_original
from check_content_stream_processes import target
from check_replica_content_gc_processes import request as gc_request
from check_replica_content_processes import Processes, digest, operation, save
from replica_parent_adoption_processes import PayloadReadBarrier

ROOT = Path(__file__).resolve().parents[2]
LAYOUT = "roboguide.replica-content-layout/v0.8"


def request(store: dict[str, Any], action: str, **fields: Any) -> dict[str, Any]:
    """Select Root8 explicitly without adding a caller path, stop flag or device declaration."""
    value = gc_request(store, action, **fields)
    value["member"]["content_layout"] = LAYOUT
    return value


def original(root: Path, command: str) -> dict[str, Any] | None:
    """Read the complete exact original command/items independently of the CLI response."""
    key = hashlib.sha256(json.dumps(command, separators=(",", ":")).encode()).hexdigest()
    with closing(
        sqlite3.connect((root / "content.sqlite3").as_uri() + "?mode=ro", uri=True, timeout=0.1)
    ) as sql:
        row = sql.execute(
            "SELECT record FROM content_staging_gc_commands WHERE operation_id=?", (key,)
        ).fetchone()
        if row is None:
            return None
        intent = json.loads(row[0])
        items = []
        for ordinal in range(len(intent["items"])):
            item = hashlib.sha256(
                json.dumps([key, ordinal], separators=(",", ":")).encode()
            ).hexdigest()
            raw = sql.execute(
                "SELECT record FROM content_staging_gc_items WHERE item_id=?", (item,)
            ).fetchone()
            assert raw is not None
            items.append(json.loads(raw[0]))
    return {"intent": intent, "items": items}


def admitted(
    process: subprocess.Popen[bytes], root: Path, command: str, barrier: PayloadReadBarrier
) -> dict[str, Any]:
    """Require real SIGIO after complete durable admission, never inferring it from file absence."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ValueError("actual staging cleanup ended before its original kernel barrier")
        try:
            row = original(root, command)
        except sqlite3.OperationalError as error:
            if error.sqlite_errorcode not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise
            row = None
        if row is not None and barrier.signals:
            assert all(item["outcome"] is None for item in row["items"])
            assert row["intent"]["budget"]["pid"] == process.pid
            return row
        time.sleep(0.002)
    raise ValueError("original staging intent/SIGIO did not arrive within its bounded wait")


def _file_limit() -> None:
    """Apply a real one-MiB kernel file limit before the actual managed receiver executes."""
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024**2, 1024**2))
    signal.signal(signal.SIGXFSZ, signal.SIG_DFL)


def limited(calls: Processes, value: dict[str, Any], label: str) -> subprocess.Popen[bytes]:
    """Own and later wait the actual original quota-limited child, retaining its exact input/PID."""
    path = calls.output / (label + ".json")
    save(path, value)
    child = subprocess.Popen(
        [str(calls.binary), str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=_file_limit,
        close_fds=True,
    )
    calls.owned.append(
        (
            child,
            {
                "pid": child.pid,
                "label": label,
                "request": str(path),
                "binary": str(calls.binary),
                "actual_rlimit_fsize": 1024**2,
            },
        )
    )
    return child


def upgrade_sources(
    source: Path, old: Processes, cli: Processes, content: Path, key: dict[str, Any]
) -> list[dict[str, Any]]:
    """Upgrade only this gate's declared reaped source, retaining original inodes and Unknown."""
    result = json.loads((source / "result.json").read_text())
    assert result["passed"] and all(row["returncode"] == 0 for row in result["actual_supervisors"])
    assert (
        result["embodied_nodes"]
        == result["devices"]
        == result["models"]
        == result["simulators"]
        == 0
    )
    receipts = []
    for index, path in enumerate(sorted(source.glob("configuration-*.json"))):
        config = json.loads(path.read_text())
        root = Path(config["content"]["root"])
        identity = json.loads((root / ".content-creation.json").read_text())
        store = {
            "root": str(root),
            "profile": identity["profile_identity"],
            "member": identity["member"],
            "reclamation": True,
        }
        with closing(
            sqlite3.connect((root / "content.sqlite3").as_uri() + "?mode=ro", uri=True)
        ) as sql:
            version = sql.execute("PRAGMA user_version").fetchone()[0]
        if version == 4:
            cli.run(gc_request(store, "upgrade-snapshot-root"), f"source-root6-{index}")
        if version in {4, 6}:
            cli.run(gc_request(store, "upgrade-reclamation-root"), f"source-root7-{index}")
        command = "original-private-copy-" + key["content_digest"][7:23] + "-" + str(index)
        if version == 8:
            # A prior attempt may have committed the generation before its final validation
            # failed. Preserve that irreversible fence and use its actual retained old source.
            with closing(
                sqlite3.connect((root / "content.sqlite3").as_uri() + "?mode=ro", uri=True)
            ) as sql:
                rows = sql.execute("SELECT record FROM content_operations LIMIT 4097").fetchall()
            assert len(rows) <= 4096
            retained = [json.loads(raw) for (raw,) in rows]
            candidates = [
                row["intent"]["command_id"]
                for row in retained
                if row["receipt"] is not None
                and row["intent"]["command_id"].startswith("original-private-copy-")
                and (
                    root
                    / "staging-v1"
                    / hashlib.sha256(json.dumps(row["intent"]["command_id"]).encode()).hexdigest()
                ).is_file()
            ]
            assert len(candidates) == 1, "explicit resumed source requires one retained legacy copy"
            command = candidates[0]
        else:
            old.run(
                {
                    "action": "copy-original",
                    "store": store,
                    "source": str(content),
                    "content": key,
                    "command_id": command,
                },
                f"legacy-source-copy-{index}",
            )
            old.run(
                {"action": "retire-copy", "store": store, "command_id": command},
                f"release-source-copy-{index}",
            )
        inode = (root / "content.sqlite3").stat().st_ino
        cli.run(request(store, "upgrade-staging-root"), f"source-root8-{index}")
        assert (root / "content.sqlite3").stat().st_ino == inode == identity["database_inode"]
        fields: dict[str, Any] = {
            "command_id": "original-source-staging-"
            + key["content_digest"][7:23]
            + "-"
            + str(index),
            "targets": [{"kind": "copy", "command_id": command}],
            "budget_ms": 30_000,
        }
        staging = root / "staging-v1" / hashlib.sha256(json.dumps(command).encode()).hexdigest()
        assert staging.is_file()
        if index == 0:
            with PayloadReadBarrier(staging) as barrier:
                child = cli.start(
                    request(store, "collect-staging", **fields), "source-staging-before-kill"
                )
                first = admitted(child, root, fields["command_id"], barrier)
                child.kill()
                assert cli.wait(child)[0] == -signal.SIGKILL
            assert original(root, fields["command_id"]) == first
            receipt = {"unknown": 1, "original": first}
        else:
            receipt = cli.run(
                request(store, "collect-staging", **fields), f"source-staging-{index}"
            )
            assert receipt["deleted"] == 1
        config["schema"] = "roboguide.controller-replica-process/v0.6"
        save(path, config)
        receipts.append(
            {
                "member": identity["member"],
                "sql_inode": inode,
                "original_generation": version,
                "original_copy_command": command,
                "original": receipt,
            }
        )
    return receipts


def partial(
    calls: Processes,
    cli: Processes,
    output: Path,
    source: Path,
    key: dict[str, Any],
    profile: str,
    member: int,
    kind: str,
) -> dict[str, Any]:
    """Retain real SIGXFSZ, nonempty partial bytes and original independent retirement."""
    store: dict[str, Any] = {
        "root": str(output / (kind + "-partial")),
        "profile": profile,
        "member": member,
        "staging_gc": True,
    }
    identity = calls.run({"action": "create", "store": store}, kind + "-root8")["identity"]
    root = Path(store["root"])
    command = "actual-limited-" + kind
    value: dict[str, Any] = {
        "action": "copy-original" if kind == "copy" else "copy",
        "store": store,
        "source": str(source),
        "content": key,
        "command_id": command,
    }
    if kind == "stream":
        value.update(target=target(store, identity, key), budget_ms=30_000, hold_after=False)
    child = limited(calls, value, kind + "-native-file-quota")
    assert calls.wait(child)[0] == -signal.SIGXFSZ
    selected = [{"kind": kind, "command_id": command}]
    operation_id = hashlib.sha256(json.dumps(command).encode()).hexdigest()
    staging = root / "staging-v1" / (("stream-" if kind == "stream" else "") + operation_id)
    before = staging.read_bytes()
    assert 0 < len(before) < key["byte_size"]
    cli.refuse(
        request(
            store,
            "collect-staging",
            command_id="unretired-" + kind,
            targets=selected,
            budget_ms=30_000,
        ),
        kind + "-missing-retirement",
        "original staging",
    )
    assert original(root, "unretired-" + kind) is None
    if kind == "stream":
        raw = stream_original(root, command)
        assert raw is not None and raw[1]["receipt"] is None and raw[1]["worker_closed_ms"] is None
        retired = cli.run(
            request(store, "retire-content-stream", command_id=command, budget_ms=30_000),
            kind + "-original-end",
        )
        assert retired["original"] == raw[1] and retired["process_end"]["original_pid"] == child.pid
    else:
        raw_copy = operation(root, command)
        assert (
            raw_copy is not None and raw_copy["receipt"] is None and raw_copy["process_end"] is None
        )
        retired = cli.run(
            request(store, "retire-ended-content-copy", command_id=command), kind + "-original-end"
        )
        after_copy = operation(root, command)
        assert (
            after_copy is not None
            and after_copy["intent"] == raw_copy["intent"]
            and after_copy["receipt"] is None
        )
        assert after_copy["process_end"]["original_pid"] == child.pid
    cleaned = cli.run(
        request(
            store,
            "collect-staging",
            command_id=kind + "-partial-cleanup",
            targets=selected,
            budget_ms=30_000,
        ),
        kind + "-partial-cleanup",
    )
    assert cleaned["deleted"] == 1 and cleaned["unknown"] == 0 and not staging.exists()
    assert (
        cleaned["original"]["items"][0]["outcome"]["observed_digest"]
        == "sha256:" + hashlib.sha256(before).hexdigest()
    )
    assert not (root / "content-v1" / key["source_owner"][15:] / key["content_digest"][7:]).exists()
    if kind == "stream":
        assert stream_original(root, command) == raw
    else:
        assert operation(root, command) == after_copy
    return {
        "native_wait": child.returncode,
        "partial_byte_size": len(before),
        "retirement": retired,
        "cleanup": cleaned,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Retain exact original faults and sources without reconstructing unavailable outcomes."""
    output: Path = args.output.absolute()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    paths = [
        Path(__file__),
        args.probe,
        args.source_probe,
        args.maintenance,
        args.old_probe,
        *sorted((ROOT / "core/controller-replication").rglob("*.rs")),
        *sorted((ROOT / "apps/controller-replica").rglob("*.rs")),
        *[
            Path(__file__).with_name(name)
            for name in (
                "check_content_stream_processes.py",
                "check_replica_content_gc_processes.py",
                "check_replica_content_processes.py",
                "replica_parent_adoption_processes.py",
            )
        ],
    ]
    identities = {str(path): digest(path) for path in paths}
    save(output / "source-identity.json", identities)
    calls, old, cli = (
        Processes(args.probe, output),
        Processes(args.old_probe, output),
        Processes(args.maintenance, output, ("--content-maintenance",)),
    )
    result: dict[str, Any] = {
        "passed": False,
        "embodied_nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
    }
    try:
        payload, source = output / "actual-input.bin", output / "actual-source"
        payload.write_bytes(os.urandom(2 * 1024**2 + 13))
        code, created = calls.wait(
            calls.start(
                {"action": "source", "root": str(source), "payload": str(payload)},
                "actual-source",
                args.source_probe,
            )
        )
        assert code == 0
        key = created["result"]["content"]
        store = {
            "root": str(output / "legacy-receiver"),
            "profile": "a" * 64,
            "member": args.member,
            "reclamation": True,
        }
        identity = old.run({"action": "create", "store": store}, "actual-old-root7")["identity"]
        copy_request = {
            "action": "copy-original",
            "store": store,
            "source": str(source),
            "content": key,
            "command_id": "original-copy",
        }
        copied = old.run(copy_request, "actual-old-completed-copy")
        root = Path(store["root"])
        cas = root / "content-v1" / key["source_owner"][15:] / key["content_digest"][7:]
        staging = (
            root / "staging-v1" / hashlib.sha256(json.dumps("original-copy").encode()).hexdigest()
        )
        assert staging.stat().st_ino == cas.stat().st_ino
        holder = old.start({"action": "hold", "store": store}, "actual-old-writer")
        old.ready(holder)
        cli.refuse(
            request(store, "upgrade-staging-root"), "live-upgrade-refused", "writer is still active"
        )
        assert old.wait(holder, finish=True)[0] == 0
        cli.run(request(store, "upgrade-staging-root"), "actual-drained-root8")
        assert (root / "content.sqlite3").stat().st_ino == identity["database_inode"]
        old.refuse({"action": "hold", "store": store}, "actual-old-root7-refused", "SQL format")
        store = {**store, "reclamation": False, "staging_gc": True}
        selected = [{"kind": "copy", "command_id": "original-copy"}]
        cli.refuse(
            request(
                store,
                "collect-staging",
                command_id="active-refusal",
                targets=selected,
                budget_ms=30_000,
            ),
            "active-copy-refused",
            "released ownership",
        )
        assert original(root, "active-refusal") is None and staging.exists()
        calls.run(
            {"action": "retire-copy", "store": store, "command_id": "original-copy"},
            "original-copy-release",
        )
        calls.run(
            {**copy_request, "store": store, "command_id": "independent-current-copy"},
            "actual-current-cas-owner",
        )
        assert len(list((root / "staging-v1").iterdir())) == 1
        fields = {"targets": selected, "budget_ms": 30_000}
        with PayloadReadBarrier(staging) as barrier:
            child = cli.start(
                request(store, "collect-staging", command_id="killed-cleanup", **fields),
                "actual-kill-after-intent",
            )
            first = admitted(child, root, "killed-cleanup", barrier)
            child.kill()
            assert cli.wait(child)[0] == -signal.SIGKILL
        assert staging.exists() and original(root, "killed-cleanup") == first
        with PayloadReadBarrier(staging) as barrier:
            child = cli.start(
                request(store, "collect-staging", command_id="unreported-unlink", **fields),
                "actual-unlink-sql-failure",
            )
            pending = admitted(child, root, "unreported-unlink", barrier)
            with closing(sqlite3.connect(root / "content.sqlite3")) as sql:
                sql.execute("BEGIN IMMEDIATE")
                barrier.close()
                code, value = cli.wait(child)
                assert code != 0 and "locked" in value.get("error", "") and not staging.exists()
                sql.rollback()
        for command, retained in (("killed-cleanup", first), ("unreported-unlink", pending)):
            observed = cli.run(
                request(store, "observe-staging-gc", command_id=command),
                command + "-original-observation",
            )
            assert observed["unknown"] == 1 and observed["original"] == retained
            assert (
                cli.run(
                    request(store, "collect-staging", command_id=command, **fields),
                    command + "-readonly-replay",
                )
                == observed
            )
        missing = cli.run(
            request(store, "collect-staging", command_id="independent-absence", **fields),
            "independent-absence",
        )
        assert missing["missing"] == 1 and missing["unknown"] == 0
        assert cas.read_bytes() == payload.read_bytes()
        assert calls.run({**copy_request, "store": store}, "original-copy-history") == copied
        held = cli.run(
            request(
                store, "collect-content", command_id="current-owner-retains-cas", budget_ms=30_000
            ),
            "independent-current-pin",
        )
        assert held["candidates"] == 0 and cas.exists()
        result["original_cleanup_unknown"] = [first, pending]
        result["partials"] = [
            partial(calls, cli, output, source, key, store["profile"], args.member, kind)
            for kind in ("copy", "stream")
        ]
        if args.upgrade_source is not None:
            result["upgraded_runnable_sources"] = upgrade_sources(
                args.upgrade_source, old, cli, source, key
            )
        assert all(digest(Path(path)) == value for path, value in identities.items())
        result["passed"] = True
        return result
    finally:
        for processes in (calls, old, cli):
            processes.cleanup()
        result["actual_processes"] = [
            row for processes in (calls, old, cli) for _, row in processes.owned
        ]
        save(output / "result.json", result)


def main() -> None:
    """Require explicit current/original binaries and no built-in device inventory."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("probe", "source-probe", "maintenance", "old-probe"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--upgrade-source", type=Path)
    parser.add_argument("--member", type=int, default=683)
    args = parser.parse_args()
    if args.member <= 0:
        parser.error("infrastructure member must be positive; it creates no embodied Node")
    for name in ("probe", "source_probe", "maintenance", "old_probe"):
        setattr(args, name, getattr(args, name).resolve(strict=True))
    print(json.dumps(run(args)))


if __name__ == "__main__":
    main()
