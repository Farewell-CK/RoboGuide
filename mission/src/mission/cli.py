"""Command-line boundary for planning and validating Mission Plan artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import cast

from mission.config import current_environment, load_settings
from mission.models import JSONObject, MissionPlan
from mission.planners import FixturePlanner, MissionPlanner
from mission.responses import ResponsesMissionPlanner


def _parser() -> argparse.ArgumentParser:
    """Build the CLI parser without reading configuration or touching the network."""
    parser = argparse.ArgumentParser(prog="roboguide-mission")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate a MissionPlan v0 artifact")
    validate.add_argument("--input", type=Path, required=True)

    plan = subparsers.add_parser("plan", help="produce a MissionPlan v0 artifact")
    plan.add_argument("--config", type=Path, default=Path("config/mission.toml"))
    plan.add_argument("--mission-id", required=True)
    plan.add_argument("--objective", required=True)
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument("--fixture", type=Path)
    return parser


def _read_plan(path: Path) -> MissionPlan:
    """Read and validate one Mission Plan artifact from disk."""
    decoded = cast(JSONObject, json.loads(path.read_text(encoding="utf-8")))
    return MissionPlan.from_json(decoded)


def _planner(arguments: argparse.Namespace) -> MissionPlanner:
    """Create the requested planner while keeping network access behind explicit selection."""
    if arguments.fixture is not None:
        return FixturePlanner(cast(Path, arguments.fixture))
    config_path = cast(Path, arguments.config)
    settings = load_settings(config_path, repository_root=Path.cwd())
    if settings.planner != "llm":
        raise ValueError(f"unsupported configured planner: {settings.planner}")
    return ResponsesMissionPlanner(settings, current_environment())


def main() -> int:
    """Run validation or planning and return a process-compatible status code."""
    arguments = _parser().parse_args()
    if arguments.command == "validate":
        _read_plan(cast(Path, arguments.input))
        return 0
    planner = _planner(arguments)
    plan = planner.plan(cast(str, arguments.mission_id), cast(str, arguments.objective))
    output_path = cast(Path, arguments.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(plan.to_json(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0
