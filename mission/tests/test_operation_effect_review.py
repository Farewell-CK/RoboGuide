"""Deterministic operation-effect composition reports never become Mission authority."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import cast

from mission.models import JSONObject, MissionPlan
from mission.operation_effect_review import operation_effect_composition_review
from mission.planning_profile import DeploymentPlanningProfile
from test_planners import _v0_8_plan

ROOT = Path(__file__).parents[2]


def _digest(value: object) -> str:
    """Compute the closed profile digest used by the production parser."""
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def _profile() -> DeploymentPlanningProfile:
    """Load one checked-in deployment-owned effect declaration."""
    return DeploymentPlanningProfile.load(
        ROOT / "scenarios/e1-shared-world-perception/planning-profile.json"
    )


def _relocation_profile() -> DeploymentPlanningProfile:
    """Load the deployment profile that declares object-location effects."""
    return DeploymentPlanningProfile.load(
        ROOT / "scenarios/e1-shared-world-rearrangement/planning-profile.json"
    )


def _object(value: object) -> dict[str, object]:
    """Narrow a decoded fixture object for explicit mutable setup."""
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _array(value: object) -> list[object]:
    """Narrow a decoded fixture array before indexing or mutation."""
    assert isinstance(value, list)
    return cast(list[object], value)


def _report_object(value: object) -> JSONObject:
    """Read one structured report object from the JSON-compatible result."""
    assert isinstance(value, dict)
    return cast(JSONObject, value)


def _report_array(report: JSONObject, key: str) -> list[object]:
    """Read one report array without weakening JSON result types globally."""
    return _array(report[key])


def _task(
    task_id: str,
    operation: str,
    parameters: dict[str, str],
    *,
    dependency: str | None = None,
) -> JSONObject:
    """Build one valid v0.8 role with an exact operation and abstract requirement."""
    if operation == "mobility.navigate":
        capability = {"namespace": "mobility", "name": "navigate", "version": "v1"}
        intent_parameters: JSONObject = {"destination": parameters["destination"]}
        objective = "Reach the declared semantic destination."
    elif operation == "observation.acquire":
        capability = {"namespace": "observation", "name": "acquire", "version": "v1"}
        intent_parameters = {"expected": parameters["expected"]}
        objective = "Actively acquire the declared semantic condition."
    elif operation == "observation.verify":
        capability = {"namespace": "observation", "name": "verify", "version": "v1"}
        intent_parameters = {"expected": parameters["expected"]}
        objective = "Read the declared semantic condition."
    elif operation == "object.relocate":
        capability = {"namespace": "object", "name": "relocate", "version": "v1"}
        intent_parameters = {
            "object": parameters["object"],
            "destination": parameters["destination"],
        }
        objective = "Relocate the declared semantic object."
    else:
        raise AssertionError(operation)
    constraints: list[JSONObject] = []
    if operation in {"observation.acquire", "observation.verify"}:
        constraints.append(
            {
                "attribute": "condition-format",
                "operator": "equals",
                "value": "detected(entity-ref)",
            }
        )
    return cast(
        JSONObject,
        {
            "id": task_id,
            "description": objective,
            "context_id": "delivery-context",
            "depends_on": [dependency] if dependency else [],
            "roles": [
                {
                    "id": f"role-{task_id}",
                    "context_role": "carrier",
                    "requirements": {
                        "capabilities": [{"contract": capability, "constraints": constraints}],
                        "resources": [{"kind": "space", "units": 1}],
                    },
                    "execution_intent": {
                        "operation": capability,
                        "objective": objective,
                        "parameters": intent_parameters,
                    },
                    "resource_scope": "task",
                }
            ],
            "timing": {
                "earliest_start_offset_ms": 0,
                "latest_start_offset_ms": None,
                "completion_deadline_offset_ms": None,
            },
            "satisfaction": {
                "expected_effect": objective,
                "basis": "execution-report",
                "verifier": None,
            },
        },
    )


def _navigate_then_acquire(
    *, dependency: bool = True, separate_actors: bool = False
) -> MissionPlan:
    """Build a synthetic navigation/acquisition pair without changing frozen goals."""
    raw = _object(copy.deepcopy(_v0_8_plan()))
    raw["tasks"] = [
        _task("arrive", "mobility.navigate", {"destination": "target-a"}),
        _task(
            "acquire",
            "observation.acquire",
            {"expected": "detected(target-a)"},
            dependency="arrive" if dependency else None,
        ),
    ]
    if separate_actors:
        mission = _object(raw["mission"])
        _array(mission["actors"]).append({"id": "observer"})
        context = _object(_array(raw["contexts"])[0])
        _array(context["roles"]).append({"id": "observer-role", "actor": "observer"})
        task = _object(_array(raw["tasks"])[1])
        role = _object(_array(task["roles"])[0])
        role["context_role"] = "observer-role"
    return MissionPlan.from_json(cast(JSONObject, raw))


def test_navigation_then_active_acquisition_reports_only_possible_pose_overlap() -> None:
    """A same-Actor chain surfaces movement risk without rejecting the joint plan."""
    profile = _profile()
    plan = _navigate_then_acquire()
    report = operation_effect_composition_review(plan, profile)
    assert report["decision"] == "advisory"
    assert report["plan_sha256"] == _digest(plan.to_json())
    assert report["planning_profile_sha256"] == profile.profile_digest
    assert len(_report_array(report, "findings")) == 1
    finding = _report_object(_report_array(report, "findings")[0])
    assert finding["code"] == "possible-shared-state-interaction"
    assert finding["task_order"] == "ordered"
    assert finding["state_domains"] == ["participant.position"]
    assert finding["physical_entity_binding"] == "not-selected"
    assert report["unknown_pairs"]
    guidance = _report_object(report["guidance"])
    assert guidance["possible_overlap_is_not_a_proven_goal_conflict"]
    assert report["digest"] == _digest(
        {key: value for key, value in report.items() if key != "digest"}
    )


def test_independent_tasks_are_not_given_an_invented_dag_order_or_physical_binding() -> None:
    """Distinct logical Actors do not prove distinct physical executors."""
    plan = _navigate_then_acquire(dependency=False, separate_actors=True)
    report = operation_effect_composition_review(plan, _profile())
    assert _report_array(report, "findings") == []
    unknown = _report_object(_report_array(report, "unknown_pairs")[0])
    assert unknown["reason"] == "physical_entity_binding_unresolved"
    guidance = _report_object(report["guidance"])
    assert guidance["logical_actor_does_not_select_physical_entity"]
    assert "physical_entity_id" not in json.dumps(report)
    assert "node_id" not in json.dumps(report)


def test_different_actors_can_interact_through_the_same_semantic_object_state() -> None:
    """Shared object effects compare by semantic object even across logical Actors."""
    raw = _object(copy.deepcopy(_v0_8_plan()))
    mission = _object(raw["mission"])
    raw["mission"] = mission
    _array(mission["actors"]).append({"id": "relocator-two"})
    context = _object(_array(raw["contexts"])[0])
    _array(context["roles"]).append({"id": "relocator-two-role", "actor": "relocator-two"})
    raw["tasks"] = [
        _task(
            "move-first",
            "object.relocate",
            {"object": "package-a", "destination": "shelf-a"},
        ),
        _task(
            "move-again",
            "object.relocate",
            {"object": "package-a", "destination": "shelf-b"},
            dependency="move-first",
        ),
    ]
    second_task = _object(_array(raw["tasks"])[1])
    second_role = _object(_array(second_task["roles"])[0])
    second_role["context_role"] = "relocator-two-role"
    plan = MissionPlan.from_json(cast(JSONObject, raw))
    report = operation_effect_composition_review(plan, _relocation_profile())

    finding = _report_object(_report_array(report, "findings")[0])
    binding = _report_object(_array(finding["effect_bindings"])[0])
    assert finding["actor_ids"] == ["courier", "relocator-two"]
    assert binding["domain"] == "entity.location"
    assert binding["result_relation"] == "different"


def test_same_actor_without_dag_edge_is_reported_as_unordered() -> None:
    """Shared possible effects never manufacture sequencing for independent Tasks."""
    report = operation_effect_composition_review(
        _navigate_then_acquire(dependency=False), _profile()
    )
    finding = _report_object(_report_array(report, "findings")[0])
    assert finding["task_order"] == "unordered"


def test_same_actor_navigation_to_distinct_goals_reports_possible_state_change() -> None:
    """A later move can change the earlier goal's location without being rejected here."""
    raw = _object(copy.deepcopy(_v0_8_plan()))
    raw["tasks"] = [
        _task("arrive-first", "mobility.navigate", {"destination": "target-a"}),
        _task(
            "arrive-second",
            "mobility.navigate",
            {"destination": "target-b"},
            dependency="arrive-first",
        ),
    ]
    plan = MissionPlan.from_json(cast(JSONObject, raw))
    report = operation_effect_composition_review(plan, _profile())
    finding = _report_object(_report_array(report, "findings")[0])
    binding = _report_object(_array(finding["effect_bindings"])[0])

    assert report["decision"] == "advisory"
    assert finding["task_order"] == "ordered"
    assert binding["domain"] == "participant.position"
    assert binding["result_relation"] == "different"
    assert report["plan_sha256"] == _digest(plan.to_json())


def test_acquisition_change_interacts_with_later_read_of_same_condition_context() -> None:
    """Neutral read/write declarations expose an interaction without claiming failure."""
    raw = _object(copy.deepcopy(_v0_8_plan()))
    raw["tasks"] = [
        _task(
            "acquire",
            "observation.acquire",
            {"expected": "detected(target-a)"},
        ),
        _task(
            "verify",
            "observation.verify",
            {"expected": "detected(target-a)"},
            dependency="acquire",
        ),
    ]
    report = operation_effect_composition_review(
        MissionPlan.from_json(cast(JSONObject, raw)), _profile()
    )
    finding = _report_object(_report_array(report, "findings")[0])
    assert finding["code"] == "possible-shared-state-interaction"
    binding = _report_object(_array(finding["effect_bindings"])[0])
    assert binding["domain"] == "observation.condition"
    assert binding["first_access"] == "may_change"
    assert binding["second_access"] == "read"
    assert finding["task_order"] == "ordered"


def test_two_read_only_operations_do_not_create_a_state_interaction() -> None:
    """Read/read access to one condition is compatible under the declared effect model."""
    raw = _object(copy.deepcopy(_v0_8_plan()))
    raw["tasks"] = [
        _task("verify-one", "observation.verify", {"expected": "detected(target-a)"}),
        _task("verify-two", "observation.verify", {"expected": "detected(target-a)"}),
    ]
    report = operation_effect_composition_review(
        MissionPlan.from_json(cast(JSONObject, raw)), _profile()
    )
    assert _report_array(report, "findings") == []
    assert _report_array(report, "unknown_pairs") == []


def test_legacy_profile_marks_missing_operation_effects_unknown() -> None:
    """A v0.1 profile remains readable but cannot be treated as effect-free."""
    profile_path = ROOT / "scenarios/e1-shared-world-perception/planning-profile.json"
    raw = _object(json.loads(profile_path.read_text(encoding="utf-8")))
    legacy = copy.deepcopy(raw)
    legacy["schema_version"] = "roboguide.deployment-planning-profile/v0.1"
    legacy.pop("operation_effects")
    legacy.pop("state_domains")
    legacy["digest"] = _digest({key: value for key, value in legacy.items() if key != "digest"})
    profile = DeploymentPlanningProfile.from_json(legacy)
    report = operation_effect_composition_review(_navigate_then_acquire(), profile)
    assert _report_array(report, "findings") == []
    unknown = _report_object(_report_array(report, "unknown_pairs")[0])
    assert unknown["reason"] == ("operation_effects_not_published")


def test_analysis_budget_is_bounded_and_explicit() -> None:
    """An oversized input produces an incomplete advisory instead of unbounded pair work."""
    raw = _object(copy.deepcopy(_v0_8_plan()))
    raw["tasks"] = [
        _task(f"task-{index}", "mobility.navigate", {"destination": f"target-{index}"})
        for index in range(129)
    ]
    plan = MissionPlan.from_json(cast(JSONObject, raw))
    report = operation_effect_composition_review(plan, _profile())
    assert report["analysis_status"] == "incomplete-slot-bound-exceeded"
    assert report["truncated"] is True
    assert _report_array(report, "findings") == []
