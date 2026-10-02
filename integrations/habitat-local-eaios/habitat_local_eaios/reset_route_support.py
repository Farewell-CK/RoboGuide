"""Observe reset-bound Local How route witnesses without placement authority.

Only the original and projected-center candidates are queried. A miss is not
the full resolver's verdict: its vertex fallback is deliberately not run here.
No live action cache, simulator step, safe-snap RNG fallback, or shared navmesh
setting is modified. This evidence is diagnostic and is not a Node exclusion.
"""

from __future__ import annotations

import math
import time
from typing import Any, cast

from .goal_region_navigation import (
    GoalRegionResolutionError,
    GoalRegionSearchMiss,
    Point3,
    any_at_conjunct_names,
    point3,
    select_goal_region_point,
)
from .navmesh_region import (
    REGION_PROFILE,
    ComponentMesh,
    RegionBudget,
    analyze_region,
    unknown_region,
)
from .preassignment_feasibility import preassignment_digest

RESET_ROUTE_SUPPORT_SCHEMA = "roboguide.deployment-reset-route-support/v0.1"
GEOMETRY_ROUTE_SUPPORT_SCHEMA = "roboguide.deployment-reset-route-support/v0.2"
MAX_ROUTE_RECORDS = 128
PATH_QUERIES_PER_RECORD = 2
MAX_TOTAL_PATH_QUERIES = MAX_ROUTE_RECORDS * PATH_QUERIES_PER_RECORD
_NAVMESH_FIELDS = frozenset(
    (
        "agent_height",
        "agent_max_climb",
        "agent_max_slope",
        "agent_radius",
        "cell_height",
        "cell_size",
        "detail_sample_dist",
        "detail_sample_max_error",
        "edge_max_error",
        "edge_max_len",
        "filter_ledge_spans",
        "filter_low_hanging_obstacles",
        "filter_walkable_low_height_spans",
        "include_static_objects",
        "region_merge_size",
        "region_min_size",
        "verts_per_poly",
    )
)


def _empty_observation() -> dict[str, Any]:
    """Keep unavailable fields explicit without fabricating observed state."""
    return {
        "status": "unavailable",
        "reason_code": "observation_error",
        "start_base_position": None,
        "start_reference_position": None,
        "start_rotation_yaw_rad": None,
        "goal_center": None,
        "official_robot_at_threshold_m": None,
        "oracle_stop_radius_m": None,
        "original_point": None,
        "navmesh_settings": None,
        "habitat_sim_version": None,
        "selection": None,
        "search": None,
        "error_type": None,
        "error": None,
        "elapsed_ms": 0.0,
    }


class ResetRouteProbe:
    """Query one isolated, agent-specific navmesh under the active Local How.

    Construction builds a detached PathFinder using copied scalar settings.
    The live Oracle's lazy initialization and target cache remain untouched.
    Unknown vendor layouts fail as unavailable instead of invoking fallbacks.
    """

    def __init__(self, environment: Any, agent_id: int, api: Any | None = None) -> None:
        """Build one independent mesh without calling the mutating vendor helper."""
        if api is None:
            import habitat_sim as api  # type: ignore[import-not-found, no-redef]

        self.api = cast(Any, api)
        self.version = getattr(api, "__version__", None)
        if not isinstance(self.version, str) or not self.version.strip():
            raise GoalRegionResolutionError("Habitat-Sim version is unavailable")
        self.environment = environment
        self.agent_id = agent_id
        self.action = environment.task.actions[f"agent_{agent_id}_oracle_nav_action"]
        if type(self.action).__name__ != "GoalRegionOracleNavDiffBaseAction":
            raise GoalRegionResolutionError("reset route probe requires the goal-region action")
        if self.action.config.spawn_max_dist_to_obj != -1:
            raise GoalRegionResolutionError("randomized Oracle target placement is unsupported")
        sim = environment.sim
        if getattr(sim, "navmesh_visualization", False):
            raise GoalRegionResolutionError("navmesh visualization prevents isolated observation")
        template = sim.pathfinder.nav_mesh_settings
        fields = {
            name
            for name in dir(template)
            if not name.startswith("_") and not callable(getattr(template, name))
        }
        if fields != _NAVMESH_FIELDS:
            raise GoalRegionResolutionError("unsupported navmesh settings layout")
        settings = self.api.NavMeshSettings()
        for name in sorted(fields):
            value = getattr(template, name)
            if not isinstance(value, (bool, int, float)) or not math.isfinite(value):
                raise GoalRegionResolutionError("navmesh settings contain unavailable scalars")
            setattr(settings, name, value)
        config = self.action.config
        for name in ("agent_radius", "agent_height", "agent_max_climb", "agent_max_slope"):
            value = float(getattr(config, name))
            if not math.isfinite(value) or value < 0:
                raise GoalRegionResolutionError("agent navmesh configuration is unavailable")
            setattr(settings, name, value)
        settings.agent_radius += 0.05
        settings.include_static_objects = True
        self.settings = {name: getattr(settings, name) for name in sorted(fields)}
        self.pathfinder = self.api.PathFinder()
        if not sim.recompute_navmesh(self.pathfinder, settings) or not self.pathfinder.is_loaded:
            raise GoalRegionResolutionError("isolated agent navmesh build failed")
        self._component_mesh: ComponentMesh | None = None
        self._component_error: str | None = None

    def observe_region(self, observation: dict[str, Any], budget: RegionBudget) -> dict[str, Any]:
        """Inspect the detached reset component only when geometry was requested.

        Failed reads and incomplete scans remain unknown. No action cache,
        native path query, random sampling, or active mesh is touched here.
        The copied mesh is retained only for this endpoint's bounded goal set.
        """
        if observation["status"] == "unavailable":
            return unknown_region("route_observation_unavailable")
        try:
            start = point3(observation["start_base_position"])
            if self._component_error is not None:
                return unknown_region(self._component_error)
            if self._component_mesh is None:
                try:
                    self._component_mesh = ComponentMesh.read(self.pathfinder, start)
                except Exception:
                    self._component_error = "component_export_unavailable"
                    raise
            return analyze_region(
                self._component_mesh,
                start,
                point3(observation["start_reference_position"]),
                point3(observation["goal_center"]),
                observation["official_robot_at_threshold_m"],
                budget,
            )
        except Exception:
            return unknown_region("component_export_unavailable")

    def observe(self, destination: str) -> dict[str, Any]:
        """Record a static route witness or bounded miss for one exact entity."""
        record = _empty_observation()
        record.update(navmesh_settings=self.settings.copy(), habitat_sim_version=self.version)
        started = time.perf_counter()
        try:
            sim = self.environment.sim
            agent = sim.get_agent_data(self.agent_id).articulated_agent
            start = point3(agent.base_pos)
            reference = point3(agent.base_transformation.translation)
            rotation = float(agent.base_rot)
            if not math.isfinite(rotation):
                raise GoalRegionResolutionError("reset rotation is unavailable")
            problem = self.environment.task.pddl_problem
            entity = problem.get_entity(destination)
            if entity is None:
                raise GoalRegionResolutionError("exact goal entity is unavailable")
            center = point3(problem.sim_info.get_entity_pos(entity))
            radius = float(problem.sim_info.robot_at_thresh)
            stop_radius = float(self.action.config.dist_thresh)
            if (
                not math.isfinite(radius)
                or radius <= 0
                or not math.isfinite(stop_radius)
                or stop_radius < 0
            ):
                raise GoalRegionResolutionError("official or Local How radius is unavailable")
            # safe_snap_point adds random sampling when this deterministic snap
            # fails. We observe that case as unavailable and leave Runtime alone.
            original = point3(sim.pathfinder.snap_point(center, sim._largest_indoor_island_idx))
            record.update(
                start_base_position=list(start),
                start_reference_position=list(reference),
                start_rotation_yaw_rad=rotation,
                goal_center=list(center),
                official_robot_at_threshold_m=radius,
                oracle_stop_radius_m=stop_radius,
                original_point=list(original),
            )

            def path_length(target: Point3) -> float | None:
                """Match the resolver's strict route check without its straight-line fallback."""
                path = self.api.ShortestPath()
                path.requested_start = start
                path.requested_end = target
                if not self.pathfinder.find_path(path):
                    return None
                points = path.points
                if len(points) < 2 or math.dist(point3(points[-1]), target) > 0.05:
                    raise GoalRegionResolutionError("pathfinder returned an incomplete route")
                distance = float(path.geodesic_distance)
                if not math.isfinite(distance) or distance < 0:
                    raise GoalRegionResolutionError("pathfinder route length is unavailable")
                return distance

            def project_center(goal: Point3) -> Point3 | None:
                """Use the same deterministic agent-floor projection as the Local How resolver."""
                return point3(self.pathfinder.snap_point((goal[0], start[1], goal[2])))

            selected = select_goal_region_point(
                original_point=original,
                goal_center=center,
                reference_offset=tuple(reference[index] - start[index] for index in range(3)),
                radius_m=radius,
                stop_radius_m=stop_radius,
                navmesh_vertices=(),
                path_length=path_length,
                project_center=project_center,
                max_path_queries=PATH_QUERIES_PER_RECORD,
            )
            record.update(
                status="supported", reason_code="static_path_witness", selection=selected.as_dict()
            )
        except GoalRegionSearchMiss as error:
            record.update(
                status="not_found",
                reason_code="initial_candidates_exhausted",
                search=error.search.copy(),
            )
        except Exception as error:
            record.update(error_type=type(error).__name__, error=str(error)[:512])
        record["elapsed_ms"] = (time.perf_counter() - started) * 1_000
        return record


def build_reset_route_support(
    environment: Any,
    semantic: dict[str, Any],
    preassignment: dict[str, Any],
    local_how: dict[str, Any],
    runtime_sources: dict[str, Any],
) -> dict[str, Any]:
    """Freeze diagnostic-only observations from the one actual preassignment reset.

    Every goal/endpoint pair is represented or the whole scope is unavailable.
    Lookup/build faults remain unavailable and never become a negative candidate
    constraint. Identity faults raise; the caller decides archival failure policy.
    """
    started = time.perf_counter()
    identity = preassignment["identity"]
    if identity["semantic_evidence_digest"] != semantic["digest"]:
        raise ValueError("route support differs from reset semantic identity")
    if (
        local_how.get("reset_route_support_enabled") is not True
        or local_how.get("navigation_point_resolver") != "official-any-at-agent-navmesh/v0.1"
    ):
        raise ValueError("route support differs from active Local How profile")
    goals = sorted(any_at_conjunct_names(semantic["goal"]))
    geometry_enabled = local_how.get("reset_route_geometry_enabled") is True
    geometry_budget = RegionBudget()
    endpoints = sorted({(item["agent_id"], item["node_id"]) for item in preassignment["records"]})
    records: list[dict[str, Any]] = []
    reason = "observed"
    if not goals:
        reason = "unsupported_goal"
    elif len(goals) * len(endpoints) > MAX_ROUTE_RECORDS:
        reason = "record_budget"
    else:
        for agent_id, node_id in endpoints:
            probe: ResetRouteProbe | None = None
            initialization_error: Exception | None = None
            try:
                probe = ResetRouteProbe(environment, agent_id)
            except Exception as error:
                initialization_error = error
            for destination in goals:
                observation = _empty_observation()
                if probe is not None:
                    observation = probe.observe(destination)
                else:
                    observation.update(
                        reason_code="probe_initialization_error",
                        error_type=type(initialization_error).__name__,
                        error=str(initialization_error)[:512],
                    )
                if (
                    observation["start_base_position"] is not None
                    and observation["start_base_position"]
                    != preassignment["initial_agent_positions"][str(agent_id)]
                ):
                    observation.update(
                        status="unavailable",
                        reason_code="reset_state_changed",
                        selection=None,
                        search=None,
                        error_type="ResetStateChanged",
                        error="agent moved since the reset snapshot",
                    )
                if geometry_enabled:
                    observation["region_analysis"] = (
                        probe.observe_region(observation, geometry_budget)
                        if probe is not None
                        else unknown_region("route_observation_unavailable")
                    )
                records.append(
                    {
                        "agent_id": agent_id,
                        "node_id": node_id,
                        "destination": destination,
                        **observation,
                    }
                )
    body: dict[str, Any] = {
        "schema_version": (
            GEOMETRY_ROUTE_SUPPORT_SCHEMA if geometry_enabled else RESET_ROUTE_SUPPORT_SCHEMA
        ),
        "authority": "deployment-observed-reset-state",
        "purpose": "diagnostic_only",
        "identity": {
            **identity,
            "preassignment_digest": preassignment["digest"],
            "local_how_digest": preassignment_digest(local_how),
            "runtime_sources_digest": preassignment_digest(runtime_sources),
        },
        "initial_agent_positions": preassignment["initial_agent_positions"],
        "probe": {
            "revision": "goal-region-initial-candidates/v0.1",
            "scope": "reset_state_only",
            "navmesh_vertex_search_performed": False,
            "max_records": MAX_ROUTE_RECORDS,
            "max_path_queries_per_record": PATH_QUERIES_PER_RECORD,
            "max_total_path_queries": MAX_TOTAL_PATH_QUERIES,
            "is_node_exclusion": False,
            **({"region_analysis": REGION_PROFILE.copy()} if geometry_enabled else {}),
        },
        "scope_status": "available" if reason == "observed" else "unavailable",
        "scope_reason": reason,
        "records": records,
        "elapsed_ms": (time.perf_counter() - started) * 1_000,
    }
    return {**body, "digest": preassignment_digest(body)}
