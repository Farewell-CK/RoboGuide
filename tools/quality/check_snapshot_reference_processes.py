"""Check actual opt-in Snapshot copies, original process interruption and exact reconciliation."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import closing
from pathlib import Path
from typing import Any, BinaryIO

from check_memory_reference_processes import (
    PROVIDER,
    ROOT,
    _import_fixture,
    request,
    save,
    start,
    stop,
)


def authority(root: Path) -> dict[str, Any]:
    """Read original operations and reference rows independently from every CLI receipt."""
    with closing(sqlite3.connect(root / "memory.sqlite3")) as connection:
        return {
            "identity": connection.execute("SELECT provider,instance FROM identity").fetchall(),
            "version": connection.execute("PRAGMA user_version").fetchone()[0],
            "operations": [
                {
                    "operation_id": row[0],
                    "request": json.loads(row[1]),
                    "state": row[2],
                    "receipt": json.loads(row[3]) if row[3] else None,
                }
                for row in connection.execute(
                    "SELECT operation_id,request_json,state,receipt_json "
                    "FROM snapshot_operations ORDER BY operation_id"
                )
            ],
            "references": connection.execute(
                "SELECT reference_id,content_digest,kind,owner,generation,active "
                "FROM content_references ORDER BY reference_id"
            ).fetchall(),
        }


def legacy_writer(root: Path, inputs: Path, output: Path, revision: str) -> dict[str, Any]:
    """Run the actual frozen older provider against v4 and inspect original authority."""
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("legacy source requires an exact committed SHA")
    archive = subprocess.run(
        ["git", "archive", revision, "integrations/memory-provider"],
        cwd=ROOT,
        capture_output=True,
        check=True,
        timeout=30,
    )
    destination = output / "legacy-source"
    destination.mkdir(mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as source:
        for member in source:
            if not member.isfile() or not member.name.endswith(".py"):
                continue
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("legacy source archive has an unsafe path")
            selected = source.extractfile(member)
            assert selected is not None
            target = destination / path
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            target.write_bytes(selected.read())
    environment = dict(os.environ, PYTHONPATH=str(destination / "integrations/memory-provider"))
    before = authority(root)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from pathlib import Path; "
            "from roboguide_memory_provider.store import MemoryStore; "
            "MemoryStore(Path(sys.argv[1]),'local-files',(Path(sys.argv[2]),),16777216)",
            str(root),
            str(inputs),
        ],
        env=environment,
        cwd=destination,
        capture_output=True,
        timeout=15,
    )
    (output / "legacy-writer.stderr").write_bytes(completed.stderr)
    assert completed.returncode != 0 and b"unsupported Memory backend schema" in completed.stderr
    assert authority(root) == before
    return {
        "revision": revision,
        "exit_code": completed.returncode,
        "authority_rows_unchanged": True,
    }


def run(output: Path, runtime: Path, legacy_ref: str | None) -> dict[str, Any]:
    """Run shipped provider/CLI processes, real SQL failures, SIGKILL/wait and cold restoration."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    root, inputs = runtime / "provider", runtime / "inputs"
    if root.exists() or inputs.exists():
        raise ValueError("snapshot runtime must contain no prior source")
    inputs.mkdir(mode=0o700)
    source = inputs / "actual-source.bin"
    source.write_bytes(Path(__file__).read_bytes() * 256)
    processes: list[subprocess.Popen[bytes]] = []
    logs: list[BinaryIO] = []
    commands: list[dict[str, Any]] = []
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    environment["PYTHONPATH"] = str(ROOT / "apps/recovery-tool")
    source_paths = [
        Path(__file__),
        ROOT / "tools/quality/check_memory_reference_processes.py",
        *sorted((PROVIDER / "roboguide_memory_provider").glob("*.py")),
        *sorted((ROOT / "apps/recovery-tool/roboguide_recovery").glob("*.py")),
    ]
    frozen = {
        str(path.relative_to(ROOT)): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        for path in source_paths
    }
    save(output / "source-identity.json", frozen)

    def launch(selected: Path, *, snapshots: bool) -> subprocess.Popen[bytes]:
        """Start one actual owned provider with the explicitly declared storage contract."""
        log = (output / f"provider-{len(logs)}.stderr").open("wb")
        logs.append(log)
        process = start(selected, inputs, log, snapshot_references=snapshots)
        processes.append(process)
        return process

    def cli(*arguments: str, passed: bool = True) -> dict[str, Any]:
        """Preserve exact commands, exits and raw outputs from the independent shipped CLI."""
        completed = subprocess.run(
            [sys.executable, "-m", "roboguide_recovery", *arguments],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            timeout=75,
        )
        label = f"command-{len(commands)}"
        (output / (label + ".stdout")).write_bytes(completed.stdout)
        (output / (label + ".stderr")).write_bytes(completed.stderr)
        commands.append({"arguments": list(arguments), "exit_code": completed.returncode})
        save(output / "commands.json", commands)
        if not passed:
            assert completed.returncode != 0
            return {}
        assert completed.returncode == 0, completed.stderr.decode(errors="replace")
        value = json.loads(completed.stdout)
        assert isinstance(value, dict)
        return value

    try:
        first = launch(root, snapshots=False)
        channel = root / "local.sock"
        status, identity = request(channel, "/v1/instance", {}, "GET")
        assert status == 200
        imported = _import_fixture(source, identity["provider_id"], identity["instance_id"])
        status, receipt = request(
            channel,
            "/v1/import",
            {
                "memory_import": imported["original"],
                "memory_manifest": imported["manifest"],
                "staged_artifact": str(source),
            },
            "POST",
        )
        assert status == 200 and receipt["result"]["outcome"] == "completed"
        assert stop(first, channel) == 0
        second = launch(root, snapshots=True)
        assert request(channel, "/v1/instance", {}, "GET") == (200, identity)
        selected: dict[str, Any] = {
            "schema": "roboguide.recovery-profile/v0.8",
            "profile_id": "actual-original-snapshot",
            "revision": 1,
            "max_bytes": 128 * 1024 * 1024,
            "max_files": 128,
            "budget_seconds": 60,
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
        cli(
            "backup",
            "--profile",
            str(profile),
            "--destination",
            str(output / "live-refused"),
            "--command-id",
            "live",
            passed=False,
        )
        assert not (output / "live-refused").exists()
        assert stop(second, channel) == 0
        legacy = legacy_writer(root, inputs, output, legacy_ref) if legacy_ref else None
        legacy_profile = output / "legacy-profile.json"
        save(legacy_profile, selected | {"schema": "roboguide.recovery-profile/v0.7"})
        cli(
            "backup",
            "--profile",
            str(legacy_profile),
            "--destination",
            str(output / "legacy-refused"),
            "--command-id",
            "legacy",
            passed=False,
        )
        bundle_path = output / "bundle"
        created = cli(
            "backup",
            "--profile",
            str(profile),
            "--destination",
            str(bundle_path),
            "--command-id",
            "original",
        )
        cli("verify", "--bundle", str(bundle_path), "--manifest-digest", created["manifest_digest"])
        assert all(value["state"] == "released" for value in authority(root)["operations"])
        save(output / "source-after-copy.json", authority(root))
        restored = runtime / "restored"
        cli(
            "restore",
            "--bundle",
            str(bundle_path),
            "--manifest-digest",
            created["manifest_digest"],
            "--destination",
            str(restored),
            "--command-id",
            "restore",
        )
        recovered = restored / "stores/provider"
        original_restored = authority(recovered)
        assert original_restored["operations"][0]["state"] == "active"
        restored_process = launch(recovered, snapshots=True)
        assert request(recovered / "local.sock", "/v1/instance", {}, "GET") == (200, identity)
        assert (
            request(
                recovered / "local.sock",
                "/v1/import-query",
                {"memory_import": imported["original"]},
                "GET",
            )[1]["result"]["outcome"]
            == "completed"
        )
        assert stop(restored_process, recovered / "local.sock") == 0
        restored_profile = output / "restored-profile.json"
        relocated = json.loads(json.dumps(selected))
        relocated["components"][0]["root"] = str(recovered)
        save(restored_profile, relocated)
        reconciled = cli(
            "reconcile-snapshots",
            "--profile",
            str(restored_profile),
            "--bundle",
            str(bundle_path),
            "--manifest-digest",
            created["manifest_digest"],
        )
        assert reconciled["copied_files"] == 0
        assert (
            authority(recovered)["operations"][0]["request"]
            == original_restored["operations"][0]["request"]
        )
        assert authority(recovered)["operations"][0]["state"] == "released"
        save(output / "restored-after-reconciliation.json", authority(recovered))
        with closing(sqlite3.connect(root / "memory.sqlite3")) as connection:
            connection.execute(
                "CREATE TRIGGER fail_snapshot_completion BEFORE UPDATE ON snapshot_operations "
                "WHEN NEW.state='released' BEGIN SELECT RAISE(ABORT,'actual receipt fault'); END"
            )
            connection.commit()
        lost = output / "lost-receipt-bundle"
        cli(
            "backup",
            "--profile",
            str(profile),
            "--destination",
            str(lost),
            "--command-id",
            "lost-receipt",
            passed=False,
        )
        before_retry = authority(root)
        original_request = next(
            value["request"]
            for value in before_retry["operations"]
            if value["request"]["command_id"] == "lost-receipt"
        )
        assert lost.is_dir()
        paths = {
            path: (path.stat().st_ino, path.stat().st_mtime_ns)
            for path in lost.rglob("*")
            if path.is_file()
        }
        with closing(sqlite3.connect(root / "memory.sqlite3")) as connection:
            connection.execute("DROP TRIGGER fail_snapshot_completion")
            connection.commit()
        cli(
            "backup",
            "--profile",
            str(profile),
            "--destination",
            str(lost),
            "--command-id",
            "lost-receipt",
        )
        assert all(
            (path.stat().st_ino, path.stat().st_mtime_ns) == value for path, value in paths.items()
        )
        assert (
            next(
                value["request"]
                for value in authority(root)["operations"]
                if value["request"]["command_id"] == "lost-receipt"
            )
            == original_request
        )

        unpublished = output / "interrupted-bundle"
        with closing(sqlite3.connect(root / "memory-use.sqlite3")) as lock:
            assert lock.execute("PRAGMA journal_mode=DELETE").fetchone() == ("delete",)
            lock.execute("BEGIN EXCLUSIVE")
            with (
                (output / "interrupted.stdout").open("wb") as stdout,
                (output / "interrupted.stderr").open("wb") as stderr,
            ):
                copier = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "roboguide_recovery",
                        "backup",
                        "--profile",
                        str(profile),
                        "--destination",
                        str(unpublished),
                        "--command-id",
                        "interrupted",
                    ],
                    env=environment,
                    cwd=ROOT,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                )
                processes.append(copier)
                try:
                    deadline = time.monotonic() + 10
                    while True:
                        rows = authority(root)["operations"]
                        active = [
                            row for row in rows if row["request"]["command_id"] == "interrupted"
                        ]
                        if active:
                            assert active[0]["state"] == "active"
                            break
                        if copier.poll() is not None or time.monotonic() >= deadline:
                            raise RuntimeError("actual copy did not retain its pre-copy intent")
                        time.sleep(0.01)
                    cli(
                        "abort-snapshots",
                        "--profile",
                        str(profile),
                        "--destination",
                        str(unpublished),
                        "--command-id",
                        "interrupted",
                        passed=False,
                    )
                    assert copier.poll() is None
                    copier.send_signal(signal.SIGKILL)
                    actual_wait = copier.wait(timeout=10)
                    assert actual_wait == -signal.SIGKILL
                finally:
                    if copier.poll() is None:
                        copier.kill()
                        copier.wait(timeout=10)
            lock.rollback()
        assert not unpublished.exists()
        interrupted_before = authority(root)
        cli(
            "backup",
            "--profile",
            str(profile),
            "--destination",
            str(unpublished),
            "--command-id",
            "interrupted",
            passed=False,
        )
        assert authority(root) == interrupted_before
        aborted = cli(
            "abort-snapshots",
            "--profile",
            str(profile),
            "--destination",
            str(unpublished),
            "--command-id",
            "interrupted",
        )
        assert aborted["copied_files"] == 0
        assert (
            next(
                row["state"]
                for row in authority(root)["operations"]
                if row["request"]["command_id"] == "interrupted"
            )
            == "aborted"
        )
        cli(
            "backup",
            "--profile",
            str(profile),
            "--destination",
            str(unpublished),
            "--command-id",
            "interrupted",
            passed=False,
        )
        assert unpublished.with_name(unpublished.name + ".partial-interrupted").is_dir()
        assert {
            str(path.relative_to(ROOT)): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            for path in source_paths
        } == frozen
        save(output / "final-source-authority.json", authority(root))
        shutil.copytree(root, output / "final-provider")
        result = {
            "passed": True,
            "provider": identity,
            "runtime": str(runtime),
            "legacy_writer": legacy,
            "actual_copy_process_exit": actual_wait,
            "snapshot_generation": 1,
            "real_original_imports": 1,
            "original_sql_and_budget_preserved": True,
            "copy_response_loss_reconciled_without_copy": True,
            "active_source_copy_blocks_abort": True,
            "interrupted_copy_irreversibly_fenced": True,
            "cold_restore_retained_original_intent": True,
            "registered_nodes": 0,
            "devices": 0,
            "models": 0,
            "simulators": 0,
            "protocol_fixture": True,
        }
        save(output / "result.json", result)
        return result
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        for log in logs:
            log.close()


def main() -> None:
    """Retain stable evidence separately from the short actual private-socket runtime."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--legacy-ref")
    args = parser.parse_args()
    runtime = args.runtime_root or Path(tempfile.mkdtemp(prefix="rg-snapshot-"))
    print(
        json.dumps(run(args.output.resolve(), runtime.resolve(), args.legacy_ref), sort_keys=True)
    )


if __name__ == "__main__":
    main()
