"""Exact quantitative MissionPlan contracts; fixtures create no deployment or device evidence."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from mission.contract_values import JSONObject, JSONValue
from mission.models import MissionPlan, MissionPlanError
from mission.provider_mission_plan import (
    ProviderMissionPlanError,
    build_mission_plan_provider_schema,
    normalize_mission_plan_provider_output,
)


def plan() -> JSONObject:
    """Extend arbitrary semantic scenario input with an explicit original Context envelope."""
    value = cast(
        JSONObject,
        json.loads(Path("scenarios/mission-front-half-v0.7/mission-plan.json").read_bytes()),
    )
    value["schema_version"] = "roboguide.mission-plan/v0.10"
    contexts = cast(list[JSONObject], value["contexts"])
    for context in contexts:
        context["executor_constraints"] = []
    task = cast(list[JSONObject], value["tasks"])[0]
    role = cast(list[JSONObject], task["roles"])[0]
    role["resource_scope"] = "context"
    demand: JSONObject = {
        "kind": "compute",
        "units": 2,
        "allocation_mode": "divisible",
        "resource_class": "unit-test.compute/v1",
    }
    cast(JSONObject, role["requirements"])["resources"] = [demand]
    for context in contexts:
        if context["id"] == task["context_id"]:
            for declared in cast(list[JSONObject], context["roles"]):
                if declared["id"] == role["context_role"]:
                    declared["resource_envelope"] = [dict(demand, units=3)]
    return value


def test_quantitative_context_roundtrip_preserves_memory_and_original_units() -> None:
    """Both canonical schema and independent domain parser retain complete typed declarations."""
    value = plan()
    schema = json.loads(Path("contracts/mission/v0.10/mission-plan.schema.json").read_bytes())
    Draft202012Validator(schema).validate(value)
    parsed = MissionPlan.from_json(value)
    roundtrip = parsed.to_json()
    selected = cast(list[JSONObject], cast(list[JSONObject], roundtrip["tasks"])[0]["roles"])[0]
    resources = cast(JSONObject, selected["requirements"])["resources"]
    original = cast(list[JSONObject], cast(list[JSONObject], value["tasks"])[0]["roles"])[0]
    assert resources == cast(JSONObject, original["requirements"])["resources"]
    assert parsed.schema_version == "roboguide.mission-plan/v0.10"
    assert parsed.contexts[0].roles[0].resource_envelope


@pytest.mark.parametrize("version", ["v0.8", "v0.9"])
def test_historical_contracts_refuse_new_quantitative_fields(version: str) -> None:
    """A changed outer label cannot hide new semantics inside an older contract."""
    value = plan()
    value["schema_version"] = f"roboguide.mission-plan/{version}"
    with pytest.raises(MissionPlanError, match="unknown"):
        MissionPlan.from_json(value)


@pytest.mark.parametrize("units", [0, 4, True])
def test_context_borrower_cannot_exceed_original_envelope(units: JSONValue) -> None:
    """Invalid borrower units fail before any model, Control or provider work."""
    value = plan()
    role = cast(list[JSONObject], cast(list[JSONObject], value["tasks"])[0]["roles"])[0]
    cast(list[JSONObject], cast(JSONObject, role["requirements"])["resources"])[0]["units"] = units
    with pytest.raises(MissionPlanError):
        MissionPlan.from_json(value)


def test_capacity_provider_requires_exact_selected_contract_and_preserves_envelope() -> None:
    """Provider adaptation retains units and refuses v0.9 through a selected v0.10 schema."""
    value = plan()
    schema = cast(
        JSONObject,
        json.loads(Path("contracts/mission/v0.10/mission-plan.schema.json").read_bytes()),
    )
    mission = cast(JSONObject, value["mission"])
    projected = build_mission_plan_provider_schema(schema, mission_id=cast(str, mission["id"]))
    definitions = cast(JSONObject, projected["$defs"])
    context = cast(JSONObject, definitions["context"])
    roles = cast(JSONObject, cast(JSONObject, context["properties"])["roles"])
    context_role = cast(JSONObject, roles["items"])
    envelope = cast(JSONObject, cast(JSONObject, context_role["properties"])["resource_envelope"])
    branches = cast(list[JSONObject], envelope["anyOf"])
    item_schema = cast(JSONObject, branches[0]["items"])
    assert item_schema["type"] == "object"
    assert set(cast(list[str], item_schema["required"])) == {
        "kind",
        "units",
        "allocation_mode",
        "resource_class",
    }
    provider = deepcopy(value)
    for task in cast(list[JSONObject], provider["tasks"]):
        for role in cast(list[JSONObject], task["roles"]):
            intent = cast(JSONObject, role["execution_intent"])
            parameters = cast(JSONObject, intent["parameters"])
            intent["parameters"] = [{"key": key, "value": item} for key, item in parameters.items()]
    normalized = normalize_mission_plan_provider_output(provider, schema)
    assert normalized == value
    provider["schema_version"] = "roboguide.mission-plan/v0.9"
    with pytest.raises(ProviderMissionPlanError):
        normalize_mission_plan_provider_output(provider, schema)
