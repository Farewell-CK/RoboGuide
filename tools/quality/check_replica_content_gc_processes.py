"""Exercise actual Root7 GC CLI, native interruption/SQL failures and drained original upgrades.

All sources and receivers are actual software files/processes. No embodied Nodes, devices,
models, simulators, semantic use or physical continuity facts are created.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from check_content_stream_processes import admitted as stream_admitted
from check_content_stream_processes import original as original_stream
from check_content_stream_processes import target
from check_replica_content_processes import Processes, digest, save
from replica_parent_adoption_processes import PayloadReadBarrier

ROOT = Path(__file__).resolve().parents[2]


def original(root: Path, command: str) -> tuple[str, dict[str, Any]] | None:
    """Read exact durable original SQL independently of CLI responses, closing before restart."""
    key = hashlib.sha256(json.dumps(command, separators=(",", ":")).encode()).hexdigest()
    with closing(
        sqlite3.connect((root / "content.sqlite3").as_uri() + "?mode=ro", uri=True, timeout=0.1)
    ) as sql:
        row = sql.execute(
            "SELECT record FROM content_gc_commands WHERE operation_id=?", (key,)
        ).fetchone()
    return None if row is None else (row[0], json.loads(row[0]))


def admitted(
    process: subprocess.Popen[bytes], root: Path, command: str, barrier: PayloadReadBarrier
) -> tuple[str, dict[str, Any]]:
    """Require actual kernel read blocking after the original tombstone/intent has committed."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ValueError("actual GC process exited before original kernel barrier")
        try:
            value = original(root, command)
        except sqlite3.OperationalError as error:
            if error.sqlite_errorcode not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise
            value = None
        if value is not None and barrier.signals:
            assert value[1]["outcomes"] == [None]
            assert value[1]["intent"]["budget"]["pid"] == process.pid
            return value
        time.sleep(0.002)
    raise ValueError("actual GC original intent/SIGIO unavailable within admission bound")


def request(store: dict[str, Any], operation: str, **fields: Any) -> dict[str, Any]:
    """Declare only the original local OS-admin member, backing and exact closed operation."""
    return {
        "operation": operation,
        "member": {
            "content_root": store["root"],
            "profile_identity": store["profile"],
            "member": store["member"],
        },
        "restore_binding_digest": store.get("restore_binding_digest"),
        **fields,
    }


def drained_source(
    directory: Path, calls: Processes, cli: Processes, source: Path, key: dict[str, Any]
) -> list[dict[str, Any]]:
    """Upgrade this fresh gate's reaped replicas while preserving original row identities."""
    result = json.loads((directory / "result.json").read_text())
    assert result["passed"] and all(row["returncode"] == 0 for row in result["actual_supervisors"])
    assert (
        result["embodied_nodes"]
        == result["devices"]
        == result["models"]
        == result["simulators"]
        == 0
    )
    upgraded = []
    for index, path in enumerate(sorted(directory.glob("configuration-*.json"))):
        config = json.loads(path.read_text())
        root = Path(config["content"]["root"])
        identity = json.loads((root / ".content-creation.json").read_text())
        store: dict[str, Any] = {
            "root": str(root),
            "profile": identity["profile_identity"],
            "member": identity["member"],
            "reclamation": True,
        }
        before = (root / "content.sqlite3").stat().st_ino
        cli.run(request(store, "upgrade-snapshot-root"), f"drained-snapshot-{index}")
        cli.run(request(store, "upgrade-reclamation-root"), f"drained-gc-{index}")
        assert (root / "content.sqlite3").stat().st_ino == before == identity["database_inode"]
        with closing(
            sqlite3.connect((root / "content.sqlite3").as_uri() + "?mode=ro", uri=True)
        ) as sql:
            original_streams = dict(sql.execute("SELECT stream_id,record FROM content_streams"))
        stream_retirements = []
        for identifier, raw in original_streams.items():
            original_stream = json.loads(raw)
            retirement = cli.run(
                request(
                    store,
                    "retire-content-stream",
                    command_id=original_stream["intent"]["command_id"],
                    budget_ms=30_000,
                ),
                f"original-stream-retirement-{index}-{identifier[:8]}",
            )
            assert retirement["original"] == original_stream
            stream_retirements.append(retirement)
        with closing(
            sqlite3.connect((root / "content.sqlite3").as_uri() + "?mode=ro", uri=True)
        ) as sql:
            assert (
                dict(sql.execute("SELECT stream_id,record FROM content_streams"))
                == original_streams
            )
        copied = calls.run(
            {
                "action": "copy-original",
                "store": store,
                "source": str(source),
                "content": key,
                "command_id": "actual-unused-copy",
            },
            f"unused-copy-{index}",
        )
        calls.run(
            {"action": "retire-copy", "store": store, "command_id": "actual-unused-copy"},
            f"retire-unused-copy-{index}",
        )
        cas = root / "content-v1" / key["source_owner"][15:] / key["content_digest"][7:]
        if index == 0:
            cas.chmod(0o600)
            cas.write_bytes(b"actual corrupt retired bytes before original GC")
            child = cli.start(
                request(
                    store, "collect-content", command_id="original-unused-gc", budget_ms=30_000
                ),
                "original-unused-unknown",
            )
            code, _ = cli.wait(child)
            assert code != 0
            row = original(root, "original-unused-gc")
            assert row is not None and row[1]["outcomes"] == [None]
            actual_source = (
                source
                / "objects-v1/sha256"
                / key["content_digest"][7:9]
                / key["content_digest"][7:]
            )
            cas.write_bytes(actual_source.read_bytes())
            repaired_gc = cli.run(
                request(
                    store, "collect-content", command_id="independent-unused-gc", budget_ms=30_000
                ),
                "independent-unused-delete",
            )
            assert repaired_gc["deleted"] == 1 and original(root, "original-unused-gc") == row
        else:
            gc = cli.run(
                request(
                    store, "collect-content", command_id="original-unused-gc", budget_ms=30_000
                ),
                f"unused-gc-{index}",
            )
            assert gc["deleted"] == 1 and gc["unknown"] == 0
        assert not cas.exists()
        assert json.loads((root / ".content-creation.json").read_text()) == identity
        upgraded.append(
            {
                "member": identity["member"],
                "sql_inode": before,
                "original_copy": copied,
                "original_gc": original(root, "original-unused-gc"),
                "original_stream_retirements": stream_retirements,
            }
        )
    return upgraded


def run(
    output: Path,
    probe: Path,
    source_probe: Path,
    maintenance: Path,
    old_probe: Path,
    member: int,
    runnable_source: Path | None,
) -> dict[str, Any]:
    """Run native operations, retaining every original PID, failure and raw receipt."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    paths = [
        probe,
        source_probe,
        maintenance,
        old_probe,
        Path(__file__),
        Path(__file__).with_name("replica_parent_adoption_processes.py"),
        Path(__file__).with_name("check_replica_content_processes.py"),
        Path(__file__).with_name("check_content_stream_processes.py"),
    ]
    paths += sorted((ROOT / "core/controller-replication").rglob("*.rs"))
    paths += sorted((ROOT / "apps/controller-replica").rglob("*.rs"))
    identities = {str(path): digest(path) for path in paths}
    save(output / "source-identity.json", identities)
    calls, cli = (
        Processes(probe, output),
        Processes(maintenance, output, ("--content-maintenance",)),
    )
    result: dict[str, Any] = {
        "passed": False,
        "embodied_nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
    }
    try:
        payload = output / "actual-payload.bin"
        payload.write_bytes(os.urandom(2 * 1024 * 1024 + 13))
        source = output / "source"
        code, created = calls.wait(
            calls.start(
                {"action": "source", "root": str(source), "payload": str(payload)},
                "actual-source",
                source_probe,
            )
        )
        assert code == 0
        key = created["result"]["content"]
        store: dict[str, Any] = {
            "root": str(output / "receiver"),
            "profile": "a" * 64,
            "member": member,
            "snapshot_receives": True,
        }
        identity = calls.run({"action": "create", "store": store}, "root6-before-upgrade")[
            "identity"
        ]
        cli.run(request(store, "upgrade-reclamation-root"), "explicit-drained-root7-upgrade")
        store = {**store, "snapshot_receives": False, "reclamation": True}
        old = {name: store[name] for name in ("root", "profile", "member")}
        code, refused = calls.wait(
            calls.start({"action": "hold", "store": old}, "old-writer", old_probe)
        )
        assert code != 0 and "SQL format" in refused.get("error", "")
        result["old_entry_point_fenced"] = True
        result["actual_old_binary_fenced"] = True if digest(old_probe) != digest(probe) else None
        copy_request = {
            "action": "copy-original",
            "store": store,
            "source": str(source),
            "content": key,
            "command_id": "actual-copy",
        }
        copied = calls.run(copy_request, "actual-copy")
        blocked = cli.run(
            request(store, "collect-content", command_id="active-copy-gc", budget_ms=30_000),
            "active-copy-blocks-gc",
        )
        assert blocked["candidates"] == 0
        calls.run(
            {"action": "retire-copy", "store": store, "command_id": "actual-copy"},
            "actual-copy-retired",
        )
        root = Path(store["root"])
        cas = root / "content-v1" / key["source_owner"][15:] / key["content_digest"][7:]
        before = cas.read_bytes()
        with closing(sqlite3.connect(root / "content.sqlite3")) as sql:
            sql.execute("BEGIN IMMEDIATE")
            child = cli.start(
                request(
                    store, "collect-content", command_id="unadmitted-sql-failure", budget_ms=30_000
                ),
                "actual-admission-sql-failure",
            )
            code, response = cli.wait(child)
            assert code != 0 and "locked" in response.get("error", "")
            sql.rollback()
        assert original(root, "unadmitted-sql-failure") is None
        assert cas.read_bytes() == before
        with PayloadReadBarrier(cas) as barrier:
            child = cli.start(
                request(
                    store, "collect-content", command_id="killed-original-gc", budget_ms=30_000
                ),
                "kill-after-original-intent",
            )
            first = admitted(child, root, "killed-original-gc", barrier)
            child.kill()
            code, _ = cli.wait(child)
            assert code == -signal.SIGKILL
        assert cas.read_bytes() == before
        observed = cli.run(
            request(store, "observe-content-gc", command_id="killed-original-gc"),
            "observe-original-killed",
        )
        assert observed["unknown"] == 1 and observed["original"] == first[1]
        assert (
            cli.run(
                request(
                    store, "collect-content", command_id="killed-original-gc", budget_ms=30_000
                ),
                "killed-original-readonly-retry",
            )
            == observed
        )
        assert original(root, "killed-original-gc") == first and cas.exists()
        with PayloadReadBarrier(cas) as barrier:
            child = cli.start(
                request(
                    store,
                    "collect-content",
                    command_id="unlink-with-lost-receipt",
                    budget_ms=30_000,
                ),
                "actual-sql-outcome-failure",
            )
            pending = admitted(child, root, "unlink-with-lost-receipt", barrier)
            with closing(sqlite3.connect(root / "content.sqlite3")) as sql:
                sql.execute("BEGIN IMMEDIATE")
                barrier.close()
                code, response = cli.wait(child)
                assert code != 0 and "locked" in response.get("error", "")
                assert not cas.exists()
                sql.rollback()
        unknown = cli.run(
            request(store, "observe-content-gc", command_id="unlink-with-lost-receipt"),
            "observe-unlink-unknown",
        )
        assert unknown["original"] == pending[1] and unknown["unknown"] == 1
        assert (
            cli.run(
                request(
                    store,
                    "collect-content",
                    command_id="unlink-with-lost-receipt",
                    budget_ms=30_000,
                ),
                "original-unlink-readonly-retry",
            )
            == unknown
        )
        missing = cli.run(
            request(store, "collect-content", command_id="independent-absence", budget_ms=30_000),
            "independent-absence-observation",
        )
        assert (missing["deleted"], missing["missing"], missing["unknown"]) == (0, 1, 0)
        assert original(root, "killed-original-gc") == first
        assert original(root, "unlink-with-lost-receipt") == pending
        cas.write_bytes(before)
        calls.refuse(
            {**copy_request, "command_id": "forbidden-reacquisition"},
            "tombstone-copy-fence",
            "tombstoned",
        )
        assert calls.run(copy_request, "original-copy-history-only") == copied
        deleted = cli.run(
            request(store, "collect-content", command_id="independent-unlink", budget_ms=30_000),
            "actual-independent-unlink",
        )
        assert deleted["deleted"] == 1 and not cas.exists()
        assert (root / "content.sqlite3").stat().st_ino == identity["database_inode"]
        stream_store = {**store, "root": str(output / "stream-receiver")}
        stream_identity = calls.run(
            {"action": "create", "store": stream_store}, "root7-stream-receiver"
        )["identity"]
        context = target(stream_store, stream_identity, key)
        source_cas = (
            source / "objects-v1/sha256" / key["content_digest"][7:9] / key["content_digest"][7:]
        )
        stream_root = Path(stream_store["root"])
        with PayloadReadBarrier(source_cas) as barrier:
            child = calls.start(
                {
                    "action": "copy",
                    "store": stream_store,
                    "source": str(source),
                    "content": key,
                    "target": context,
                    "command_id": "killed-original-stream",
                    "budget_ms": 30_000,
                    "hold_after": False,
                },
                "kill-actual-stream-after-intent",
            )
            stream_before = stream_admitted(child, stream_root, "killed-original-stream", barrier)
            child.kill()
            code, _ = calls.wait(child)
            assert code == -signal.SIGKILL
        retirement = cli.run(
            request(
                stream_store,
                "retire-content-stream",
                command_id="killed-original-stream",
                budget_ms=30_000,
            ),
            "actual-stream-process-end-retirement",
        )
        assert retirement["original"] == stream_before[1]
        assert retirement["original"]["receipt"] is None
        assert retirement["process_end"]["original_pid"] == child.pid
        assert retirement["process_end"]["reason"] in {"original-process-absent", "pid-reused"}
        assert (
            cli.run(
                request(
                    stream_store,
                    "retire-content-stream",
                    command_id="killed-original-stream",
                    budget_ms=30_000,
                ),
                "original-stream-retirement-readonly",
            )
            == retirement
        )
        assert original_stream(stream_root, "killed-original-stream") == stream_before
        calls.run(
            {**copy_request, "store": stream_store, "command_id": "independent-stream-repair"},
            "independent-stream-repair",
        )
        calls.run(
            {
                "action": "retire-copy",
                "store": stream_store,
                "command_id": "independent-stream-repair",
            },
            "independent-stream-repair-retired",
        )
        stream_gc = cli.run(
            request(
                stream_store,
                "collect-content",
                command_id="after-independent-stream-repair",
                budget_ms=30_000,
            ),
            "actual-stream-independent-gc",
        )
        assert stream_gc["deleted"] == 1
        assert original_stream(stream_root, "killed-original-stream") == stream_before
        result["actual_original_stream_retirement"] = retirement
        result["declared_retention_target_is_consensus"] = False
        if runnable_source is not None:
            result["actual_drained_original_replicas"] = drained_source(
                runnable_source, calls, cli, source, key
            )
        assert all(digest(Path(path)) == value for path, value in identities.items())
        result.update(
            passed=True,
            original_unknown=observed,
            actual_unlink_unknown=unknown,
            actual_absence=missing,
            actual_independent_delete=deleted,
            original_copy=copied,
            original_identity=identity,
            actual_payload_digest=key["content_digest"],
        )
        return result
    finally:
        cli.cleanup()
        calls.cleanup()
        save(
            output / "processes.json",
            [row for _, row in calls.owned] + [row for _, row in cli.owned],
        )
        save(output / "result.json", result)


def main() -> None:
    """Require explicit successfully frozen executables, member and a new native evidence root."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--source-probe", type=Path, required=True)
    parser.add_argument("--maintenance", type=Path, required=True)
    parser.add_argument("--old-probe", type=Path, required=True)
    parser.add_argument("--member", type=int, required=True)
    parser.add_argument("--runnable-source", type=Path)
    args = parser.parse_args()
    if not 0 < args.member < 2**64:
        parser.error("member must be an explicit nonzero u64 infrastructure identity")
    result = run(
        args.output.resolve(),
        args.probe.resolve(strict=True),
        args.source_probe.resolve(strict=True),
        args.maintenance.resolve(strict=True),
        args.old_probe.resolve(strict=True),
        args.member,
        args.runnable_source.resolve(strict=True) if args.runnable_source else None,
    )
    print(json.dumps({"passed": result["passed"], "result": str(args.output / "result.json")}))


if __name__ == "__main__":
    main()
