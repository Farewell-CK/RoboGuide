"""Contract tests for MissionPlan compatibility and current scheduling invariants."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from mission.models import JSONObject, MissionPlan, MissionPlanError

FIXTURE = Path("scenarios/phase1-mission-v0.3/mission-plan.json")
RELATION_FIXTURE = Path("scenarios/execution-relations-v0.1/mission-plan.json")
NORMALIZED_FIXTURE = Path("scenarios/mission-front-half-v0.7/mission-plan.json")


def _fixture_json() -> JSONObject:
    """Load a mutable copy of the approved MVP Mission Plan fixture."""
    decoded = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return cast(JSONObject, decoded)


def _relation_fixture_json() -> JSONObject:
    """Load a mutable execution coordination relation fixture."""
    decoded = json.loads(RELATION_FIXTURE.read_text(encoding="utf-8"))
    return cast(JSONObject, decoded)


def _v0_7_fixture_json() -> JSONObject:
    """Load the normalized semantic MissionPlan fixture."""
    decoded = json.loads(NORMALIZED_FIXTURE.read_text(encoding="utf-8"))
    return cast(JSONObject, decoded)


def _v0_4_fixture_json() -> JSONObject:
    """Build one v0.4 plan covering typed relations and selective Group State binding."""
    raw = _relation_fixture_json()
    raw["schema_version"] = "roboguide.mission-plan/v0.4"
    contexts = cast(list[JSONObject], raw["contexts"])
    context = contexts[0]
    context["coupling_mode"] = "concurrent-cooperation"
    context["shared_view"] = {
        "bindings": [
            {
                "context_role_id": "safety",
                "field": "pose",
                "state_export_id": "safety-pose",
                "payload_schema": "roboguide.pose/v1",
            }
        ],
        "include_freshness": True,
        "spatial_reference": {
            "map_id": "campus",
            "revision_id": "r1",
            "frame_id": "map",
        },
    }
    relations = cast(list[JSONObject], context["relations"])
    relations[0] = {
        "id": "safety-guards-navigation",
        "kind": "state-requirement",
        "state_key": "hazard",
        "requirement": "available",
        "source": {"task_id": "observe-safety", "role_id": "safety-observer"},
        "target": {"task_id": "navigate", "role_id": "navigator"},
    }
    tasks = cast(list[JSONObject], raw["tasks"])
    tasks[1]["coupling_mode"] = "tightly-coupled-cooperation"
    context["peer_channel"] = {
        "profile_id": "guidance-peer",
        "message_schema": "roboguide.guidance-peer/v1",
    }
    return raw


def _v0_5_fixture_json() -> JSONObject:
    """Upgrade the coordination fixture with quantitative resources and Task timing."""
    raw = _v0_4_fixture_json()
    raw["schema_version"] = "roboguide.mission-plan/v0.5"
    tasks = cast(list[JSONObject], raw["tasks"])
    for task in tasks:
        task["timing"] = {
            "earliest_start_offset_ms": 0,
            "latest_start_offset_ms": 10_000,
            "completion_deadline_offset_ms": 20_000,
            "estimated_duration_ms": 5_000,
        }
        roles = cast(list[JSONObject], task["roles"])
        for role in roles:
            resource_kind = role.pop("resource_kind")
            role["resources"] = (
                [] if resource_kind is None else [{"kind": resource_kind, "units": 2}]
            )
    return raw


def _v0_6_fixture_json() -> JSONObject:
    """Upgrade the scheduling fixture with explicit Task satisfaction policy."""
    raw = _v0_5_fixture_json()
    raw["schema_version"] = "roboguide.mission-plan/v0.6"
    tasks = cast(list[JSONObject], raw["tasks"])
    for task in tasks:
        task["satisfaction"] = {"basis": "execution-report"}
    return raw


def test_valid_fixture_round_trips() -> None:
    """The approved fixture must parse and serialize without contract drift."""
    raw = _fixture_json()
    assert MissionPlan.from_json(raw).to_json() == raw


def test_v0_6_schema_requires_typed_relations_and_satisfaction() -> None:
    """The current provider schema retains relation kinds and their required typed fields."""
    schema = json.loads(
        Path("contracts/mission/v0.6/mission-plan.schema.json").read_text(encoding="utf-8")
    )
    version = schema["properties"]["schema_version"]
    assert version == {"type": "string", "const": "roboguide.mission-plan/v0.6"}
    relation_kind = schema["$defs"]["relation"]["properties"]["kind"]
    assert "state-requirement" in relation_kind["enum"]
    conditional_requirements = {
        tuple(branch["then"]["required"]) for branch in schema["$defs"]["relation"]["allOf"]
    }
    assert ("state_key", "requirement") in conditional_requirements
    assert schema["$defs"]["task_satisfaction"]["properties"]["basis"] == {
        "const": "execution-report"
    }


def test_execution_relation_round_trips_logical_endpoints() -> None:
    """Relation endpoints remain logical Task/Role slots rather than physical Nodes."""
    plan = MissionPlan.from_json(_relation_fixture_json())
    relation = plan.contexts[0].relations[0]
    assert relation.relation_id == "safety-guards-navigation"
    assert relation.kind == "requires-active"
    assert relation.source.task_id == "observe-safety"
    assert relation.target.role_id == "navigator"
    assert plan.to_json() == _relation_fixture_json()


def test_v0_4_coupling_and_typed_relation_round_trip() -> None:
    """Current Mission output retains mode, typed relation, shared view, and peer descriptor."""
    raw = _v0_4_fixture_json()
    plan = MissionPlan.from_json(raw)
    context = plan.contexts[0]
    relation = context.relations[0]
    assert context.coupling_mode == "concurrent-cooperation"
    assert context.shared_view is not None
    assert context.shared_view.bindings[0].state_export_id == "safety-pose"
    assert context.shared_view.spatial_reference is not None
    assert context.shared_view.spatial_reference.map_id == "campus"
    assert relation.kind == "state-requirement"
    assert relation.state_key == "hazard"
    assert relation.requirement == "available"
    assert plan.tasks[1].coupling_mode == "tightly-coupled-cooperation"
    assert plan.to_json() == raw

    execution_view = _v0_4_fixture_json()
    contexts = cast(list[JSONObject], execution_view["contexts"])
    shared_view = cast(JSONObject, contexts[0]["shared_view"])
    bindings = cast(list[JSONObject], shared_view["bindings"])
    bindings.append({"context_role_id": "guide", "field": "execution"})
    execution_plan = MissionPlan.from_json(execution_view)
    assert execution_plan.contexts[0].shared_view is not None
    assert execution_plan.contexts[0].shared_view.bindings[1].state_export_id is None
    assert execution_plan.to_json() == execution_view


def test_v0_5_quantitative_resources_and_timing_round_trip() -> None:
    """Current plans retain bounded Task timing and all exclusive resource demands."""
    raw = _v0_5_fixture_json()
    plan = MissionPlan.from_json(raw)
    assert plan.tasks[0].timing is not None
    assert plan.tasks[0].timing.estimated_duration_ms == 5_000
    assert plan.tasks[1].roles[0].resources[0].units == 2
    assert plan.to_json() == raw


def test_v0_6_task_satisfaction_round_trip_and_v0_5_default() -> None:
    """Current plans declare satisfaction while historical v0.5 normalizes compatibly."""
    current = _v0_6_fixture_json()
    plan = MissionPlan.from_json(current)
    assert plan.tasks[0].satisfaction_basis == "execution-report"
    assert plan.to_json() == current

    historical = MissionPlan.from_json(_v0_5_fixture_json())
    assert historical.tasks[0].satisfaction_basis == "execution-report"
    assert historical.to_json() == _v0_5_fixture_json()


def test_v0_7_normalizes_actor_role_operation_timing_and_satisfaction() -> None:
    """Current plans avoid duplicated Actor/contract fields and retain semantic completion."""
    raw = _v0_7_fixture_json()
    plan = MissionPlan.from_json(raw)
    role = plan.tasks[0].roles[0]

    assert plan.to_json() == raw
    assert plan.mission.actors[0].actor_id == "courier"
    assert role.actor_id is None
    assert role.context_role == "carrier"
    assert len(role.capabilities) == 1
    assert role.capabilities[0].constraints[0].attribute == "max-payload-grams"
    assert role.execution.operation.name == "relocate"
    assert role.execution.operation.to_json() == role.capabilities[0].contract.to_json()
    assert plan.tasks[0].timing is not None
    assert plan.tasks[0].timing.estimated_duration_ms is None
    assert plan.tasks[0].satisfaction.expected_effect.startswith("急救包")
    assert plan.tasks[0].satisfaction.verifier is not None


def test_v0_7_rejects_duplicated_role_actor_and_planner_duration() -> None:
    """Current Role and timing fields cannot reintroduce legacy duplication or guessed estimates."""
    duplicated_actor = _v0_7_fixture_json()
    tasks = cast(list[JSONObject], duplicated_actor["tasks"])
    roles = cast(list[JSONObject], tasks[0]["roles"])
    roles[0]["actor"] = "courier"
    with pytest.raises(MissionPlanError, match="unknown=\\['actor'\\]"):
        MissionPlan.from_json(duplicated_actor)

    guessed_duration = _v0_7_fixture_json()
    tasks = cast(list[JSONObject], guessed_duration["tasks"])
    timing = cast(JSONObject, tasks[0]["timing"])
    timing["estimated_duration_ms"] = 120_000
    with pytest.raises(MissionPlanError, match="estimated_duration_ms"):
        MissionPlan.from_json(guessed_duration)


def test_v0_6_requires_supported_task_satisfaction_basis() -> None:
    """Missing or unknown satisfaction policy fails before a Task can reach execution."""
    missing = _v0_6_fixture_json()
    missing_tasks = cast(list[JSONObject], missing["tasks"])
    del missing_tasks[0]["satisfaction"]
    with pytest.raises(MissionPlanError, match=r"missing=\['satisfaction'\]"):
        MissionPlan.from_json(missing)

    unknown = _v0_6_fixture_json()
    unknown_tasks = cast(list[JSONObject], unknown["tasks"])
    satisfaction = cast(JSONObject, unknown_tasks[0]["satisfaction"])
    satisfaction["basis"] = "world-state-evidence"
    with pytest.raises(MissionPlanError, match="basis is unsupported"):
        MissionPlan.from_json(unknown)


def test_v0_5_rejects_duplicate_resource_kind_and_infeasible_timing() -> None:
    """Ambiguous capacity demands and impossible local windows fail at contract admission."""
    duplicate = _v0_5_fixture_json()
    tasks = cast(list[JSONObject], duplicate["tasks"])
    roles = cast(list[JSONObject], tasks[1]["roles"])
    resources = cast(list[JSONObject], roles[0]["resources"])
    resources.append(deepcopy(resources[0]))
    with pytest.raises(MissionPlanError, match="duplicate kinds"):
        MissionPlan.from_json(duplicate)

    infeasible = _v0_5_fixture_json()
    tasks = cast(list[JSONObject], infeasible["tasks"])
    timing = cast(JSONObject, tasks[0]["timing"])
    timing["latest_start_offset_ms"] = -1
    with pytest.raises(MissionPlanError, match="nonnegative integer"):
        MissionPlan.from_json(infeasible)

    unprovable = _v0_5_fixture_json()
    tasks = cast(list[JSONObject], unprovable["tasks"])
    timing = cast(JSONObject, tasks[0]["timing"])
    timing["estimated_duration_ms"] = None
    with pytest.raises(MissionPlanError, match="requires estimated duration"):
        MissionPlan.from_json(unprovable)

    null_earliest = _v0_5_fixture_json()
    tasks = cast(list[JSONObject], null_earliest["tasks"])
    timing = cast(JSONObject, tasks[0]["timing"])
    timing["earliest_start_offset_ms"] = None
    with pytest.raises(MissionPlanError, match="earliest_start_offset_ms"):
        MissionPlan.from_json(null_earliest)

    oversized = _v0_5_fixture_json()
    tasks = cast(list[JSONObject], oversized["tasks"])
    roles = cast(list[JSONObject], tasks[1]["roles"])
    resources = cast(list[JSONObject], roles[0]["resources"])
    resources[0]["units"] = 1 << 32
    with pytest.raises(MissionPlanError, match="32-bit"):
        MissionPlan.from_json(oversized)

    overflowing = _v0_5_fixture_json()
    tasks = cast(list[JSONObject], overflowing["tasks"])
    timing = cast(JSONObject, tasks[0]["timing"])
    timing["earliest_start_offset_ms"] = (1 << 64) - 1
    timing["latest_start_offset_ms"] = None
    timing["completion_deadline_offset_ms"] = (1 << 64) - 1
    with pytest.raises(MissionPlanError, match="overflows"):
        MissionPlan.from_json(overflowing)

    duration_only_overflow = _v0_5_fixture_json()
    tasks = cast(list[JSONObject], duration_only_overflow["tasks"])
    timing = cast(JSONObject, tasks[0]["timing"])
    timing["earliest_start_offset_ms"] = (1 << 64) - 1
    timing["latest_start_offset_ms"] = None
    timing["completion_deadline_offset_ms"] = None
    timing["estimated_duration_ms"] = 1
    with pytest.raises(MissionPlanError, match="overflows earliest start"):
        MissionPlan.from_json(duration_only_overflow)


def test_implementation_profile_rejects_valid_future_relation() -> None:
    """Planner preflight separates valid contract syntax from executable mechanisms."""
    plan = MissionPlan.from_json(_v0_4_fixture_json())
    with pytest.raises(MissionPlanError, match="valid contract syntax but is not executable"):
        plan.validate_implementation_support()

    executable = _v0_4_fixture_json()
    contexts = cast(list[JSONObject], executable["contexts"])
    relations = cast(list[JSONObject], contexts[0]["relations"])
    relations[0] = {
        "id": "shared-localization",
        "kind": "shared-spatial-reference",
        "reference": {
            "map_id": "campus",
            "revision_id": "r1",
            "frame_id": "map",
        },
        "source": {"task_id": "observe-safety", "role_id": "safety-observer"},
        "target": {"task_id": "navigate", "role_id": "navigator"},
    }
    MissionPlan.from_json(executable).validate_implementation_support()


def test_v0_4_rejects_incomplete_relation_and_unknown_view_member() -> None:
    """Typed relation payloads and Group view members fail closed at the Mission boundary."""
    incomplete = _v0_4_fixture_json()
    contexts = cast(list[JSONObject], incomplete["contexts"])
    relations = cast(list[JSONObject], contexts[0]["relations"])
    del relations[0]["requirement"]
    with pytest.raises(MissionPlanError, match="requirement must be nonblank"):
        MissionPlan.from_json(incomplete)

    unknown_member = _v0_4_fixture_json()
    contexts = cast(list[JSONObject], unknown_member["contexts"])
    shared_view = cast(JSONObject, contexts[0]["shared_view"])
    bindings = cast(list[JSONObject], shared_view["bindings"])
    bindings[0]["context_role_id"] = "missing-member"
    with pytest.raises(MissionPlanError, match="shared view references unknown"):
        MissionPlan.from_json(unknown_member)

    wrong_authority = _v0_4_fixture_json()
    contexts = cast(list[JSONObject], wrong_authority["contexts"])
    shared_view = cast(JSONObject, contexts[0]["shared_view"])
    bindings = cast(list[JSONObject], shared_view["bindings"])
    bindings[0]["field"] = "execution"
    with pytest.raises(MissionPlanError, match="cannot select a State export"):
        MissionPlan.from_json(wrong_authority)


def test_v0_4_rejects_modes_without_required_static_mechanisms() -> None:
    """Mode acceptance fails before Runtime when its required declarations are absent."""
    missing_view = _v0_4_fixture_json()
    contexts = cast(list[JSONObject], missing_view["contexts"])
    del contexts[0]["shared_view"]
    with pytest.raises(MissionPlanError, match="requires a Group shared view"):
        MissionPlan.from_json(missing_view)

    missing_peer = _v0_4_fixture_json()
    contexts = cast(list[JSONObject], missing_peer["contexts"])
    del contexts[0]["peer_channel"]
    with pytest.raises(MissionPlanError, match="requires a direct peer channel"):
        MissionPlan.from_json(missing_peer)


def test_execution_relation_rejects_unknown_or_dag_ordered_endpoints() -> None:
    """Relations may connect only exact roles that can be concurrently active."""
    unknown = _relation_fixture_json()
    contexts = cast(list[JSONObject], unknown["contexts"])
    relations = cast(list[JSONObject], contexts[0]["relations"])
    source = cast(JSONObject, relations[0]["source"])
    source["role_id"] = "missing-role"
    with pytest.raises(MissionPlanError, match="unknown role"):
        MissionPlan.from_json(unknown)

    ordered = _relation_fixture_json()
    tasks = cast(list[JSONObject], ordered["tasks"])
    tasks[1]["depends_on"] = ["observe-safety"]
    with pytest.raises(MissionPlanError, match="ordered by the DAG"):
        MissionPlan.from_json(ordered)


def test_structured_execution_parameter_is_rejected() -> None:
    """Mission Plan v0.3 accepts only scalar transport-neutral parameters."""
    raw = deepcopy(_fixture_json())
    tasks = cast(list[JSONObject], raw["tasks"])
    roles = cast(list[JSONObject], tasks[0]["roles"])
    execution = cast(JSONObject, roles[0]["execution"])
    parameters = cast(JSONObject, execution["parameters"])
    parameters["unsafe"] = {"command": "walk"}
    with pytest.raises(MissionPlanError, match="must be a scalar"):
        MissionPlan.from_json(raw)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_execution_parameter_is_rejected(value: float) -> None:
    """Mission artifacts must remain portable standard JSON across language boundaries."""
    raw = deepcopy(_fixture_json())
    tasks = cast(list[JSONObject], raw["tasks"])
    roles = cast(list[JSONObject], tasks[0]["roles"])
    execution = cast(JSONObject, roles[0]["execution"])
    parameters = cast(JSONObject, execution["parameters"])
    parameters["non_finite"] = value
    with pytest.raises(MissionPlanError, match="finite number"):
        MissionPlan.from_json(raw)


def test_unknown_dependency_is_rejected() -> None:
    """A task may depend only on another task declared in the same graph."""
    raw = deepcopy(_fixture_json())
    tasks = cast(list[JSONObject], raw["tasks"])
    tasks[0]["depends_on"] = ["task-missing"]
    with pytest.raises(MissionPlanError, match="unknown dependencies"):
        MissionPlan.from_json(raw)


def test_cycle_is_rejected() -> None:
    """Mission Intelligence must never emit a cyclic Task Graph."""
    raw = deepcopy(_fixture_json())
    tasks = cast(list[JSONObject], raw["tasks"])
    second = deepcopy(tasks[0])
    second["id"] = "task-second"
    second["depends_on"] = [tasks[0]["id"]]
    tasks[0]["depends_on"] = ["task-second"]
    tasks.append(second)
    with pytest.raises(MissionPlanError, match="cycle"):
        MissionPlan.from_json(raw)


def test_unknown_contract_field_is_rejected() -> None:
    """Unknown fields must fail loudly instead of silently changing semantics."""
    raw = _fixture_json()
    raw["provider_metadata"] = {}
    with pytest.raises(MissionPlanError, match="keys mismatch"):
        MissionPlan.from_json(raw)


def test_role_contract_must_match_execution_contract() -> None:
    """A role cannot declare one executable contract and invoke another."""
    raw = _fixture_json()
    tasks = cast(list[JSONObject], raw["tasks"])
    roles = cast(list[JSONObject], tasks[0]["roles"])
    roles[0]["contract"] = {"namespace": "camera", "name": "capture", "version": "v1"}
    with pytest.raises(MissionPlanError, match="differs"):
        MissionPlan.from_json(raw)


@pytest.mark.parametrize(
    ("namespace", "name", "version"),
    [
        ("spatial", "map.build", "v0"),
        ("spatial..map", "build", "v0"),
        ("spatial.map", "build", "v0@draft"),
    ],
)
def test_ambiguous_contract_identity_is_rejected(namespace: str, name: str, version: str) -> None:
    """Structured contracts must round-trip through the configured canonical string."""
    raw = _fixture_json()
    tasks = cast(list[JSONObject], raw["tasks"])
    roles = cast(list[JSONObject], tasks[0]["roles"])
    contract: JSONObject = {"namespace": namespace, "name": name, "version": version}
    roles[0]["contract"] = contract
    execution = cast(JSONObject, roles[0]["execution"])
    execution["capability_contract"] = contract
    with pytest.raises(MissionPlanError, match="canonical"):
        MissionPlan.from_json(raw)
