"""Keep model Catalog violations on the bounded rejected-draft path."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from mission.capability_catalog import CapabilityCatalogError
from mission.intent import GroundedIntent
from mission.models import JSONObject, MissionPlan
from mission.rejected_draft import RejectedPlanError
from mission.request_store import MissionRequestStore
from mission.responses import ResponsesMissionPlanner, ResponsesMissionRepairer
from mission.review import MissionPlanReview
from test_planners import (
    FakeTransport,
    _current_catalog,
    _first_role,
    _grounding,
    _local_settings,
    _provider_plan,
    _response,
    _review_output,
    _v0_8_plan,
)
from test_rejected_draft_recovery import _engine


def _invalid_plan(defect: str) -> JSONObject:
    """Change one Catalog boundary while retaining a structurally valid plan."""
    plan = _v0_8_plan()
    role = _first_role(plan)
    intent = cast(JSONObject, role["execution_intent"])
    if defect == "missing_baseline":
        intent["operation"] = {"namespace": "compute", "name": "noop", "version": "v1"}
        intent["parameters"] = {}
    elif defect == "unknown_operation":
        cast(JSONObject, intent["operation"])["name"] = "unpublished-operation"
    elif defect == "unknown_capability":
        cast(JSONObject, role["requirements"])["capabilities"] = [
            {
                "contract": {"namespace": "object", "name": "unpublished", "version": "v1"},
                "constraints": [],
            }
        ]
    elif defect == "unsupported_parameter":
        cast(JSONObject, intent["parameters"])["unsupported"] = "invalid"
    else:
        raise AssertionError("unexpected test defect")
    return plan


@pytest.mark.parametrize("writer", ["planner", "repairer"])
@pytest.mark.parametrize(
    "defect",
    ["missing_baseline", "unknown_operation", "unknown_capability", "unsupported_parameter"],
)
def test_catalog_violation_preserves_rejected_draft(writer: str, defect: str) -> None:
    """Both model writers refuse invalid Catalog content and retain exact recovery evidence."""
    invalid = _invalid_plan(defect)
    catalog = _current_catalog()
    with pytest.raises(CapabilityCatalogError):
        catalog.validate_plan(MissionPlan.from_json(invalid))
    raw = _provider_plan(invalid)
    original = deepcopy(raw)
    transport = FakeTransport([_response(raw)])
    settings = _local_settings()
    mission = cast(JSONObject, invalid["mission"])
    mission_id = cast(str, mission["id"])
    intent = GroundedIntent(cast(str, mission["objective"]), (), ())
    with pytest.raises(RejectedPlanError) as caught:
        if writer == "planner":
            ResponsesMissionPlanner(settings, {"OPENAI_API_KEY": "test-only"}, transport).plan(
                mission_id, intent, catalog, _grounding()
            )
        else:
            ResponsesMissionRepairer(settings, {"OPENAI_API_KEY": "test-only"}, transport).repair(
                mission_id,
                intent,
                MissionPlan.from_json(_v0_8_plan()),
                MissionPlanReview.from_json(_review_output()),
                catalog,
                _grounding(),
            )
    error = caught.value
    assert error.stage == "plan_validation"
    assert error.provider_output == original == raw
    assert error.normalized_output == invalid
    assert error.validation_errors == (
        {
            "stage": "plan_validation",
            "code": "plan.capability_catalog_invalid",
            "path": "/",
            "message": str(error),
        },
    )
    assert len(transport.requests) == 1


@pytest.mark.parametrize("budget", [0, 2])
def test_catalog_recovery_budget_retains_every_request_draft(tmp_path: Path, budget: int) -> None:
    """The real Planner and request store exhaust the original budget without submission."""
    invalid = _invalid_plan("missing_baseline")
    mission = cast(JSONObject, invalid["mission"])
    mission.update(
        id="mission-00000000000000000000000000000002",
        objective="deliver the payload through the approved route",
    )
    raw = _provider_plan(invalid)
    transport = FakeTransport([_response(raw) for _ in range(budget + 1)])
    planner = ResponsesMissionPlanner(_local_settings(), {"OPENAI_API_KEY": "test-only"}, transport)
    engine = _engine(tmp_path, planner, budget=budget)
    record = engine.create("deliver the payload through the approved route")
    assert record.lifecycle.value == "Failed"
    assert record.plan is None
    assert len(transport.requests) == budget + 1
    assert len(record.rejected_drafts) == budget + 1
    assert all(draft.provider_output == raw for draft in record.rejected_drafts)
    restored = MissionRequestStore(tmp_path / "requests.sqlite3").get(record.request_id)
    assert restored is not None and restored.rejected_drafts == record.rejected_drafts
    first = json.loads(cast(str, transport.requests[0][2]["input"]))
    for request in transport.requests[1:]:
        payload = json.loads(cast(str, request[2]["input"]))
        feedback = payload.pop("prevalidation_recovery_feedback")
        assert feedback["previous_rejected_provider_output"] == raw
        assert feedback["validation_errors"][0]["code"] == "plan.capability_catalog_invalid"
        assert payload == first
