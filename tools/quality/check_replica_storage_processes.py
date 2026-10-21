"""Run independent processes against Replica storage v1 without Node/device evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import selectors
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any


def digest(path: Path) -> str:
    """Hash one pinned executable so evidence cannot silently use a different probe."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def start(
    binary: Path, database: Path, profile: str, schema: str
) -> tuple[subprocess.Popen[bytes], str]:
    """Start one owned storage process with a bounded nonblocking readiness deadline."""
    process = subprocess.Popen(
        [str(binary), str(database), profile, schema],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
    )
    try:
        if process.stdout is None:
            raise RuntimeError("storage probe did not expose stdout")
        deadline = time.monotonic() + 10
        data = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while b"\n" not in data:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise RuntimeError("storage probe readiness deadline expired")
                chunk = os.read(process.stdout.fileno(), 513 - len(data))
                if not chunk:
                    raise RuntimeError("storage probe exited before readiness")
                data.extend(chunk)
                if len(data) > 512:
                    raise RuntimeError("storage probe readiness exceeded its bound")
        return process, bytes(data).decode().strip()
    except BaseException:
        process.kill()
        process.communicate(timeout=5)
        raise


def stop(process: subprocess.Popen[bytes]) -> tuple[int, str, str]:
    """Close and reap an exact owned probe, including an unresponsive probe's timeout path."""
    try:
        stdout, stderr = process.communicate(b"done\n", timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate(timeout=5)
    return (
        process.returncode,
        stdout[-1024:].decode(errors="replace"),
        stderr[-1024:].decode(errors="replace"),
    )


def run(
    new_probe: Path,
    legacy_probe: Path,
    output: Path,
    profile: str,
    schema: str,
    previous_probe: Path | None = None,
) -> dict[str, Any]:
    """Exercise actual old connections, hardlinks, migration and cold old-writer refusal."""
    for probe in (new_probe, legacy_probe, *([previous_probe] if previous_probe else [])):
        if not probe.is_file() or not os.access(probe, os.X_OK):
            raise ValueError("each storage probe must be an executable regular file")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    records: list[dict[str, Any]] = []
    owned: list[subprocess.Popen[bytes]] = []
    result: dict[str, Any] = {
        "schema": "roboguide.replica-storage-process-check/v0.3",
        "new_probe_sha256": digest(new_probe),
        "legacy_probe_sha256": digest(legacy_probe),
        "profile": profile,
        "application_schema": schema,
        "processes": records,
        "nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "passed": False,
    }
    if previous_probe is not None:
        result["previous_probe_sha256"] = digest(previous_probe)

    def launch(binary: Path, database: Path) -> tuple[subprocess.Popen[bytes], dict[str, Any]]:
        """Retain each actual PID before any later assertion can fail."""
        process, ready = start(binary, database, profile, schema)
        owned.append(process)
        record: dict[str, Any] = {"pid": process.pid, "probe": str(binary), "ready": ready}
        records.append(record)
        return process, record

    def finish(process: subprocess.Popen[bytes], record: dict[str, Any]) -> None:
        """Retain the actual wait result for one original owned process."""
        code, stdout, stderr = stop(process)
        record.update(returncode=code, stdout_tail=stdout, stderr_tail=stderr)

    try:
        database, alias = output / "replica.sqlite3", output / "hardlink.sqlite3"
        legacy, legacy_record = launch(legacy_probe, database)
        if legacy_record["ready"] != "opened":
            raise ValueError("original legacy probe did not create its declared real store")
        os.link(database, alias)
        candidate, candidate_record = launch(new_probe, alias)
        finish(candidate, candidate_record)
        result["live_legacy_connection_refused"] = "not quiescent" in candidate_record["ready"]
        finish(legacy, legacy_record)
        writer, writer_record = launch(new_probe, alias)
        result["migration_after_legacy_release"] = writer_record["ready"] == "opened"
        blocked, blocked_record = launch(new_probe, database)
        finish(blocked, blocked_record)
        result["hardlink_writer_refused"] = "inode already owned" in blocked_record["ready"]
        old, old_record = launch(legacy_probe, database)
        finish(old, old_record)
        result["legacy_writer_refused"] = "refused:" in old_record["ready"]
        finish(writer, writer_record)
        if previous_probe is not None:
            previous, previous_record = launch(previous_probe, database)
            finish(previous, previous_record)
            result["previous_format_writer_refused"] = "refused:" in previous_record["ready"]
        reopened, reopened_record = launch(new_probe, database)
        finish(reopened, reopened_record)
        result["reopen_after_release"] = reopened_record["ready"] == "opened"
        if previous_probe is not None:
            original_v1 = output / "original-v1.sqlite3"
            alias_v1 = output / "original-v1-alias.sqlite3"
            previous, previous_record = launch(previous_probe, original_v1)
            if previous_record["ready"] != "opened":
                raise ValueError("original v1 probe did not open its actual declared store")
            os.link(original_v1, alias_v1)
            candidate, candidate_record = launch(new_probe, alias_v1)
            finish(candidate, candidate_record)
            result["active_previous_format_writer_refused"] = (
                "inode already owned" in candidate_record["ready"]
            )
            finish(previous, previous_record)
            migrated, migrated_record = launch(new_probe, alias_v1)
            finish(migrated, migrated_record)
            result["previous_format_migrated_after_release"] = migrated_record["ready"] == "opened"
            connection = sqlite3.connect(original_v1.as_uri() + "?mode=ro&immutable=1", uri=True)
            try:
                result["migrated_storage_version"] = connection.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]
                result["original_identity_preserved"] = json.loads(
                    connection.execute(
                        "SELECT value FROM replica_v1_meta WHERE key='identity'"
                    ).fetchone()[0]
                ) == [profile, schema]
            finally:
                connection.close()
        result["passed"] = all(
            result.get(key) is True
            for key in (
                "live_legacy_connection_refused",
                "migration_after_legacy_release",
                "hardlink_writer_refused",
                "legacy_writer_refused",
                "reopen_after_release",
            )
        ) and all(record.get("returncode") == 0 for record in records)
        if previous_probe is not None:
            result["passed"] = (
                result["passed"]
                and all(
                    result.get(key) is True
                    for key in (
                        "previous_format_writer_refused",
                        "active_previous_format_writer_refused",
                        "previous_format_migrated_after_release",
                        "original_identity_preserved",
                    )
                )
                and result["migrated_storage_version"] == 2
            )
    except Exception as error:
        result["error"] = str(error)
    finally:
        for process, record in zip(owned, records, strict=True):
            if "returncode" not in record:
                finish(process, record)
        result["all_owned_processes_reaped"] = all(process.poll() is not None for process in owned)
        result["probe_identities_unchanged"] = (
            digest(new_probe) == result["new_probe_sha256"]
            and digest(legacy_probe) == result["legacy_probe_sha256"]
            and (
                previous_probe is None or digest(previous_probe) == result["previous_probe_sha256"]
            )
        )
        result["passed"] = bool(
            result["passed"]
            and result["all_owned_processes_reaped"]
            and result["probe_identities_unchanged"]
        )
        (output / "RESULT.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
        (output / "owned-process-exits.json").write_text(
            json.dumps(
                {
                    "all_owned_processes_reaped": result["all_owned_processes_reaped"],
                    "pids": [process.pid for process in owned],
                },
                sort_keys=True,
            )
            + "\n"
        )
    return result


def main() -> None:
    """Parse explicit probe paths and write one bounded process receipt."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--new-probe", type=Path, required=True)
    parser.add_argument("--legacy-probe", type=Path, required=True)
    parser.add_argument("--previous-probe", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", default="a" * 64)
    parser.add_argument("--application-schema", default="roboguide.controller-checkpoint/v45")
    arguments = parser.parse_args()
    result = run(
        arguments.new_probe.resolve(),
        arguments.legacy_probe.resolve(),
        arguments.output.resolve(),
        arguments.profile,
        arguments.application_schema,
        arguments.previous_probe.resolve() if arguments.previous_probe is not None else None,
    )
    print(json.dumps(result, sort_keys=True))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
