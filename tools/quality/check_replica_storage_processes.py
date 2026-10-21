"""Run independent processes against Replica storage v1 without Node/device evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def digest(path: Path) -> str:
    """Hash one pinned executable so evidence cannot silently use a different probe."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def start(binary: Path, database: Path) -> tuple[subprocess.Popen[str], str]:
    """Start one owned real storage process and capture its bounded readiness line."""
    process = subprocess.Popen(
        [str(binary), str(database)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        close_fds=True,
    )
    if process.stdout is None:
        process.kill()
        raise RuntimeError("storage probe did not expose stdout")
    line = process.stdout.readline().strip()
    if len(line) > 512:
        process.kill()
        process.wait(timeout=5)
        raise RuntimeError("storage probe readiness exceeded its bound")
    return process, line


def stop(process: subprocess.Popen[str]) -> tuple[int, str, str]:
    """Close one owned probe and reap it before returning bounded output."""
    if process.stdin is not None:
        try:
            process.stdin.write("done\n")
            process.stdin.close()
        except BrokenPipeError:
            process.stdin.close()
    returncode = process.wait(timeout=10)
    stdout = process.stdout.read()[-1024:] if process.stdout is not None else ""
    stderr = process.stderr.read()[-1024:] if process.stderr is not None else ""
    return returncode, stdout, stderr


def run(new_probe: Path, legacy_probe: Path, output: Path) -> dict[str, Any]:
    """Prove inode exclusion, old-writer refusal and post-release reopen with actual processes."""
    for probe in (new_probe, legacy_probe):
        if not probe.is_file() or not os.access(probe, os.X_OK):
            raise ValueError("each storage probe must be an executable regular file")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="roboguide-replica-storage-") as temporary:
        root = Path(temporary)
        database = root / "replica.sqlite3"
        alias = root / "hardlink.sqlite3"
        records: list[dict[str, Any]] = []
        process, ready = start(new_probe, database)
        records.append({"pid": process.pid, "probe": str(new_probe), "ready": ready})
        os.link(database, alias)
        blocked, blocked_ready = start(new_probe, alias)
        code, stdout, stderr = stop(blocked)
        records.append(
            {
                "pid": blocked.pid,
                "probe": str(new_probe),
                "ready": blocked_ready,
                "returncode": code,
                "stdout_tail": stdout,
                "stderr_tail": stderr,
            }
        )
        legacy, legacy_ready = start(legacy_probe, alias)
        code, stdout, stderr = stop(legacy)
        records.append(
            {
                "pid": legacy.pid,
                "probe": str(legacy_probe),
                "ready": legacy_ready,
                "returncode": code,
                "stdout_tail": stdout,
                "stderr_tail": stderr,
            }
        )
        code, stdout, stderr = stop(process)
        records[0].update({"returncode": code, "stdout_tail": stdout, "stderr_tail": stderr})
        reopened, reopened_ready = start(new_probe, alias)
        code, stdout, stderr = stop(reopened)
        records.append(
            {
                "pid": reopened.pid,
                "probe": str(new_probe),
                "ready": reopened_ready,
                "returncode": code,
                "stdout_tail": stdout,
                "stderr_tail": stderr,
            }
        )
        result: dict[str, Any] = {
            "schema": "roboguide.replica-storage-v1-process-check/v0.1",
            "new_probe_sha256": digest(new_probe),
            "legacy_probe_sha256": digest(legacy_probe),
            "processes": records,
            "nodes": 0,
            "devices": 0,
            "models": 0,
            "simulators": 0,
            "hardlink_writer_refused": "inode already owned" in records[1]["ready"],
            "legacy_writer_refused": "refused:" in records[2]["ready"],
            "reopen_after_release": records[3]["ready"] == "opened",
        }
        result["passed"] = bool(
            result["hardlink_writer_refused"]
            and result["legacy_writer_refused"]
            and result["reopen_after_release"]
            and all(record.get("returncode") == 0 for record in records)
        )
    (output / "RESULT.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    (output / "owned-process-exits.json").write_text(
        json.dumps(
            {
                "all_owned_processes_reaped": all(
                    record.get("returncode") is not None for record in result["processes"]
                ),
                "pids": [record["pid"] for record in result["processes"]],
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
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    result = run(
        arguments.new_probe.resolve(), arguments.legacy_probe.resolve(), arguments.output.resolve()
    )
    print(json.dumps(result, sort_keys=True))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
