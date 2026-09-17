"""Contract tests for canonical MissionPlan and strict provider DTO adaptation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from mission.contract_values import JSONObject, JSONValue, MissionPlanError
from mission.models import MissionPlan
from mission.provider_mission_plan import (
    build_mission_plan_provider_schema,
    normalize_mission_plan_provider_output,
)

FIXTURE = Path("scenarios/mission-front-half-v0.7/mission-plan.json")
SCHEMA = Path("contracts/mission/v0.8/mission-plan.schema.json")


def _canonical_schema() -> JSONObject:
    """Load the canonical v0.8 schema used by both provider directions."""
    return cast(JSONObject, json.loads(SCHEMA.read_text(encoding="utf-8")))


def _provider_plan() -> JSONObject:
    """Build one v0.8 provider DTO value from the current canonical fixture."""
    plan = cast(JSONObject, json.loads(FIXTURE.read_text(encoding="utf-8")))
    plan["schema_version"] = "roboguide.mission-plan/v0.8"
    for context in cast(list[JSONObject], plan["contexts"]):
        context["executor_constraints"] = []
    for task in cast(list[JSONObject], plan["tasks"]):
        for role in cast(list[JSONObject], task["roles"]):
            intent = cast(JSONObject, role["execution_intent"])
            parameters = cast(JSONObject, intent["parameters"])
            intent["parameters"] = [
                {"key": key, "value": parameters[key]} for key in sorted(parameters)
            ]
    return plan


def _allows_null(schema: JSONValue) -> bool:
    """Recognize explicit null support in the projected test schema."""
    if not isinstance(schema, dict):
        return False
    type_value = schema.get("type")
    if type_value == "null" or (isinstance(type_value, list) and "null" in type_value):
        return True
    alternatives = schema.get("anyOf")
    return isinstance(alternatives, list) and any(_allows_null(item) for item in alternatives)


def _audit_optional_properties(
    canonical: JSONValue,
    provider: JSONValue,
    path: str = "$",
) -> tuple[set[str], set[str]]:
    """Audit strict nullable projection and classify omission versus canonical-null paths."""
    omission_paths: set[str] = set()
    canonical_null_paths: set[str] = set()
    if isinstance(canonical, list) and isinstance(provider, list):
        for index, (canonical_item, provider_item) in enumerate(
            zip(canonical, provider, strict=True)
        ):
            nested_omissions, nested_nulls = _audit_optional_properties(
                canonical_item, provider_item, f"{path}[{index}]"
            )
            omission_paths.update(nested_omissions)
            canonical_null_paths.update(nested_nulls)
        return omission_paths, canonical_null_paths
    if not isinstance(canonical, dict) or not isinstance(provider, dict):
        return omission_paths, canonical_null_paths

    canonical_properties = canonical.get("properties")
    provider_properties = provider.get("properties")
    if isinstance(canonical_properties, dict) and isinstance(provider_properties, dict):
        canonical_required_value = canonical.get("required", [])
        canonical_required = (
            {item for item in canonical_required_value if isinstance(item, str)}
            if isinstance(canonical_required_value, list)
            else set()
        )
        provider_required = provider.get("required")
        assert isinstance(provider_required, list)
        assert set(provider_required) == set(provider_properties)
        for name, canonical_property in canonical_properties.items():
            provider_property = provider_properties[name]
            property_path = f"{path}.properties.{name}"
            if name not in canonical_required:
                assert _allows_null(provider_property), property_path
                target = (
                    canonical_null_paths if _allows_null(canonical_property) else omission_paths
                )
                target.add(property_path)
            nested_omissions, nested_nulls = _audit_optional_properties(
                canonical_property, provider_property, property_path
            )
            omission_paths.update(nested_omissions)
            canonical_null_paths.update(nested_nulls)

    canonical_definitions = canonical.get("$defs")
    provider_definitions = provider.get("$defs")
    if isinstance(canonical_definitions, dict) and isinstance(provider_definitions, dict):
        assert set(provider_definitions) == set(canonical_definitions)
        for name, canonical_definition in canonical_definitions.items():
            nested_omissions, nested_nulls = _audit_optional_properties(
                canonical_definition,
                provider_definitions[name],
                f"{path}.$defs.{name}",
            )
            omission_paths.update(nested_omissions)
            canonical_null_paths.update(nested_nulls)

    for keyword in ("items", "anyOf", "oneOf"):
        canonical_child = canonical.get(keyword)
        provider_child = provider.get(keyword)
        if canonical_child is None or provider_child is None:
            continue
        nested_omissions, nested_nulls = _audit_optional_properties(
            canonical_child, provider_child, f"{path}.{keyword}"
        )
        omission_paths.update(nested_omissions)
        canonical_null_paths.update(nested_nulls)
    return omission_paths, canonical_null_paths


def test_provider_schema_audits_every_canonical_optional_property() -> None:
    """Every strict nullable optional field has omission or canonical-null semantics."""
    canonical = _canonical_schema()
    provider = build_mission_plan_provider_schema(canonical)

    omission_paths, canonical_null_paths = _audit_optional_properties(canonical, provider)

    assert omission_paths == {
        "$.properties.mission.properties.actors.items.properties.physical_entity",
        "$.$defs.relation.properties.state_key",
        "$.$defs.relation.properties.reference",
        "$.$defs.relation.properties.frame_id",
        "$.$defs.relation.properties.requirement",
        "$.$defs.relation.properties.policy_id",
        "$.$defs.shared_view.properties.bindings.items.properties.state_export_id",
        "$.$defs.shared_view.properties.bindings.items.properties.payload_schema",
        "$.$defs.context.properties.coupling_mode",
        "$.$defs.context.properties.shared_view",
        "$.$defs.context.properties.peer_channel",
        "$.$defs.task.properties.coupling_mode",
    }
    assert canonical_null_paths == {"$.$defs.shared_view.properties.spatial_reference"}


def test_provider_null_for_optional_fields_normalizes_to_canonical_omission() -> None:
    """Provider null sentinels become omission at root, nested, and object properties."""
    provider = _provider_plan()
    mission = cast(JSONObject, provider["mission"])
    actor = cast(list[JSONObject], mission["actors"])[0]
    actor["physical_entity"] = None
    context = cast(list[JSONObject], provider["contexts"])[0]
    context["coupling_mode"] = None
    context["peer_channel"] = None
    context["shared_view"] = None
    task = cast(list[JSONObject], provider["tasks"])[0]
    task["coupling_mode"] = None

    normalized = normalize_mission_plan_provider_output(provider, _canonical_schema())

    normalized_mission = cast(JSONObject, normalized["mission"])
    normalized_actor = cast(list[JSONObject], normalized_mission["actors"])[0]
    normalized_context = cast(list[JSONObject], normalized["contexts"])[0]
    normalized_task = cast(list[JSONObject], normalized["tasks"])[0]
    assert "physical_entity" not in normalized_actor
    assert "coupling_mode" not in normalized_context
    assert "peer_channel" not in normalized_context
    assert "shared_view" not in normalized_context
    assert "coupling_mode" not in normalized_task
    MissionPlan.from_json(normalized)


def test_provider_optional_value_is_preserved() -> None:
    """A non-null canonical optional physical entity survives provider normalization."""
    provider = _provider_plan()
    mission = cast(JSONObject, provider["mission"])
    actor = cast(list[JSONObject], mission["actors"])[0]
    actor["physical_entity"] = "physical.robot-1"

    normalized = normalize_mission_plan_provider_output(provider, _canonical_schema())

    normalized_mission = cast(JSONObject, normalized["mission"])
    normalized_actor = cast(list[JSONObject], normalized_mission["actors"])[0]
    assert normalized_actor["physical_entity"] == "physical.robot-1"
    assert MissionPlan.from_json(normalized).mission.actors[0].physical_entity == "physical.robot-1"


def test_canonical_required_nullable_value_is_preserved() -> None:
    """A required timing null remains a canonical semantic value after normalization."""
    provider = _provider_plan()
    task = cast(list[JSONObject], provider["tasks"])[0]
    timing = cast(JSONObject, task["timing"])
    timing["latest_start_offset_ms"] = None

    normalized = normalize_mission_plan_provider_output(provider, _canonical_schema())

    normalized_task = cast(list[JSONObject], normalized["tasks"])[0]
    normalized_timing = cast(JSONObject, normalized_task["timing"])
    assert "latest_start_offset_ms" in normalized_timing
    assert normalized_timing["latest_start_offset_ms"] is None
    MissionPlan.from_json(normalized)


def test_nested_optional_null_is_omitted_but_canonical_optional_null_is_preserved() -> None:
    """Nested DTO sentinels are removed while explicit canonical-null semantics survive."""
    provider = _provider_plan()
    context = cast(list[JSONObject], provider["contexts"])[0]
    context_role = cast(list[JSONObject], context["roles"])[0]
    context["shared_view"] = {
        "bindings": [
            {
                "context_role_id": context_role["id"],
                "field": "execution",
                "state_export_id": None,
                "payload_schema": None,
            }
        ],
        "include_freshness": False,
        "spatial_reference": None,
    }

    normalized = normalize_mission_plan_provider_output(provider, _canonical_schema())

    normalized_context = cast(list[JSONObject], normalized["contexts"])[0]
    shared_view = cast(JSONObject, normalized_context["shared_view"])
    binding = cast(list[JSONObject], shared_view["bindings"])[0]
    assert "state_export_id" not in binding
    assert "payload_schema" not in binding
    assert "spatial_reference" in shared_view
    assert shared_view["spatial_reference"] is None
    MissionPlan.from_json(normalized)


@pytest.mark.parametrize("physical_entity", ["", "   ", {"id": "physical.robot-1"}])
def test_malformed_non_null_optional_value_still_fails_canonical_validation(
    physical_entity: JSONValue,
) -> None:
    """Normalization never erases malformed non-null canonical optional values."""
    provider = _provider_plan()
    mission = cast(JSONObject, provider["mission"])
    actor = cast(list[JSONObject], mission["actors"])[0]
    actor["physical_entity"] = physical_entity

    normalized = normalize_mission_plan_provider_output(provider, _canonical_schema())

    with pytest.raises(MissionPlanError, match="physical_entity must be nonblank text"):
        MissionPlan.from_json(normalized)
