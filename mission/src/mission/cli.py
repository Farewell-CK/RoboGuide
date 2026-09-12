"""Command-line boundary for planning and validating Mission Plan artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import cast

from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.config import MissionSettings, current_environment, load_settings
from mission.grounding_context import GroundingContextSnapshot
from mission.grounding_reader import EmptyMissionGroundingReader
from mission.intent import GroundedIntent
from mission.models import JSONObject, MissionPlan
from mission.planners import FixturePlanner, MissionPlanner
from mission.request_record import DialogueSpeaker, DialogueTurn, DialogueTurnKind
from mission.responses import (
    ResponsesMissionPlanner,
    ResponsesMissionRepairer,
    ResponsesMissionReviewer,
)
from mission.review import MissionReviewError, MissionReviewRoute, route_mission_review


def _parser() -> argparse.ArgumentParser:
    """Build the CLI parser without reading configuration or touching the network."""
    parser = argparse.ArgumentParser(prog="roboguide-mission")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate a MissionPlan v0 artifact")
    validate.add_argument("--input", type=Path, required=True)
    validate.add_argument(
        "--catalog",
        type=Path,
        default=Path("contracts/capability/v0.3/catalog.json"),
    )

    plan = subparsers.add_parser("plan", help="produce a MissionPlan v0 artifact")
    plan.add_argument("--config", type=Path, default=Path("config/mission.toml"))
    plan.add_argument("--mission-id", required=True)
    plan.add_argument("--objective", required=True)
    plan.add_argument("--constraint", action="append", default=[])
    plan.add_argument("--assumption", action="append", default=[])
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument("--fixture", type=Path)
    return parser


def _read_plan(path: Path, capability_catalog: CanonicalCapabilityCatalog) -> MissionPlan:
    """Read and validate one Mission Plan against syntax and canonical vocabulary."""
    decoded = cast(JSONObject, json.loads(path.read_text(encoding="utf-8")))
    plan = MissionPlan.from_json(decoded)
    capability_catalog.validate_plan(plan)
    return plan


def _planner(arguments: argparse.Namespace, settings: MissionSettings) -> MissionPlanner:
    """Create the requested planner while keeping network access behind explicit selection."""
    if arguments.fixture is not None:
        return FixturePlanner(cast(Path, arguments.fixture))
    if settings.planner != "llm":
        raise ValueError(f"unsupported configured planner: {settings.planner}")
    return ResponsesMissionPlanner(settings, current_environment())


def _review_and_repair_plan(
    settings: MissionSettings,
    mission_id: str,
    grounded_intent: GroundedIntent,
    capability_catalog: CanonicalCapabilityCatalog,
    plan: MissionPlan,
    grounding_context: GroundingContextSnapshot,
) -> MissionPlan:
    """Apply the same bounded semantic review policy for direct LLM CLI planning."""
    if not settings.review_enabled:
        return plan
    environment = current_environment()
    reviewer = ResponsesMissionReviewer(settings, environment)
    repairer = ResponsesMissionRepairer(settings, environment)
    repairs = 0
    while True:
        review = reviewer.review(grounded_intent, plan, capability_catalog, grounding_context)
        route = route_mission_review(review)
        if route is MissionReviewRoute.APPROVED:
            return plan
        messages = [issue.message for issue in review.issues]
        if route is MissionReviewRoute.CLARIFICATION:
            raise MissionReviewError(f"mission review requires clarification: {messages}")
        if route is MissionReviewRoute.REJECTED:
            raise MissionReviewError(f"mission review rejected automatic repair: {messages}")
        if repairs >= settings.max_repair_attempts:
            raise MissionReviewError(f"mission review repair attempts exhausted: {messages}")
        plan = repairer.repair(
            mission_id,
            grounded_intent,
            plan,
            review,
            capability_catalog,
            grounding_context,
        )
        repairs += 1


def main() -> int:
    """Run validation or planning and return a process-compatible status code."""
    arguments = _parser().parse_args()
    if arguments.command == "validate":
        capability_catalog = CanonicalCapabilityCatalog.load(cast(Path, arguments.catalog))
        _read_plan(cast(Path, arguments.input), capability_catalog)
        return 0
    settings = load_settings(cast(Path, arguments.config), repository_root=Path.cwd())
    capability_catalog = CanonicalCapabilityCatalog.load(settings.capability_catalog_path)
    planner = _planner(arguments, settings)
    grounded_intent = GroundedIntent(
        cast(str, arguments.objective),
        tuple(cast(list[str], arguments.constraint)),
        tuple(cast(list[str], arguments.assumption)),
    )
    mission_id = cast(str, arguments.mission_id)
    dialogue = (
        DialogueTurn(
            "turn-cli-0001",
            DialogueSpeaker.USER,
            DialogueTurnKind.INSTRUCTION,
            grounded_intent.objective,
            0,
        ),
    )
    grounding_context = EmptyMissionGroundingReader().capture(
        f"request-cli-{mission_id}", dialogue, 0
    )
    plan = planner.plan(
        mission_id=mission_id,
        grounded_intent=grounded_intent,
        capability_catalog=capability_catalog,
        grounding_context=grounding_context,
    )
    if arguments.fixture is None:
        plan = _review_and_repair_plan(
            settings,
            mission_id,
            grounded_intent,
            capability_catalog,
            plan,
            grounding_context,
        )
    output_path = cast(Path, arguments.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(plan.to_json(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0
