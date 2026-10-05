"""Offline spatial control and actual action-dispatch regression tests."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.crabagent_backend import CrabAgentBackendConfig  # noqa: E402
from habitat_local_eaios.diagnostics import PhysicalDiagnostics  # noqa: E402
from habitat_local_eaios.emos_stage2 import _configure_goal_region_navigation  # noqa: E402
from habitat_local_eaios.goal_region_navigation import GoalRegionResolutionError  # noqa: E402
from habitat_local_eaios.model import IntegrationError  # noqa: E402
from habitat_local_eaios.spatial_navigation import (  # noqa: E402
    GOAL_AWARE_ARRIVAL_PROFILE,
    MAX_ROUTE_POINTS,
    SPATIAL_ARRIVAL_PROFILE,
    NavigationGoalRegion,
    SpatialNavigationDecision,
    spatial_navigation_decision,
)


def _decision(**changes: Any) -> SpatialNavigationDecision:
    """Supply a bounded neutral route with a next waypoint away from the final X/Z."""
    values: dict[str, Any] = {
        "position": (0.0, 0.0, 0.0),
        "final_point": (0.1, 3.0, 0.0),
        "entity_point": (0.1, 3.2, 0.0),
        "route_points": ((0.0, 0.0, 0.0), (3.0, 0.0, 0.0), (0.1, 3.0, 0.0)),
        "forward": (1.0, 0.0),
        "distance_threshold_m": 0.5,
        "turn_threshold_radians": 0.2,
    }
    return spatial_navigation_decision(**(values | changes))


def test_horizontal_proximity_on_another_level_continues_route() -> None:
    """Follow the actual next waypoint despite horizontal overlap with the destination."""
    result = _decision()
    assert result.horizontal_distance_m < 0.5 < result.distance_m
    assert result.vertical_distance_m == 3.0
    assert result.branch == "follow-route" and result.direction == (3.0, 0.0)


@pytest.mark.parametrize("forward, branch", [((1.0, 0.0), "arrived"), ((-1.0, 0.0), "face-entity")])
def test_spatial_arrival_retains_original_entity_heading(
    forward: tuple[float, float], branch: str
) -> None:
    """After true local proximity, preserve facing and the original threshold."""
    result = _decision(
        final_point=(0.1, 0.0, 0.0),
        entity_point=(0.3, 0.1, 0.0),
        route_points=((0.0, 0.0, 0.0), (0.1, 0.0, 0.0)),
        forward=forward,
    )
    assert result.branch == branch


def test_exact_distance_threshold_is_not_arrival() -> None:
    """Preserve the vendor's strict threshold rather than broaden completion."""
    result = _decision(final_point=(0.5, 0.0, 0.0), route_points=((0.0, 0.0, 0.0), (0.5, 0.0, 0.0)))
    assert result.branch == "follow-route"


def test_vertical_only_path_cannot_fake_planar_motion_or_success() -> None:
    """An unusable next waypoint fails rather than fabricating a base action."""
    with pytest.raises(GoalRegionResolutionError, match="no planar waypoint"):
        _decision(final_point=(0.0, 3.0, 0.0), route_points=((0.0, 0.0, 0.0), (0.0, 3.0, 0.0)))


@pytest.mark.parametrize(
    "changes",
    [
        {"distance_threshold_m": float("nan")},
        {"turn_threshold_radians": 0.0},
        {"position": (0.0, float("inf"), 0.0)},
        {"forward": (0.0, 0.0)},
        {"route_points": ()},
        {"route_points": ((0.0, 0.0, 0.0),)},
        {"route_points": ((0.1, 3.0, 0.0),) * (MAX_ROUTE_POINTS + 1)},
    ],
)
def test_invalid_or_unbounded_observations_fail_closed(changes: dict[str, Any]) -> None:
    """Malformed geometry and routes never enter a fabricated completion branch."""
    with pytest.raises(GoalRegionResolutionError):
        _decision(**changes)


def _action_module(monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, list[tuple[str, Any]]]:
    """Load the real adapter action with transport-free vendor motion spies."""
    commands: list[tuple[str, Any]] = []

    class OriginalAction:
        """Retain only the original immutable target and reset interfaces."""

        def _get_target_for_idx(self, index: int) -> Any:
            """Return exactly the supplied canonical target without rewriting it."""
            return self.targets[index]  # type: ignore[attr-defined]

        def reset(self, *_args: Any, **_kwargs: Any) -> str:
            """Expose delegation to the original reset without a simulator."""
            return "original-reset"

        def navigation_selection_evidence(self) -> list[dict[str, Any]]:
            """Expose one unchanged selected entity for provenance checks."""
            return [
                {
                    "entity_id": "neutral-entity",
                    "navmesh_resolution_profile": "step-preserving-cell-height/v0.1",
                }
            ]

    class Cylinder:
        """Record the one original cylindrical-base dispatch."""

        @staticmethod
        def step(_action: Any, *_args: Any, **kwargs: Any) -> None:
            """Record original base velocities without physical stepping."""
            commands.append(("cylinder", kwargs["agent_0_base_vel"]))

    class NonCylinder:
        """Record the one original non-cylindrical-base dispatch."""

        @staticmethod
        def step(_action: Any, *_args: Any, **kwargs: Any) -> None:
            """Record original base velocities without physical stepping."""
            commands.append(("non-cylinder", kwargs["agent_0_base_vel"]))

    def original_turn(_direction: Any, velocity: float, _forward: Any) -> list[float]:
        """Expose the unchanged vendor turning command and configured velocity."""
        return [0.0, velocity]

    stubs = {
        name: ModuleType(name)
        for name in [
            "habitat_sim",
            "numpy",
            "habitat",
            "habitat.core",
            "habitat.core.registry",
            "habitat.tasks",
            "habitat.tasks.rearrange",
            "habitat.tasks.rearrange.actions",
            "habitat.tasks.rearrange.actions.actions",
            "habitat.tasks.rearrange.actions.oracle_nav_action",
            "habitat_local_eaios.goal_region_action",
        ]
    }
    vars(stubs["habitat_sim"])["ShortestPath"] = SimpleNamespace
    vars(stubs["numpy"])["array"] = list
    vars(stubs["habitat.core.registry"])["registry"] = SimpleNamespace(
        register_task_action=lambda value: value
    )
    vars(stubs["habitat.tasks.rearrange.actions.actions"]).update(
        BaseVelAction=Cylinder, BaseVelNonCylinderAction=NonCylinder
    )
    vars(stubs["habitat.tasks.rearrange.actions.oracle_nav_action"])["OracleNavAction"] = (
        SimpleNamespace(_compute_turn=original_turn)
    )
    vars(stubs["habitat_local_eaios.goal_region_action"]).update(
        GoalRegionOracleNavDiffBaseAction=OriginalAction,
        StepAwareGoalRegionOracleNavDiffBaseAction=OriginalAction,
    )
    for name, module in stubs.items():
        monkeypatch.setitem(sys.modules, name, module)
    name = "habitat_local_eaios._spatial_action_test"
    spec = importlib.util.spec_from_file_location(
        name, INTEGRATION_ROOT / "habitat_local_eaios/spatial_navigation_action.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module, commands


def _action(module: ModuleType, *, arrival: bool = False, goal_aware: bool = False) -> Any:
    """Construct a real action over a bounded successful fake route and fixed velocities."""
    action = (
        module.LiveGoalArrivalGoalRegionOracleNavDiffBaseAction()
        if goal_aware
        else module.SpatialArrivalGoalRegionOracleNavDiffBaseAction()
    )
    final = (0.1, 0.0 if arrival else 3.0, 0.0)
    route = [(0.0, 0.0, 0.0), (3.0, 0.0, 0.0), final]
    action.route_calls = 0

    def find_path(path: Any) -> bool:
        """Record one actual route query and supply its bounded result."""
        action.route_calls += 1
        path.points = route
        return True

    action._sim = SimpleNamespace(ep_info=SimpleNamespace(episode_id="neutral"))
    action.ep_id = "neutral"
    action.pathfinder = SimpleNamespace(find_path=find_path)
    action._action_arg_prefix = "agent_0_"
    action._poss_entities = ["neutral-entity", "other-entity"]
    action.targets = [(final, (0.3, final[1], 0.0)), ((8.0, 0.0, 0.0), (8.2, 0.0, 0.0))]
    action._config = SimpleNamespace(
        dist_thresh=0.5, turn_thresh=0.2, forward_velocity=0.7, turn_velocity=0.3
    )
    action.cur_articulated_agent = SimpleNamespace(
        base_pos=(0.0, 0.0, 0.0),
        base_transformation=SimpleNamespace(
            transform_vector=lambda value: value, translation=(0.0, 0.0, 0.0)
        ),
    )
    action.prev_nav_done = action.skill_done = False
    action.prev_match_target_id = -1
    action.motion_type = "base_velocity_non_cylinder"
    if goal_aware:
        action._poss_entities = [SimpleNamespace(name=name) for name in action._poss_entities]
        action._roboguide_selections = {
            index: {
                "entity_id": entity.name,
                "mode": "official_any_at_region",
                "status": "selected",
                "goal_center": target[1],
                "official_robot_at_threshold_m": 2.0,
            }
            for index, (entity, target) in enumerate(
                zip(action._poss_entities, action.targets, strict=True)
            )
        }
        action._task = SimpleNamespace(
            pddl_problem=SimpleNamespace(
                sim_info=SimpleNamespace(
                    robot_at_thresh=2.0,
                    get_entity_pos=lambda entity: action.targets[
                        action._poss_entities.index(entity)
                    ][1],
                )
            )
        )
    return action


def test_live_reference_outside_goal_cannot_finish_at_a_nearby_selected_point() -> None:
    """Continue moving when selected-point proximity would finish outside the goal."""
    values: dict[str, Any] = {
        "position": (-0.49, 0.0, 0.0),
        "final_point": (0.0, 0.0, 0.0),
        "entity_point": (0.0, 1.94, 0.0),
        "route_points": ((-0.49, 0.0, 0.0), (0.0, 0.0, 0.0)),
    }
    assert _decision(**values).branch == "arrived"
    region = NavigationGoalRegion(values["position"], values["entity_point"], 2.0)
    result = _decision(**values, goal_region=region)
    assert result.branch == "follow-route"
    assert result.goal_reference_distance_m is not None
    assert result.goal_reference_distance_m > 2.0


@pytest.mark.parametrize("reference", [(0.0, 2.5, 0.0), (3.0, 0.0, 0.0)])
def test_actual_reference_is_not_substituted_with_the_base_position(reference: Any) -> None:
    """Retain full 3D actual reference geometry even when the base appears near the goal."""
    result = _decision(
        final_point=(0.1, 0.0, 0.0),
        entity_point=(0.3, 0.0, 0.0),
        route_points=((0.0, 0.0, 0.0), (0.1, 0.0, 0.0)),
        goal_region=NavigationGoalRegion(reference, (0.3, 0.0, 0.0), 2.0),
    )
    assert result.branch == "follow-route"


@pytest.mark.parametrize("radius", [0.0, float("nan"), float("inf"), True])
def test_missing_live_goal_geometry_cannot_downgrade_to_legacy_completion(radius: float) -> None:
    """Unknown official geometry fails rather than skipping the opt-in stopping gate."""
    with pytest.raises(GoalRegionResolutionError):
        _decision(goal_region=NavigationGoalRegion((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), radius))


def test_live_arrival_already_at_goal_needs_no_fabricated_route_segment() -> None:
    """A successful one-point route can finish only when both live conditions hold."""
    result = _decision(
        final_point=(0.0, 0.0, 0.0),
        entity_point=(0.0, 1.94, 0.0),
        route_points=((0.0, 0.0, 0.0),),
        goal_region=NavigationGoalRegion((0.0, 0.0, 0.0), (0.0, 1.94, 0.0), 2.0),
    )
    assert result.branch == "arrived"


def test_live_action_preserves_commands_and_finishes_only_after_actual_region_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the real action with exact target, one dispatch and actual reference evidence."""
    module, commands = _action_module(monkeypatch)
    action = _action(module, arrival=True, goal_aware=True)
    center = (0.1, 1.94, 0.0)
    action.targets[0] = ((0.1, 0.0, 0.0), center)
    action._roboguide_selections[0]["goal_center"] = center
    action.cur_articulated_agent.base_pos = (-0.39, 0.0, 0.0)
    action.cur_articulated_agent.base_transformation.translation = (-0.39, 0.0, 0.0)
    action.step(agent_0_oracle_nav_action=[1])
    assert commands == [("non-cylinder", [0.7, 0.0])]
    assert not action.skill_done and action.route_calls == 1
    evidence = action.navigation_arrival_evidence()
    assert evidence["profile"] == GOAL_AWARE_ARRIVAL_PROFILE
    assert evidence["goal_reference_within_local_bound"] is False
    action.cur_articulated_agent.base_pos = (0.1, 0.0, 0.0)
    action.cur_articulated_agent.base_transformation.translation = (0.1, 0.0, 0.0)
    action.step(agent_0_oracle_nav_action=[1])
    assert action.skill_done and action.prev_nav_done and action.route_calls == 2
    assert commands[-1] == ("non-cylinder", [0.0, 0.0])
    assert action.navigation_arrival_evidence()["goal_reference_distance_m"] == 1.94
    assert action.navigation_selection_evidence()[0]["navigation_arrival_profile"] == (
        GOAL_AWARE_ARRIVAL_PROFILE
    )


@pytest.mark.parametrize("fault", ["reference", "center", "radius", "missing_contract"])
def test_live_prepared_geometry_changes_cannot_dispatch_or_complete(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Fence changes beyond the base pose between preparation and the original dispatch."""
    module, commands = _action_module(monkeypatch)
    action = _action(module, arrival=True, goal_aware=True)
    action.prepare_navigation_step(agent_0_oracle_nav_action=[1])
    if fault == "reference":
        action.cur_articulated_agent.base_transformation.translation = (3.0, 0.0, 0.0)
    elif fault == "center":
        action.targets[0] = (action.targets[0][0], (3.0, 0.0, 0.0))
    elif fault == "radius":
        action._task.pddl_problem.sim_info.robot_at_thresh = 3.0
    else:
        action._roboguide_selections.clear()
    with pytest.raises(GoalRegionResolutionError):
        action.step(agent_0_oracle_nav_action=[1])
    assert commands == [] and not action.skill_done


def test_live_reference_read_failure_has_local_attribution_before_any_motion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A read fault cannot escape preparation attribution or invent local completion."""
    module, commands = _action_module(monkeypatch)
    action = _action(module, arrival=True, goal_aware=True)

    def unavailable(_entity: Any) -> Any:
        """Represent a vendor geometry reader that cannot obtain current state."""
        raise RuntimeError("geometry read failed")

    action._task.pddl_problem.sim_info.get_entity_pos = unavailable
    with pytest.raises(GoalRegionResolutionError, match="geometry is unavailable") as error:
        action.prepare_navigation_step(agent_0_oracle_nav_action=[1])
    assert isinstance(error.value.__cause__, RuntimeError)
    assert commands == [] and not action.skill_done and action.route_calls == 0


def test_live_arrival_reads_current_entity_geometry_without_replacing_the_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordinary target settling is read freshly; immutable target metadata is not rewritten."""
    module, commands = _action_module(monkeypatch)
    action = _action(module, arrival=True, goal_aware=True)
    targets = list(action.targets)
    current_center = (0.3, 0.01, 0.0)
    action._task.pddl_problem.sim_info.get_entity_pos = lambda _entity: current_center
    action.step(agent_0_oracle_nav_action=[1])
    assert action.skill_done and commands == [("non-cylinder", [0.0, 0.0])]
    assert action.targets == targets
    assert action.navigation_arrival_evidence()["goal_center"] == list(current_center)


def test_goal_aware_profile_is_default_off_and_requires_explicit_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Select the new registered class only with the complete deployment prerequisites."""
    common: dict[str, Any] = {
        "config_path": Path("neutral.yaml"),
        "episode_id": "neutral",
        "agent_id": 0,
        "max_steps": 10,
        "step_period_ms": 0,
    }
    assert CrabAgentBackendConfig(**common).goal_aware_navigation_arrival is False
    with pytest.raises(IntegrationError, match="requires spatial"):
        CrabAgentBackendConfig(**common, goal_aware_navigation_arrival=True)
    module, _ = _action_module(monkeypatch)
    stub = ModuleType("habitat_local_eaios.spatial_navigation_action")
    vars(stub)["LiveGoalArrivalGoalRegionOracleNavDiffBaseAction"] = (
        module.LiveGoalArrivalGoalRegionOracleNavDiffBaseAction
    )
    monkeypatch.setitem(sys.modules, stub.__name__, stub)
    actions = {"agent_0_oracle_nav_action": SimpleNamespace(type="OracleNavDiffBaseAction")}
    config = SimpleNamespace(
        habitat=SimpleNamespace(
            task=SimpleNamespace(actions=actions),
            simulator=SimpleNamespace(agents_order=["agent_0"]),
        )
    )

    @contextmanager
    def read_write(_config: Any) -> Iterator[None]:
        """Expose only the temporary deployment configuration scope."""
        yield

    with pytest.raises(IntegrationError, match="requires spatial"):
        _configure_goal_region_navigation(config, read_write, goal_aware_arrival=True)
    assert actions["agent_0_oracle_nav_action"].type == "OracleNavDiffBaseAction"
    _configure_goal_region_navigation(
        config, read_write, step_aware=True, spatial_arrival=True, goal_aware_arrival=True
    )
    assert actions["agent_0_oracle_nav_action"].type == (
        "LiveGoalArrivalGoalRegionOracleNavDiffBaseAction"
    )
    assert module.LiveGoalArrivalGoalRegionOracleNavDiffBaseAction.require_stop_envelope is False


@pytest.mark.parametrize(
    "motion, dispatcher",
    [("base_velocity", "cylinder"), ("base_velocity_non_cylinder", "non-cylinder")],
)
def test_real_action_continues_the_route_with_one_original_motion_dispatch(
    monkeypatch: pytest.MonkeyPatch, motion: str, dispatcher: str
) -> None:
    """Wrong-level horizontal proximity triggers movement without another target or step."""
    module, commands = _action_module(monkeypatch)
    action = _action(module)
    action.motion_type = motion
    original_targets = list(action.targets)
    action.step(agent_0_oracle_nav_action=[1])
    assert action.targets == original_targets
    assert commands == [(dispatcher, [0.7, 0.0])]
    assert action.route_calls == 1 and action.skill_done is False
    assert action.navigation_arrival_evidence()["branch"] == "follow-route"
    assert (
        action.navigation_selection_evidence()[0]["navigation_arrival_profile"]
        == SPATIAL_ARRIVAL_PROFILE
    )


def test_actual_arrival_uses_original_finished_flag_and_never_redispatches_done_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finish exactly once and preserve original same-target suppression."""
    module, commands = _action_module(monkeypatch)
    action = _action(module, arrival=True)
    action.step(agent_0_oracle_nav_action=[1])
    assert action.skill_done and action.prev_nav_done
    assert commands == [("non-cylinder", [0.0, 0.0])]
    action.step(agent_0_oracle_nav_action=[1])
    assert len(commands) == 1 and action.route_calls == 1
    assert action.navigation_arrival_evidence()["_status"] == "unavailable"
    assert action.reset() == "original-reset"
    assert action.navigation_arrival_evidence()["_status"] == "unavailable"


def test_route_failure_is_not_a_straight_line_or_a_local_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Propagate a missing actual path before any vendor base movement."""
    module, commands = _action_module(monkeypatch)
    action = _action(module)
    action.pathfinder.find_path = lambda _path: False
    with pytest.raises(GoalRegionResolutionError, match="active route is unavailable"):
        action.step(agent_0_oracle_nav_action=[1])
    assert commands == [] and not action.skill_done


def test_prepared_navigation_reuses_one_route_and_defers_motion_and_finished_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Separate preparation from dispatch without a second query or early completion."""
    module, commands = _action_module(monkeypatch)
    action = _action(module, arrival=True)
    action.prepare_navigation_step(agent_0_oracle_nav_action=[1])
    assert commands == [] and action.route_calls == 1 and not action.skill_done
    action.step(agent_0_oracle_nav_action=[1])
    assert commands == [("non-cylinder", [0.0, 0.0])]
    assert action.route_calls == 1 and action.skill_done
    assert action._roboguide_prepared_navigation is None


@pytest.mark.parametrize("changed", ["position", "episode", "target"])
def test_stale_prepared_navigation_cannot_dispatch_motion(
    monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    """Fence a changed world or target instead of silently consuming a prior command."""
    module, commands = _action_module(monkeypatch)
    action = _action(module)
    action.prepare_navigation_step(agent_0_oracle_nav_action=[1])
    target = 1
    if changed == "position":
        action.cur_articulated_agent.base_pos = (1.0, 0.0, 0.0)
    elif changed == "episode":
        action._sim.ep_info.episode_id = "other"
    else:
        target = 2
    with pytest.raises(GoalRegionResolutionError, match="prepared command is stale"):
        action.step(agent_0_oracle_nav_action=[target])
    assert commands == [] and action.route_calls == 1
    assert action._roboguide_prepared_navigation is None


@pytest.mark.parametrize("index", [float("nan"), 1.5])
def test_invalid_tool_index_cannot_change_the_target(
    monkeypatch: pytest.MonkeyPatch, index: float
) -> None:
    """Reject malformed local indices before route queries or physical calls."""
    module, commands = _action_module(monkeypatch)
    action = _action(module)
    with pytest.raises(GoalRegionResolutionError, match="target index"):
        action.step(agent_0_oracle_nav_action=[index])
    assert commands == [] and action.route_calls == 0


@pytest.mark.parametrize("index", [0, 3])
def test_idle_or_out_of_range_action_does_not_start_navigation(
    monkeypatch: pytest.MonkeyPatch, index: int
) -> None:
    """Keep the original idle sentinel and range behavior without an extra route query."""
    module, commands = _action_module(monkeypatch)
    action = _action(module)
    action.step(agent_0_oracle_nav_action=[index])
    assert commands == [] and action.route_calls == 0 and not action.skill_done


def test_unsupported_motion_fails_before_physical_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not silently apply the new differential-base controller to a humanoid."""
    module, commands = _action_module(monkeypatch)
    action = _action(module)
    action.motion_type = "human_joints"
    with pytest.raises(GoalRegionResolutionError, match="differential-base"):
        action.step(agent_0_oracle_nav_action=[1])
    assert commands == [] and action.route_calls == 0


def test_spatial_diagnostic_read_failure_is_unavailable() -> None:
    """Keep raw finished evidence when optional local decision observation fails."""

    def unavailable() -> dict[str, Any]:
        """Represent a best-effort reader failure without touching physical state."""
        raise RuntimeError("observation failed")

    action = SimpleNamespace(skill_done=True, navigation_arrival_evidence=unavailable)
    environment = SimpleNamespace(
        task=SimpleNamespace(actions={"agent_0_oracle_nav_action": action})
    )
    diagnostic = PhysicalDiagnostics.__new__(PhysicalDiagnostics)
    observed = diagnostic._oracle_flags(environment, 0)
    assert observed["oracle_skill_done"] is True
    assert observed["spatial_arrival_decision"]["_status"] == "unavailable"


def test_profile_default_off_and_requires_explicit_prerequisites(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep old execution selection and require both mesh and point profiles for opt-in."""
    common: dict[str, Any] = {
        "config_path": Path("neutral.yaml"),
        "episode_id": "neutral",
        "agent_id": 0,
        "max_steps": 10,
        "step_period_ms": 0,
    }
    assert CrabAgentBackendConfig(**common).spatial_navigation_arrival is False
    with pytest.raises(IntegrationError, match="requires step-aware"):
        CrabAgentBackendConfig(**common, spatial_navigation_arrival=True)
    module, _ = _action_module(monkeypatch)
    stub = ModuleType("habitat_local_eaios.spatial_navigation_action")
    vars(stub)["SpatialArrivalGoalRegionOracleNavDiffBaseAction"] = (
        module.SpatialArrivalGoalRegionOracleNavDiffBaseAction
    )
    monkeypatch.setitem(sys.modules, stub.__name__, stub)
    actions = {"agent_0_oracle_nav_action": SimpleNamespace(type="OracleNavDiffBaseAction")}
    config = SimpleNamespace(
        habitat=SimpleNamespace(
            task=SimpleNamespace(actions=actions),
            simulator=SimpleNamespace(agents_order=["agent_0"]),
        )
    )

    @contextmanager
    def read_write(_config: Any) -> Iterator[None]:
        """Expose only the existing temporary config-write scope."""
        yield

    with pytest.raises(IntegrationError, match="requires step-aware"):
        _configure_goal_region_navigation(config, read_write, spatial_arrival=True)
    assert actions["agent_0_oracle_nav_action"].type == "OracleNavDiffBaseAction"
    _configure_goal_region_navigation(config, read_write, step_aware=True, spatial_arrival=True)
    assert (
        actions["agent_0_oracle_nav_action"].type
        == "SpatialArrivalGoalRegionOracleNavDiffBaseAction"
    )
