#!/usr/bin/env python3
"""Atomically record the public, minimal state of an externally managed run."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path


SCHEMA = "roboguide.codex-sync-active-run/v0.1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--status", choices=("idle", "running", "blocked"), required=True)
    parser.add_argument("--public-task")
    parser.add_argument("--run-id")
    parser.add_argument("--fault-profile")
    parser.add_argument("--roboguide-commit")
    parser.add_argument("--observed-at")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.status == "running" and not all(
        (args.public_task, args.run_id, args.fault_profile, args.roboguide_commit)
    ):
        raise SystemExit(
            "running state requires task, run ID, fault profile, and RoboGuide commit"
        )
    observed_at = args.observed_at or datetime.now().astimezone().isoformat(
        timespec="seconds"
    )
    datetime.fromisoformat(observed_at.replace("Z", "+00:00"))

    state: dict[str, str] = {
        "schema": SCHEMA,
        "status": args.status,
        "observed_at": observed_at,
    }
    for key in ("public_task", "run_id", "fault_profile", "roboguide_commit"):
        value = getattr(args, key)
        if value:
            state[key] = value

    path = args.state_file.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
