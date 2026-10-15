"""Check semantic prerequisite authority boundaries without starting a Node or provider."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import cast

import pytest
from mission.models import JSONObject, MissionPlan, MissionPlanError


def plan() -> JSONObject:
    """Adapt the maintained semantic contract fixture; this is metadata, not device evidence."""
    result = cast(
        JSONObject,
        json.loads(Path("scenarios/mission-front-half-v0.7/mission-plan.json").read_bytes()),
    )
    result["schema_version"] = "roboguide.mission-plan/v0.9"
    for context in cast(list[JSONObject], result["contexts"]):
        context["executor_constraints"] = []
    for task in cast(list[JSONObject], result["tasks"]):
        for role in cast(list[JSONObject], task["roles"]):
            cast(JSONObject, role["requirements"])["memories"] = []
    requirements(result)["memories"] = [
        {
            "selector": {"memory_id": "declared-content", "revision_id": "immutable"},
            "kind": "artifact",
            "payload_schema": "opaque-file/v1",
            "media_type": "application/octet-stream",
        }
    ]
    return result


def requirements(document: JSONObject) -> JSONObject:
    """Return the first logical role's semantic requirements for bounded mutation checks."""
    task = cast(list[JSONObject], document["tasks"])[0]
    role = cast(list[JSONObject], task["roles"])[0]
    return cast(JSONObject, role["requirements"])


def test_memory_prerequisites_survive_parse_checkpoint_and_round_trip() -> None:
    """A model-facing semantic selector remains distinct from a Control-selected receiver."""
    original = plan()
    admitted = MissionPlan.from_json(original)
    assert admitted.to_json() == original
    memory = admitted.tasks[0].roles[0].memories[0]
    assert (memory.memory_id, memory.revision_id) == ("declared-content", "immutable")
    assert not hasattr(memory, "consumer_provider_id")
    assert not hasattr(memory, "node_id")
    assert MissionPlan.from_json(admitted.to_json()) == admitted


@pytest.mark.parametrize("version", ["v0.7", "v0.8"])
def test_old_plan_versions_cannot_smuggle_prerequisites(version: str) -> None:
    """Old contracts cannot silently discard a gate that would have prevented Execute."""
    document = plan()
    document["schema_version"] = "roboguide.mission-plan/" + version
    with pytest.raises(MissionPlanError):
        MissionPlan.from_json(document)


@pytest.mark.parametrize(
    "case", ["provider", "node", "path", "wildcard", "duplicate", "foreign-selector", "kind"]
)
def test_semantic_memory_rejects_inventory_and_ambiguous_inputs(case: str) -> None:
    """Input validation cannot turn a guessed provider, path or duplicate into authority."""
    document = plan()
    memory = cast(list[JSONObject], requirements(document)["memories"])[0]
    if case == "provider":
        memory["consumer_provider_id"] = "invented-selection"
    elif case == "node":
        memory["node_id"] = "invented-selection"
    elif case == "path":
        cast(JSONObject, memory["selector"])["memory_id"] = "../outside"
    elif case == "wildcard":
        memory["payload_schema"] = "*"
    elif case == "duplicate":
        cast(list[JSONObject], requirements(document)["memories"]).append(copy.deepcopy(memory))
    elif case == "foreign-selector":
        cast(JSONObject, memory["selector"])["consumer_provider_id"] = "hidden-owner"
    else:
        memory["kind"] = "fabricated-device-kind"
    with pytest.raises(MissionPlanError):
        MissionPlan.from_json(document)
