"""Deterministic checks for deployment-owned execution resource profiles."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import cast

import pytest
from mission.execution_profile import DeploymentExecutionProfile, ExecutionProfileError
from mission.models import JSONObject, JSONValue, MissionPlan, MissionPlanError

PROFILE = Path("scenarios/e1-shared-world-episode-51/execution-profile.json")
NODE_CONFIGS = (
    Path("scenarios/e1-shared-world-episode-51/node-a.toml"),
    Path("scenarios/e1-shared-world-episode-51/node-b.toml"),
)


@pytest.mark.parametrize(
    ("field", "value"),
    [("schema_version", "roboguide.execution-profile/v9"), ("node_id", "node-a")],
)
def test_execution_profile_rejects_invalid_schema_or_node_selection(
    field: str, value: JSONValue
) -> None:
    """A deployment file cannot silently change version or smuggle a Node selector."""
    raw = cast(JSONObject, json.loads(PROFILE.read_text(encoding="utf-8")))
    raw[field] = value
    with pytest.raises((ExecutionProfileError, MissionPlanError)):
        DeploymentExecutionProfile.from_json(raw)


def test_shared_world_profile_is_operation_scoped_and_node_neutral() -> None:
    """The MI profile carries operation minima without selecting endpoint identities."""
    raw = cast(JSONObject, json.loads(PROFILE.read_text(encoding="utf-8")))
    profile = DeploymentExecutionProfile.from_json(raw)

    assert profile.to_json()["schema_version"] == "roboguide.execution-profile/v0.1"
    assert [item.operation.name for item in profile.profiles] == ["move", "navigate"]
    assert all(
        resource.kind == "space" and resource.units == 1
        for item in profile.profiles
        for resource in item.resources
    )
    assert "resource_id" not in json.dumps(raw)
    assert "node_id" not in json.dumps(raw)


def test_shared_world_nodes_bind_move_and_navigate_to_their_space_slot() -> None:
    """Each endpoint exposes the profile's capacity fact through both mobility operations."""
    for path in NODE_CONFIGS:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        resources = {item["id"]: item for item in cast(list[dict[str, object]], raw["resources"])}
        assert len(resources) == 1
        resource_id, resource = next(iter(resources.items()))
        assert resource["kind"] == "space"
        assert resource["capacity"] == 1
        operations = {
            item["operation"]: item for item in cast(list[dict[str, object]], raw["operations"])
        }
        for operation in ("mobility.move@v1", "mobility.navigate@v1"):
            assert operations[operation]["required_resources"] == [resource_id]


def test_shared_world_profile_does_not_add_semantic_executor_constraints() -> None:
    """One actor can keep a two-step DAG while acquiring endpoint capacity per task."""
    raw = cast(
        JSONObject,
        json.loads(
            Path("scenarios/e1-shared-world-episode-51/mission-plan.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    raw["schema_version"] = "roboguide.mission-plan/v0.8"
    cast(JSONObject, raw["mission"])["actors"] = [{"id": "one-robot"}]
    for context in cast(list[JSONObject], raw["contexts"]):
        context["executor_constraints"] = []
        for role in cast(list[JSONObject], context["roles"]):
            role["actor"] = "one-robot"
    tasks = cast(list[JSONObject], raw["tasks"])
    tasks[1]["depends_on"] = [tasks[0]["id"]]
    for task in tasks:
        for role in cast(list[JSONObject], task["roles"]):
            cast(JSONObject, role["requirements"])["resources"] = []
    plan = MissionPlan.from_json(raw)
    profiled = DeploymentExecutionProfile.load(PROFILE).apply(plan)

    assert profiled.mission.actors == plan.mission.actors
    assert profiled.contexts == plan.contexts
    assert len(profiled.tasks) == len(plan.tasks)
    assert all(actor.physical_entity is None for actor in profiled.mission.actors)
    assert profiled.tasks[1].depends_on == plan.tasks[1].depends_on
    assert all(role.resource_scope == "task" for task in profiled.tasks for role in task.roles)
    assert all(role.resources for task in profiled.tasks for role in task.roles)
