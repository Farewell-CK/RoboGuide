"""Observed-condition feedback, bounded pauses and unchanged non-observation dispatch."""

from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from habitat_local_eaios.model import (  # noqa: E402
    CanonicalObservationAcquisitionInvocation,
    IntegrationError,
)
from habitat_local_eaios.observation_control import (  # noqa: E402
    MAX_OBSERVATION_PAUSE_STEPS,
    ObservationAcquisitionControl,
)
from habitat_local_eaios.stage2_contract import (  # noqa: E402
    Stage2ContractViolation,
    Stage2ExecutionContract,
    install_stage2_contract_guard,
)
from habitat_local_eaios.stage2_feedback import Stage2ExecutionFeedback  # noqa: E402
from test_stage2_feedback import VendorAgent, VendorModel, _contract  # noqa: E402


def _control(max_steps: int = 256) -> ObservationAcquisitionControl:
    """Create an exact generic acquisition attempt with an immutable original budget."""
    invocation = CanonicalObservationAcquisitionInvocation(
        mission_id="inspection",
        task_id="observe-marker",
        group_id="inspection-group",
        role_id="observer",
        operation="observation.acquire@v1",
        objective="Acquire visual detection of the admitted marker.",
        parameters={"expected": "detected(marker:north)"},
        resource_ids=("observer-space",),
        attempt_id="original-attempt",
    )
    return ObservationAcquisitionControl(invocation, 0, 10, max_steps, "sha256:" + "a" * 64)


def _sample(
    control: ObservationAcquisitionControl, step: int, holds: bool | None
) -> dict[str, Any]:
    """Represent one already-returned cache sample, without invoking sensors or physics."""
    return {
        "invocation_digest": "sha256:" + control.invocation.request_key(),
        "authoritative_semantic_evidence_digest": control.semantic_digest,
        "agent_id": control.agent_id,
        "condition": control.invocation.parameters["expected"],
        "source": "existing-gym-observation",
        "sensor_scope": "agent",
        "sensor_uuid": "agent_0_detected_objects",
        "simulator_steps": step,
        "status": "unavailable" if holds is None else "observed",
        "condition_holds": holds,
    }


class ObservationModel(VendorModel):
    """Retain the exact tool declarations offered with each scripted original response."""

    def __init__(self) -> None:
        """Expose original-shaped navigation, pause and unauthorized manipulation tools."""
        super().__init__()
        self.actions.extend(
            [
                {"name": "wait", "parameters": {"type": "object", "properties": {}}},
                {"name": "pick", "parameters": {"type": "object", "properties": {}}},
            ]
        )
        self.offered: list[Any] = []

    def chat(self, content: str, crab_planning: bool = False) -> Any:
        """Record actual model input/schema and preserve the original raw result unchanged."""
        self.offered.append(deepcopy(self.actions))
        return super().chat(content, crab_planning)


@pytest.mark.parametrize("holds", [True, False, None])
def test_feedback_preserves_true_false_and_unavailable_before_the_first_call(
    holds: bool | None,
) -> None:
    """A first model decision sees current evidence and budget without any previous tool."""
    control = _control()
    control.update(_sample(control, 10, holds))
    contract = Stage2ExecutionContract.for_invocation(control.invocation)
    rows: list[dict[str, Any]] = []
    feedback = Stage2ExecutionFeedback(
        {"observer": contract}, rows.append, observation_control=control
    )
    content = feedback.before_call("observer", object(), "Current scene")
    evidence = json.loads(content.split("Current operation observation:\n", 1)[1])
    assert evidence["sample"]["condition_holds"] is holds
    assert evidence["sample"]["status"] == ("unavailable" if holds is None else "observed")
    assert evidence["remaining_simulator_steps"] == 256
    assert evidence["condition_persistence_guaranteed"] is False
    assert evidence["local_completion_is_official_success"] is False
    evidence["sample"]["condition_holds"] = "changed by caller"
    assert control.snapshot()["sample"]["condition_holds"] is holds
    assert rows[0]["event"] == "operation-observation-input"


@pytest.mark.parametrize(
    "field,value",
    [
        ("invocation_digest", "sha256:" + "b" * 64),
        ("authoritative_semantic_evidence_digest", "sha256:" + "b" * 64),
        ("agent_id", 1),
        ("agent_id", False),
        ("condition", "detected(marker:south)"),
        ("source", "invented"),
        ("simulator_steps", 9),
        ("simulator_steps", True),
        ("status", "success"),
        ("condition_holds", "true"),
    ],
)
def test_detached_or_malformed_feedback_cannot_replace_the_current_sample(
    field: str,
    value: Any,
) -> None:
    """Wrong attempt/world/condition and invalid readings fail before further model work."""
    control = _control()
    control.update(_sample(control, 12, False))
    original = control.snapshot()
    wrong = _sample(control, 12, True)
    wrong[field] = value
    with pytest.raises(IntegrationError, match="malformed, stale or detached"):
        control.update(wrong)
    assert control.snapshot() == original
    with pytest.raises(IntegrationError):
        control.update(_sample(control, 11, True))
    with pytest.raises(IntegrationError, match="conflicting observation"):
        control.update(_sample(control, 12, True))
    control.update(_sample(control, 12, False))
    assert control.snapshot() == original


def test_disclosed_pause_mapping_returns_control_within_the_original_budget() -> None:
    """The same raw wait becomes a declared 16-step pause; final budget clips without renewal."""
    control = _control(40)
    control.update(_sample(control, 10, False))
    agent = VendorAgent()
    model = ObservationModel()
    agent.llm_model = model
    model.next_action = ("wait", {})
    original_tools = deepcopy(model.actions)
    contract = Stage2ExecutionContract.for_invocation(control.invocation)
    rows: list[dict[str, Any]] = []
    feedback = Stage2ExecutionFeedback(
        {agent.name: contract}, rows.append, observation_control=control
    )
    restore = install_stage2_contract_guard(
        [agent], {agent.name: contract}, rows.append, feedback=feedback
    )
    try:
        for elapsed, expected_pause in ((0, 16), (16, 16), (32, 8)):
            control.update(_sample(control, 10 + elapsed, False))
            result = agent.chat("Choose the next local action")
            assert result == {"name": "wait", "arguments": [str(expected_pause)]}
            offered = model.offered[-1]
            assert {tool["name"] for tool in offered} == {"nav_to_obj", "wait"}
            wait = next(tool for tool in offered if tool["name"] == "wait")
            assert f"pauses for {expected_pause} simulator steps" in wait["description"]
            assert model.chat_history[-1][-1]["name"] == "wait"
            raw_call = model.chat_history[-1][1].tool_calls[0]
            assert raw_call.function.name == "wait" and raw_call.function.arguments == "{}"
            assert model.actions == original_tools
        assert len(model.requests) == 3
        assert control.max_steps == 40
        control.update(_sample(control, 50, False))
        with pytest.raises(IntegrationError, match="budget exhausted"):
            agent.chat("No budget remains")
        assert len(model.requests) == 3
        dispatches = [row for row in rows if row.get("event") == "local-pause-dispatch-bound"]
        assert len(dispatches) == 3
        assert dispatches[0]["original_vendor_mapping"]["arguments"] == ["500"]
    finally:
        restore()
        feedback.close()


def test_navigation_without_acquisition_keeps_the_original_wait_duration() -> None:
    """The observation profile does not alter navigation, relocation or idle execution."""
    agent = VendorAgent()
    agent.llm_model.next_action = ("wait", {})
    contract = _contract()
    feedback = Stage2ExecutionFeedback({agent.name: contract}, lambda row: None)
    restore = install_stage2_contract_guard(
        [agent], {agent.name: contract}, lambda row: None, feedback=feedback
    )
    try:
        assert agent.chat("Wait for the next instruction") == {
            "name": "wait",
            "arguments": ["500"],
        }
    finally:
        restore()
        feedback.close()


def test_observation_guard_still_rejects_wrong_targets_and_manipulation() -> None:
    """Narrowed tools and new feedback grant neither alternate destinations nor pick/place."""
    control = _control()
    control.update(_sample(control, 10, False))
    contract = Stage2ExecutionContract.for_invocation(control.invocation)
    for action in (
        {"name": "nav_to_obj", "arguments": {"target_obj": "marker:south"}},
        {"name": "pick", "arguments": {"target_obj": "marker:north"}},
    ):
        with pytest.raises(Stage2ContractViolation):
            contract.validate("observer", action, frozenset())
    assert control.pause_steps() == MAX_OBSERVATION_PAUSE_STEPS


def test_feedback_cannot_bind_a_different_operation_or_attempt() -> None:
    """A valid observation sample does not authorize another contract's model feedback."""
    control = _control()
    with pytest.raises(IntegrationError, match="exactly its own acquisition"):
        Stage2ExecutionFeedback(
            {"observer": _contract()}, lambda row: None, observation_control=control
        )


@pytest.mark.parametrize("mutation", [None, "pause", "persistence", "extra", "schema"])
def test_new_local_how_profile_is_checked_without_weakening_legacy_evidence(
    tmp_path: Path,
    mutation: str | None,
) -> None:
    """Actual run-local source validation accepts only the exact disclosed feedback profile."""
    from habitat_local_eaios.observation_control import observation_control_declaration
    from roboguide_eval.b1_deployment_feasibility import (  # type: ignore[import-untyped]
        preflight_deployment_feasibility,
    )
    from test_live_relocation_sources import live_relocation_run

    run = live_relocation_run(tmp_path, version=6, recoveries=2)
    how_path = run / "evidence/local-how-profile.json"
    how = json.loads(how_path.read_bytes())
    how.update(
        schema_version="roboguide.habitat-local-how-profile/v0.11",
        observation_control=observation_control_declaration(),
    )
    how["execution_options"]["enable_observation_acquisition"] = True
    deployment_path = run / "b1-deployment-used.json"
    declaration = json.loads(deployment_path.read_bytes())
    declaration["declaration"]["enable_observation_acquisition"] = True
    deployment_path.write_text(json.dumps(declaration))
    requirement_path = run / "b1-local-execution-profile-required.json"
    requirement = json.loads(requirement_path.read_bytes())
    requirement["deployment_sha256"] = hashlib.sha256(deployment_path.read_bytes()).hexdigest()
    requirement_path.write_text(json.dumps(requirement))
    if mutation == "pause":
        how["observation_control"]["max_pause_simulator_steps"] = 500
    elif mutation == "persistence":
        how["observation_control"]["condition_persistence_guaranteed"] = True
    elif mutation == "extra":
        how["observation_control"]["hidden_motion"] = True
    elif mutation == "schema":
        how["schema_version"] = "roboguide.habitat-local-how-profile/v0.10"
    how_path.write_text(json.dumps(how))
    if mutation is None:
        preflight_deployment_feasibility(run)
    else:
        with pytest.raises(ValueError, match="observation feedback|Local How"):
            preflight_deployment_feasibility(run)
