"""Explicit spatial-arrival Local How; imported only in the Habitat process.

The original Stage2 model and skill select the exact entity and invoke this
action. It performs one original base action per invocation, using the original
velocities and budgets. This profile changes route/arrival control and must be
disclosed; it never changes official goal evaluation or external EMOS files.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import habitat_sim  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]
from habitat.core.registry import registry  # type: ignore[import-not-found]
from habitat.tasks.rearrange.actions.actions import (  # type: ignore[import-not-found]
    BaseVelAction,
    BaseVelNonCylinderAction,
)
from habitat.tasks.rearrange.actions.oracle_nav_action import (  # type: ignore[import-not-found]
    OracleNavAction,
)

from .goal_region_action import StepAwareGoalRegionOracleNavDiffBaseAction
from .goal_region_navigation import GOAL_REGION_SELECTION_SCHEMA, GoalRegionResolutionError, point3
from .spatial_navigation import (
    MAX_ROUTE_POINTS,
    SPATIAL_ARRIVAL_PROFILE,
    heading_error,
    spatial_navigation_decision,
)


@dataclass(frozen=True)
class PreparedNavigation:
    """Retain one exact pre-motion command until its original action dispatch."""

    episode_id: str
    target_value: float
    position: tuple[float, float, float]
    forward: tuple[float, float, float]
    velocity: tuple[float, float] | None
    target_index: int | None
    observation: dict[str, Any] | None


@registry.register_task_action
class SpatialArrivalGoalRegionOracleNavDiffBaseAction(StepAwareGoalRegionOracleNavDiffBaseAction):
    """Keep the exact target and ability while following spatial route arrival."""

    ep_id: str | None
    pathfinder: Any
    prev_nav_done: bool
    prev_match_target_id: int

    def reset(self, *args: Any, **kwargs: Any) -> Any:
        """Clear local observation counters before the existing action reset."""
        self._roboguide_arrival_calls = 0
        self._roboguide_arrival_observation: dict[str, Any] | None = None
        self._roboguide_prepared_navigation: PreparedNavigation | None = None
        return super().reset(*args, **kwargs)

    def _path_to_point(self, point: Any, pathfinder: Any = None) -> Any:
        """Query one route per action; never substitute the vendor straight-line fallback."""
        path = habitat_sim.ShortestPath()
        path.requested_start = self.cur_articulated_agent.base_pos
        path.requested_end = point
        finder = self.pathfinder if pathfinder is None else pathfinder
        if not finder.find_path(path):
            raise GoalRegionResolutionError("spatial navigation active route is unavailable")
        points = path.points
        if not 0 < len(points) <= MAX_ROUTE_POINTS:
            raise GoalRegionResolutionError("spatial navigation active route exceeds its bounds")
        return points

    def prepare_navigation_step(self, **kwargs: Any) -> None:
        """Resolve this exact action once before any member of a joint step moves.

        This is Local How preparation, not a read-only diagnostic: it initializes
        the same private mesh and target cache the original action would use.
        It never dispatches motion, changes finished flags, calls a model or
        steps Gym. The subsequent action consumes the prepared route decision
        without repeating target selection or its route query.
        """
        self.discard_prepared_navigation()
        episode_id = self._sim.ep_info.episode_id
        if self.ep_id != episode_id:
            self.ep_id = episode_id
            self.pathfinder = self._create_pathfinder(self.config)
        raw_index = kwargs[self._action_arg_prefix + "oracle_nav_action"]
        value = float(raw_index[0])
        if not math.isfinite(value):
            raise GoalRegionResolutionError("spatial navigation target index is invalid")
        base = self.cur_articulated_agent
        position = point3(base.base_pos)
        forward_world = point3(base.base_transformation.transform_vector([1.0, 0.0, 0.0]))
        if value <= 0 or value > len(self._poss_entities):
            self._roboguide_prepared_navigation = PreparedNavigation(
                episode_id, value, position, forward_world, None, None, None
            )
            return
        target_index = int(value) - 1
        if value != int(value):
            raise GoalRegionResolutionError("spatial navigation target index is not integral")
        if self.prev_nav_done and target_index == self.prev_match_target_id:
            self._roboguide_prepared_navigation = PreparedNavigation(
                episode_id, value, position, forward_world, None, None, None
            )
            return
        if self.motion_type not in {"base_velocity", "base_velocity_non_cylinder"}:
            raise GoalRegionResolutionError("spatial arrival requires differential-base motion")
        final_point, entity_point = self._get_target_for_idx(target_index)
        points = self._path_to_point(final_point)
        forward = (forward_world[0], forward_world[2])
        decision = spatial_navigation_decision(
            position=position,
            final_point=point3(final_point),
            entity_point=point3(entity_point),
            route_points=tuple(point3(point) for point in points),
            forward=forward,
            distance_threshold_m=float(self._config.dist_thresh),
            turn_threshold_radians=float(self._config.turn_thresh),
        )
        observation = {
            "profile": SPATIAL_ARRIVAL_PROFILE,
            "phase": "pre-base-action",
            "action_invocations": getattr(self, "_roboguide_arrival_calls", 0) + 1,
            "branch": decision.branch,
            "distance_m": decision.distance_m,
            "horizontal_distance_m": decision.horizontal_distance_m,
            "vertical_distance_m": decision.vertical_distance_m,
        }
        if decision.branch == "arrived":
            velocity = [0.0, 0.0]
        elif (
            decision.branch == "follow-route"
            and heading_error(forward, decision.direction) < self._config.turn_thresh
        ):
            velocity = [self._config.forward_velocity, 0.0]
        else:
            velocity = OracleNavAction._compute_turn(
                np.array(decision.direction), self._config.turn_velocity, np.array(forward)
            )
        self._roboguide_prepared_navigation = PreparedNavigation(
            episode_id,
            value,
            position,
            forward_world,
            (float(velocity[0]), float(velocity[1])),
            target_index,
            observation,
        )

    def discard_prepared_navigation(self) -> None:
        """Discard only the pending command; retain the original per-episode target cache."""
        self._roboguide_prepared_navigation = None

    def step(self, *args: Any, **kwargs: Any) -> None:
        """Consume one exact prepared command and dispatch one original base action.

        Direct callers may prepare here, preserving the standalone action path.
        A changed episode, target or pose cannot consume a stale prepared command.
        This does not make arbitrary simulator errors transactionally reversible.
        """
        prepared = getattr(self, "_roboguide_prepared_navigation", None)
        if prepared is None:
            self.prepare_navigation_step(**kwargs)
            prepared = self._roboguide_prepared_navigation
        self.discard_prepared_navigation()
        assert prepared is not None
        base = self.cur_articulated_agent
        if (
            prepared.episode_id != self._sim.ep_info.episode_id
            or prepared.target_value
            != float(kwargs[self._action_arg_prefix + "oracle_nav_action"][0])
            or prepared.position != point3(base.base_pos)
            or prepared.forward
            != point3(base.base_transformation.transform_vector([1.0, 0.0, 0.0]))
        ):
            raise GoalRegionResolutionError("spatial navigation prepared command is stale")
        self.skill_done = False
        self._roboguide_arrival_observation = prepared.observation
        if prepared.velocity is None:
            return
        assert prepared.observation is not None and prepared.target_index is not None
        self._roboguide_arrival_calls = prepared.observation["action_invocations"]
        self.prev_nav_done = prepared.observation["branch"] == "arrived"
        if self.prev_nav_done:
            self.skill_done = True
            self.prev_match_target_id = prepared.target_index
        kwargs[self._action_arg_prefix + "base_vel"] = np.array(prepared.velocity)
        if self.motion_type == "base_velocity":
            BaseVelAction.step(self, *args, **kwargs)
        else:
            BaseVelNonCylinderAction.step(self, *args, **kwargs)

    def navigation_selection_evidence(self) -> list[dict[str, Any]]:
        """Disclose the spatial controller separately from point and mesh selection."""
        return [
            {
                **record,
                "schema_version": GOAL_REGION_SELECTION_SCHEMA,
                "navigation_arrival_profile": SPATIAL_ARRIVAL_PROFILE,
            }
            for record in super().navigation_selection_evidence()
        ]

    def navigation_arrival_evidence(self) -> dict[str, Any]:
        """Return the latest local decision without reading world state or inferring success."""
        observation = getattr(self, "_roboguide_arrival_observation", None)
        return (
            dict(observation)
            if observation is not None
            else {"_status": "unavailable", "reason": "no spatial navigation decision this action"}
        )
