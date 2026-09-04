"""Contract tests for MissionPlan v0.3 compatibility and v0.4 graph invariants."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from mission.models import JSONObject, MissionPlan, MissionPlanError

FIXTURE = Path("scenarios/phase1-mission-v0.3/mission-plan.json")
RELATION_FIXTURE = Path("scenarios/execution-relations-v0.1/mission-plan.json")


def _fixture_json() -> JSONObject:
    """Load a mutable copy of the approved MVP Mission Plan fixture."""
    decoded = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return cast(JSONObject, decoded)


def _relation_fixture_json() -> JSONObject:
    """Load a mutable execution coordination relation fixture."""
    decoded = json.loads(RELATION_FIXTURE.read_text(encoding="utf-8"))
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


def test_valid_fixture_round_trips() -> None:
    """The approved fixture must parse and serialize without contract drift."""
    raw = _fixture_json()
    assert MissionPlan.from_json(raw).to_json() == raw


def test_v0_4_schema_requires_typed_relation_fields() -> None:
    """The current provider schema exposes v0.4 kinds and their required typed fields."""
    schema = json.loads(
        Path("contracts/mission/v0.4/mission-plan.schema.json").read_text(encoding="utf-8")
    )
    version = schema["properties"]["schema_version"]
    assert version == {"type": "string", "const": "roboguide.mission-plan/v0.4"}
    relation_kind = schema["$defs"]["relation"]["properties"]["kind"]
    assert "state-requirement" in relation_kind["enum"]
    conditional_requirements = {
        tuple(branch["then"]["required"]) for branch in schema["$defs"]["relation"]["allOf"]
    }
    assert ("state_key", "requirement") in conditional_requirements


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
