"""Explicit spatial-arrival Local How; imported only in the Habitat process.

The original Stage2 model and skill select the exact entity and invoke this
action. It performs one original base action per invocation, using the original
velocities and budgets. This profile changes route/arrival control and must be
disclosed; it never changes official goal evaluation or external EMOS files.
"""

from __future__ import annotations

import math
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
from .goal_region_navigation import GoalRegionResolutionError, point3
from .spatial_navigation import (
    MAX_ROUTE_POINTS,
    SPATIAL_ARRIVAL_PROFILE,
    heading_error,
    spatial_navigation_decision,
)


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

    def step(self, *args: Any, **kwargs: Any) -> None:
        """Choose one original base velocity command without another actor or gym step.

        The existing finished sensor reads skill_done as before. Only spatial
        arrival sets it; budget exhaustion still belongs to the original skill.
        Unsupported motion profiles and missing routes fail before base motion.
        """
        episode_id = self._sim.ep_info.episode_id
        if self.ep_id != episode_id:
            self.ep_id = episode_id
            self.pathfinder = self._create_pathfinder(self.config)
        self.skill_done = False
        self._roboguide_arrival_observation = None
        raw_index = kwargs[self._action_arg_prefix + "oracle_nav_action"]
        value = float(raw_index[0])
        if not math.isfinite(value):
            raise GoalRegionResolutionError("spatial navigation target index is invalid")
        if value <= 0 or value > len(self._poss_entities):
            return
        target_index = int(value) - 1
        if value != int(value):
            raise GoalRegionResolutionError("spatial navigation target index is not integral")
        if self.prev_nav_done and target_index == self.prev_match_target_id:
            return
        if self.motion_type not in {"base_velocity", "base_velocity_non_cylinder"}:
            raise GoalRegionResolutionError("spatial arrival requires differential-base motion")
        final_point, entity_point = self._get_target_for_idx(target_index)
        points = self._path_to_point(final_point)
        base = self.cur_articulated_agent
        world_forward = point3(base.base_transformation.transform_vector([1.0, 0.0, 0.0]))
        forward = (world_forward[0], world_forward[2])
        decision = spatial_navigation_decision(
            position=point3(base.base_pos),
            final_point=point3(final_point),
            entity_point=point3(entity_point),
            route_points=tuple(point3(point) for point in points),
            forward=forward,
            distance_threshold_m=float(self._config.dist_thresh),
            turn_threshold_radians=float(self._config.turn_thresh),
        )
        self._roboguide_arrival_calls = getattr(self, "_roboguide_arrival_calls", 0) + 1
        self._roboguide_arrival_observation = {
            "profile": SPATIAL_ARRIVAL_PROFILE,
            "phase": "pre-base-action",
            "action_invocations": self._roboguide_arrival_calls,
            "branch": decision.branch,
            "distance_m": decision.distance_m,
            "horizontal_distance_m": decision.horizontal_distance_m,
            "vertical_distance_m": decision.vertical_distance_m,
        }
        self.prev_nav_done = decision.branch == "arrived"
        if self.prev_nav_done:
            velocity = [0.0, 0.0]
            self.skill_done = True
            self.prev_match_target_id = target_index
        elif (
            decision.branch == "follow-route"
            and heading_error(forward, decision.direction) < self._config.turn_thresh
        ):
            velocity = [self._config.forward_velocity, 0.0]
        else:
            velocity = OracleNavAction._compute_turn(
                np.array(decision.direction), self._config.turn_velocity, np.array(forward)
            )
        kwargs[self._action_arg_prefix + "base_vel"] = np.array(velocity)
        if self.motion_type == "base_velocity":
            BaseVelAction.step(self, *args, **kwargs)
        else:
            BaseVelNonCylinderAction.step(self, *args, **kwargs)

    def navigation_selection_evidence(self) -> list[dict[str, Any]]:
        """Disclose the spatial controller separately from point and mesh selection."""
        return [
            {
                **record,
                "schema_version": "roboguide.habitat-goal-region-navigation/v0.4",
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
