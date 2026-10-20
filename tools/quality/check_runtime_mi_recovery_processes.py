"""Recover actual MI, Controller, Node and Request stores without device or model calls."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from check_recovery_bundle_processes import logical_rows
from check_state_verifier_processes import ROOT, save, stop, wait_for


def command(
    arguments: list[str], directory: Path, name: str, old: Path | None = None
) -> tuple[int, dict[str, Any]]:
    """Run the actual independent recovery CLI and preserve its bounded result and error log."""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(old or ROOT / "apps/recovery-tool")
    completed = subprocess.run(
        [sys.executable, "-m", "roboguide_recovery", *arguments],
        env=environment,
        capture_output=True,
        cwd=ROOT,
        timeout=90,
    )
    (directory / (name + ".stderr.log")).write_bytes(completed.stderr)
    (directory / (name + ".stdout.json")).write_bytes(completed.stdout)
    if completed.returncode != 0:
        return completed.returncode, {"error_type": "recovery-command-refused"}
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("actual recovery command returned a nonobject")
    return 0, value


def profile(source: Path) -> dict[str, Any]:
    """Declare actual original writer paths; this profile contains zero Node inventory facts."""
    components: list[dict[str, Any]] = []
    for identity, kind, root, database, dependencies in (
        ("controller", "controller", source, "controller.sqlite3", ["artifacts"]),
        ("node", "node", source / "local-eaios/node-state", "execution-journal.sqlite3", []),
        ("requests", "mission", source, "mission.sqlite3", []),
        ("runtime-mi", "runtime-mi", source, "runtime-mi.sqlite3", ["controller", "requests"]),
    ):
        components.append(
            {
                "component_id": identity,
                "kind": kind,
                "root": str(root),
                "database": database,
                "trees": [],
                "depends_on": dependencies,
            }
        )
    components.append(
        {
            "component_id": "artifacts",
            "kind": "artifact",
            "root": str(source / "artifacts"),
            "database": None,
            "trees": [],
            "depends_on": [],
        }
    )
    return {
        "schema": "roboguide.recovery-profile/v0.3",
        "profile_id": "actual-runtime-mi",
        "revision": 1,
        "max_bytes": 128 * 1024 * 1024,
        "max_files": 4096,
        "budget_seconds": 60,
        "components": components,
        "artifact_replica_sets": [],
    }


def run(source: Path, directory: Path, old: Path) -> dict[str, Any]:
    """Refuse real active/alias writers, then compare every original and restored schema/row."""
    directory.mkdir(parents=True, exist_ok=False)
    previous = json.loads((source / "RESULT.json").read_bytes())
    assert previous["passed"] and previous["runtime_mi"]["passed"]
    evidence = source / "runtime-mi-after-observation-and-review.json"
    assert (
        hashlib.sha256(evidence.read_bytes()).hexdigest()
        == previous["runtime_mi"]["original_sql_evidence_sha256"]
    )
    document = profile(source)
    profile_path = directory / "profile.json"
    save(profile_path, document)
    ready = directory / "actual-worker-lease-held.json"
    with (directory / "lease-holder.log").open("wb") as log:
        worker = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__)),
                "--hold-worker",
                str(source / "runtime-mi.sqlite3"),
                str(ready),
            ],
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=log,
        )
        try:
            wait_for(ready.is_file, "actual original MI worker writer lease")
            status, _ = command(
                [
                    "backup",
                    "--profile",
                    str(profile_path),
                    "--destination",
                    str(directory / "active-refused"),
                    "--command-id",
                    "actual-active",
                ],
                directory,
                "active-refused",
            )
            assert status != 0 and not (directory / "active-refused").exists()
            alias = directory / "actual-inode-alias"
            alias.mkdir()
            os.link(source / "runtime-mi.sqlite3", alias / "runtime-mi.sqlite3")
            (alias / "runtime-mi.sqlite3.worker.lock").touch()
            alias_profile = {
                **document,
                "components": [{**document["components"][3], "root": str(alias), "depends_on": []}],
            }
            save(directory / "alias-profile.json", alias_profile)
            status, _ = command(
                [
                    "backup",
                    "--profile",
                    str(directory / "alias-profile.json"),
                    "--destination",
                    str(directory / "alias-refused"),
                    "--command-id",
                    "actual-alias",
                ],
                directory,
                "alias-refused",
            )
            assert status != 0 and not (directory / "alias-refused").exists()
        finally:
            stop(worker)
    assert worker.poll() is not None
    original = {
        component["component_id"]: logical_rows(Path(component["root"]) / component["database"])
        for component in document["components"]
        if component["database"] is not None
    }
    save(directory / "original-sql-oracle.json", original)
    status, created = command(
        [
            "backup",
            "--profile",
            str(profile_path),
            "--destination",
            str(directory / "bundle"),
            "--command-id",
            "actual-runtime-mi-backup",
        ],
        directory,
        "backup",
    )
    assert status == 0, created
    status, verified = command(
        [
            "verify",
            "--bundle",
            str(directory / "bundle"),
            "--manifest-digest",
            created["manifest_digest"],
        ],
        directory,
        "verify",
    )
    assert status == 0 and verified["verified"]
    status, _ = command(
        [
            "backup",
            "--profile",
            str(profile_path),
            "--destination",
            str(directory / "old-tool-refused"),
            "--command-id",
            "old-tool",
        ],
        directory,
        "old-tool-refused",
        old,
    )
    assert status != 0 and not (directory / "old-tool-refused").exists()
    arguments = [
        "restore",
        "--bundle",
        str(directory / "bundle"),
        "--manifest-digest",
        created["manifest_digest"],
        "--destination",
        str(directory / "restored"),
        "--command-id",
        "actual-runtime-mi-restore",
    ]
    status, receipt = command(arguments, directory, "restore")
    assert status == 0 and receipt["schema"] == "roboguide.restore-receipt/v0.3"
    status, replayed = command(arguments, directory, "original-restore-retry")
    assert status == 0 and replayed == receipt
    restored = {
        component["component_id"]: logical_rows(
            directory / "restored/stores" / component["component_id"] / component["database"]
        )
        for component in document["components"]
        if component["database"] is not None
    }
    save(directory / "restored-sql-oracle.json", restored)
    assert restored == original
    return {
        "passed": True,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "nodes_started": 0,
        "actual_mi_worker_refused": True,
        "actual_inode_alias_refused": True,
        "old_tool_refused": True,
        "original_sql_databases": len(original),
        "original_sql_tables": sum(len(value) - 1 for value in original.values()),
        "all_original_schema_rows_equal": True,
        "original_restore_retry_readonly": True,
        "manifest_digest": created["manifest_digest"],
        "receipt": receipt,
        "source_result_sha256": hashlib.sha256((source / "RESULT.json").read_bytes()).hexdigest(),
        "checker_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "owned_processes_reaped": worker.poll() is not None,
    }


def main() -> None:
    """Run a fresh owned recovery check or hold only the actual original worker lease."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hold-worker", nargs=2, type=Path)
    parser.add_argument("--from", dest="source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--old-recovery-root", type=Path)
    arguments = parser.parse_args()
    if arguments.hold_worker:
        from mission.runtime_work_store import RuntimeWorkStore

        database, ready = arguments.hold_worker
        store = RuntimeWorkStore(database)
        try:
            save(ready, {"actual_instance": store.instance_id})
            sys.stdin.buffer.read()
        finally:
            store.close()
        return
    if None in (arguments.source, arguments.output, arguments.old_recovery_root):
        parser.error("--from, --output and --old-recovery-root are required")
    output = arguments.output.resolve()
    try:
        result = run(arguments.source.resolve(), output, arguments.old_recovery_root.resolve())
    except Exception as error:
        if output.exists():
            save(output / "RESULT.json", {"passed": False, "error": str(error)})
        raise
    save(output / "RESULT.json", result)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
