"""Run the frozen six-run F0/F1 task17 recovery pilot sequentially."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from statistics import mean
from typing import Any, cast


def json_write(path: Path, value: object) -> None:
    """Atomically persist batch progress."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def git(repo: Path, *arguments: str) -> str:
    """Read repository provenance."""
    return subprocess.check_output(
        ["git", "-C", str(repo), *arguments], text=True, encoding="utf-8"
    ).strip()


def load_manifest(path: Path) -> dict[str, Any]:
    """Validate the counterbalanced three-clean/three-fault pilot."""
    manifest = json.loads(path.read_text(encoding="utf-8"))
    runs = manifest.get("runs")
    if not isinstance(runs, list) or len(runs) != 6:
        raise ValueError("recovery pilot must contain exactly six runs")
    profiles = [run.get("fault_profile") for run in runs if isinstance(run, dict)]
    if profiles.count("f0-clean") != 3 or profiles.count("f1-node-loss") != 3:
        raise ValueError("pilot must contain three F0 and three F1 runs")
    if any(set(run) != {"fault_profile", "replicate"} for run in runs):
        raise ValueError("each run must contain only fault_profile and replicate")
    if sorted(run["replicate"] for run in runs if run["fault_profile"] == "f0-clean") != [1, 2, 3]:
        raise ValueError("F0 replicate identities must be 1, 2, 3")
    fault_replicates = sorted(
        run["replicate"] for run in runs if run["fault_profile"] == "f1-node-loss"
    )
    if fault_replicates != [1, 2, 3]:
        raise ValueError("F1 replicate identities must be 1, 2, 3")
    return cast(dict[str, Any], manifest)


def summary(records: list[dict[str, Any]], profile: str) -> dict[str, Any]:
    """Aggregate machine results with missing verdicts retained as failures."""
    selected = [record for record in records if record["fault_profile"] == profile]
    verdicts = [record["verdict"] for record in selected if record.get("verdict")]
    successes = [verdict for verdict in verdicts if verdict["full_fault_run_success"]]
    task_successes = [verdict for verdict in verdicts if verdict["task_success"]]
    wall = [
        float(verdict["base_task_verdict"]["wall_time_seconds"])
        for verdict in verdicts
        if verdict.get("base_task_verdict")
    ]
    return {
        "scheduled_runs": len(selected),
        "machine_verdicts": len(verdicts),
        "task_successes": len(task_successes),
        "full_fault_run_successes": len(successes),
        "success_rate": len(successes) / len(selected),
        "mean_wall_time_seconds": mean(wall) if wall else None,
    }


def run(args: argparse.Namespace) -> int:
    """Run every frozen case even when an individual experiment fails."""
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is required")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    manifest_path = args.manifest.resolve()
    manifest = load_manifest(manifest_path)
    settings = tomllib.loads(args.config.read_text(encoding="utf-8"))
    experiment = {
        "schema": "roboguide.e2-recovery-pilot/v0.1",
        "started_at_unix": time.time(),
        "roboguide_commit": git(args.repo, "rev-parse", "HEAD"),
        "roboguide_status": git(args.repo, "status", "--short"),
        "coherent_commit": git(args.coherent_root, "rev-parse", "HEAD"),
        "manifest": manifest,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
        "model": settings["mission"]["llm"]["model"],
        "review_model": settings["mission"]["llm"]["review_model"],
        "prompt_profile": "fair",
        "dag_profile": "serial",
        "credentials_recorded": False,
        "execution_order": "counterbalanced F0/F1/F1/F0/F0/F1",
    }
    if experiment["model"] != "gpt-6.1-sol" or experiment["review_model"] != "gpt-6.1-sol":
        raise ValueError("all Mission Intelligence calls must use gpt-6.1-sol")
    json_write(output / "experiment-config.json", experiment)
    records: list[dict[str, Any]] = []
    progress = output / "batch-progress.json"
    for index, case in enumerate(manifest["runs"], start=1):
        profile = case["fault_profile"]
        replicate = case["replicate"]
        run_output = output / f"{index:02d}-{profile}-rep{replicate}"
        offset = args.port_offset + args.port_stride * (index - 1)
        command = [
            sys.executable,
            str(args.repo / "tools/e2-recovery/run_fault.py"),
            "--repo",
            str(args.repo),
            "--coherent-root",
            str(args.coherent_root),
            "--binary-root",
            str(args.binary_root),
            "--config",
            str(args.config),
            "--output",
            str(run_output),
            "--env",
            manifest["env"],
            "--task",
            str(manifest["task"]),
            "--fault-profile",
            profile,
            "--inject-after-steps",
            str(manifest["inject_after_completed_primitives"]),
            "--max-segments",
            str(manifest["max_segments"]),
            "--execution-timeout",
            str(manifest["execution_timeout_seconds"]),
            "--port-offset",
            str(offset),
        ]
        record: dict[str, Any] = {
            "index": index,
            "fault_profile": profile,
            "replicate": replicate,
            "state": "RUNNING",
            "command": command,
            "port_offset": offset,
            "started_at_unix": time.time(),
        }
        records.append(record)
        json_write(progress, {"complete": False, "runs": records})
        console = output / f"{index:02d}-{profile}-rep{replicate}.console.log"
        with console.open("wb") as stream:
            completed = subprocess.run(
                command,
                stdout=stream,
                stderr=subprocess.STDOUT,
                env=dict(os.environ),
                check=False,
            )
        verdict_path = run_output / "fault-verdict.json"
        verdict = json.loads(verdict_path.read_text()) if verdict_path.exists() else None
        record.update(
            {
                "state": "FINISHED" if verdict else "MISSING_VERDICT",
                "return_code": completed.returncode,
                "finished_at_unix": time.time(),
                "console_path": str(console),
                "verdict_path": str(verdict_path),
                "verdict": verdict,
            }
        )
        json_write(progress, {"complete": False, "runs": records})
    result = {
        "schema": "roboguide.e2-recovery-pilot-summary/v0.1",
        "complete": all(record.get("verdict") for record in records),
        "profiles": {
            profile: summary(records, profile) for profile in ("f0-clean", "f1-node-loss")
        },
        "scientific_boundary": (
            "This pilot measures clean execution versus recoverable Node loss before primitive "
            "k+1. It does not measure ambiguous post-effect failures or goal reconciliation."
        ),
    }
    json_write(output / "summary.json", result)
    json_write(progress, {"complete": result["complete"], "runs": records})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["complete"] else 1


def main() -> int:
    """Parse frozen deployment paths and ports."""
    parser = argparse.ArgumentParser()
    for option in ("repo", "coherent-root", "binary-root", "config", "manifest", "output"):
        parser.add_argument("--" + option, type=Path, required=True)
    parser.add_argument("--port-offset", type=int, default=400)
    parser.add_argument("--port-stride", type=int, default=100)
    arguments = parser.parse_args()
    return run(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
