"""Bounded static component geometry, separate from physical reachability.

Only detached mesh arrays are inspected. A disjoint result concerns the frozen
component, goal and base/reference geometry; it never authorizes Node exclusion,
changes a navigation target, or proves a dynamic world cannot satisfy a goal.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .goal_region_navigation import Point3, point3
from .preassignment_feasibility import preassignment_digest
from .triangle_geometry import closest_triangle_point as closest_triangle_point

REGION_SCHEMA = "roboguide.static-navigation-region/v0.1"
MAX_COMPONENT_VERTICES = 25_000
MAX_COMPONENT_TRIANGLES = 50_000
MAX_TOTAL_TRIANGLE_CHECKS = 200_000
MAX_START_SNAP_DISTANCE_M = 0.25
GEOMETRY_MARGIN_M = 0.001
MAX_ANALYSIS_SECONDS = 1.0
REGION_PROFILE = {
    "revision": REGION_SCHEMA,
    "scope": "reset_static_start_component",
    "max_component_vertices": MAX_COMPONENT_VERTICES,
    "max_component_triangles": MAX_COMPONENT_TRIANGLES,
    "max_total_triangle_checks": MAX_TOTAL_TRIANGLE_CHECKS,
    "max_start_snap_distance_m": MAX_START_SNAP_DISTANCE_M,
    "geometry_margin_m": GEOMETRY_MARGIN_M,
    "max_analysis_seconds_per_record": MAX_ANALYSIS_SECONDS,
    "is_node_exclusion": False,
    "proves_physical_reachability": False,
}


def unknown_region(reason: str) -> dict[str, Any]:
    """Preserve absent or incomplete geometry without publishing a false minimum."""
    return {
        "schema_version": REGION_SCHEMA,
        "scope": "reset_static_start_component",
        "status": "unknown",
        "reason_code": reason,
        "component_id": None,
        "mesh_digest": None,
        "start_snap_position": None,
        "start_snap_distance_m": None,
        "vertices": 0,
        "triangles": 0,
        "triangles_examined": 0,
        "complete": False,
        "minimum_reference_distance_m": None,
        "minimum_base_distance_m": None,
        "orientation_safe_distance_lower_bound_m": None,
        "closest_base_position": None,
        "closest_triangle": None,
        "closest_base_center_position": None,
        "closest_base_center_triangle": None,
        "elapsed_ms": 0.0,
    }


def _subtract(first: Point3, second: Point3) -> Point3:
    """Subtract finite mesh vectors without consulting simulator state."""
    return first[0] - second[0], first[1] - second[1], first[2] - second[2]


@dataclass
class RegionBudget:
    """Bound total triangle work across every endpoint/goal of one reset."""

    remaining: int = MAX_TOTAL_TRIANGLE_CHECKS


@dataclass(frozen=True)
class ComponentMesh:
    """Retain one validated complete component under explicit array bounds."""

    component_id: int
    snapped_start: Point3
    vertices: tuple[Point3, ...]
    triangles: tuple[tuple[int, int, int], ...]
    digest: str

    @classmethod
    def read(cls, pathfinder: Any, start: Point3) -> ComponentMesh:
        """Read only a detached mesh; oversized or malformed exports remain unknown.

        The vendor allocates its returned arrays before their size is known.
        These limits bound retained Python geometry, not native recomputation
        memory or wall time; callers must not claim a whole-process hard cap.
        """
        component = int(pathfinder.get_island(start))
        if component < 0:
            raise ValueError("missing_start_component")
        snapped = point3(pathfinder.snap_point(start, component))
        if math.dist(start, snapped) > MAX_START_SNAP_DISTANCE_M:
            raise ValueError("start_projection_unresolved")
        raw_vertices = pathfinder.build_navmesh_vertices(component)
        if not 0 < len(raw_vertices) <= MAX_COMPONENT_VERTICES:
            raise ValueError("component_vertex_budget")
        raw_indices = pathfinder.build_navmesh_vertex_indices(component)
        if not 0 < len(raw_indices) <= MAX_COMPONENT_TRIANGLES * 3 or len(raw_indices) % 3:
            raise ValueError("component_triangle_budget_or_layout")
        vertices = tuple(point3(value) for value in raw_vertices)
        indices: list[int] = []
        for value in raw_indices:
            # Compiled Habitat uses numpy integer scalars; never round float indices.
            if isinstance(value, bool) or not hasattr(value, "__index__"):
                raise ValueError("component_index_invalid")
            index = value.__index__()
            if not isinstance(index, int) or not 0 <= index < len(vertices):
                raise ValueError("component_index_invalid")
            indices.append(index)
        triangles = tuple(
            (indices[k], indices[k + 1], indices[k + 2]) for k in range(0, len(indices), 3)
        )
        digest = preassignment_digest({"vertices": [list(p) for p in vertices], "indices": indices})
        return cls(component, snapped, vertices, triangles, digest)


def analyze_region(
    mesh: ComponentMesh,
    start: Point3,
    reference: Point3,
    center: Point3,
    radius: float,
    budget: RegionBudget,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """Compute a complete static minimum or explicitly retain unknown evidence.

    Reference rotation can change the snapshot offset. A disjoint conclusion
    additionally uses a conservative bound over all rotations of that offset.
    Contact with the radius or a partial/time-limited scan never proves disjoint.
    No positive intersection is a path witness or a physical success verdict.
    """
    result = unknown_region("invalid_geometry")
    if not math.isfinite(radius) or radius <= 0:
        return result
    started = clock()
    result.update(
        component_id=mesh.component_id,
        mesh_digest=mesh.digest,
        start_snap_position=list(mesh.snapped_start),
        start_snap_distance_m=math.dist(start, mesh.snapped_start),
        vertices=len(mesh.vertices),
        triangles=len(mesh.triangles),
    )
    offset = _subtract(reference, start)
    base_center = _subtract(center, offset)
    minimum, base_minimum = math.inf, math.inf
    closest: Point3 | None = None
    closest_vertices: Sequence[Point3] | None = None
    base_closest: Point3 | None = None
    base_closest_vertices: Sequence[Point3] | None = None
    reason = "triangle_budget"
    for indices in mesh.triangles:
        if budget.remaining <= 0:
            break
        if clock() - started >= MAX_ANALYSIS_SECONDS:
            reason = "analysis_time_budget"
            break
        budget.remaining -= 1
        triangle = mesh.vertices[indices[0]], mesh.vertices[indices[1]], mesh.vertices[indices[2]]
        point = closest_triangle_point(base_center, *triangle)
        distance = math.dist(point, base_center)
        if not math.isfinite(distance):
            reason = "nonfinite_geometry"
            break
        if distance < minimum:
            minimum, closest, closest_vertices = distance, point, triangle
        base_point = closest_triangle_point(center, *triangle)
        base_distance = math.dist(base_point, center)
        if not math.isfinite(base_distance):
            reason = "nonfinite_geometry"
            break
        if base_distance < base_minimum:
            base_minimum, base_closest, base_closest_vertices = base_distance, base_point, triangle
        result["triangles_examined"] += 1
    complete = result["triangles_examined"] == len(mesh.triangles) and closest is not None
    result.update(complete=complete, reason_code=reason, elapsed_ms=(clock() - started) * 1000)
    if not complete or closest is None:
        return result
    lower_bound = max(0.0, base_minimum - math.dist(start, reference))
    if minimum < radius - GEOMETRY_MARGIN_M:
        status, reason = "intersects", "static_triangle_intersection"
    elif lower_bound > radius + GEOMETRY_MARGIN_M:
        status, reason = "disjoint", "static_component_disjoint"
    else:
        status, reason = "unknown", "boundary_or_reference_rotation"
    result.update(
        status=status,
        reason_code=reason,
        minimum_reference_distance_m=minimum,
        minimum_base_distance_m=base_minimum,
        orientation_safe_distance_lower_bound_m=lower_bound,
        closest_base_position=list(closest),
        closest_triangle=[list(value) for value in closest_vertices or ()],
        closest_base_center_position=list(base_closest or ()),
        closest_base_center_triangle=[list(value) for value in base_closest_vertices or ()],
    )
    return result
