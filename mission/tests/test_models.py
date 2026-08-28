"""Contract tests for MissionPlan v0.2 parsing and graph invariants."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from mission.models import JSONObject, MissionPlan, MissionPlanError

FIXTURE = Path("scenarios/phase1-mission-v0.2/mission-plan.json")


def _fixture_json() -> JSONObject:
    """Load a mutable copy of the approved MVP Mission Plan fixture."""
    decoded = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return cast(JSONObject, decoded)


def test_valid_fixture_round_trips() -> None:
    """The approved fixture must parse and serialize without contract drift."""
    raw = _fixture_json()
    assert MissionPlan.from_json(raw).to_json() == raw


def test_schema_version_declares_string_type_for_strict_providers() -> None:
    """Strict Responses providers require a type beside the version const constraint."""
    schema = json.loads(
        Path("contracts/mission/v0.2/mission-plan.schema.json").read_text(encoding="utf-8")
    )
    version = schema["properties"]["schema_version"]
    assert version == {"type": "string", "const": "roboguide.mission-plan/v0.2"}


def test_structured_execution_parameter_is_rejected() -> None:
    """Mission Plan v0.2 accepts only scalar transport-neutral parameters."""
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
