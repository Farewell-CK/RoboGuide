"""Keep bounded preparation feedback separate from model errors and physical execution."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from habitat_local_eaios import navigation_dispatch, view_recovery_binding, view_scan  # noqa: E402
from habitat_local_eaios.model import IntegrationError  # noqa: E402
from habitat_local_eaios.navigation_dispatch import NavigationDispatchLifecycle  # noqa: E402
from habitat_local_eaios.stage2_contract import (  # noqa: E402
    Stage2ExecutionContract,
    install_stage2_contract_guard,
)
from habitat_local_eaios.stage2_feedback import (  # noqa: E402
    Stage2ExecutionFeedback,
    Stage2PreparationRejected,
)
from habitat_local_eaios.view_recovery import (  # noqa: E402
    ObservationRecoveryUnavailable,
    preparation_reselection_declaration,
    validate_preparation_reselection,
)
from habitat_local_eaios.view_recovery_binding import ObservationViewpointRecovery  # noqa: E402
from habitat_local_eaios.view_scan import ViewScanBinding  # noqa: E402
from test_navigation_dispatch import OracleCommand  # noqa: E402
from test_observation_control import ObservationModel, _control, _sample  # noqa: E402
from test_stage2_feedback import VendorSkill  # noqa: E402
from test_view_motion import original_action, quaternion  # noqa: E402
from test_view_recovery import Geometry  # noqa: E402
from test_view_recovery_flow import OriginalMappedAgent, RecoveryOracle  # noqa: E402
from test_view_scan import BASE_TYPES, controls, vector_width  # noqa: E402

SCAN = ("scan_for_object", {"target_obj": "marker:north"})
NAV = ("nav_to_obj", {"target_obj": "marker:north"})
WAIT: tuple[str, dict[str, Any]] = ("wait", {})


class Choices(ObservationModel):
    """Preserve a fixed sequence of raw fake model choices, including Provider exceptions."""

    def __init__(self, choices: list[Any]) -> None:
        """Freeze choices before the first call without manufacturing an environment outcome."""
        super().__init__()
        self.choices = deepcopy(choices)

    def chat(self, content: str, crab_planning: bool = False) -> Any:
        """Record each real fake-client call; never retry an exception inside the model."""
        selected = self.choices[len(self.requests)]
        if isinstance(selected, BaseException):
            self.error = selected
        else:
            self.next_action = deepcopy(selected)
        return super().chat(content, crab_planning)


def prepared(
    monkeypatch: pytest.MonkeyPatch,
    choices: list[Any],
    *,
    budget: int = 1,
    enabled: bool = True,
    record_fails: bool = False,
) -> Any:
    """Bind the production Guard, feedback, original mapping and reversible recovery hooks."""
    monkeypatch.setattr(view_scan, "_original_base_types", lambda: BASE_TYPES)
    monkeypatch.setattr(view_scan, "_original_vector_width", vector_width)
    monkeypatch.setattr(view_scan, "_skill_factory", lambda *args: VendorSkill())
    monkeypatch.setattr(navigation_dispatch, "_original_action_type", lambda: OracleCommand)
    monkeypatch.setattr(view_recovery_binding, "NativeViewpointGeometry", Geometry)
    world, policy = controls()
    world.task.actions[
        "agent_0_base_velocity"
    ].cur_articulated_agent = original_action().cur_articulated_agent
    navigation = RecoveryOracle(("marker:north",))
    peer = RecoveryOracle(("peer:target",))
    world.task.actions["agent_0_oracle_nav_action"] = navigation
    world.task.actions["agent_1_oracle_nav_action"] = peer
    world.sim.ctrl_freq = 30.0
    control = _control()
    control.update(_sample(control, 10, False))
    rows: list[dict[str, Any]] = []

    def record(row: dict[str, Any]) -> None:
        """Either retain observational evidence or reproduce an independent writer failure."""
        if record_fails:
            raise OSError("observation archive unavailable")
        rows.append(deepcopy(row))

    scan = ViewScanBinding(world, policy, 0, control, record)
    recovery = ObservationViewpointRecovery(navigation, scan.spec, control, record)
    contract = Stage2ExecutionContract.for_invocation(control.invocation, allow_view_scan=True)
    dispatch = NavigationDispatchLifecycle(world, 0, contract, record, view_recovery=recovery)
    feedback = Stage2ExecutionFeedback(
        {"agent_0": contract},
        record,
        observation_control=control,
        view_scan=scan,
        navigation_dispatch=dispatch,
        preparation_reselection=enabled,
    )
    agent, model = OriginalMappedAgent("agent_0"), Choices(choices)
    agent.llm_model = model
    restore = install_stage2_contract_guard(
        [agent],
        {agent.name: contract},
        record,
        feedback=feedback,
        recovery_attempts=budget,
    )
    return SimpleNamespace(
        agent=agent,
        model=model,
        feedback=feedback,
        recovery=recovery,
        scan=scan,
        policy=policy,
        rows=rows,
        restore=restore,
        navigation=navigation,
        peer=peer,
        peer_before=dict(vars(peer)),
        base=world.task.actions["agent_0_base_velocity"],
        control=control,
    )


def stationary_scan(state: Any) -> None:
    """Observe a completed fake physical step with zero actual scan rotation."""
    assert state.agent.chat("unchanged task and observation")["name"] == "scan_for_object"
    state.policy._cur_skills = [state.scan.index]
    state.base.cur_articulated_agent.sim_obj.rotation = quaternion(0.0)
    state.feedback.physical_step()


def close(state: Any) -> None:
    """Always restore the original bound model, scan and navigation methods."""
    state.restore()
    state.feedback.close()
    assert not state.recovery._hooks
    assert vars(state.peer) == state.peer_before


@pytest.mark.parametrize("record_fails", [False, True])
def test_search_miss_returns_facts_to_model_without_rewriting_action(
    monkeypatch: pytest.MonkeyPatch,
    record_fails: bool,
) -> None:
    """Only a fresh chosen wait follows the refused navigation; no failure becomes success."""
    state = prepared(monkeypatch, [SCAN, NAV, WAIT], record_fails=record_fails)
    try:
        stationary_scan(state)
        state.navigation.scan_allowed = False
        result = state.agent.chat("retain the full original task")
        assert result == {"name": "wait", "arguments": ["16"]}
        assert len(state.model.requests) == 3
        correction = state.model.requests[-1]["content"]
        assert "bounded-viewpoint-search-miss" in correction
        assert "local-capability-preparation" in correction
        assert '"action_executed": false' in correction
        assert '"remaining_recoveries": 0' in correction
        receipt = state.model.chat_history[1][-1]
        rejected = json.loads(receipt["content"])
        assert rejected["status"] == "preparation-rejected"
        assert rejected["tool_name"] == "nav_to_obj"
        assert rejected["action_executed"] is False
        assert rejected["physical_steps_since_call"] == 0
        assert state.navigation.prev_nav_done and state.navigation.skill_done
        assert state.recovery._used == 1
        assert state.control.snapshot()["remaining_simulator_steps"] == 256
        if not record_fails:
            rows = [r for r in state.rows if r.get("decision") == "reselection_requested"]
            assert len(rows) == 1 and rows[0]["rejected_action"] == {
                "name": NAV[0],
                "arguments": NAV[1],
            }
    finally:
        close(state)


@pytest.mark.parametrize(
    "budget,enabled,error_type",
    [
        (0, True, Stage2PreparationRejected),
        (2, False, ObservationRecoveryUnavailable),
    ],
)
def test_zero_budget_and_legacy_profile_do_not_reselect(
    monkeypatch: pytest.MonkeyPatch,
    budget: int,
    enabled: bool,
    error_type: type[Exception],
) -> None:
    """Opt-out and exhausted original budgets preserve a real refusal without extra calls."""
    state = prepared(monkeypatch, [SCAN, NAV, WAIT], budget=budget, enabled=enabled)
    try:
        stationary_scan(state)
        state.navigation.scan_allowed = False
        with pytest.raises(error_type):
            state.agent.chat("unchanged task")
        assert len(state.model.requests) == 2
        assert state.navigation.prev_nav_done and state.recovery._used == 1
    finally:
        close(state)


def test_contract_and_preparation_rejections_share_one_attempt_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A prior wrong-entity rejection cannot borrow another preparation recovery budget."""
    wrong = ("nav_to_obj", {"target_obj": "marker:other"})
    state = prepared(monkeypatch, [SCAN, wrong, NAV, WAIT], budget=1)
    try:
        stationary_scan(state)
        state.navigation.scan_allowed = False
        with pytest.raises(Stage2PreparationRejected):
            state.agent.chat("unchanged task")
        assert len(state.model.requests) == 3
        assert state.recovery._used == 1
    finally:
        close(state)


def test_viewpoint_search_limit_is_not_renewed_by_model_reselection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two misses consume the original search quota; no third search or fifth model call runs."""
    state = prepared(monkeypatch, [SCAN, NAV, NAV, NAV, WAIT], budget=3)
    try:
        stationary_scan(state)
        state.navigation.scan_allowed = False
        with pytest.raises(ObservationRecoveryUnavailable, match="attempt budget"):
            state.agent.chat("unchanged task")
        assert len(state.model.requests) == 4
        assert state.recovery._used == 2
        assert state.navigation.prev_nav_done and state.navigation.skill_done
    finally:
        close(state)


def test_reselection_budget_is_not_renewed_after_a_valid_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later model-selected scan does not reset the canonical attempt's spent recovery."""
    state = prepared(monkeypatch, [SCAN, NAV, WAIT, SCAN, NAV, WAIT], budget=1)
    try:
        stationary_scan(state)
        state.navigation.scan_allowed = False
        assert state.agent.chat("first rejection")["name"] == "wait"
        stationary_scan(state)
        with pytest.raises(Stage2PreparationRejected):
            state.agent.chat("second rejection")
        assert len(state.model.requests) == 5
        assert state.recovery._used == 2
    finally:
        close(state)


def test_provider_error_after_preparation_feedback_propagates_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow or failed Provider is not converted into another local repair opportunity."""
    state = prepared(monkeypatch, [SCAN, NAV, TimeoutError("original provider timeout")], budget=3)
    try:
        stationary_scan(state)
        state.navigation.scan_allowed = False
        with pytest.raises(TimeoutError, match="original provider timeout"):
            state.agent.chat("unchanged task")
        assert len(state.model.requests) == 3
        assert state.recovery._used == 1
    finally:
        close(state)


def test_unknown_geometry_exception_is_not_a_model_reselection_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the explicit bounded-search miss is eligible, never an arbitrary internal error."""
    state = prepared(monkeypatch, [SCAN, NAV, WAIT], budget=3)
    try:
        stationary_scan(state)

        def unavailable(*args: Any) -> Any:
            """Reproduce a failed original geometry reader without producing a candidate."""
            raise RuntimeError("original geometry unavailable")

        monkeypatch.setattr(view_recovery_binding, "NativeViewpointGeometry", unavailable)
        with pytest.raises(ObservationRecoveryUnavailable) as caught:
            state.agent.chat("unchanged task")
        assert isinstance(caught.value.__cause__, RuntimeError)
        assert len(state.model.requests) == 2
    finally:
        close(state)


def test_valid_navigation_needs_no_reselection(monkeypatch: pytest.MonkeyPatch) -> None:
    """The successful original mapping and admitted local point keep their existing path."""
    state = prepared(monkeypatch, [SCAN, NAV, WAIT], budget=2)
    try:
        stationary_scan(state)
        result = state.agent.chat("unchanged task")
        assert result == {
            "name": "nav_to_obj",
            "arguments": {
                "target_obj": "marker:north",
                "robot": "agent_0",
            },
        }
        assert len(state.model.requests) == 2 and state.recovery._used == 1
        assert not state.navigation.prev_nav_done and not state.navigation.skill_done
        assert not [r for r in state.rows if r.get("decision") == "reselection_requested"]
    finally:
        close(state)


def test_only_the_exact_owned_zero_step_rejection_can_request_reselection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A copied, changed or physically advanced refusal cannot borrow an earlier rejection."""
    state = prepared(monkeypatch, [SCAN, NAV], budget=0)
    try:
        stationary_scan(state)
        state.navigation.scan_allowed = False
        with pytest.raises(Stage2PreparationRejected) as caught:
            state.agent.chat("unchanged task")
        rejection = caught.value
        assert state.feedback.owns_preparation_rejection(rejection)
        other = Stage2PreparationRejected(
            rejection.agent_name,
            rejection.action,
            rejection.reason,
            rejection.invocation_digest,
            rejection.tool_call_id,
            rejection.progress,
        )
        assert not state.feedback.owns_preparation_rejection(other)
        state.feedback.physical_step()
        assert not state.feedback.owns_preparation_rejection(rejection)
        assert len(state.model.requests) == 2
    finally:
        close(state)


@pytest.mark.parametrize(
    "field,value",
    [
        ("profile", "invented"),
        ("eligible_failure", "any-error"),
        ("boundary", "after-physics"),
        ("failure_owner", "model"),
        ("reselection_budget", "per-call-renewed"),
        ("search_budget", "renew-on-model-call"),
        ("requires_exact_invocation_and_call", False),
        ("requires_zero_physical_steps", 1),
        ("provider_errors_retried", True),
        ("unknown_errors_retried", True),
        ("model_actions_rewritten", 0),
        ("official_success_authority", "local-skill"),
        ("new-authority", True),
    ],
)
def test_independent_readers_refuse_broadened_or_coerced_preparation_profiles(
    field: str,
    value: Any,
) -> None:
    """Both actual readers retain strict authority, types and the original scope boundary."""
    from roboguide_eval.b1_deployment_feasibility import (  # type: ignore[import-untyped]
        _validate_preparation_feedback,
    )

    declared = preparation_reselection_declaration()
    validate_preparation_reselection(declared)
    _validate_preparation_feedback(declared)
    declared[field] = value
    with pytest.raises(ValueError, match="preparation feedback"):
        _validate_preparation_feedback(declared)
    with pytest.raises(IntegrationError, match="preparation feedback"):
        validate_preparation_reselection(declared)
