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
    MAX_ROUTE_POINTS,
    SPATIAL_ARRIVAL_PROFILE,
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
    spec.loader.exec_module(module)
    return module, commands


def _action(module: ModuleType, *, arrival: bool = False) -> Any:
    """Construct a real action over a bounded successful fake route and fixed velocities."""
    action = module.SpatialArrivalGoalRegionOracleNavDiffBaseAction()
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
        base_transformation=SimpleNamespace(transform_vector=lambda value: value),
    )
    action.prev_nav_done = action.skill_done = False
    action.prev_match_target_id = -1
    action.motion_type = "base_velocity_non_cylinder"
    return action


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
