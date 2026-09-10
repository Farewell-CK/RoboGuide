"""Offline tests for fixture and Responses-compatible Mission planners."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from mission.capability_catalog import CanonicalCapabilityCatalog, CapabilityCatalogError
from mission.config import MissionSettings, load_settings
from mission.intent import GroundedIntent
from mission.models import JSONObject
from mission.planners import FixturePlanner
from mission.responses import (
    MissionProviderError,
    ResponsesMissionInterpreter,
    ResponsesMissionPlanner,
)

FIXTURE = Path("scenarios/phase1-mission-v0.3/mission-plan.json")
CATALOG = Path("contracts/capability/v0.1/catalog.json")


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


def _catalog() -> CanonicalCapabilityCatalog:
    """Load the checked-in semantic vocabulary used by deterministic planner tests."""
    return CanonicalCapabilityCatalog.load(CATALOG)


def test_fixture_planner_loads_the_approved_plan() -> None:
    """The deterministic planner returns the approved artifact for the exact request."""
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    mission = raw["mission"]
    plan = FixturePlanner(FIXTURE).plan(
        mission["id"], GroundedIntent(mission["objective"], (), ()), _catalog()
    )
    assert plan.mission.mission_id == "mission-phase1-001"


def test_fixture_planner_rejects_unrepresented_grounding_facts() -> None:
    """A fixture cannot silently claim that unknown constraints are encoded in its plan."""
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    mission = raw["mission"]
    with pytest.raises(ValueError, match="cannot prove"):
        FixturePlanner(FIXTURE).plan(
            mission["id"],
            GroundedIntent(mission["objective"], ("keep the marked aisle clear",), ()),
            _catalog(),
        )


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
    grounded_intent = GroundedIntent(
        mission["objective"],
        ("keep the marked aisle clear",),
        ("the payload remains available at the pickup point",),
    )

    capability_catalog = _catalog()
    plan = planner.plan(mission["id"], grounded_intent, capability_catalog)

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
    planning_input = json.loads(cast(str, planning_payload["input"]))
    assert planning_input == {
        "mission_id": mission["id"],
        "grounded_intent": grounded_intent.to_json(),
        "capability_catalog": capability_catalog.to_json(),
    }
    review_input = json.loads(cast(str, review_payload["input"]))
    assert review_input == {
        "grounded_intent": grounded_intent.to_json(),
        "mission_plan": plan_json,
        "capability_catalog": capability_catalog.to_json(),
    }
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
    contexts_schema = cast(JSONObject, cast(JSONObject, properties["contexts"])["items"])
    context_properties = cast(JSONObject, contexts_schema["properties"])
    assert set(cast(list[str], contexts_schema["required"])) == set(context_properties)
    shared_view_schema = cast(JSONObject, context_properties["shared_view"])
    assert {"type": "null"} in cast(list[JSONObject], shared_view_schema["anyOf"])
    definitions = cast(JSONObject, provider_schema["$defs"])
    relation_schema = cast(JSONObject, definitions["relation"])
    relation_properties = cast(JSONObject, relation_schema["properties"])
    assert "allOf" not in relation_schema
    assert set(cast(list[str], relation_schema["required"])) == set(relation_properties)
    state_key_schema = cast(JSONObject, relation_properties["state_key"])
    assert {"type": "null"} in cast(list[JSONObject], state_key_schema["anyOf"])


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
        planner.plan(
            mission["id"],
            GroundedIntent(mission["objective"], ("avoid stairs",), ()),
            _catalog(),
        )


def test_responses_planner_rejects_unknown_contract_before_review() -> None:
    """Deterministic Catalog admission prevents an invented contract reaching Reviewer."""
    plan_json = json.loads(FIXTURE.read_text(encoding="utf-8"))
    role = plan_json["tasks"][0]["roles"][0]
    invented = {"namespace": "delivery", "name": "magic_move", "version": "v1"}
    role["contract"] = invented
    role["execution"]["capability_contract"] = invented
    transport = FakeTransport([_response(plan_json)])
    planner = ResponsesMissionPlanner(
        _local_settings(),
        {"OPENAI_API_KEY": "test-only-key"},
        transport,
    )

    with pytest.raises(CapabilityCatalogError, match="delivery.magic_move@v1"):
        planner.plan(
            plan_json["mission"]["id"],
            GroundedIntent(plan_json["mission"]["objective"], (), ()),
            _catalog(),
        )

    assert len(transport.requests) == 1


def test_responses_interpreter_preserves_open_questions_before_planning() -> None:
    """Structured interpretation exposes ambiguity without inventing a Task Graph."""
    output: JSONObject = {
        "objective": "让一只可建图的机器狗建立地图",
        "constraints": [],
        "assumptions": [],
        "open_questions": ["需要建立哪个区域的地图？"],
    }
    transport = FakeTransport([_response(output)])
    interpreter = ResponsesMissionInterpreter(
        _local_settings(), {"OPENAI_API_KEY": "test-only-key"}, transport
    )
    assessment = interpreter.interpret("一只可建图的机器狗", ())

    assert assessment.open_questions == ("需要建立哪个区域的地图？",)
    assert len(transport.requests) == 1
    request_input = json.loads(cast(str, transport.requests[0][2]["input"]))
    assert request_input == {"instruction": "一只可建图的机器狗", "messages": []}
