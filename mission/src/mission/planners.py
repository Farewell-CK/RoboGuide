"""Planner interface and deterministic fixture implementation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol, cast

from mission.intent import GroundedIntent
from mission.models import JSONObject, MissionPlan


class MissionPlanner(Protocol):
    """Produce a validated Task Graph without exposing planner implementation details."""

    def plan(self, mission_id: str, grounded_intent: GroundedIntent) -> MissionPlan:
        """Plan one resolved intent or raise a configuration, provider, or contract error."""
        ...


class FixturePlanner:
    """Load an approved deterministic Mission Plan for offline core integration."""

    def __init__(self, fixture_path: Path) -> None:
        """Bind the planner to one immutable fixture path without reading it eagerly."""
        self._fixture_path = fixture_path

    def plan(self, mission_id: str, grounded_intent: GroundedIntent) -> MissionPlan:
        """Load an objective-matched fixture when no unrepresented grounding facts exist."""
        if grounded_intent.constraints or grounded_intent.assumptions:
            raise ValueError(
                "fixture planning cannot prove nonempty constraints or assumptions are represented"
            )
        raw = cast(JSONObject, json.loads(self._fixture_path.read_text(encoding="utf-8")))
        plan = MissionPlan.from_json(raw)
        if plan.mission.mission_id != mission_id:
            raise ValueError(
                f"fixture mission {plan.mission.mission_id} does not match request {mission_id}"
            )
        if plan.mission.objective != grounded_intent.objective:
            raise ValueError("fixture objective does not match the requested objective")
        return plan
