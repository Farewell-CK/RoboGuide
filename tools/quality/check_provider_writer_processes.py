"""Exercise actual provider writer lifetimes, old aliases and complete storage recovery."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from typing import Any, BinaryIO

from check_state_verifier_processes import ROOT, save

DATABASES = (
    "memory.sqlite3",
    "memory-use.sqlite3",
    "file-jobs.sqlite3",
    "active-map/active-map.sqlite3",
)
ACTOR = """from pathlib import Path
import hashlib,json,sys
from roboguide_memory_provider.store import MemoryStore
from roboguide_memory_provider.use_leases import UseLeases
from roboguide_memory_provider.process_jobs import ProcessFileJobs
from roboguide_memory_provider.active_map import ActiveMapLedger
root=Path(sys.argv[1]);root.mkdir(parents=True,exist_ok=True)
store=MemoryStore(root,'files',(root,),1024*1024)
uses=UseLeases(store);jobs=ProcessFileJobs(store,uses)
maps=ActiveMapLedger(root/'active-map',store,uses)
payload=b'actual owned provider storage bytes'
(store._content/hashlib.sha256(payload).hexdigest()).write_bytes(payload)
store._db.execute('PRAGMA wal_checkpoint(FULL)')
version=store._db.execute('PRAGMA user_version').fetchone()[0]
print(json.dumps({'ready':True,'instance':store.instance_id,'version':version}),flush=True)
for raw in sys.stdin:
 command=raw.strip()
 if command=='close-store':store.close()
 elif command=='close-use':uses.close()
 elif command=='close-jobs':jobs.close()
 elif command=='close-maps':maps.close()
 elif command=='exit':break
 print(json.dumps({'command':command,'accepted':True}),flush=True)
maps.close();jobs.close();uses.close();store.close()
"""
PROBE = """from pathlib import Path
import json,sys
from roboguide_memory_provider.store import MemoryStore
root=Path(sys.argv[1])
try:
 store=MemoryStore(root,'files',(root,),1024*1024)
 print(json.dumps({'opened':True,'instance':store.instance_id}));store.close()
except Exception as error:
 print(json.dumps({'opened':False,'error':str(error),'type':type(error).__name__}))
"""


def sql(path: Path) -> dict[str, Any]:
    """Read every real schema and row independently from the actor and recovery implementation."""
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        schema = connection.execute(
            "SELECT type,name,sql FROM sqlite_schema ORDER BY name"
        ).fetchall()
        return {
            "version": connection.execute("PRAGMA user_version").fetchone()[0],
            "schema": schema,
            "tables": {
                name: sorted(
                    repr(row)
                    for row in connection.execute('SELECT * FROM "' + name.replace('"', '""') + '"')
                )
                for kind, name, _ in schema
                if kind == "table"
            },
        }


def run(output: Path, old_provider: Path, old_recovery: Path) -> dict[str, Any]:
    """Use only owned OS processes and original files; all actors create zero Node/device facts."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    output = output.resolve()
    source = ROOT / "integrations/memory-provider"
    recovery = ROOT / "apps/recovery-tool"
    environment = {
        k: v for k, v in os.environ.items() if not k.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    processes: list[subprocess.Popen[bytes]] = []
    logs: list[BinaryIO] = []
    code_paths = [Path(__file__)]
    for package, name in (
        (source, "roboguide_memory_provider"),
        (old_provider, "roboguide_memory_provider"),
        (recovery, "roboguide_recovery"),
        (old_recovery, "roboguide_recovery"),
    ):
        code_paths.extend(sorted((package / name).glob("*.py")))
    identity = {
        str(path): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() for path in code_paths
    }
    save(output / "source-identity.json", identity)

    def start(
        root: Path, package: Path, name: str
    ) -> tuple[subprocess.Popen[bytes], dict[str, Any]]:
        """Start a real owner from the explicitly pinned current or archived package."""
        env = dict(environment, PYTHONPATH=str(package))
        log = (output / (name + ".stderr")).open("wb")
        logs.append(log)
        actor = subprocess.Popen(
            [sys.executable, "-c", ACTOR, str(root)],
            cwd=ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,
        )
        processes.append(actor)
        assert actor.stdout is not None
        ready = json.loads(actor.stdout.readline())
        assert ready["ready"] is True
        return actor, ready

    def send(actor: subprocess.Popen[bytes], command: str) -> None:
        """Send lifecycle requests to the owned process; responses grant no authority."""
        assert actor.stdin is not None and actor.stdout is not None
        actor.stdin.write((command + "\n").encode())
        actor.stdin.flush()
        if command != "exit":
            response = json.loads(actor.stdout.readline())
            assert response == {"command": command, "accepted": True}
        else:
            assert actor.wait(timeout=10) == 0

    def probe(root: Path, package: Path = source) -> dict[str, Any]:
        """Attempt actual initialization in another process without bypassing any implementation."""
        completed = subprocess.run(
            [sys.executable, "-c", PROBE, str(root)],
            cwd=ROOT,
            env=dict(environment, PYTHONPATH=str(package)),
            capture_output=True,
            check=True,
            timeout=15,
        )
        result: dict[str, Any] = json.loads(completed.stdout)
        return result

    def cli(arguments: list[str], package: Path = recovery, success: bool = True) -> dict[str, Any]:
        """Run the actual standard-library recovery CLI and retain its real refusal or receipt."""
        completed = subprocess.run(
            [sys.executable, "-m", "roboguide_recovery", *arguments],
            cwd=ROOT,
            env=dict(environment, PYTHONPATH=str(package)),
            capture_output=True,
            timeout=30,
        )
        if success:
            assert completed.returncode == 0, completed.stderr.decode()
            value: dict[str, Any] = json.loads(completed.stdout)
            return value
        assert completed.returncode != 0
        return {"returncode": completed.returncode, "error": completed.stderr.decode()[-2048:]}

    try:
        old_root = output / "old-owned"
        legacy, old_identity = start(old_root, old_provider, "old-owner")
        assert old_identity["version"] == 2
        send(legacy, "close-store")
        send(legacy, "close-use")
        alias = output / "old-alias"
        for name in DATABASES:
            target = alias / name
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(old_root / name, target)
        blocked_legacy = probe(alias)
        assert blocked_legacy["opened"] is False and "locked" in blocked_legacy["error"]
        assert sql(old_root / "memory.sqlite3")["version"] == 2
        send(legacy, "exit")
        upgraded = probe(old_root)
        assert upgraded["opened"] is True and upgraded["instance"] == old_identity["instance"]
        assert sql(old_root / "memory.sqlite3")["version"] == 3
        old_refusal = probe(old_root, old_provider)
        assert (
            old_refusal["opened"] is False
            and "unsupported Memory backend schema" in old_refusal["error"]
        )
        save(
            output / "original-legacy-upgrade.json",
            {
                "old_identity": old_identity,
                "live_alias_refused": blocked_legacy,
                "upgraded": upgraded,
                "old_writer_refused": old_refusal,
            },
        )

        root = output / "current-owned"
        actor, actor_identity = start(root, source, "current-owner")
        alias = output / "current-alias"
        for name in DATABASES:
            target = alias / name
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(root / name, target)
        alias_refusal = probe(alias)
        assert alias_refusal["opened"] is False and alias_refusal["type"] == "BlockingIOError"
        selected = {
            "schema": "roboguide.recovery-profile/v0.4",
            "profile_id": "actual-provider-owner",
            "revision": 1,
            "max_bytes": 16 * 1024 * 1024,
            "max_files": 128,
            "budget_seconds": 30,
            "artifact_replica_sets": [],
            "components": [
                {
                    "component_id": "provider",
                    "kind": "memory",
                    "root": str(root),
                    "database": None,
                    "trees": [],
                    "depends_on": [],
                }
            ],
        }
        profile = output / "profile.json"
        save(profile, selected)
        send(actor, "close-store")
        root_refusal = probe(root)
        assert root_refusal["opened"] is False and root_refusal["type"] == "BlockingIOError"
        active_backup = cli(
            [
                "backup",
                "--profile",
                str(profile),
                "--destination",
                str(output / "active-refused"),
                "--command-id",
                "active",
            ],
            success=False,
        )
        for command in ["close-use", "close-jobs"]:
            send(actor, command)
            assert probe(root)["opened"] is False
        send(actor, "exit")
        original = {name: sql(root / name) for name in DATABASES}
        created = cli(
            [
                "backup",
                "--profile",
                str(profile),
                "--destination",
                str(output / "bundle"),
                "--command-id",
                "backup",
            ]
        )
        old_tool = cli(
            [
                "backup",
                "--profile",
                str(profile),
                "--destination",
                str(output / "old-tool-refused"),
                "--command-id",
                "old-tool",
            ],
            package=old_recovery,
            success=False,
        )
        receipt = cli(
            [
                "restore",
                "--bundle",
                str(output / "bundle"),
                "--manifest-digest",
                created["manifest_digest"],
                "--destination",
                str(output / "restored"),
                "--command-id",
                "restore",
            ]
        )
        assert receipt["schema"] == "roboguide.restore-receipt/v0.4"
        restored = output / "restored/stores/provider"
        assert {name: sql(restored / name) for name in DATABASES} == original
        assert (
            cli(
                [
                    "restore",
                    "--bundle",
                    str(output / "bundle"),
                    "--manifest-digest",
                    created["manifest_digest"],
                    "--destination",
                    str(output / "restored"),
                    "--command-id",
                    "restore",
                ]
            )
            == receipt
        )
        restarted = probe(restored)
        assert restarted["opened"] and restarted["instance"] == actor_identity["instance"]
        save(output / "original-sql.json", original)
        result = {
            "passed": True,
            "devices": 0,
            "software_nodes": 0,
            "live_legacy_alias_refused": True,
            "actual_inode_alias_refused": alias_refusal,
            "parent_closed_backup_refused": active_backup,
            "old_writer_refused": old_refusal,
            "old_tool_refused": old_tool,
            "restored_databases": len(DATABASES),
            "restored_tables": sum(len(v["tables"]) for v in original.values()),
            "identity_preserved": True,
            "manifest_digest": created["manifest_digest"],
            "source_identity": identity,
        }
        assert {
            str(path): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            for path in code_paths
        } == identity
        save(output / "RESULT.json", result)
        return result
    finally:
        for actor in reversed(processes):
            if actor.poll() is None:
                try:
                    send(actor, "exit")
                except (BrokenPipeError, subprocess.TimeoutExpired):
                    actor.kill()
                    actor.wait()
        for log in logs:
            log.close()
        save(
            output / "owned-process-exits.json",
            {
                "all_owned_processes_reaped": all(p.poll() is not None for p in processes),
                "processes": [{"pid": p.pid, "returncode": p.returncode} for p in processes],
            },
        )


def main() -> None:
    """Run only explicit owned roots with actual archived implementation and recovery code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--old-provider-source", type=Path, required=True)
    parser.add_argument("--old-recovery-root", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.output, args.old_provider_source, args.old_recovery_root), sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
