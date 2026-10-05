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
    GOAL_REGION_SELECTION_SCHEMA,
    MAX_GEOMETRY_SEARCH_SECONDS,
    MAX_NAVMESH_TRIANGLES,
    MAX_NAVMESH_VERTICES,
    MAX_PATH_QUERIES,
    TRIANGLE_SEARCH_PROFILE,
    GoalRegionResolutionError,
    GoalRegionSearchMiss,
    Point3,
    any_at_conjunct_names,
    point3,
    select_goal_region_point,
)
from .navmesh_profile import STEP_AWARE_PROFILE, copied_agent_settings, settings_snapshot
from .semantic_evidence import _expression


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
                "schema_version": GOAL_REGION_SELECTION_SCHEMA,
                "entity_id": entity_name,
                "mode": "original_non_distance_goal",
                "original_point": [float(value) for value in original_point],
            }
            return original_point, object_point
        record: dict[str, Any] = {
            "schema_version": GOAL_REGION_SELECTION_SCHEMA,
            "entity_id": entity_name,
            "mode": "official_any_at_region",
            "original_point": [float(value) for value in original_point],
            "goal_center": [float(value) for value in object_point],
            "max_navmesh_vertices": MAX_NAVMESH_VERTICES,
            "candidate_search_profile": TRIANGLE_SEARCH_PROFILE,
            "max_navmesh_triangles": MAX_NAVMESH_TRIANGLES,
            "max_geometry_search_seconds": MAX_GEOMETRY_SEARCH_SECONDS,
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
                navmesh_indices=self.pathfinder.build_navmesh_vertex_indices,
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
            if isinstance(error, GoalRegionSearchMiss):
                record["search"] = error.search.copy()
            raise

    def navigation_selection_evidence(self) -> list[dict[str, Any]]:
        """Expose bounded local selections for best-effort terminal archival."""
        return [
            self._roboguide_selections[index].copy() for index in sorted(self._roboguide_selections)
        ]


@registry.register_task_action
class StepAwareGoalRegionOracleNavDiffBaseAction(GoalRegionOracleNavDiffBaseAction):
    """Retain Oracle control while avoiding climb loss in coarse vertical voxels.

    Deployment selects this class explicitly. Only the detached agent mesh's
    cell height changes; targets, robot abilities, stepping, finished sensors,
    skill budgets and the official success calculation retain their owners.
    """

    def _create_pathfinder(self, config: Any) -> Any:
        """Build the active mesh with the same copied profile used by observation."""
        settings = copied_agent_settings(self._sim, config, habitat_sim, step_aware=True)
        pathfinder = habitat_sim.PathFinder()
        if not self._sim.recompute_navmesh(pathfinder, settings) or not pathfinder.is_loaded:
            raise GoalRegionResolutionError("step-aware agent navmesh build failed")
        self._roboguide_mesh_settings = settings_snapshot(settings)
        return pathfinder

    def navigation_selection_evidence(self) -> list[dict[str, Any]]:
        """Archive the actual active settings independently of physical success."""
        return [
            {
                **record,
                "schema_version": GOAL_REGION_SELECTION_SCHEMA,
                "navmesh_resolution_profile": STEP_AWARE_PROFILE,
                "active_navmesh_settings": (
                    dict(self._roboguide_mesh_settings)
                    if hasattr(self, "_roboguide_mesh_settings")
                    else None
                ),
            }
            for record in super().navigation_selection_evidence()
        ]
