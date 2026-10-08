#!/usr/bin/env python3
"""Run a sequential, paired fair-versus-informed E2 prompt ablation."""

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
    """Atomically persist one readable progress or result document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def git(repo: Path, *arguments: str) -> str:
    """Read one repository identity fact without mutating the checkout."""
    return subprocess.check_output(
        ["git", "-C", str(repo), *arguments], text=True, encoding="utf-8"
    ).strip()


def load_manifest(path: Path) -> dict[str, Any]:
    """Validate the frozen two-profile, three-paired-task pilot manifest."""
    manifest = json.loads(path.read_text(encoding="utf-8"))
    runs = manifest.get("runs")
    if not isinstance(runs, list) or len(runs) != 6:
        raise ValueError("pilot manifest must contain exactly six runs")
    pairs: dict[tuple[str, int], set[str]] = {}
    for run in runs:
        if not isinstance(run, dict) or set(run) != {"env", "task", "prompt_profile"}:
            raise ValueError("each run must contain env/task/prompt_profile")
        env_name, task, profile = run["env"], run["task"], run["prompt_profile"]
        if env_name not in {f"env{i}" for i in range(5)} or type(task) is not int:
            raise ValueError("invalid official task identity")
        if profile not in {"fair", "informed"}:
            raise ValueError("prompt profile must be fair or informed")
        pairs.setdefault((env_name, task), set()).add(profile)
    if len(pairs) != 3 or any(profiles != {"fair", "informed"} for profiles in pairs.values()):
        raise ValueError("pilot must pair fair and informed on exactly three tasks")
    if manifest.get("dag_profile") != "serial":
        raise ValueError("prompt ablation freezes the serial DAG profile")
    return cast(dict[str, Any], manifest)


def profile_summary(records: list[dict[str, Any]], profile: str) -> dict[str, Any]:
    """Aggregate only machine verdicts while retaining failures in the denominator."""
    selected = [record for record in records if record["prompt_profile"] == profile]
    verdicts = [record["verdict"] for record in selected if record.get("verdict") is not None]
    successes = [verdict for verdict in verdicts if verdict["success"]]
    return {
        "scheduled_runs": len(selected),
        "machine_verdicts": len(verdicts),
        "successes": len(successes),
        "success_rate": len(successes) / len(selected) if selected else None,
        "mean_primitive_steps": (
            mean(float(verdict["primitive_steps"]) for verdict in verdicts) if verdicts else None
        ),
        "mean_planning_segments": (
            mean(float(verdict["planning_segments"]) for verdict in verdicts) if verdicts else None
        ),
        "mean_model_calls": (
            mean(float(verdict["model_calls"]) for verdict in verdicts) if verdicts else None
        ),
        "mean_wall_time_seconds": (
            mean(float(verdict["wall_time_seconds"]) for verdict in verdicts) if verdicts else None
        ),
        "failures": [
            {
                "public_task": record["public_task"],
                "return_code": record["return_code"],
                "failure_reason": (
                    None if record.get("verdict") is None else record["verdict"]["failure_reason"]
                ),
            }
            for record in selected
            if record.get("verdict") is None or not record["verdict"]["success"]
        ],
    }


def run_port_offset(base: int, stride: int, index: int) -> int:
    """Give every sequential run a disjoint port range."""
    if stride < 1 or index < 1:
        raise ValueError("port stride and run index must be positive")
    return base + stride * (index - 1)


def unused_attempt_path(path: Path) -> Path:
    """Preserve every earlier console or partial run instead of overwriting it."""
    if not path.exists():
        return path
    for attempt in range(1, 100):
        candidate = path.with_name(f"{path.stem}.resume-{attempt:02d}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"too many preserved attempts for {path}")


def matching_resume_config(existing: dict[str, Any], expected: dict[str, Any]) -> None:
    """Reject a resume if any scientific input changed."""
    frozen = (
        "coherent_commit",
        "manifest_sha256",
        "config_sha256",
        "model",
        "review_model",
        "execution_order",
    )
    changed = [key for key in frozen if existing.get(key) != expected.get(key)]
    if changed:
        raise ValueError(f"resume inputs differ: {', '.join(changed)}")


def run(args: argparse.Namespace) -> int:
    """Execute all frozen runs sequentially and retain failures without editing plans."""
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is required")
    repo = args.repo.resolve()
    output = args.output.resolve()
    manifest_path = args.manifest.resolve()
    manifest = load_manifest(manifest_path)
    config_bytes = args.config.resolve().read_bytes()
    settings = tomllib.loads(config_bytes.decode("utf-8"))
    started = time.time()
    experiment = {
        "schema": "roboguide.e2-prompt-ablation-run/v0.1",
        "started_at_unix": started,
        "roboguide_commit": git(repo, "rev-parse", "HEAD"),
        "roboguide_status": git(repo, "status", "--short"),
        "coherent_commit": git(args.coherent_root.resolve(), "rev-parse", "HEAD"),
        "manifest": manifest,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "model": settings["mission"]["llm"]["model"],
        "review_model": settings["mission"]["llm"]["review_model"],
        "credentials_recorded": False,
        "execution_order": "sequential paired AB/BA/AB",
    }
    progress_path = output / "batch-progress.json"
    prior_records: dict[int, dict[str, Any]] = {}
    if args.resume:
        if not output.is_dir():
            raise ValueError("resume output directory does not exist")
        existing_experiment = json.loads(
            (output / "experiment-config.json").read_text(encoding="utf-8")
        )
        matching_resume_config(existing_experiment, experiment)
        if progress_path.exists():
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
            prior_records = {int(record["index"]): record for record in progress["runs"]}
        resume_path = unused_attempt_path(output / "resume-event.json")
        json_write(
            resume_path,
            {
                "resumed_at_unix": started,
                "original_roboguide_commit": existing_experiment["roboguide_commit"],
                "resume_roboguide_commit": experiment["roboguide_commit"],
                "reason": "continue only runs without a machine verdict",
            },
        )
    else:
        output.mkdir(parents=True, exist_ok=False)
        json_write(output / "experiment-config.json", experiment)
    records: list[dict[str, Any]] = []
    for index, specification in enumerate(manifest["runs"], start=1):
        env_name = specification["env"]
        task = specification["task"]
        profile = specification["prompt_profile"]
        public_task = f"{env_name}/task{task}"
        run_output = output / f"{index:02d}-{env_name}-task{task}-{profile}"
        verdict_path = run_output / "verdict.json"
        if args.resume and verdict_path.exists():
            verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
            prior = prior_records.get(index, {})
            records.append(
                {
                    **prior,
                    "index": index,
                    "public_task": public_task,
                    "prompt_profile": profile,
                    "state": "FINISHED",
                    "return_code": prior.get("return_code", 0),
                    "verdict_path": str(verdict_path),
                    "verdict": verdict,
                    "resume_action": "reused_existing_machine_verdict",
                }
            )
            json_write(progress_path, {"runs": records, "complete": False})
            continue
        actual_output = unused_attempt_path(run_output) if run_output.exists() else run_output
        offset = run_port_offset(args.port_offset, args.port_stride, index)
        command = [
            sys.executable,
            str(repo / "tools/e2-generic/run_dag.py"),
            "--repo",
            str(repo),
            "--coherent-root",
            str(args.coherent_root.resolve()),
            "--binary-root",
            str(args.binary_root.resolve()),
            "--config",
            str(args.config.resolve()),
            "--env",
            env_name,
            "--task",
            str(task),
            "--prompt-profile",
            profile,
            "--dag-profile",
            manifest["dag_profile"],
            "--max-segments",
            str(manifest["max_segments"]),
            "--execution-timeout",
            str(manifest["execution_timeout_seconds"]),
            "--port-offset",
            str(offset),
            "--output",
            str(actual_output),
        ]
        record: dict[str, Any] = {
            "index": index,
            "public_task": public_task,
            "prompt_profile": profile,
            "command": command,
            "started_at_unix": time.time(),
            "state": "RUNNING",
            "port_offset": offset,
            "prior_infrastructure_attempt": prior_records.get(index),
        }
        records.append(record)
        json_write(progress_path, {"runs": records, "complete": False})
        actual_output.parent.mkdir(parents=True, exist_ok=True)
        console = unused_attempt_path(
            output / f"{index:02d}-{env_name}-task{task}-{profile}.console.log"
        )
        with console.open("wb") as log:
            completed = subprocess.run(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=dict(os.environ),
                check=False,
            )
        verdict_path = actual_output / "verdict.json"
        verdict = (
            json.loads(verdict_path.read_text(encoding="utf-8")) if verdict_path.exists() else None
        )
        record.update(
            {
                "state": "FINISHED" if verdict is not None else "MISSING_VERDICT",
                "return_code": completed.returncode,
                "finished_at_unix": time.time(),
                "console_path": str(console),
                "verdict_path": str(verdict_path),
                "verdict": verdict,
            }
        )
        json_write(progress_path, {"runs": records, "complete": False})
    summary = {
        "schema": "roboguide.e2-prompt-ablation-summary/v0.1",
        "study_id": manifest["study_id"],
        "complete": all(record.get("verdict") is not None for record in records),
        "paired_tasks": sorted({record["public_task"] for record in records}),
        "profiles": {
            profile: profile_summary(records, profile) for profile in ("fair", "informed")
        },
        "wall_time_seconds": time.time() - started,
        "interpretation_boundary": (
            "This pilot isolates prompt-profile effects only; it is not evidence of an "
            "architectural advantage over PEFA."
        ),
    }
    json_write(output / "summary.json", summary)
    json_write(progress_path, {"runs": records, "complete": summary["complete"]})
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["complete"] else 1


def main() -> int:
    """Parse frozen deployment paths for one sequential pilot batch."""
    parser = argparse.ArgumentParser()
    for option in ("repo", "coherent-root", "binary-root", "config", "manifest", "output"):
        parser.add_argument("--" + option, type=Path, required=True)
    parser.add_argument("--port-offset", type=int, default=0)
    parser.add_argument("--port-stride", type=int, default=100)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
