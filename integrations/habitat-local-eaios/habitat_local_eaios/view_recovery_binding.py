"""Attempt-scoped observation point selection through the existing navigation tool.

Only a fresh model-selected navigation after an observed stationary scan can
enter this path. Native geometry is queried on detached candidate transforms;
the original navigation action, joint stepping and collision rules still move
the robot. This is an explicit Local How extension, not read-only diagnostics.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from numbers import Integral
from types import SimpleNamespace
from typing import Any

from .goal_region_navigation import Point3, point3
from .model import IntegrationError
from .observation_control import ObservationAcquisitionControl
from .observation_optics import loaded_fixed_camera_optics
from .view_recovery import (
    MAX_FOOTPRINT_POINTS,
    MAX_RECOVERIES,
    MAX_ROUTE_POINTS,
    ObservationRecoveryUnavailable,
    ViewpointSearchMiss,
    local_grid_candidates,
    select_viewpoint,
    sphere_may_intersect_camera_frustum,
    view_recovery_declaration,
)
from .view_scan import MAX_SCAN_STEPS, ViewScanSpec

_MISSING = object()
_LOG = logging.getLogger(__name__)


def permits_terminal_arrival(
    reference: Point3, target: Point3, radius: float, margin: float
) -> bool:
    """Check only a requested terminal stance, retaining its existing stopping uncertainty.

    This necessary geometric envelope is neither a route nor a visibility
    certificate, and imposes no constraint on intermediate robot positions.
    """
    if (
        any(not math.isfinite(value) for value in reference + target)
        or not math.isfinite(radius)
        or radius <= 0
        or not math.isfinite(margin)
        or margin < 0
    ):
        raise IntegrationError("compound acquisition arrival envelope is unavailable")
    return math.dist(reference, target) + margin < radius


def supports_viewpoint_recovery(scan: ViewScanSpec) -> bool:
    """Gate only on a loaded supported control; this does not prove any candidate is feasible."""
    from .view_scan import _original_base_types

    try:
        return (
            type(scan.action) is _original_base_types()[1]
            and 0 < len(scan.action._navmesh_offset) <= MAX_FOOTPRINT_POINTS
        )
    except Exception:  # noqa: BLE001 - absent geometry must preserve ordinary navigation
        return False


class NativeViewpointGeometry:
    """Read actual route and original scan-footprint checks without moving any robot."""

    def __init__(self, navigation: Any, scan: ViewScanSpec, target: str) -> None:
        """Require the exact endpoint, existing meshes and immutable entity correspondence."""
        import habitat_sim  # type: ignore[import-not-found]
        import magnum as mn  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
        from habitat.tasks.rearrange.actions.actions import (  # type: ignore[import-not-found]
            BaseVelNonCylinderAction,
        )

        if (
            type(scan.action) is not BaseVelNonCylinderAction
            or navigation._agent_index != scan.agent
            or scan.action._sim is not navigation._sim
            or navigation._task.actions.get(scan.action._action_arg_prefix + "base_velocity")
            is not scan.action
            or navigation.cur_articulated_agent is not scan.action.cur_articulated_agent
            or not getattr(navigation.pathfinder, "is_loaded", False)
            or not getattr(scan.action.pathfinder, "is_loaded", False)
            or not 0 < len(scan.action._navmesh_offset) <= MAX_FOOTPRINT_POINTS
            or scan.action.cur_grasp_mgr.snap_idx is not None
            or scan.action._ang_speed != scan.angular_scale
            or navigation._sim.ctrl_freq != scan.ctrl_freq
        ):
            raise IntegrationError("observation viewpoint lacks original own-footprint geometry")
        for offset in scan.action._navmesh_offset:
            if len(offset) != 2 or not all(math.isfinite(float(value)) for value in offset):
                raise IntegrationError("observation viewpoint original footprint is malformed")
        matches = [i for i, entity in enumerate(navigation._poss_entities) if entity.name == target]
        if len(matches) != 1:
            raise IntegrationError("observation viewpoint target does not resolve uniquely")
        self.navigation, self.scan = navigation, scan
        self.require_arrival = False
        self.target_index = matches[0]
        self.entity = navigation._poss_entities[self.target_index]
        self.center = point3(navigation._task.pddl_problem.sim_info.get_entity_pos(self.entity))
        self.start = point3(navigation.cur_articulated_agent.base_pos)
        self.episode = navigation._sim.ep_info.episode_id
        self.mesh = navigation.pathfinder
        self._hs, self._mn, self._np = habitat_sim, mn, np
        self._camera_geometry = self._load_camera_geometry()

    def _load_camera_geometry(self) -> tuple[dict[str, Any], ...]:
        """Freeze actual own semantic-camera geometry for detached candidate checks.

        Camera matrices and object bounds are read from already constructed
        Habitat objects. No sensor observation, render, predicate evaluation,
        simulator step or RNG access occurs. Missing or malformed optics fail
        closed instead of allowing a blind viewpoint move.
        """
        robot_pose = self._mn.Matrix4(self.navigation.cur_articulated_agent.sim_obj.transformation)
        cameras: list[dict[str, Any]] = []
        sensors = getattr(self.navigation._sim, "_sensors", {})
        fixed = loaded_fixed_camera_optics(
            SimpleNamespace(sim=self.navigation._sim), self.scan.agent
        )
        names = [fixed["sensor_uuid"]]
        for name, wrapper in sensors.items():
            if name not in names:
                continue
            sensor = getattr(wrapper, "_sensor_object", None)
            renderer = getattr(sensor, "render_camera", None)
            if sensor is None or renderer is None:
                raise IntegrationError("observation semantic camera is unavailable")
            hfov = float(sensor.hfov)
            viewport = renderer.viewport
            width, height = int(viewport[0]), int(viewport[1])
            if not math.isfinite(hfov) or not 0 < hfov < 180 or width <= 0 or height <= 0:
                raise IntegrationError("observation semantic camera geometry is malformed")
            view_from_body = renderer.camera_matrix @ robot_pose
            values = self._np.asarray(view_from_body)
            if values.shape != (4, 4) or not bool(self._np.isfinite(values).all()):
                raise IntegrationError("observation semantic camera matrix is malformed")
            half_horizontal = math.tan(math.radians(hfov) / 2)
            cameras.append(
                {
                    "name": name,
                    "sensor": sensor,
                    "view_from_body": view_from_body,
                    "hfov_degrees": hfov,
                    "viewport": [width, height],
                    "horizontal_tangent": half_horizontal,
                    "vertical_tangent": half_horizontal * height / width,
                }
            )
        if not cameras:
            raise IntegrationError("observation semantic camera geometry is unavailable")
        object_manager = self.navigation._sim.get_rigid_object_manager()
        object_index = self.navigation._task.pddl_problem.sim_info.search_for_entity(self.entity)
        if (
            isinstance(object_index, bool)
            or not isinstance(object_index, Integral)
            or object_index < 0
        ):
            raise IntegrationError("observation target object index is malformed")
        object_id = self.navigation._sim.scene_obj_ids[int(object_index)]
        object_instance = object_manager.get_object_by_id(object_id)
        if object_instance is None:
            raise IntegrationError("observation target object geometry is unavailable")
        box = object_instance.root_scene_node.cumulative_bb
        world_transform = object_instance.root_scene_node.transformation
        corners = [
            world_transform.transform_point(self._mn.Vector3(x, y, z))
            for x in (box.min[0], box.max[0])
            for y in (box.min[1], box.max[1])
            for z in (box.min[2], box.max[2])
        ]
        radius = max(math.dist(self.center, tuple(corner)) for corner in corners)
        if not math.isfinite(radius) or radius < 0:
            raise IntegrationError("observation target geometry is malformed")
        for camera in cameras:
            camera["target_bounding_sphere_radius_m"] = radius
        return tuple(cameras)

    def _candidate_pose(self, point: Point3, heading: float) -> Any:
        """Construct a detached upright body pose without assigning it to Habitat."""
        rotation = self._mn.Quaternion.rotation(self._mn.Rad(heading), self._mn.Vector3.y_axis())
        offset = self.scan.action.cur_articulated_agent.params.base_offset
        return self._mn.Matrix4.from_(
            rotation.to_matrix(), self._mn.Vector3(point) - rotation.transform_vector(offset)
        )

    def _camera_projections(self, point: Point3, heading: float) -> dict[str, Any]:
        """Project the target sphere through frozen camera-body matrices."""
        pose = self._candidate_pose(point, heading)
        projections: dict[str, Any] = {}
        for camera in self._camera_geometry:
            camera_point = point3(
                (camera["view_from_body"] @ pose.inverted()).transform_point(
                    self._mn.Vector3(self.center)
                )
            )
            may_intersect = sphere_may_intersect_camera_frustum(
                camera_point,
                camera["horizontal_tangent"],
                camera["vertical_tangent"],
                camera["target_bounding_sphere_radius_m"],
            )
            projections[camera["name"]] = {
                "target_camera_position": list(camera_point),
                "bounding_sphere_may_intersect_frustum": may_intersect,
                "hfov_degrees": camera["hfov_degrees"],
                "viewport": camera["viewport"],
                "occlusion": "unavailable",
                "pixel_detection": "unavailable",
            }
        return projections

    def permits_viewpoint(self, point: Point3, heading: float) -> bool:
        """Require one own camera's conservative frustum intersection."""
        return any(
            value["bounding_sphere_may_intersect_frustum"]
            for value in self._camera_projections(point, heading).values()
        )

    def viewpoint_evidence(self, point: Point3, heading: float) -> dict[str, Any]:
        """Record geometric necessary conditions without claiming visual truth."""
        projections = self._camera_projections(point, heading)
        return {
            "camera_check": "actual-own-semantic-camera-bounding-sphere-frustum",
            "camera_projections": projections,
            "source": "actual-loaded-own-cameras-and-object-bounds",
            "camera_pose_model": "frozen-body-relative-optics-at-dispatch",
            "independent_camera_control": False,
            "other_task_effects_preserved": False,
            "visibility_guaranteed": False,
            "occlusion": "unavailable",
            "pixel_detection": "unavailable",
        }

    def project(self, point: Point3) -> Point3:
        """Project only on the already loaded original scan mesh, without random sampling."""
        return point3(self.scan.action.pathfinder.snap_point(point))

    def path(self, point: Point3, *, start: Point3 | None = None) -> Any:
        """Read an actual native route; a failed query never becomes a straight line."""
        path = self._hs.ShortestPath()
        path.requested_start = (
            self.navigation.cur_articulated_agent.base_pos if start is None else start
        )
        path.requested_end = point
        if not self.mesh.find_path(path):
            return None
        if (
            not 0 < len(path.points) <= MAX_ROUTE_POINTS
            or math.dist(point3(path.points[-1]), point) > 1e-4
        ):
            raise IntegrationError("observation viewpoint native route is incomplete")
        return path

    def route_length(self, point: Point3) -> float | None:
        """Return only a successful native geodesic, never inferred physical reachability."""
        path = self.path(point, start=self.start)
        return None if path is None else float(path.geodesic_distance)

    def permits_scan(self, point: Point3, heading: float) -> bool:
        """Replay one complete bounded yaw on independent poses with unchanged native checks.

        Exact candidate prediction does not cover arrival error, dynamic peers
        or all intermediate physics. Live movement remains authoritative.
        No original action or world transform is assigned by this query.
        """
        mn, action = self._mn, self.scan.action
        rotation = mn.Quaternion.rotation(mn.Rad(heading), mn.Vector3.y_axis())
        offset = action.cur_articulated_agent.params.base_offset
        transform = mn.Matrix4.from_(
            rotation.to_matrix(), mn.Vector3(point) - rotation.transform_vector(offset)
        )
        if self.require_arrival:
            # The requested terminal stance must satisfy the explicit own-arrival
            # condition. This does not restrict intermediate physical movement.
            radius = float(self.navigation._task.pddl_problem.sim_info.robot_at_thresh)
            margin = float(self.navigation._config.dist_thresh)
            if not permits_terminal_arrival(
                point3(transform.translation), self.center, radius, margin
            ):
                return False
        # Preserve any existing navigation controller's independently declared
        # any_at envelope; this is compatibility, never a new success verdict.
        record = getattr(self.navigation, "_roboguide_selections", {}).get(self.target_index, {})
        if record.get("mode") == "official_any_at_region":
            radius = float(record["official_robot_at_threshold_m"])
            margin = float(self.navigation._config.dist_thresh)
            if (
                math.dist(point3(transform.translation), point3(record["goal_center"])) + margin
                >= radius
            ):
                return False
        delta = self.scan.normalized_yaw * self.scan.angular_scale / self.scan.ctrl_freq
        for step in range(1, MAX_SCAN_STEPS + 1):
            next_rotation = mn.Quaternion.rotation(
                mn.Rad(heading + delta * step), mn.Vector3.y_axis()
            )
            target = mn.Matrix4.from_(next_rotation.to_matrix(), transform.translation)
            rigid = self._hs.RigidState(next_rotation, target.translation)
            collision, checked = action.collision_check(
                mn.Matrix4(transform), mn.Matrix4(target), rigid, False
            )
            if collision or not self._np.allclose(
                self._np.array(checked), self._np.array(target), atol=1e-7, rtol=0
            ):
                return False
            transform = target
        return True

    def target(self, index: int) -> Any:
        """Resolve only the original entity at its unchanged observed location and episode."""
        if (
            index != self.target_index
            or self.navigation._sim.ep_info.episode_id != self.episode
            or self.navigation.pathfinder is not self.mesh
            or self.navigation._poss_entities[index] is not self.entity
            or point3(self.navigation._task.pddl_problem.sim_info.get_entity_pos(self.entity))
            != self.center
        ):
            raise IntegrationError("observation viewpoint geometry changed after selection")
        pose = self.navigation.cur_articulated_agent.sim_obj.transformation
        for camera in self._camera_geometry:
            sensor = self.navigation._sim._sensors[camera["name"]]._sensor_object
            if sensor is not camera["sensor"] or not self._np.allclose(
                self._np.asarray(sensor.render_camera.camera_matrix @ pose),
                self._np.asarray(camera["view_from_body"]),
                atol=1e-4,
                rtol=0,
            ):
                raise IntegrationError("observation fixed-camera geometry changed after selection")
        return self._np.asarray(self.center)


class ObservationViewpointRecovery:
    """Own two bounded selections and reversible navigation hooks for one acquisition attempt."""

    def __init__(
        self,
        navigation: Any,
        scan: ViewScanSpec,
        control: ObservationAcquisitionControl,
        record: Callable[[dict[str, Any]], None],
    ) -> None:
        """Freeze the existing capability and nonrenewable acquisition owner, without new motion."""
        self.navigation, self.scan, self.control, self.record = navigation, scan, control, record
        self._used = 0
        self._previous: tuple[Point3, ...] = ()
        self._hooks: list[tuple[str, Any, Any]] = []

    def _emit(self, document: dict[str, Any]) -> None:
        """Keep evidence storage failures separate from planning and original physical results."""
        try:
            self.record(document)
        except Exception:  # noqa: BLE001 - recording cannot relax a rejected recovery
            _LOG.exception("observation viewpoint evidence unavailable")

    def dispatch(
        self, call_id: str, sequence: int, stationary_scan: tuple[str, int] | None
    ) -> None:
        """Change a local arrival point only for an independently admitted fresh navigation.

        Unavailable geometry or a bounded miss stops before physical dispatch.
        It never retries the model, extends the acquisition, changes the entity
        or overwrites native target caches. Every hook is removed on next
        navigation or attempt close, including exception/cancellation cleanup.
        """
        self.close()
        if stationary_scan is None:
            return
        snapshot = self.control.snapshot()
        sample = snapshot["sample"]
        document = {
            "event": "observation-viewpoint-selection",
            "viewpoint_recovery": view_recovery_declaration(),
            "invocation_digest": snapshot["invocation_digest"],
            "tool_call_id": call_id,
            "action_sequence": sequence,
            "stationary_scan_call": list(stationary_scan),
            "remaining_simulator_steps": snapshot["remaining_simulator_steps"],
            "status": "unavailable",
            "failure_domain": "local-observation-recovery",
            "action_executed": False,
            "local_skill_completed": None,
            "benchmark_goal_satisfied": None,
        }
        try:
            if sample is None or sample["condition_holds"] is not False:
                raise IntegrationError("observation viewpoint requires current negative detection")
            if self._used >= MAX_RECOVERIES or snapshot["remaining_simulator_steps"] <= 0:
                raise IntegrationError("observation viewpoint original attempt budget exhausted")
            self._used += 1
            geometry = NativeViewpointGeometry(
                self.navigation, self.scan, self.control.invocation.object_ref
            )
            geometry.require_arrival = self.control.invocation.requires_arrival
            selected = select_viewpoint(
                geometry.start,
                geometry.center,
                geometry.project,
                geometry.route_length,
                geometry.permits_scan,
                self._previous,
                candidate_points=local_grid_candidates,
                permits_viewpoint=geometry.permits_viewpoint,
                viewpoint_evidence=geometry.viewpoint_evidence,
            )
            point = selected.point

            def target(index: int) -> Any:
                """Keep the semantic entity while selecting only a local arrival point."""
                return geometry._np.asarray(point), geometry.target(index)

            def path(destination: Any) -> Any:
                """Follow a successful original-mesh route without the vendor's fallback."""
                geometry.target(geometry.target_index)
                if point3(destination) != point:
                    raise IntegrationError("observation viewpoint received a different local point")
                result = geometry.path(point)
                if result is None:
                    raise IntegrationError("observation viewpoint route became unavailable")
                points = result.points
                if len(points) == 1:
                    if (
                        math.dist(point3(self.navigation.cur_articulated_agent.base_pos), point)
                        > 1e-6
                    ):
                        raise IntegrationError("observation viewpoint route lost its start")
                    return [points[0], points[0]]
                return points

            for name, hook in (("_get_target_for_idx", target), ("_path_to_point", path)):
                prior = vars(self.navigation).get(name, _MISSING)
                setattr(self.navigation, name, hook)
                self._hooks.append((name, prior, hook))
            self._previous += (point,)
            document.update(
                status="candidate-selected",
                selected_ground_position=list(point),
                target_entity=self.control.invocation.object_ref,
                target_position=list(geometry.center),
                candidates_examined=selected.candidates_examined,
                route_length_m=selected.route_length_m,
                heading_radians=selected.heading_radians,
                route_queries=selected.route_queries,
                scan_candidates=selected.scan_candidates,
                viewpoint_evidence=selected.viewpoint_evidence,
                recoveries_used=self._used,
                source="original-loaded-mesh-and-scan-controller",
                visibility_guaranteed=False,
                original_target_cache_changed=False,
            )
        except Exception as error:
            self.close()
            document["reason"] = str(error)[:256]
            if isinstance(error, ViewpointSearchMiss):
                document["search_progress"] = error.progress
            self._emit(document)
            raise ObservationRecoveryUnavailable(
                str(error)
                if isinstance(error, IntegrationError)
                else "observation viewpoint geometry unavailable"
            ) from error
        self._emit(document)

    def close(self) -> None:
        """Restore only this attempt's own method overrides; leave caches and peers unchanged."""
        for name, prior, hook in reversed(self._hooks):
            if vars(self.navigation).get(name) is hook:
                if prior is _MISSING:
                    delattr(self.navigation, name)
                else:
                    setattr(self.navigation, name, prior)
        self._hooks.clear()
