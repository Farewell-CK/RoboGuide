"""Expose semantic validation limits and test review evidence delivery without a model."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import cast

import pytest
from mission.intent import GroundedIntent
from mission.models import JSONObject
from mission.responses import (
    ResponsesMissionPlanner,
    ResponsesMissionRepairer,
    ResponsesMissionReviewer,
)
from mission.review import MissionReviewRoute, route_mission_review
from test_coordination_guidance import _context, _coordination_plan, _intent
from test_planners import (
    FakeTransport,
    _current_catalog,
    _grounding,
    _local_settings,
    _provider_plan,
    _response,
)


@pytest.mark.parametrize("required", [False, True])
def test_relation_necessity_reaches_review_without_automatic_mode_rewrite(required: bool) -> None:
    """Script both review outcomes; validate input fidelity, not a model's semantic judgement."""
    raw = _coordination_plan(cooperative=required)
    # Identical cooperation mechanisms can be justified or unjustified by the supplied objective.
    raw["contexts"] = _coordination_plan()["contexts"]
    original = deepcopy(raw)
    issue: JSONObject = {
        "code": "coordination.unsupported_relation",
        "path": "/contexts/0/relations/0",
        "message": (
            "The objective requests observation and inference outcomes, not an observer that "
            "must remain active throughout inference. This relation adds that restriction."
        ),
        "required_action": "RepairPlan",
    }
    review_output: JSONObject = {
        "approved": required,
        "issues": [] if required else [issue],
    }
    expected = _coordination_plan(cooperative=required)
    responses = [_response(_provider_plan(raw)), _response(review_output)]
    if not required:
        responses.append(_response(_provider_plan(expected)))
    transport = FakeTransport(responses)
    settings, catalog, grounding = _local_settings(), _current_catalog(), _grounding()
    environment = {"OPENAI_API_KEY": "test-only-key"}
    intent = _intent(raw)
    plan = ResponsesMissionPlanner(settings, environment, transport).plan(
        "mission-inspection", intent, catalog, grounding
    )
    # Deterministic validation accepts both: do not describe a Prompt change as a new gate.
    assert plan.to_json() == original
    review = ResponsesMissionReviewer(settings, environment, transport).review(
        intent, plan, catalog, grounding
    )
    assert review.to_json() == review_output
    assert route_mission_review(review) == (
        MissionReviewRoute.APPROVED if required else MissionReviewRoute.REPAIR
    )
    if not required:
        repaired = ResponsesMissionRepairer(settings, environment, transport).repair(
            "mission-inspection", intent, plan, review, catalog, grounding
        )
        assert repaired.to_json() == expected
        assert repaired.to_json()["tasks"] == original["tasks"]
        repair_input = json.loads(cast(str, transport.requests[2][2]["input"]))
        assert repair_input["rejected_plan"] == original
        assert repair_input["review"] == review_output
    review_input = json.loads(cast(str, transport.requests[1][2]["input"]))
    assert review_input["mission_plan"] == original
    for request in transport.requests:
        model_input = json.loads(cast(str, request[2]["input"]))
        assert model_input["grounded_intent"] == intent.to_json()
        assert model_input["grounding_context"] == grounding.to_json()
    assert plan.to_json() == original and raw == original
    assert len(transport.requests) == (2 if required else 3)


@pytest.mark.parametrize("substitute", ["independent", "execution-only", "invented-export"])
def test_missing_pose_contract_remains_visible_to_semantic_review(substitute: str) -> None:
    """Syntactically valid evasions retain the pose requirement and a scripted RejectDraft gap."""
    raw = _coordination_plan(cooperative=substitute != "independent")
    intent = GroundedIntent(
        _intent(raw).objective,
        ("Inference must consume the safety observer's current pose throughout execution.",),
        (),
    )
    if substitute == "invented-export":
        cast(JSONObject, _context(raw)["shared_view"])["bindings"] = [
            {
                "context_role_id": "watch",
                "field": "pose",
                "state_export_id": "not-supplied-export",
                "payload_schema": "not.supplied/v1",
            }
        ]
    expected_review: JSONObject = {
        "approved": False,
        "issues": [
            {
                "code": "coordination.state_contract_missing",
                "path": "/contexts/0",
                "message": (
                    "The confirmed constraint requires current observer pose, but the frozen "
                    "input supplies no State export/schema for it. Execution status or guessed "
                    "identifiers cannot provide this observation."
                ),
                "required_action": "RejectDraft",
            }
        ],
    }
    transport = FakeTransport([_response(_provider_plan(raw)), _response(expected_review)])
    settings, grounding, catalog = _local_settings(), _grounding(), _current_catalog()
    environment = {"OPENAI_API_KEY": "test-only-key"}
    plan = ResponsesMissionPlanner(settings, environment, transport).plan(
        "mission-inspection", intent, catalog, grounding
    )
    # Current structural validation cannot prove a natural-language constraint or export source.
    assert plan.to_json() == raw
    review = ResponsesMissionReviewer(settings, environment, transport).review(
        intent, plan, catalog, grounding
    )
    assert review.to_json() == expected_review
    assert route_mission_review(review) is MissionReviewRoute.REJECTED
    payload = transport.requests[1][2]
    review_input = json.loads(cast(str, payload["input"]))
    assert review_input["mission_plan"] == raw
    assert review_input["grounded_intent"] == intent.to_json()
    assert review_input["grounding_context"] == grounding.to_json()
    rules = " ".join(cast(str, payload["instructions"]).split())
    assert (
        "must not hide a required execution dependency or required pose/velocity sharing" in rules
    )
    assert "use `RejectDraft`: this is a deployment contract gap" in rules
    assert "Missing live providers remain Control's concern" in rules
