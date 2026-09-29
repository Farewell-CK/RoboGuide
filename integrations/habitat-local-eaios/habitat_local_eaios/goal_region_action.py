"""Optional Habitat action that resolves official distance goals to local routes.

Imported only inside the deployment's Habitat Conda process. The original
EMOS Oracle action still owns stepping, control, and local completion.
"""

from __future__ import annotations

import math
import time
from typing import Any

import habitat_sim  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]
from habitat.core.registry import registry  # type: ignore[import-not-found]
from habitat.tasks.rearrange.actions.habitat_mas_actions import (  # type: ignore[import-not-found]
    OracleNavDiffBaseAction,
)

from .goal_region_navigation import (
    MAX_NAVMESH_VERTICES,
    MAX_PATH_QUERIES,
    GoalRegionResolutionError,
    Point3,
    any_at_conjunct_names,
    point3,
    select_goal_region_point,
)
from .semantic_evidence import _expression

_SELECTION_SCHEMA = "roboguide.habitat-goal-region-navigation/v0.1"


@registry.register_task_action
class GoalRegionOracleNavDiffBaseAction(OracleNavDiffBaseAction):  # type: ignore[misc]
    """Keep original Oracle control while changing only goal-point selection."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the original action and bounded per-episode audit state."""
        super().__init__(*args, **kwargs)
        self._roboguide_selections: dict[int, dict[str, Any]] = {}

    def reset(self, *args: Any, **kwargs: Any) -> Any:
        """Discard selection evidence when the original action resets a new episode."""
        prior = self._prev_ep_id
        result = super().reset(*args, **kwargs)
        if self._prev_ep_id != prior:
            self._roboguide_selections.clear()
        return result

    def _path_length(self, point: Point3) -> float | None:
        """Query the action's own active navmesh; reject original two-point fallback."""
        path = habitat_sim.ShortestPath()
        path.requested_start = self.cur_articulated_agent.base_pos
        path.requested_end = point
        if not self.pathfinder.find_path(path):
            return None
        points = list(path.points)
        if len(points) < 2 or math.dist(point3(points[-1]), point) > 0.05:
            return None
        distance = float(path.geodesic_distance)
        return distance if math.isfinite(distance) and distance >= 0 else None

    def _project_center(self, center: Point3) -> Point3 | None:
        """Snap the goal's X/Z on this agent's current navmesh height."""
        base = point3(self.cur_articulated_agent.base_pos)
        projected = self.pathfinder.snap_point((center[0], base[1], center[2]))
        try:
            point = point3(projected)
        except GoalRegionResolutionError:
            return None
        # A snap to another floor is still checked by the official 3D region
        # and actual agent-specific route before this point can be selected.
        return point

    def _get_target_for_idx(self, nav_to_target_idx: int) -> Any:
        """Select a reachable point for an exact conjunctive ``any_at`` entity.

        The returned object position and selected entity index remain the
        original Oracle values. All other PDDL entities use original selection.
        """
        cached = self._roboguide_selections.get(nav_to_target_idx)
        if cached is not None:
            if cached.get("status") == "failed":
                raise GoalRegionResolutionError(
                    "previous goal-region selection failed for this entity"
                )
            return self._targets[nav_to_target_idx]
        original_point, object_point = super()._get_target_for_idx(nav_to_target_idx)
        problem = self._task.pddl_problem
        entity = self._poss_entities[nav_to_target_idx]
        entity_name = getattr(entity, "name", None)
        if not isinstance(entity_name, str):
            raise GoalRegionResolutionError("Oracle target entity has no exact name")
        if entity_name not in any_at_conjunct_names(_expression(problem.goal)):
            self._roboguide_selections[nav_to_target_idx] = {
                "schema_version": _SELECTION_SCHEMA,
                "entity_id": entity_name,
                "mode": "original_non_distance_goal",
                "original_point": [float(value) for value in original_point],
            }
            return original_point, object_point
        record: dict[str, Any] = {
            "schema_version": _SELECTION_SCHEMA,
            "entity_id": entity_name,
            "mode": "official_any_at_region",
            "original_point": [float(value) for value in original_point],
            "goal_center": [float(value) for value in object_point],
            "max_navmesh_vertices": MAX_NAVMESH_VERTICES,
            "max_path_queries": MAX_PATH_QUERIES,
        }
        self._roboguide_selections[nav_to_target_idx] = record
        started = time.perf_counter()
        try:
            if self.pathfinder is None or not self.pathfinder.is_loaded:
                raise GoalRegionResolutionError("agent-specific navmesh is unavailable")
            sim_info = problem.sim_info
            threshold = float(sim_info.robot_at_thresh)
            base = np.asarray(self.cur_articulated_agent.base_pos, dtype=float)
            reference = np.asarray(
                self.cur_articulated_agent.base_transformation.translation, dtype=float
            )
            selected = select_goal_region_point(
                original_point=original_point,
                goal_center=object_point,
                reference_offset=reference - base,
                radius_m=threshold,
                stop_radius_m=float(self._config.dist_thresh),
                navmesh_vertices=self.pathfinder.build_navmesh_vertices,
                path_length=self._path_length,
                project_center=self._project_center,
            )
            self._targets[nav_to_target_idx] = (
                np.asarray(selected.point),
                np.asarray(object_point),
            )
            record.update(
                {
                    "status": "selected",
                    "official_robot_at_threshold_m": threshold,
                    "selection": selected.as_dict(),
                    "selection_elapsed_ms": (time.perf_counter() - started) * 1_000,
                }
            )
            return self._targets[nav_to_target_idx]
        except Exception as error:
            record.update(
                {
                    "status": "failed",
                    "error": str(error),
                    "selection_elapsed_ms": (time.perf_counter() - started) * 1_000,
                }
            )
            raise

    def navigation_selection_evidence(self) -> list[dict[str, Any]]:
        """Expose bounded local selections for best-effort terminal archival."""
        return [
            self._roboguide_selections[index].copy() for index in sorted(self._roboguide_selections)
        ]
