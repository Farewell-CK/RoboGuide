"""Exercise explicit private receiving Archive retirement/GC from an actual reaped producer gate."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/recovery-tool"))
from roboguide_recovery.snapshot_references import canonical, digest  # noqa: E402


def save(path: Path, value: object) -> None:
    """Retain actual administrative input/output without mutating original producer evidence."""
    path.write_bytes(canonical(value))


def legacy_profile(raw_profile: dict[str, Any]) -> dict[str, Any]:
    """Project current producer metadata onto the explicit old recipient profile contract."""
    supported = {f"roboguide.recovery-profile/v0.{version}" for version in range(12, 32)}
    schema = (
        raw_profile["schema"]
        if raw_profile["schema"] in supported
        else "roboguide.recovery-profile/v0.14"
    )
    profile = dict(raw_profile)
    profile["schema"] = schema
    profile["profile_id"] = "private-recipient"
    if schema == "roboguide.recovery-profile/v0.14":
        profile["components"] = [
            {
                key: value
                for key, value in component.items()
                if key not in {"storage_schema", "restore_binding_digest"}
            }
            for component in raw_profile["components"]
        ]
    return profile


def run(output: Path, writer_controller: Path) -> dict[str, Any]:
    """Own a fresh actual producer before retiring only its explicitly disposable recipient."""
    output.mkdir(parents=True, exist_ok=False)
    source = output / "producer"
    producer = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/quality/check_content_archive_processes.py"),
            "--output",
            str(source),
            "--skip-build",
            "--abort-unpublished",
        ],
        cwd=ROOT,
        capture_output=True,
        timeout=240,
    )
    (output / "producer.stdout").write_bytes(producer.stdout)
    (output / "producer.stderr").write_bytes(producer.stderr)
    assert producer.returncode == 0, producer.stderr.decode(errors="replace")
    original = json.loads((source / "result.json").read_bytes())
    assert original["passed"] is True and original["registered_nodes"] == 0
    raw_profile = json.loads((source / "profile.json").read_bytes())
    profile = legacy_profile(raw_profile)
    archive_root = Path(
        next(item["root"] for item in profile["components"] if item["kind"] == "archive")
    )
    # The producer's original complete pinned bundle remains immutable. Only this fresh gate's
    # explicitly owned live recipient is consumed; historical external sources are never modified.
    profile_path = output / "profile.json"
    save(profile_path, profile)
    controller = next(item for item in profile["components"] if item["kind"] == "controller")
    database = Path(controller["root"]) / controller["database"]
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        rows = connection.execute(
            "SELECT intent_json,receipt_json FROM event_archive_operations "
            "WHERE state='compacted' ORDER BY command_id"
        ).fetchall()
    if not rows:
        raise ValueError("original producer has no compacted Archive")
    intent = json.loads(rows[0][0])
    target = (
        archive_root
        / "archives-v1"
        / hashlib.sha256(intent["request"]["authority_id"].encode()).hexdigest()
        / ("archive-" + hashlib.sha256(intent["request"]["command_id"].encode()).hexdigest())
        / "content"
    )
    frozen = {
        name: digest((target / name).read_bytes()) for name in (".retention-owner.json", "blobs")
    }
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    environment["PYTHONPATH"] = str(ROOT / "apps/recovery-tool")
    commands: list[dict[str, Any]] = []

    def execute(
        operation: str,
        command: str,
        *,
        passed: bool = True,
        reason: str = "explicit owned acceptance retention decision",
    ) -> dict[str, Any]:
        """Retain actual CLI output and exits, including refused commands."""
        argv = [
            sys.executable,
            "-m",
            "roboguide_recovery",
            operation,
            "--profile",
            str(profile_path),
            "--controller",
            controller["component_id"],
            "--archive-command",
            intent["request"]["command_id"],
            "--original-intent-digest",
            digest(canonical(intent)),
            "--command-id",
            command,
            "--reason",
            reason,
        ]
        completed = subprocess.run(argv, cwd=ROOT, env=environment, capture_output=True, timeout=90)
        label = f"command-{len(commands)}"
        (output / (label + ".stdout")).write_bytes(completed.stdout)
        (output / (label + ".stderr")).write_bytes(completed.stderr)
        commands.append({"argv": argv, "exit_code": completed.returncode})
        save(output / "commands.json", commands)
        if not passed:
            assert completed.returncode != 0
            return {"stderr": completed.stderr.decode(errors="replace")}
        assert completed.returncode == 0, completed.stderr.decode(errors="replace")
        return dict(json.loads(completed.stdout))

    # A real existing recipient writer independently blocks admission before a tombstone exists.
    with (target / ".writer.lock").open("r+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        refused = execute("retire-archive", "active-writer-refused", passed=False)
        assert "writer is active" in refused["stderr"]
        assert not (target / "retirement-v1").exists()
    first = execute("retire-archive", "original-retirement")
    assert first["status"] == "retired" and first["unlink_count"] == 0
    tombstone = (target / "retirement-v1/intent.json").read_bytes()
    inputs = intent["inputs"]
    for item in inputs:
        checksum = item["content_digest"][7:]
        path = target / "objects-v1/sha256" / checksum[:2] / checksum
        assert digest(path.read_bytes()) == item["content_digest"]
    retry = execute("retire-archive", "original-retirement")
    assert retry["observation_only"] and retry["unlink_count"] == 0
    execute(
        "retire-archive", "original-retirement", passed=False, reason="changed original decision"
    )
    # A real create-new failure occurs after the original GC intent is durable, before unlink.
    fault = target / "retirement-v1/.gc-commands-update.json.pending"
    fault.mkdir()
    execute("gc-archive", "uncertain-original-gc", passed=False)
    assert execute("gc-archive", "uncertain-original-gc")["status"] == "unknown"
    fault.rmdir()
    for item in inputs:
        checksum = item["content_digest"][7:]
        assert (
            digest((target / "objects-v1/sha256" / checksum[:2] / checksum).read_bytes())
            == item["content_digest"]
        )
    # Actual filesystem permissions can reject a later unlink after earlier bytes were removed.
    parents = [target / "objects-v1/sha256" / item["content_digest"][7:9] for item in inputs]
    partial_unlink = len(set(parents)) > 1
    already_missing = 0
    if partial_unlink:
        denied = parents[-1]
        mode = denied.stat().st_mode & 0o777
        denied.chmod(0o500)
        try:
            failed = execute("gc-archive", "original-partial-gc", passed=False)
            assert "PermissionError" in failed["stderr"]
        finally:
            denied.chmod(mode)
        already_missing = sum(
            not (
                target
                / "objects-v1/sha256"
                / item["content_digest"][7:9]
                / item["content_digest"][7:]
            ).exists()
            for item in inputs
        )
        assert 0 < already_missing < len(inputs)
        assert execute("gc-archive", "original-partial-gc")["status"] == "unknown"
    gc = execute("gc-archive", "original-gc")
    assert gc["status"] == "complete" and gc["unlink_count"] == len(inputs) - already_missing
    assert execute("gc-archive", "original-gc")["unlink_count"] == 0
    assert (target / "retirement-v1/intent.json").read_bytes() == tombstone
    for item in inputs:
        checksum = item["content_digest"][7:]
        assert not (target / "objects-v1/sha256" / checksum[:2] / checksum).exists()
    # The supplied actual writer must refuse this sealed owner before opening content.
    addresses = []
    for _ in range(3):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            addresses.append(f"127.0.0.1:{listener.getsockname()[1]}")
    old_environment = environment | {
        "ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA": "roboguide.artifact-storage/v3"
    }
    controller = next(value for value in profile["components"] if value["kind"] == "controller")
    database = Path(controller["root"]) / controller["database"]
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as sql:
        source_schema = sql.execute(
            "SELECT schema FROM controller_checkpoint WHERE singleton=1"
        ).fetchone()[0]
    writer_schema = subprocess.check_output(
        [str(writer_controller), "--application-schema"], text=True, timeout=5
    ).strip()
    prefix = "roboguide.controller-checkpoint/v"
    assert isinstance(source_schema, str) and source_schema.startswith(prefix)
    assert writer_schema.startswith(prefix)
    older = int(writer_schema.removeprefix(prefix)) < int(source_schema.removeprefix(prefix))
    (output / "writer-application-schema.txt").write_text(writer_schema + "\n")
    old = subprocess.run(
        [
            str(writer_controller),
            addresses[0],
            str(output / "old.sqlite3"),
            addresses[1],
            addresses[2],
            str(target),
        ],
        cwd=ROOT,
        env=old_environment,
        capture_output=True,
        timeout=15,
    )
    (output / "old-controller.stdout").write_bytes(old.stdout)
    (output / "old-controller.stderr").write_bytes(old.stderr)
    assert old.returncode != 0 and any(
        word in old.stderr.decode(errors="replace").lower()
        for word in (
            "artifact directory component",
            "owner",
            "unsupported",
            "schema",
            "original maintenance journal requires explicit artifact storage v2",
        )
    ), old.stderr

    def recover(*arguments: str, passed: bool = True) -> dict[str, Any]:
        """Exercise the actual CLI cold path and retain original outcomes before interpretation."""
        argv = [sys.executable, "-m", "roboguide_recovery", *arguments]
        completed = subprocess.run(argv, cwd=ROOT, env=environment, capture_output=True, timeout=90)
        label = f"command-{len(commands)}"
        (output / (label + ".stdout")).write_bytes(completed.stdout)
        (output / (label + ".stderr")).write_bytes(completed.stderr)
        commands.append({"argv": argv, "exit_code": completed.returncode})
        save(output / "commands.json", commands)
        if not passed:
            assert completed.returncode != 0
            return {"stderr": completed.stderr.decode(errors="replace")}
        assert completed.returncode == 0, completed.stderr.decode(errors="replace")
        return dict(json.loads(completed.stdout))

    backed = output / "retired-bundle"
    backup = recover(
        "backup",
        "--profile",
        str(profile_path),
        "--destination",
        str(backed),
        "--command-id",
        "retired-original",
    )
    pin = backup["manifest_digest"]
    recover("verify", "--bundle", str(backed), "--manifest-digest", pin)
    restored = output / "restored"
    restore_args = (
        "restore",
        "--bundle",
        str(backed),
        "--manifest-digest",
        pin,
        "--destination",
        str(restored),
        "--command-id",
        "restore-original",
    )
    receipt = recover(*restore_args)
    assert receipt["schema"] == profile["schema"].replace("recovery-profile", "restore-receipt")
    assert recover(*restore_args) == receipt
    old_profile = output / "old-profile.json"
    old_definition = json.loads(json.dumps(profile))
    old_definition["schema"] = "roboguide.recovery-profile/v0.13"
    for component in old_definition["components"]:
        component.pop("storage_schema", None)
    save(old_profile, old_definition)
    refused = recover(
        "backup",
        "--profile",
        str(old_profile),
        "--destination",
        str(output / "old-refused"),
        "--command-id",
        "old-refused",
        passed=False,
    )
    required = (f"recovery v0.{profile['schema'].rsplit('/v0.', 1)[1]}",)
    assert any(version in refused["stderr"] for version in required), refused["stderr"]
    assert not (output / "old-refused").exists()
    damaged = output / "missing-tombstone"
    shutil.copytree(backed, damaged)
    manifest = json.loads((damaged / "manifest.json").read_bytes())
    tombstone_record = next(
        item for item in manifest["files"] if item["path"].endswith("/retirement-v1/intent.json")
    )
    (damaged / tombstone_record["path"]).unlink()
    manifest["files"].remove(tombstone_record)
    # bundle canonical bytes use escaped non-ASCII, independently of operation canonical JSON.
    raw_manifest = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    (damaged / "manifest.json").write_bytes(raw_manifest)
    rejected = recover(
        "verify", "--bundle", str(damaged), "--manifest-digest", digest(raw_manifest), passed=False
    )
    assert "intent.json" in rejected["stderr"] or "retirement" in rejected["stderr"]
    result = {
        "passed": True,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "nodes": 0,
        "actual_original_producer_result": digest((source / "result.json").read_bytes()),
        "actual_exclusive_recipient_writer_refused": True,
        "actual_retirement_before_unlink": True,
        "actual_gc_intent_before_publication_fault": True,
        "unknown_original_gc_not_repeated": True,
        "actual_unlink_count": gc["unlink_count"],
        "actual_prior_unlink_observed_without_receipt": already_missing,
        "actual_partial_unlink_failure": partial_unlink,
        "exact_retries_unlink_count": 0,
        "original_tombstone_unchanged": True,
        "pre_retirement_owner_headers": frozen,
        "actual_writer_controller_exit_code": old.returncode,
        "actual_writer_controller_digest": digest(writer_controller.read_bytes()),
        "actual_writer_controller_schema": writer_schema,
        "actual_producer_controller_schema": source_schema,
        "actual_older_controller_proof": older,
        "actual_old_controller_exit_code": old.returncode if older else None,
        "actual_old_controller_digest": digest(writer_controller.read_bytes()) if older else None,
        "actual_retired_cold_backup_verify_restore_retry": True,
        "missing_tombstone_refused_after_manifest_repin": True,
        "source_consumed_for_owned_retention_test": True,
    }
    save(output / "result.json", result)
    return result


def main() -> None:
    """Require explicit owned actual source and an immutable new acceptance output directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--writer-controller", "--old-controller", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output.absolute(), args.writer_controller), sort_keys=True))


if __name__ == "__main__":
    main()
