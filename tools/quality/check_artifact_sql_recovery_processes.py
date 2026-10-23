"""Verify actual original GC rollback, nonrenewing interruption and complete cold recovery."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from artifact_sql_recovery_processes import Case, canonical, digest, gc_rows
from check_artifact_authority_processes import call
from check_controller_storage_processes import first_observation
from check_resource_quota_processes import ROOT, request


def _start_paused(case: Case, directory: Path) -> tuple[subprocess.Popen[bytes], list[str]]:
    """Stop the actual original CLI after durable intent while its real leases remain held."""
    arguments = case.repair_arguments(directory)
    args = [sys.executable, "-m", "roboguide_recovery", *arguments]
    with (
        (case.output / "interrupted-repair.stdout").open("xb") as out,
        (case.output / "interrupted-repair.stderr").open("xb") as errors,
    ):
        child = subprocess.Popen(args, env=case.cli_environment, stdout=out, stderr=errors)
    record: dict[str, Any] = {"pid": child.pid, "arguments": args}
    case.processes.append((child, record))
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if (directory / "intent.json").exists():
            os.kill(child.pid, signal.SIGSTOP)
            break
        if child.poll() is not None:
            raise ValueError("actual repair exited before the intended process stop")
        time.sleep(0.001)
    else:
        raise TimeoutError("actual original intent did not appear")
    while time.monotonic() < deadline:
        status = Path(f"/proc/{child.pid}/status").read_text()
        if "State:\tT" in status:
            record["actual_stopped_after_intent"] = True
            break
        time.sleep(0.001)
    else:
        raise TimeoutError("original repair did not reach actual OS stopped state")
    if (directory / "receipt.json").exists() or not case.journal.exists():
        raise ValueError("actual source already progressed beyond the requested fault window")
    return child, arguments


def _wait_failed(case: Case, child: subprocess.Popen[bytes]) -> None:
    """Require this exact original faulted incarnation's nonzero native exit."""
    result = child.wait(timeout=40)
    if result == 0:
        raise ValueError("actual interrupted/failed repair unexpectedly completed")
    for original, record in case.processes:
        if original is child:
            record["native_wait"] = result
            return
    raise ValueError("actual repair process lost its original ownership record")


def _unknown(case: Case, arguments: list[str], directory: Path) -> dict[str, Any]:
    """Verify immutable original Unknown beside independent readonly zero-effect replay."""
    original = (directory / "intent.json").read_bytes()
    observed = case.command(
        ["observe-artifact-journal-recovery", "--evidence-directory", str(directory)]
    )
    assert observed["phase"] == "unknown" and observed["writable_sql_opens"] == 0
    replay = case.command(arguments)
    assert replay["phase"] == "unknown" and replay["replayed"] is True
    assert replay["writable_sql_opens"] == 0
    assert (directory / "intent.json").read_bytes() == original
    intent = observed["intent"]
    if not isinstance(intent, dict):
        raise ValueError("original Unknown intent is not a complete object")
    return intent


def _repair(case: Case, directory: Path) -> dict[str, Any]:
    """Perform a new actual explicitly pinned repair and check its independent original receipt."""
    value = case.command(case.repair_arguments(directory))
    assert value["phase"] == "completed" and value["writable_sql_opens"] == 1
    assert value["replayed"] is False
    case.verify_current()
    return value


def _normal(case: Case) -> dict[str, Any]:
    """Preserve real nonempty history/content across rollback, replay and full restore."""
    case.hot()
    evidence = case.output / "evidence"
    args = case.repair_arguments(evidence)
    value = _repair(case, evidence)
    replay = case.command(args)
    assert replay["phase"] == "completed" and replay["writable_sql_opens"] == 0
    assert replay["intent"] == value["intent"] and replay["receipt"] == value["receipt"]
    receipt_path = evidence / "receipt.json"
    original_receipt = receipt_path.read_bytes()
    malformed = json.loads(original_receipt)
    malformed["original_gc_semantics_verified"] = False
    receipt_path.write_bytes(canonical(malformed))
    refused = case.command(
        ["observe-artifact-journal-recovery", "--evidence-directory", str(evidence)], passed=False
    )
    assert "receipt or original budget differs" in refused["stderr"]
    receipt_path.write_bytes(original_receipt)
    changed = list(args)
    changed[-1] = "different original reason"
    case.command(changed, passed=False)
    profile = json.loads(canonical(case.profile))
    profile["components"].append(
        {
            "component_id": "forensic-evidence",
            "kind": "files",
            "root": str(evidence),
            "database": None,
            "trees": [],
            "depends_on": ["artifact"],
        }
    )
    selected = case.output / "complete-profile.json"
    selected.write_bytes(canonical(profile))
    bundle = case.output / "bundle"
    backup = case.command(
        [
            "backup",
            "--profile",
            str(selected),
            "--destination",
            str(bundle),
            "--command-id",
            "original-native-sql-backup",
        ]
    )
    case.command(
        ["verify", "--bundle", str(bundle), "--manifest-digest", backup["manifest_digest"]]
    )
    restored = case.output / "restored"
    receipt = case.command(
        [
            "restore",
            "--bundle",
            str(bundle),
            "--manifest-digest",
            backup["manifest_digest"],
            "--destination",
            str(restored),
            "--command-id",
            "original-native-sql-restore",
        ]
    )
    assert receipt["schema"] == case.profile["schema"].replace(
        "recovery-profile", "restore-receipt"
    )
    assert (
        gc_rows(restored / "stores/artifact/reclamation-v1/journal.sqlite3") == case.original_rows
    )
    retained = case.command(
        [
            "observe-artifact-journal-recovery",
            "--evidence-directory",
            str(restored / "stores/forensic-evidence"),
        ]
    )
    assert retained["intent"] == value["intent"] and retained["receipt"] == value["receipt"]
    child, operator, artifact = case.start(
        database=restored / "stores/controller/events.sqlite3",
        artifact=restored / "stores/artifact",
    )
    assert request(operator, case.controllers.admin, "/v1/inventory")[1]["nodes"] == []
    assert call(
        artifact, case.controllers.admin, "GET", "/v1/artifacts/" + case.retained_digest
    ) == (200, case.retained_payload)
    assert (
        call(artifact, case.controllers.admin, "GET", "/v1/artifacts/" + case.released_digest)[0]
        != 200
    )
    case.controllers.finish(child)
    return {
        "original_gc_rows_and_tombstone_preserved": True,
        "actual_retained_content": True,
        "complete_cold_restore": True,
        "original_id": value["intent"]["operation_id"],
    }


def _interrupted(case: Case, mode: str) -> dict[str, Any]:
    """Exercise actual process/file/SQL/time faults, preserving original Unknown and new repair."""
    case.hot(mib=32)
    directory = case.output / "original-unknown"
    if mode == "expiry":
        case.profile["budget_seconds"] = 2
        case.save_profile()
    before = digest(case.gc_database), digest(case.journal)
    child, arguments = _start_paused(case, directory)
    descriptor: int | None = None
    try:
        if mode == "copy":
            if (directory / "rollback-journal.raw").exists():
                raise ValueError("actual SQL copy completed before the intended fault window")
            (directory / "rollback-journal.raw").mkdir()
        elif mode == "receipt":
            (directory / "receipt.json.pending").write_bytes(b"actual owned output collision")
        elif mode == "sql":
            descriptor = os.open(case.gc_database, os.O_RDWR | os.O_NOFOLLOW)
            fcntl.lockf(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif mode == "source":
            original = case.output / "detached-original-sql"
            case.gc_database.rename(original)
            shutil.copyfile(original, case.gc_database)
        elif mode == "expiry":
            time.sleep(2.2)
        elif mode == "kill":
            child.kill()
        else:
            raise ValueError("unknown actual fault selection")
        if mode != "kill":
            os.kill(child.pid, signal.SIGCONT)
        _wait_failed(case, child)
    finally:
        if descriptor is not None:
            fcntl.lockf(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
    original_intent = _unknown(case, arguments, directory)
    if mode == "receipt":
        assert not case.journal.exists()
        case.verify_current()
        return {
            "original_unknown_retained": True,
            "independent_original_gc_readable": True,
            "original_id": original_intent["operation_id"],
        }
    assert (digest(case.gc_database), digest(case.journal)) == before
    if mode == "expiry":
        case.profile = json.loads(canonical(case.profile))
        case.profile["budget_seconds"] = 30
        case.profile["revision"] += 1
        case.profile_path = case.output / "explicit-new-profile.json"
        case.save_profile()
    repaired = _repair(case, case.output / "new-explicit-repair")
    assert repaired["intent"]["operation_id"] != original_intent["operation_id"]
    assert (
        case.command(["observe-artifact-journal-recovery", "--evidence-directory", str(directory)])[
            "intent"
        ]
        == original_intent
    )
    assert not (directory / "receipt.json").exists()
    return {
        "original_unknown_retained": True,
        "new_explicit_repair": True,
        "original_id": original_intent["operation_id"],
        "repair_id": repaired["intent"]["operation_id"],
    }


def _guards(case: Case) -> dict[str, Any]:
    """Fence actual root/inode writers and source faults without trusting declarations."""
    writer = case.hot(keep_alive=True)
    arguments = case.repair_arguments(case.output / "writer-refused")
    assert "writer is still active" in case.command(arguments, passed=False)["stderr"]
    assert not (case.output / "writer-refused").exists()
    writer.kill()
    assert writer.wait(timeout=10) == -signal.SIGKILL
    before = digest(case.gc_database), digest(case.journal)
    wrong = case.repair_arguments(case.output / "wrong-digest")
    index = wrong.index("--sql-digest") + 1
    wrong[index] = "sha256:" + ("0" * 64)
    assert "pinned input" in case.command(wrong, passed=False)["stderr"]
    profile = json.loads(canonical(case.profile))
    profile["components"] = [profile["components"][1] | {"depends_on": []}]
    incomplete = case.output / "missing-known-controller.json"
    incomplete.write_bytes(canonical(profile))
    missing = case.repair_arguments(case.output / "missing-known-controller")
    missing[missing.index("--profile") + 1] = str(incomplete)
    assert "known original Controller" in case.command(missing, passed=False)["stderr"]
    alias = case.database.with_name("hardlink.sqlite3")
    os.link(case.database, alias)
    code = (
        "import fcntl,json,os,sys\n"
        "f=open(sys.argv[1],'r+b')\n"
        "fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
        "print(json.dumps({'pid':os.getpid(),"
        "'actual_sql_inode':os.fstat(f.fileno()).st_ino}),flush=True)\n"
        "sys.stdin.buffer.read(1)\n"
    )
    holder_args = [sys.executable, "-c", code, str(alias)]
    live = subprocess.Popen(
        holder_args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    record: dict[str, Any] = {"pid": live.pid, "arguments": holder_args}
    case.processes.append((live, record))
    record["actual_original_inode_hold"] = first_observation(live)
    refused = case.repair_arguments(case.output / "controller-inode-refused")
    assert "writer is still active" in case.command(refused, passed=False)["stderr"]
    live.communicate(b"x", timeout=10)
    assert live.returncode == 0
    record["native_wait"] = live.returncode
    original_journal = case.output / "actual-original-journal"
    case.journal.rename(original_journal)
    case.journal.symlink_to(original_journal)
    symlink = case.repair_arguments(case.output / "symlink-refused")
    case.command(symlink, passed=False)
    case.journal.unlink()
    original_journal.rename(case.journal)
    assert (digest(case.gc_database), digest(case.journal)) == before
    _repair(case, case.output / "valid-after-guards")
    return {
        "actual_writer_and_controller_inode_fenced": True,
        "missing_consumer_and_digest_fenced": True,
        "symlink_refused": True,
    }


def _missing_history(case: Case) -> dict[str, Any]:
    """Native rollback cannot manufacture a missing committed original authority table."""
    with closing(sqlite3.connect(case.gc_database)) as connection:
        connection.execute("DROP TABLE gc_commands")
        connection.commit()
    case.hot()
    directory = case.output / "missing-history-refused"
    result = case.command(case.repair_arguments(directory), passed=False)
    assert "tables/authority changed" in result["stderr"]
    assert not (directory / "receipt.json").exists()
    with closing(sqlite3.connect(case.gc_database.as_uri() + "?mode=ro", uri=True)) as connection:
        assert (
            connection.execute("SELECT 1 FROM sqlite_master WHERE name='gc_commands'").fetchone()
            is None
        )
    assert (
        case.command(["observe-artifact-journal-recovery", "--evidence-directory", str(directory)])[
            "phase"
        ]
        == "unknown"
    )
    return {"missing_original_table_not_recreated": True, "no_completion_receipt": True}


def _wal(case: Case) -> dict[str, Any]:
    """Reject actual WAL conversion before any rollback-journal assumption or effect."""
    descriptor = os.open(case.artifact / ".writer.lock", os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with closing(sqlite3.connect(case.gc_database)) as connection:
            assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    finally:
        os.close(descriptor)
    before = digest(case.gc_database)
    directory = case.output / "wal-refused"
    value = case.command(
        [
            "recover-artifact-journal",
            "--profile",
            str(case.profile_path),
            "--artifact",
            "artifact",
            "--sql-digest",
            before,
            "--journal-digest",
            "sha256:" + "0" * 64,
            "--evidence-directory",
            str(directory),
            "--reason",
            "Refuse actual unsupported WAL mode",
        ],
        passed=False,
    )
    assert "DELETE-mode header" in value["stderr"]
    assert digest(case.gc_database) == before and not directory.exists()
    assert gc_rows(case.gc_database) == case.original_rows
    return {"actual_native_wal_refused_before_effects": True, "original_gc_rows_unchanged": True}


def run(output: Path, binary: Path, selections: list[str]) -> dict[str, Any]:
    """Retain actual cases, source identity and failures without claiming physical or HA proof."""
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    paths = [
        *sorted((ROOT / "apps/recovery-tool/roboguide_recovery").glob("*.py")),
        Path(__file__),
        Path(__file__).with_name("recovery_profile_selection.py"),
        Path(__file__).with_name("artifact_sql_recovery_processes.py"),
        Path(__file__).with_name("check_controller_storage_processes.py"),
    ]
    frozen = {str(path.relative_to(ROOT)): digest(path) for path in paths}
    binary_digest = digest(binary)
    result: dict[str, Any] = {
        "schema": "roboguide.actual-artifact-sql-recovery/v0.1",
        "passed": False,
        "binary_digest": binary_digest,
        "source_identity": frozen,
        "nodes_created": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "cases": {},
    }
    try:
        for selection in selections:
            case = Case(output / selection, binary)
            case_result: dict[str, Any] = {"passed": False}
            try:
                case.seed()
                if selection == "normal":
                    evidence = _normal(case)
                elif selection == "guards":
                    evidence = _guards(case)
                elif selection == "missing-history":
                    evidence = _missing_history(case)
                elif selection == "wal":
                    evidence = _wal(case)
                else:
                    evidence = _interrupted(case, selection)
                case_result.update(evidence, passed=True)
                result["cases"][selection] = case_result
                print(json.dumps({"case": selection, "passed": True}), flush=True)
            finally:
                case.close()
                (case.output / "result.json").write_bytes(canonical(case_result))
        if (
            any(digest(path) != frozen[str(path.relative_to(ROOT))] for path in paths)
            or digest(binary) != binary_digest
        ):
            raise ValueError("actual native recovery gate source/binary changed")
        result["passed"] = True
        return result
    finally:
        (output / "result.json").write_bytes(canonical(result))


def main() -> None:
    """Execute owned software faults and original-data recovery with zero devices."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--controller", type=Path, default=ROOT / "target/debug/integration-server")
    parser.add_argument(
        "--case",
        action="append",
        choices=[
            "normal",
            "guards",
            "sql",
            "copy",
            "receipt",
            "expiry",
            "source",
            "kill",
            "missing-history",
            "wal",
        ],
    )
    args = parser.parse_args()
    selections = args.case or [
        "normal",
        "guards",
        "sql",
        "copy",
        "receipt",
        "expiry",
        "source",
        "kill",
        "missing-history",
        "wal",
    ]
    run(args.output.absolute(), args.controller.resolve(strict=True), selections)


if __name__ == "__main__":
    main()
