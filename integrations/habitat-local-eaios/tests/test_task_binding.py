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
        agent_id=0,
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


@pytest.mark.parametrize("missing_resume", [False, True])
def test_verified_deployment_identity_binds_slots_without_synthesizing_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing_resume: bool
) -> None:
    """Verified loaded identities bind exact assignments when the vendor resume is incomplete."""
    utils = types.ModuleType("habitat_mas.utils")
    utils.AgentArguments = FakeAgentArguments  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "habitat_mas.utils", utils)
    runtime = _runtime("entity-grounded")
    runtime._config.evidence_dir = tmp_path
    runtime._relocation_agent_identity = {
        "registration_profile_digest": "sha256:" + "a" * 64,
        "robot_types": {"agent_0": "FetchRobot", "agent_1": "StretchRobot"},
    }
    resumes = {"agent_0": {"robot_type": "FetchRobot"}}
    if not missing_resume:
        resumes["agent_1"] = {"robot_type": "StretchRobot"}
    context = {"robot_resume": json.dumps(resumes)}
    original = dict(context)
    invocations = _invocations()
    pair = runtime._pair_arguments(context, invocations)
    for index, robot_type in enumerate(("FetchRobot", "StretchRobot")):
        assert pair[f"agent_{index}"].values["robot_type"] == robot_type
        assert (
            pair[f"agent_{index}"].values["subtask_description"]
            == f"Navigate to {invocations[index].destination}."
        )
    runtime._config.agent_id = 1
    serial = runtime._assigned_arguments(context, invocations[1])
    assert serial["agent_0"].values["subtask_description"] == "Nothing to do"
    assert (
        serial["agent_1"].values["subtask_description"]
        == pair["agent_1"].values["subtask_description"]
    )
    evidence = json.loads((tmp_path / "stage2-agent-identity.json").read_text())
    assert evidence["capability_resume_synthesized"] is False
    assert evidence["unavailable_vendor_resume_agent_names"] == (
        ["agent_1"] if missing_resume else []
    )
    assert (
        evidence["registration_profile_digest"]
        == runtime._relocation_agent_identity["registration_profile_digest"]
    )
    assert context == original


@pytest.mark.parametrize(
    "resume",
    [
        {"agent_0": {"robot_type": "OtherRobot"}},
        {"agent_9": {"robot_type": "FetchRobot"}},
        {"agent_0": {"robot_type": None}},
    ],
)
def test_verified_deployment_identity_rejects_contradictory_vendor_identity(
    tmp_path: Path, resume: dict[str, Any]
) -> None:
    """A present contradictory identity is never silently overridden by the deployment profile."""
    runtime = _runtime("entity-grounded")
    runtime._config.evidence_dir = tmp_path
    runtime._relocation_agent_identity = {
        "registration_profile_digest": "sha256:" + "a" * 64,
        "robot_types": {"agent_0": "FetchRobot", "agent_1": "StretchRobot"},
    }
    with pytest.raises(IntegrationError, match="identity differs|lacks robot_type"):
        runtime._assignment_robot_types({"robot_resume": json.dumps(resume)})
    assert not (tmp_path / "stage2-agent-identity.json").exists()


def test_natural_objective_exposes_canonical_destination_to_stage2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stage2 sees the committed target even when the objective names several entities."""
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
        assert values["subtask_description"].startswith(invocation.objective)
        assert (
            f'Assigned navigation destination for this execution: "{invocation.destination}".'
            in values["subtask_description"]
        )
        assert "use this exact entity as target_obj" in values["subtask_description"]


def test_single_actor_joint_objective_retains_its_one_committed_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A joint outcome cannot obscure the exact target of one serial execution."""
    utils = types.ModuleType("habitat_mas.utils")
    utils.AgentArguments = FakeAgentArguments  # type: ignore[attr-defined]
    package = types.ModuleType("habitat_mas")
    package.utils = utils  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "habitat_mas", package)
    monkeypatch.setitem(sys.modules, "habitat_mas.utils", utils)
    original = _invocations()[0]
    invocation = CanonicalMobilityInvocation(
        mission_id=original.mission_id,
        task_id=original.task_id,
        group_id=original.group_id,
        role_id=original.role_id,
        operation=original.operation,
        objective="Jointly satisfy pickup_marker|7 and delivery_marker|7.",
        parameters={"destination": "delivery_marker|7"},
        resource_ids=original.resource_ids,
    )
    assigned = _runtime("natural-objective")._assigned_arguments(_context(), invocation)
    text = assigned["agent_0"].values["subtask_description"]
    assert text.startswith(invocation.objective)
    assert 'Assigned navigation destination for this execution: "delivery_marker|7".' in text
    assert assigned["agent_1"].values["subtask_description"] == "Nothing to do"
