"""Contract tests for canonical MissionPlan and strict provider DTO adaptation."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from mission.contract_values import JSONObject, JSONValue, MissionPlanError
from mission.models import MissionPlan
from mission.provider_mission_plan import (
    HOST_OBJECTIVE_PROFILE,
    ProviderMissionPlanError,
    build_mission_plan_provider_schema,
    normalize_mission_plan_provider_output,
)
from mission.submission_evidence import canonical_plan_digest

FIXTURE = Path("scenarios/mission-front-half-v0.7/mission-plan.json")
SCHEMA = Path("contracts/mission/v0.8/mission-plan.schema.json")


def test_explicit_memory_provider_dto_retains_original_semantic_requirements() -> None:
    """Round-trip nonempty prerequisites and audit every nullable projection under explicit v0.9."""
    canonical = cast(
        JSONObject, json.loads(Path("contracts/mission/v0.9/mission-plan.schema.json").read_bytes())
    )
    provider_schema = build_mission_plan_provider_schema(canonical, mission_id="memory-mission")
    _audit_optional_properties(canonical, provider_schema)
    provider = _provider_plan()
    provider["schema_version"] = "roboguide.mission-plan/v0.9"
    requirement: JSONObject = {
        "selector": {"memory_id": "original-memory", "revision_id": "original-revision"},
        "kind": "artifact",
        "payload_schema": "actual-file/v1",
        "media_type": "application/octet-stream",
    }
    for index, task in enumerate(cast(list[JSONObject], provider["tasks"])):
        for role in cast(list[JSONObject], task["roles"]):
            cast(JSONObject, role["requirements"])["memories"] = (
                [requirement] if index == 0 else None
            )
    normalized = normalize_mission_plan_provider_output(provider, canonical)
    parsed = MissionPlan.from_json(normalized)
    tasks = cast(list[JSONObject], parsed.to_json()["tasks"])
    first = cast(list[JSONObject], tasks[0]["roles"])[0]
    assert cast(JSONObject, first["requirements"])["memories"] == [requirement]
    assert canonical["properties"] != provider_schema["properties"]


@pytest.mark.parametrize(
    "canonical_version,output_version",
    [
        ("v0.8", "v0.9"),
        ("v0.9", "v0.8"),
        ("v0.9", "v0.7"),
        ("v0.9", "v0.10"),
    ],
)
def test_provider_output_cannot_cross_the_selected_memory_contract(
    canonical_version: str, output_version: str
) -> None:
    """A different provider version cannot discard or smuggle Memory prerequisites."""
    canonical = cast(
        JSONObject,
        json.loads(
            (
                Path("contracts/mission") / canonical_version / "mission-plan.schema.json"
            ).read_bytes()
        ),
    )
    provider = _provider_plan()
    provider["schema_version"] = "roboguide.mission-plan/" + output_version
    with pytest.raises(ProviderMissionPlanError, match="provider MissionPlan output must use"):
        normalize_mission_plan_provider_output(provider, canonical)


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
    provider = build_mission_plan_provider_schema(canonical, mission_id="mission-audit")

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


def test_provider_schema_binds_only_the_requested_mission_identity() -> None:
    """Each call fences its own ID without mutating the shared canonical schema."""
    canonical = _canonical_schema()
    original = json.dumps(canonical, sort_keys=True)
    first = build_mission_plan_provider_schema(canonical, mission_id="mission-one")
    second = build_mission_plan_provider_schema(canonical, mission_id="mission-two")

    first_id = cast(JSONObject, cast(JSONObject, first["properties"])["mission"])["properties"]
    second_id = cast(JSONObject, cast(JSONObject, second["properties"])["mission"])["properties"]
    assert cast(JSONObject, first_id)["id"] == {"type": "string", "enum": ["mission-one"]}
    assert cast(JSONObject, second_id)["id"] == {"type": "string", "enum": ["mission-two"]}
    assert json.dumps(canonical, sort_keys=True) == original


def test_provider_schema_rejects_blank_or_unexpected_identity_contract() -> None:
    """A malformed local schema cannot silently lose request-specific fencing."""
    from mission.provider_mission_plan import ProviderMissionPlanError

    with pytest.raises(ProviderMissionPlanError, match="nonblank"):
        build_mission_plan_provider_schema(_canonical_schema(), mission_id="")
    canonical = _canonical_schema()
    mission = cast(JSONObject, cast(JSONObject, canonical["properties"])["mission"])
    properties = cast(JSONObject, mission["properties"])
    properties["id"] = {"type": "string"}
    with pytest.raises(ProviderMissionPlanError, match="unexpected"):
        build_mission_plan_provider_schema(canonical, mission_id="mission-one")


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


def test_static_provider_schema_mode_stays_invariant_across_mission_ids() -> None:
    """Unbound Mission ID keeps the strict provider schema identical across Missions."""
    canonical = _canonical_schema()
    first = build_mission_plan_provider_schema(
        canonical, mission_id="mission-one", bind_mission_id=False
    )
    second = build_mission_plan_provider_schema(
        canonical, mission_id="mission-two", bind_mission_id=False
    )
    assert first == second
    id_schema = cast(
        JSONObject,
        cast(
            JSONObject,
            cast(JSONObject, cast(JSONObject, first["properties"])["mission"])["properties"],
        )["id"],
    )
    assert id_schema == {"$ref": "#/$defs/id"}


def _compact_fixture() -> tuple[JSONObject, JSONObject, JSONObject, str]:
    """Build an equivalent four-task strict DTO pair with explicit safety constraints."""
    canonical = cast(
        JSONObject,
        json.loads(Path("contracts/mission/v0.10/mission-plan.schema.json").read_bytes()),
    )
    legacy = _provider_plan()
    legacy["schema_version"] = "roboguide.mission-plan/v0.10"
    legacy["provider_profile"] = HOST_OBJECTIVE_PROFILE
    objective = cast(str, cast(JSONObject, legacy["mission"]).pop("objective"))
    tasks = cast(list[JSONObject], legacy["tasks"])
    for index in range(len(tasks), 4):
        task = deepcopy(tasks[-1])
        task["depends_on"] = [tasks[-1]["id"]]
        task["id"] = f"added-task-{index}"
        tasks.append(task)
    for task in tasks:
        task["timing"] = {
            "earliest_start_offset_ms": 0,
            "latest_start_offset_ms": None,
            "completion_deadline_offset_ms": None,
        }
        task["satisfaction"] = {
            "expected_effect": "Task reports the requested effect",
            "basis": "execution-report",
            "verifier": None,
        }
        for role in cast(list[JSONObject], task["roles"]):
            cast(JSONObject, role["requirements"])["memories"] = None

    tasks[-1]["timing"] = {
        "earliest_start_offset_ms": 2,
        "latest_start_offset_ms": 40,
        "completion_deadline_offset_ms": 60,
    }
    tasks[-1]["satisfaction"] = {
        "expected_effect": "Independent evidence confirms the requested effect",
        "basis": "verifier-evidence",
        "verifier": {
            "contract": {"namespace": "example", "name": "verification", "version": "v1"},
            "predicate": "verified",
            "max_evidence_age_ms": 1_000,
        },
    }
    last_requirements = cast(
        JSONObject, cast(list[JSONObject], tasks[-1]["roles"])[0]["requirements"]
    )
    last_requirements["memories"] = [
        {
            "selector": {"memory_id": "original-memory", "revision_id": "original-revision"},
            "kind": "artifact",
            "payload_schema": "actual-file/v1",
            "media_type": "application/octet-stream",
        }
    ]
    compact = deepcopy(legacy)
    for index, task in enumerate(cast(list[JSONObject], compact["tasks"])):
        if index != len(tasks) - 1:
            task["timing"] = None
            del cast(JSONObject, task["satisfaction"])["verifier"]
        for role in cast(list[JSONObject], task["roles"]):
            requirements = cast(JSONObject, role["requirements"])
            if requirements["memories"] is None:
                del requirements["memories"]
    return canonical, legacy, compact, objective


def test_compact_planner_output_preserves_canonical_plan_and_digest() -> None:
    """Compact-only omissions preserve exact bindings, DAG and non-neutral requirements."""
    canonical, legacy, compact, objective = _compact_fixture()

    def normalize(value: JSONObject, use_compact: bool) -> JSONObject:
        """Run the real provider normalization and canonical parser."""
        return MissionPlan.from_json(
            normalize_mission_plan_provider_output(
                value,
                canonical,
                profile=HOST_OBJECTIVE_PROFILE,
                objective=objective,
                compact_planner_output=use_compact,
            )
        ).to_json()

    legacy_plan = normalize(legacy, False)
    compact_plan = normalize(compact, True)
    assert compact_plan == legacy_plan
    assert canonical_plan_digest(compact_plan) == canonical_plan_digest(legacy_plan)
    assert len(json.dumps(compact, separators=(",", ":"))) < len(
        json.dumps(legacy, separators=(",", ":"))
    )
    assert cast(list[JSONObject], compact["tasks"])[0]["timing"] is None


@pytest.mark.parametrize(
    "problem",
    [
        "verifier_conflict",
        "verifier_missing",
        "null_memories",
        "missing_timing",
        "broken_dependency",
    ],
)
def test_compact_planner_output_does_not_mask_invalid_drafts(problem: str) -> None:
    """Only explicit neutral timing and execution-report shorthand may be expanded."""
    canonical, _, compact, objective = _compact_fixture()
    tasks = cast(list[JSONObject], compact["tasks"])
    first = tasks[0]
    if problem == "verifier_conflict":
        cast(JSONObject, first["satisfaction"])["verifier"] = None
    elif problem == "verifier_missing":
        del cast(JSONObject, tasks[-1]["satisfaction"])["verifier"]
    elif problem == "null_memories":
        role = cast(list[JSONObject], first["roles"])[0]
        cast(JSONObject, role["requirements"])["memories"] = None
    elif problem == "missing_timing":
        del first["timing"]
    else:
        tasks[-1]["depends_on"] = ["missing-task"]

    with pytest.raises((MissionPlanError, ProviderMissionPlanError)):
        normalized = normalize_mission_plan_provider_output(
            compact,
            canonical,
            profile=HOST_OBJECTIVE_PROFILE,
            objective=objective,
            compact_planner_output=True,
        )
        MissionPlan.from_json(normalized)


def test_compact_planner_schema_uses_static_closed_variants() -> None:
    """Static compact schemas preserve cache identity and the full reference schema."""
    canonical, _, _, _ = _compact_fixture()
    first = build_mission_plan_provider_schema(
        canonical,
        mission_id="mission-one",
        profile=HOST_OBJECTIVE_PROFILE,
        bind_mission_id=False,
        compact_planner_output=True,
    )
    second = build_mission_plan_provider_schema(
        canonical,
        mission_id="mission-two",
        profile=HOST_OBJECTIVE_PROFILE,
        bind_mission_id=False,
        compact_planner_output=True,
    )
    assert first == second
    assert (
        cast(JSONObject, first["$defs"])["satisfaction"]
        != cast(JSONObject, canonical["$defs"])["satisfaction"]
    )
    with pytest.raises(ProviderMissionPlanError, match="compact Planner output requires"):
        build_mission_plan_provider_schema(
            _canonical_schema(), mission_id="mission-one", compact_planner_output=True
        )
