"""Deterministic end-to-end binding checks for the E1 mobility adapter."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any, cast

import pytest
from mission.models import JSONObject, MissionPlan

INTEGRATION_ROOT = Path(__file__).parents[1]
REPO_ROOT = INTEGRATION_ROOT.parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.model import CanonicalMobilityInvocation, IntegrationError  # noqa: E402
from habitat_local_eaios.shared_world import SharedEmosStage2Runtime  # noqa: E402


class FakeAgentArguments:
    """Capture exactly what the shared-world adapter passes to EMOS Stage1."""

    def __init__(self, **values: Any) -> None:
        """Retain every assignment field without importing the EMOS package."""
        self.values = values


def _plan() -> MissionPlan:
    """Load the checked-in E1 plan used for destination-binding regression tests."""
    path = REPO_ROOT / "scenarios/e1-shared-world-episode-51/mission-plan.json"
    return MissionPlan.from_json(cast(JSONObject, json.loads(path.read_text(encoding="utf-8"))))


def _invocations() -> dict[int, CanonicalMobilityInvocation]:
    """Convert the two checked-in plan roles into committed invocation records."""
    plan = _plan()
    return {
        agent_id: CanonicalMobilityInvocation(
            mission_id=plan.mission.mission_id,
            task_id=task.task_id,
            group_id="group-task-binding",
            role_id=task.roles[0].role_id,
            operation=(
                f"{task.roles[0].execution.operation.namespace}."
                f"{task.roles[0].execution.operation.name}@"
                f"{task.roles[0].execution.operation.version}"
            ),
            objective=task.roles[0].execution.objective,
            parameters=dict(task.roles[0].execution.parameters),
            resource_ids=(f"node-{agent_id}-space",),
        )
        for agent_id, task in enumerate(plan.tasks)
    }


def _runtime(subtask_mode: str) -> SharedEmosStage2Runtime:
    """Create a shared runtime shell without importing Habitat or starting EMOS."""
    runtime = SharedEmosStage2Runtime.__new__(SharedEmosStage2Runtime)
    runtime._config = types.SimpleNamespace(
        subtask_mode=subtask_mode,
        episode_id="51",
    )
    runtime._agent_ids = (0, 1)
    return runtime


def _context() -> dict[str, str]:
    """Expose the same robot identity envelope as the deployed EMOS context."""
    return {
        "robot_resume": json.dumps(
            {
                "agent_0": {"robot_type": "SpotRobot"},
                "agent_1": {"robot_type": "FetchRobot"},
            }
        )
    }


def test_mission_plan_targets_are_bound_to_the_matching_agent_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each committed task keeps its own destination through the Stage1 boundary."""
    utils = types.ModuleType("habitat_mas.utils")
    utils.AgentArguments = FakeAgentArguments  # type: ignore[attr-defined]
    package = types.ModuleType("habitat_mas")
    package.utils = utils  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "habitat_mas", package)
    monkeypatch.setitem(sys.modules, "habitat_mas.utils", utils)

    invocations = _invocations()
    context = _context()
    context["robot_resume"] = json.dumps(
        {"agent_1": {"robot_type": "FetchRobot"}, "agent_0": {"robot_type": "SpotRobot"}}
    )
    assigned = _runtime("entity-grounded")._pair_arguments(context, invocations)

    assert set(assigned) == {"agent_0", "agent_1"}
    for agent_id, invocation in invocations.items():
        values = assigned[f"agent_{agent_id}"].values
        assert values["robot_id"] == f"agent_{agent_id}"
        assert values["robot_type"] == ("SpotRobot" if agent_id == 0 else "FetchRobot")
        assert values["task_description"] == invocation.objective
        assert values["subtask_description"] == f"Navigate to {invocation.destination}."
        assert invocation.destination in values["subtask_description"]


def test_binding_rejects_missing_or_unknown_agent_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    """The adapter never guesses a physical slot when the deployment envelope is incomplete."""
    utils = types.ModuleType("habitat_mas.utils")
    utils.AgentArguments = FakeAgentArguments  # type: ignore[attr-defined]
    package = types.ModuleType("habitat_mas")
    package.utils = utils  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "habitat_mas", package)
    monkeypatch.setitem(sys.modules, "habitat_mas.utils", utils)

    invocations = _invocations()
    with pytest.raises(IntegrationError, match="slots are unavailable"):
        _runtime("entity-grounded")._pair_arguments(
            {"robot_resume": json.dumps({"agent_0": {"robot_type": "SpotRobot"}})},
            invocations,
        )
    with pytest.raises(IntegrationError, match="no configured agent slot"):
        _runtime("entity-grounded")._pair_arguments(
            {
                "robot_resume": json.dumps(
                    {
                        "agent_0": {"robot_type": "SpotRobot"},
                        "agent_9": {"robot_type": "UnknownRobot"},
                    }
                )
            },
            invocations,
        )


def test_natural_objective_does_not_change_canonical_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Natural wording preserves the canonical objective while remaining separately auditable."""
    utils = types.ModuleType("habitat_mas.utils")
    utils.AgentArguments = FakeAgentArguments  # type: ignore[attr-defined]
    package = types.ModuleType("habitat_mas")
    package.utils = utils  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "habitat_mas", package)
    monkeypatch.setitem(sys.modules, "habitat_mas.utils", utils)

    invocations = _invocations()
    assigned = _runtime("natural-objective")._pair_arguments(_context(), invocations)
    for agent_id, invocation in invocations.items():
        values = assigned[f"agent_{agent_id}"].values
        assert values["task_description"] == invocation.objective
        assert values["subtask_description"] == invocation.objective
