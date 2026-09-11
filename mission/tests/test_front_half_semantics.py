"""Systematic dry-run checks for the normalized Mission front-half boundary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.models import JSONObject, MissionPlan, TaskSatisfactionBasis

PLAN_FIXTURE = Path("scenarios/mission-front-half-v0.7/mission-plan.json")
CATALOG_FIXTURE = Path("contracts/capability/v0.3/catalog.json")


def _raw_plan() -> JSONObject:
    """Load the maintained front-half fixture as mutable contract JSON."""
    return cast(JSONObject, json.loads(PLAN_FIXTURE.read_text(encoding="utf-8")))


def _contract_id(namespace: str, name: str, version: str) -> str:
    """Format one exact canonical identity for concise boundary assertions."""
    return f"{namespace}.{name}@{version}"


def test_front_half_fixture_keeps_one_semantic_task_above_local_how() -> None:
    """A relocatable Local EAIOS objective stays one schedulable and recoverable Task."""
    raw = _raw_plan()
    plan = MissionPlan.from_json(raw)
    catalog = CanonicalCapabilityCatalog.load(CATALOG_FIXTURE)

    catalog.validate_plan(plan)
    assert len(plan.tasks) == 1
    task = plan.tasks[0]
    assert task.task_id == "relocate-aid-kit"
    assert len(task.roles) == 1
    role = task.roles[0]
    assert role.context_role == "carrier"
    assert role.actor_id is None
    assert {
        _contract_id(item.contract.namespace, item.contract.name, item.contract.version)
        for item in role.capabilities
    } == {"object.relocate@v1"}
    assert (
        _contract_id(
            role.execution.operation.namespace,
            role.execution.operation.name,
            role.execution.operation.version,
        )
        == "object.relocate@v1"
    )
    assert dict(role.execution.parameters) == {
        "destination": "reception-1f",
        "object": "first-aid-kit-2f",
        "source": "storage-room-2f",
    }


def test_front_half_fixture_separates_constraints_estimates_and_satisfaction() -> None:
    """The Plan carries Mission constraints and goal evidence without deployment guesses."""
    raw = _raw_plan()
    tasks = cast(list[JSONObject], raw["tasks"])
    task_json = tasks[0]
    roles = cast(list[JSONObject], task_json["roles"])
    role_json = roles[0]
    timing_json = cast(JSONObject, task_json["timing"])
    plan = MissionPlan.from_json(raw)
    task = plan.tasks[0]

    assert "actor" not in role_json
    assert "capability" not in role_json
    assert "contract" not in role_json
    assert "estimated_duration_ms" not in timing_json
    assert not any(key in raw for key in ("nodes", "inventory", "provider_availability"))
    assert task.timing is not None
    assert task.timing.estimated_duration_ms is None
    assert task.satisfaction.basis is TaskSatisfactionBasis.VERIFIER_EVIDENCE
    assert task.satisfaction.verifier is not None
    assert task.satisfaction.expected_effect != task.roles[0].execution.objective
