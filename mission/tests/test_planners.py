"""Offline tests for fixture and Responses-compatible Mission planners."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from mission.config import MissionSettings, load_settings
from mission.models import JSONObject
from mission.planners import FixturePlanner
from mission.responses import MissionProviderError, ResponsesMissionPlanner

FIXTURE = Path("scenarios/phase1-mission-v0.2/mission-plan.json")


def _response(output: JSONObject) -> JSONObject:
    """Wrap structured output in the minimal completed Responses payload shape."""
    return {
        "status": "completed",
        "error": None,
        "output": [
            {
                "type": "message",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(output, ensure_ascii=False),
                    }
                ],
            }
        ],
    }


class FakeTransport:
    """Return scripted provider responses and retain inspectable requests."""

    def __init__(self, responses: list[JSONObject]) -> None:
        """Initialize a finite response queue used without network access."""
        self.responses = responses
        self.requests: list[tuple[str, Mapping[str, str], JSONObject, float]] = []

    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: JSONObject,
        timeout_seconds: float,
    ) -> JSONObject:
        """Record a request and return the next scripted response."""
        self.requests.append((url, headers, payload, timeout_seconds))
        if not self.responses:
            raise AssertionError("fake provider response queue is empty")
        return self.responses.pop(0)


def _local_settings() -> MissionSettings:
    """Build repository settings with a safe localhost provider for offline tests."""
    settings = load_settings(Path("config/mission.toml"), repository_root=Path.cwd())
    return replace(
        settings,
        provider=replace(settings.provider, base_url="http://127.0.0.1:8080"),
    )


def test_fixture_planner_loads_the_approved_plan() -> None:
    """The deterministic planner returns the approved artifact for the exact request."""
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    mission = raw["mission"]
    plan = FixturePlanner(FIXTURE).plan(mission["id"], mission["objective"])
    assert plan.mission.mission_id == "mission-phase1-001"


def test_responses_planner_uses_strict_output_and_review() -> None:
    """The LLM planner disables storage, uses Luna, validates output, and runs review."""
    plan_json = json.loads(FIXTURE.read_text(encoding="utf-8"))
    transport = FakeTransport([_response(plan_json), _response({"approved": True, "issues": []})])
    settings = _local_settings()
    planner = ResponsesMissionPlanner(
        settings,
        {"OPENAI_API_KEY": "test-only-key"},
        transport,
    )
    mission = plan_json["mission"]

    plan = planner.plan(mission["id"], mission["objective"])

    assert plan.to_json() == plan_json
    assert len(transport.requests) == 2
    planning_payload = transport.requests[0][2]
    review_payload = transport.requests[1][2]
    assert planning_payload["model"] == "gpt-5.6-luna"
    assert review_payload["model"] == "gpt-5.6-luna"
    assert (
        planning_payload["instructions"]
        == Path("mission/prompts/v0/planner.md").read_text(encoding="utf-8").strip()
    )
    assert (
        review_payload["instructions"]
        == Path("mission/prompts/v0/reviewer.md").read_text(encoding="utf-8").strip()
    )
    assert planning_payload["store"] is False
    text_config = planning_payload["text"]
    assert isinstance(text_config, dict)
    output_format = text_config["format"]
    assert isinstance(output_format, dict)
    assert output_format["strict"] is True
    provider_schema = output_format["schema"]
    assert isinstance(provider_schema, dict)
    assert "$schema" not in provider_schema
    properties = cast(JSONObject, provider_schema["properties"])
    tasks_schema = cast(JSONObject, cast(JSONObject, properties["tasks"])["items"])
    task_properties = cast(JSONObject, tasks_schema["properties"])
    depends_on_schema = cast(JSONObject, task_properties["depends_on"])
    assert "uniqueItems" not in depends_on_schema


def test_responses_planner_rejects_failed_review() -> None:
    """A structurally valid plan cannot pass when the configured reviewer rejects it."""
    plan_json = json.loads(FIXTURE.read_text(encoding="utf-8"))
    transport = FakeTransport(
        [_response(plan_json), _response({"approved": False, "issues": ["selects a node"]})]
    )
    settings = _local_settings()
    planner = ResponsesMissionPlanner(
        settings,
        {"OPENAI_API_KEY": "test-only-key"},
        transport,
    )
    mission = plan_json["mission"]
    with pytest.raises(MissionProviderError, match="review rejected"):
        planner.plan(mission["id"], mission["objective"])
