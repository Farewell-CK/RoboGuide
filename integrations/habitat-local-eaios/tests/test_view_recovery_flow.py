"""Exercise real Guard, feedback and navigation binding without a model or simulator."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from habitat_local_eaios import navigation_dispatch, view_recovery_binding, view_scan  # noqa: E402
from habitat_local_eaios.navigation_dispatch import NavigationDispatchLifecycle  # noqa: E402
from habitat_local_eaios.stage2_contract import (  # noqa: E402
    Stage2ContractViolation,
    Stage2ExecutionContract,
    install_stage2_contract_guard,
)
from habitat_local_eaios.stage2_feedback import Stage2ExecutionFeedback  # noqa: E402
from habitat_local_eaios.view_recovery_binding import ObservationViewpointRecovery  # noqa: E402
from habitat_local_eaios.view_scan import ViewScanBinding  # noqa: E402
from test_navigation_dispatch import OracleCommand  # noqa: E402
from test_observation_control import ObservationModel, _control, _sample  # noqa: E402
from test_stage2_feedback import VendorAgent, VendorSkill  # noqa: E402
from test_view_motion import original_action, quaternion  # noqa: E402
from test_view_recovery import Geometry  # noqa: E402
from test_view_scan import BASE_TYPES, controls, vector_width  # noqa: E402


class RecoveryOracle(OracleCommand):
    """Declare the map fixture attributes without extending any original physical authority."""

    scan_allowed = True
    route_available = True
    target_changed = False


class OriginalMappedAgent(VendorAgent):
    """Use CrabAgent's original known-tool injection and unknown-tool passthrough."""

    def chat(self, observation: str) -> dict[str, Any]:
        """Preserve the raw scan; inject robot only for the original physical tool names."""
        name, parameters = self.llm_model.chat(observation)
        if name in {"wait", "send_request"}:
            result = {"name": "wait", "arguments": ["500"]}
        else:
            if name in {"nav_to_obj", "nav_to_goal", "nav_to_robot", "pick", "place"}:
                parameters["robot"] = self.name
            result = {"name": name, "arguments": parameters}
        self.dispatches.append(result)
        return result


@pytest.mark.parametrize("rotation", [None, 0.0, 0.2])
def test_only_new_guarded_navigation_after_actual_stationarity_selects_a_viewpoint(
    monkeypatch: pytest.MonkeyPatch,
    rotation: float | None,
) -> None:
    """Moving/unknown scans keep ordinary navigation; only actual stationarity triggers search."""
    monkeypatch.setattr(view_scan, "_original_base_types", lambda: BASE_TYPES)
    monkeypatch.setattr(view_scan, "_original_vector_width", vector_width)
    monkeypatch.setattr(view_scan, "_skill_factory", lambda *args: VendorSkill())
    monkeypatch.setattr(navigation_dispatch, "_original_action_type", lambda: OracleCommand)
    monkeypatch.setattr(view_recovery_binding, "NativeViewpointGeometry", Geometry)
    world, policy = controls()
    base = world.task.actions["agent_0_base_velocity"]
    base.cur_articulated_agent = original_action().cur_articulated_agent
    nav = RecoveryOracle()
    nav.scan_allowed, nav.route_available, nav.target_changed = True, True, False
    world.task.actions["agent_0_oracle_nav_action"] = nav
    peer = OracleCommand()
    peer_before = dict(vars(peer))
    world.task.actions["agent_1_oracle_nav_action"] = peer
    control = _control()
    control.update(_sample(control, 10, False))
    rows: list[dict[str, Any]] = []
    scan = ViewScanBinding(world, policy, 0, control, rows.append)
    recovery = ObservationViewpointRecovery(nav, scan.spec, control, rows.append)
    contract = Stage2ExecutionContract.for_invocation(control.invocation, allow_view_scan=True)
    dispatch = NavigationDispatchLifecycle(world, 0, contract, rows.append, view_recovery=recovery)
    feedback = Stage2ExecutionFeedback(
        {"agent_0": contract},
        rows.append,
        observation_control=control,
        view_scan=scan,
        navigation_dispatch=dispatch,
    )
    agent, model = OriginalMappedAgent("agent_0"), ObservationModel()
    agent.llm_model = model
    restore = install_stage2_contract_guard(
        [agent], {agent.name: contract}, rows.append, feedback=feedback
    )
    try:
        model.next_action = ("scan_for_object", {"target_obj": "marker:north"})
        selected_scan = agent.chat("choose a tool")
        assert selected_scan["name"] == "scan_for_object" and recovery._used == 0
        policy._cur_skills = [scan.index]
        if rotation is None:
            base.cur_articulated_agent.sim_obj.rotation = SimpleNamespace(
                vector=(0, 0, 0), scalar=None
            )
        else:
            base.cur_articulated_agent.sim_obj.rotation = quaternion(rotation)
        feedback.physical_step()
        assert not any(row.get("event") == "observation-viewpoint-selection" for row in rows)
        model.next_action = ("nav_to_obj", {"target_obj": "marker:south"})
        with pytest.raises(Stage2ContractViolation, match="canonical destination"):
            agent.chat("same operation input")
        assert recovery._used == 0 and nav.prev_nav_done is True
        model.next_action = ("nav_to_obj", {"target_obj": "marker:north"})
        selected_nav = agent.chat("same operation input")
        assert selected_nav == {
            "name": "nav_to_obj",
            "arguments": {"target_obj": "marker:north", "robot": "agent_0"},
        }
        assert recovery._used == (1 if rotation == 0.0 else 0)
        assert nav.prev_nav_done is False and nav.skill_done is False
        assert nav._targets == {0: ("original-navigation-point", "original-object-point")}
        assert vars(peer) == peer_before
        assert len(model.requests) == 3  # No retry or hidden Provider call.
        assert control.snapshot()["remaining_simulator_steps"] == 256
        tool = next(item for item in model.offered[-1] if item["name"] == "nav_to_obj")
        assert tool["parameters"]["properties"]["target_obj"]["enum"] == ["marker:north"]
        assert "original scan-footprint checks" in tool["description"]
    finally:
        restore()
        feedback.close()
    assert "_get_target_for_idx" not in vars(nav) and "_path_to_point" not in vars(nav)
    assert not scan.motion_feedback("unrelated-call", 100)
