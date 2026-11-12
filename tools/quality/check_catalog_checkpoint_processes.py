"""Measure standalone Catalog recovery on owned local copies with hard process resource limits."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import shutil
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

ADDRESS_SPACE_LIMIT_BYTES = 2147483648
DEFAULT_CPU_SECONDS = 180
DEFAULT_WALL_SECONDS = 240
MAXIMUM_BUDGET_SECONDS = 3600


def bounded_probe_seconds(value: str) -> int:
    """Reject noninteger or unbounded probe budgets before creating the owned output root."""
    try:
        seconds = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "probe budget must be an integer number of seconds"
        ) from error
    if not 1 <= seconds <= MAXIMUM_BUDGET_SECONDS:
        raise argparse.ArgumentTypeError(
            f"probe budget must be between 1 and {MAXIMUM_BUDGET_SECONDS} seconds"
        )
    return seconds


def original_identity(database: Path) -> dict[str, Any]:
    """Hash original event, application and command rows without historical arrays."""
    result = {}
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps/recovery-tool"))
    from roboguide_recovery.controller_values import install_functions

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    install_functions(connection)
    try:
        for table, order in (
            ("events", "sequence"),
            ("controller_checkpoint", "singleton"),
            ("control_command_journal", "command_id"),
        ):
            digest = hashlib.sha256()
            count = 0
            for row in connection.execute(f"SELECT * FROM {table} ORDER BY {order}"):
                data = json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode()
                digest.update(len(data).to_bytes(8, "little"))
                digest.update(data)
                count += 1
            result[table] = {"rows": count, "sha256": digest.hexdigest()}
    finally:
        connection.close()
    return result


def run_probe(
    binary: Path,
    database: Path,
    mode: str,
    output: Path,
    *,
    cpu_seconds: int = DEFAULT_CPU_SECONDS,
    wall_seconds: int = DEFAULT_WALL_SECONDS,
) -> dict[str, Any]:
    """Constrain an owned probe and retain its exact bounded budget even if it is interrupted."""
    cpu_seconds = bounded_probe_seconds(str(cpu_seconds))
    wall_seconds = bounded_probe_seconds(str(wall_seconds))
    limits = {
        "address_space_bytes": ADDRESS_SPACE_LIMIT_BYTES,
        "cpu_seconds": cpu_seconds,
        "wall_seconds": wall_seconds,
    }
    with output.with_suffix(".limits.json").open("x") as evidence:
        evidence.write(json.dumps(limits, indent=2))
    raw_path = output.with_suffix(".raw.json")
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    with raw_path.open("xb") as raw:
        process = subprocess.Popen(
            [
                "/usr/bin/time",
                "-f",
                "%M",
                "-o",
                str(output.with_suffix(".rss")),
                "prlimit",
                f"--as={ADDRESS_SPACE_LIMIT_BYTES}",
                f"--cpu={cpu_seconds}",
                "--",
                str(binary),
                mode,
                str(database),
            ],
            stdout=raw,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            _, stderr = process.communicate(timeout=wall_seconds)
        except BaseException:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise
    output.with_suffix(".stderr").write_bytes(stderr)
    if process.returncode:
        raise RuntimeError(f"owned probe failed ({process.returncode}); see {output}.stderr")
    result = cast(dict[str, Any], json.loads(raw_path.read_text()))
    checkpoint = result.pop("checkpoint_json", None)
    result["checkpoint_sha256"] = (
        hashlib.sha256(checkpoint.encode()).hexdigest() if checkpoint is not None else None
    )
    result["peak_rss_kib"] = int(output.with_suffix(".rss").read_text().strip())
    result["cpu_seconds"] = resource.getrusage(resource.RUSAGE_CHILDREN).ru_utime - before.ru_utime
    result["resource_limits"] = limits
    output.write_text(json.dumps(result, indent=2))
    return result


def refuse_old_writer(binary: Path, database: Path, root: Path) -> dict[str, Any]:
    """Run an actual previous Controller under bounded resources and retain refusal evidence."""
    with database.open("rb") as source:
        before = hashlib.file_digest(source, "sha256").hexdigest()
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("ROBOGUIDE_")
    }
    environment["ROBOGUIDE_CONTROLLER_STORAGE_SCHEMA"] = "1"
    process = subprocess.run(
        [
            "prlimit",
            "--as=2147483648",
            "--cpu=20",
            "--",
            str(binary),
            "127.0.0.1:0",
            str(database),
            "127.0.0.1:0",
            "127.0.0.1:0",
            str(root / "old-writer-artifacts"),
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        timeout=25,
        check=False,
    )
    (root / "old-writer.stderr").write_bytes(process.stderr)
    assert (
        process.returncode != 0 and b"storage v1 mandatory version/view differs" in process.stderr
    )
    with database.open("rb") as source:
        assert hashlib.file_digest(source, "sha256").hexdigest() == before
    with binary.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    return {
        "refused": True,
        "exit": process.returncode,
        "database_unchanged": True,
        "binary_sha256": digest,
    }


def main() -> None:
    """Compare identical snapshots around migration while preserving original history."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--previous-controller", type=Path)
    parser.add_argument("--storage-schema", choices=("2", "3"), default="2")
    parser.add_argument(
        "--migration-cpu-seconds",
        type=bounded_probe_seconds,
        default=DEFAULT_CPU_SECONDS,
        help="first migration CPU budget in seconds, 1..3600 (default: 180)",
    )
    parser.add_argument(
        "--migration-wall-seconds",
        type=bounded_probe_seconds,
        default=DEFAULT_WALL_SECONDS,
        help="first migration wall-clock budget in seconds, 1..3600 (default: 240)",
    )
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    database = root / "owned.sqlite3"
    shutil.copy2(args.snapshot, database)
    original = original_identity(database)
    results = {}
    for mode, name in (
        ("legacy", "streaming"),
        (args.storage_schema, "migration"),
        (args.storage_schema, "restart"),
    ):
        limits = (
            {"cpu_seconds": args.migration_cpu_seconds, "wall_seconds": args.migration_wall_seconds}
            if name == "migration"
            else {}
        )
        results[name] = run_probe(
            args.probe.resolve(), database, mode, root / (name + ".json"), **limits
        )
        assert results[name]["checkpoint_sha256"] == results["streaming"]["checkpoint_sha256"]
        assert (
            results[name]["original_latest_sequence"]
            == results["streaming"]["original_latest_sequence"]
        )
        assert results[name]["timestamp_high_water"] == results["streaming"]["timestamp_high_water"]
        assert (
            results[name]["retained_catalog_events"]
            == results["streaming"]["retained_catalog_events"]
        )
    assert original_identity(database) == original, "original source rows or checkpoint changed"
    previous = (
        refuse_old_writer(args.previous_controller.resolve(), database, root)
        if args.previous_controller
        else None
    )
    with args.probe.open("rb") as source:
        probe_digest = hashlib.file_digest(source, "sha256").hexdigest()
    report = {
        "passed": True,
        "input_snapshot": str(args.snapshot),
        "original_rows": original,
        "measurements": results,
        "no_node_or_controller_service": True,
        "history_deleted": False,
        "memory_limit_bytes": ADDRESS_SPACE_LIMIT_BYTES,
        "probe_resource_limits": {
            name: measurement["resource_limits"] for name, measurement in results.items()
        },
        "probe_sha256": probe_digest,
        "old_writer": previous,
        "storage_schema": args.storage_schema,
    }
    (root / "result.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"passed": True, "measurements": results}))


if __name__ == "__main__":
    main()
