"""Contract tests for MissionPlan v0 parsing and graph invariants."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from mission.models import JSONObject, MissionPlan, MissionPlanError

FIXTURE = Path("scenarios/mvp-slice-v0.1/mission-plan.json")


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
        Path("contracts/mission/v0/mission-plan.schema.json").read_text(encoding="utf-8")
    )
    version = schema["properties"]["schema_version"]
    assert version == {"type": "string", "const": "roboguide.mission-plan/v0"}


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
