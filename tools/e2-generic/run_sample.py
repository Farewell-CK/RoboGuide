#!/usr/bin/env python3
"""Run a frozen COHERENT task sample sequentially with resumable batch evidence."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import cast


def write_json(path: Path, value: object) -> None:
    """Atomically replace one human-readable batch status file."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_task(value: str) -> tuple[str, int]:
    """Parse one frozen ``envX/taskY`` identity."""
    env_name, raw_task = value.split("/", 1)
    if not raw_task.startswith("task"):
        raise ValueError(f"invalid task identity {value!r}")
    return env_name, int(raw_task[4:])


def main() -> int:
    """Run unfinished sample tasks one at a time and retain failures without retrying."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--coherent-root", type=Path, required=True)
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reuse", action="append", default=[])
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads(args.sample.read_text(encoding="utf-8"))
    tasks = cast(list[str], manifest["tasks"])
    reuse: dict[str, Path] = {}
    for item in args.reuse:
        identity, raw_path = item.split("=", 1)
        reuse[identity] = Path(raw_path).resolve()
    records: list[dict[str, object]] = []
    started = time.time()
    status: dict[str, object] = {
        "schema": "roboguide.coherent-generic-batch/v0.1",
        "state": "RUNNING",
        "started_at_unix": started,
        "sample": manifest,
        "records": records,
    }
    write_json(output / "batch-progress.json", status)
    for index, identity in enumerate(tasks, start=1):
        env_name, task_index = parse_task(identity)
        task_dir = output / f"{index:02d}-{env_name}-task{task_index}"
        task_started = time.time()
        if identity in reuse:
            source = reuse[identity]
            if not (source / "verdict.json").is_file():
                raise RuntimeError(f"reused run lacks verdict.json: {source}")
            shutil.copytree(source, task_dir)
            returncode = 0
            reused_from: str | None = str(source)
        else:
            reused_from = None
            command = [
                sys.executable,
                str(args.repo / "tools/e2-generic/run_task.py"),
                "--repo",
                str(args.repo),
                "--coherent-root",
                str(args.coherent_root),
                "--env",
                env_name,
                "--task",
                str(task_index),
                "--config",
                str(args.config),
                "--output",
                str(task_dir),
            ]
            with (output / f"{index:02d}-{env_name}-task{task_index}.log").open("wb") as log:
                completed = subprocess.run(
                    command, stdout=log, stderr=subprocess.STDOUT, check=False
                )
            returncode = completed.returncode
        verdict = json.loads((task_dir / "verdict.json").read_text(encoding="utf-8"))
        records.append(
            {
                "index": index,
                "task": identity,
                "returncode": returncode,
                "success": verdict.get("success"),
                "primitive_steps": verdict.get("primitive_steps"),
                "decision_count": verdict.get("decision_count"),
                "reused_from": reused_from,
                "result_directory": str(task_dir),
                "wall_time_seconds": time.time() - task_started,
            }
        )
        status["records"] = records
        status["completed"] = len(records)
        status["remaining"] = len(tasks) - len(records)
        write_json(output / "batch-progress.json", status)
    status["state"] = "COMPLETED"
    status["finished_at_unix"] = time.time()
    status["wall_time_seconds"] = time.time() - started
    status["successes"] = sum(record["success"] is True for record in records)
    status["failures"] = sum(record["success"] is not True for record in records)
    write_json(output / "batch-progress.json", status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
