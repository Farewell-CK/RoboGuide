"""Bounded, deterministic Local How target selection for official distance goals."""

from __future__ import annotations

import math
import operator
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from .triangle_geometry import closest_triangle_point

Point3 = tuple[float, float, float]
MAX_NAVMESH_VERTICES = 100_000
MAX_NAVMESH_TRIANGLES = 200_000
MAX_PATH_QUERIES = 256
MAX_GEOMETRY_SEARCH_SECONDS = 2.0
TRIANGLE_SEARCH_PROFILE = "vertices-and-triangle-interiors/v0.1"
GOAL_REGION_SELECTION_SCHEMA = "roboguide.habitat-goal-region-navigation/v0.5"


class GoalRegionResolutionError(RuntimeError):
    """Report missing or exhausted evidence for a navigable goal-region point."""


class GoalRegionSearchMiss(GoalRegionResolutionError):
    """Retain bounded search progress without claiming physical impossibility."""

    def __init__(self, message: str, search: dict[str, object]) -> None:
        """Preserve the original failure message and JSON-safe search counters."""
        super().__init__(message)
        self.search = search.copy()


@dataclass(frozen=True)
class GoalRegionSelection:
    """Retain the selected Local How point and its bounded search provenance."""

    point: Point3
    source: Literal["original", "agent-navmesh"]
    original_status: Literal[
        "reachable", "outside_goal_region", "stop_envelope_exceeds_goal", "no_agent_path"
    ]
    reference_distance_m: float
    estimated_stop_envelope_distance_m: float
    path_length_m: float
    vertices_seen: int
    candidates_in_region: int
    path_queries: int
    search_truncated: bool
    geometry_search: dict[str, object] | None = None

    def as_dict(self) -> dict[str, object]:
        """Return JSON-safe evidence without claiming official goal truth."""
        return {
            "point": list(self.point),
            "source": self.source,
            "original_status": self.original_status,
            "estimated_pddl_reference_distance_m": self.reference_distance_m,
            "estimated_stop_envelope_distance_m": self.estimated_stop_envelope_distance_m,
            "path_length_m": self.path_length_m,
            "vertices_seen": self.vertices_seen,
            "candidates_in_region": self.candidates_in_region,
            "path_queries": self.path_queries,
            "search_truncated": self.search_truncated,
            **({"geometry_search": self.geometry_search.copy()} if self.geometry_search else {}),
        }


def point3(value: Iterable[object]) -> Point3:
    """Normalize a world point and reject missing or non-finite coordinates."""
    try:
        values = tuple(float(cast(Any, component)) for component in value)
    except (TypeError, ValueError) as error:
        raise GoalRegionResolutionError("navigation position is not numeric") from error
    if len(values) != 3 or not all(math.isfinite(component) for component in values):
        raise GoalRegionResolutionError("navigation position must be finite 3D")
    return values[0], values[1], values[2]


def any_at_conjunct_names(expression: Mapping[str, Any]) -> frozenset[str]:
    """Identify direct official distance goals without interpreting OR or quantifiers."""
    if expression.get("kind") == "predicate":
        arguments = expression.get("arguments")
        if (
            expression.get("name") == "any_at"
            and isinstance(arguments, list)
            and len(arguments) == 1
            and isinstance(arguments[0], str)
            and arguments[0]
        ):
            return frozenset((arguments[0],))
        return frozenset()
    if (
        expression.get("kind") != "logical"
        or expression.get("operator") != "and"
        or expression.get("quantifier") is not None
    ):
        return frozenset()
    operands = expression.get("operands")
    if not isinstance(operands, list):
        return frozenset()
    names: set[str] = set()
    for item in operands:
        if not isinstance(item, dict):
            return frozenset()
        names.update(any_at_conjunct_names(item))
    return frozenset(names)


def select_goal_region_point(
    *,
    original_point: Sequence[object],
    goal_center: Sequence[object],
    reference_offset: Sequence[object],
    radius_m: float,
    stop_radius_m: float,
    navmesh_vertices: Sequence[Sequence[object]] | Callable[[], Sequence[Sequence[object]]],
    path_length: Callable[[Point3], float | None],
    project_center: Callable[[Point3], Point3 | None] | None = None,
    max_vertices: int = MAX_NAVMESH_VERTICES,
    max_path_queries: int = MAX_PATH_QUERIES,
    navmesh_indices: Sequence[object] | Callable[[], Sequence[object]] | None = None,
    max_triangles: int = MAX_NAVMESH_TRIANGLES,
    max_geometry_seconds: float = MAX_GEOMETRY_SEARCH_SECONDS,
    clock: Callable[[], float] = time.perf_counter,
    require_stop_envelope: bool = True,
) -> GoalRegionSelection:
    """Prefer a reachable original point, then search the agent's own navmesh.

    The official radius is read from Habitat but is never changed here. The
    current base-to-PDDL-reference offset estimates the reference position at
    a candidate; only the official evaluator can decide actual predicate truth.
    Optional triangle indices must describe the same vertex export. Closest
    interior/edge points and centroids supplement vertices under count/time
    bounds, but each selected point still requires an actual path query and
    the stop envelope by default. An explicitly goal-aware controller may
    instead admit a point inside the region and check the actual reference
    before finishing; this option never grants that check to the legacy action.
    The envelope estimate is still recorded. This is not an exhaustive feasibility test.
    Native export allocation/time is outside the Python search's hard bounds.
    Selection uses no RNG or simulator step. A bounded miss fails explicitly.
    """
    center = point3(goal_center)
    offset = point3(reference_offset)
    original = point3(original_point)
    if not isinstance(require_stop_envelope, bool):
        raise GoalRegionResolutionError("goal-region arrival policy is invalid")
    if not math.isfinite(radius_m) or radius_m <= 0:
        raise GoalRegionResolutionError("official any_at radius is unavailable")
    if not math.isfinite(stop_radius_m) or stop_radius_m < 0:
        raise GoalRegionResolutionError("local Oracle stop radius is unavailable")
    if (
        max_vertices < 1
        or max_path_queries < 1
        or max_triangles < 1
        or not math.isfinite(max_geometry_seconds)
        or max_geometry_seconds <= 0
    ):
        raise GoalRegionResolutionError("goal-region search budget is invalid")
    # The original Oracle stops within its own distance threshold from the
    # selected point. A point barely inside the official radius can therefore
    # still produce a local-complete / official-false outcome.
    bound = radius_m - min(0.02, radius_m * 0.01)

    def estimated_distance(candidate: Point3) -> float:
        """Estimate the official 3D reference distance at a candidate base."""
        reference = tuple(candidate[index] + offset[index] for index in range(3))
        return math.dist(reference, center)

    def stop_envelope(candidate: Point3) -> float:
        """Rank base points by planar stop tolerance and PDDL reference offset.

        The navmesh projection estimates final height. Dynamic movement and
        rotation remain unproven, so this value is never official goal truth.
        """
        horizontal = math.hypot(candidate[0] - center[0], candidate[2] - center[2])
        offset_horizontal = math.hypot(offset[0], offset[2])
        vertical = candidate[1] + offset[1] - center[1]
        return math.hypot(vertical, horizontal + offset_horizontal + stop_radius_m)

    def admitted(candidate: Point3) -> bool:
        """Admit an exact region point under the explicitly selected arrival policy."""
        return estimated_distance(candidate) < bound and (
            not require_stop_envelope or stop_envelope(candidate) < bound
        )

    queries = 0
    vertices_seen = 0
    candidates_seen = 0
    geometry_search: dict[str, object] | None = (
        {
            "profile": TRIANGLE_SEARCH_PROFILE,
            "max_vertices": max_vertices,
            "max_triangles": max_triangles,
            "max_geometry_seconds": max_geometry_seconds,
            "triangles_seen": 0,
            "triangle_candidates_in_region": 0,
            "time_budget_exhausted": False,
        }
        if navmesh_indices is not None
        else None
    )

    def search_miss(message: str, reason: str, truncated: bool) -> GoalRegionSearchMiss:
        """Attach actual query counts to a miss, never infer exhaustive reachability."""
        return GoalRegionSearchMiss(
            message,
            {
                "reason_code": reason,
                "vertices_seen": vertices_seen,
                "candidates_in_region": candidates_seen,
                "path_queries": queries,
                "search_truncated": truncated,
                **({"geometry_search": geometry_search.copy()} if geometry_search else {}),
            },
        )

    original_distance = estimated_distance(original)
    original_status: Literal[
        "reachable", "outside_goal_region", "stop_envelope_exceeds_goal", "no_agent_path"
    ] = "outside_goal_region"
    if original_distance < bound and stop_envelope(original) >= bound:
        original_status = "stop_envelope_exceeds_goal"
    if admitted(original):
        queries += 1
        original_path = path_length(original)
        if original_path is not None and math.isfinite(original_path) and original_path >= 0:
            return GoalRegionSelection(
                original,
                "original",
                "reachable",
                original_distance,
                stop_envelope(original),
                original_path,
                0,
                0,
                queries,
                False,
            )
        original_status = "no_agent_path"

    projected: Point3 | None = None
    if project_center is not None:
        projected = project_center(center)
        if projected is not None:
            projected = point3(projected)
            if admitted(projected) and projected != original:
                candidates_seen += 1
                if queries >= max_path_queries:
                    raise search_miss(
                        "goal-region path-query budget exhausted", "path_query_budget", True
                    )
                queries += 1
                projected_path = path_length(projected)
                if (
                    projected_path is not None
                    and math.isfinite(projected_path)
                    and projected_path >= 0
                ):
                    return GoalRegionSelection(
                        projected,
                        "agent-navmesh",
                        original_status,
                        estimated_distance(projected),
                        stop_envelope(projected),
                        projected_path,
                        0,
                        1,
                        queries,
                        False,
                    )

    if queries >= max_path_queries:
        raise search_miss("goal-region path-query budget exhausted", "path_query_budget", True)
    started = clock()
    vertices = navmesh_vertices() if callable(navmesh_vertices) else navmesh_vertices
    if len(vertices) > max_vertices:
        raise search_miss("agent navmesh vertex budget exhausted", "vertex_budget", True)
    candidates: set[Point3] = set()
    normalized: list[Point3 | None] = []

    def exhausted_time() -> bool:
        """Bound Python geometry work without pretending to cap native exports."""
        return geometry_search is not None and clock() - started >= max_geometry_seconds

    def add_candidate(candidate: Point3) -> None:
        """Deduplicate admitted points before the independent route-query budget."""
        if admitted(candidate) and candidate != original and candidate != projected:
            candidates.add(candidate)

    for raw_vertex in vertices:
        if exhausted_time():
            break
        vertices_seen += 1
        try:
            vertex = point3(raw_vertex)
        except GoalRegionResolutionError:
            normalized.append(None)
            continue
        normalized.append(vertex)
        add_candidate(vertex)
    geometry_truncated = len(normalized) < len(vertices)
    if navmesh_indices is not None and not geometry_truncated:
        indices = navmesh_indices() if callable(navmesh_indices) else navmesh_indices
        if len(indices) % 3:
            raise GoalRegionResolutionError("agent navmesh triangle index layout is invalid")
        if len(indices) // 3 > max_triangles:
            raise search_miss("agent navmesh triangle budget exhausted", "triangle_budget", True)
        reference_center = tuple(center[index] - offset[index] for index in range(3))
        envelope_center = center[0], center[1] - offset[1], center[2]
        interior: set[Point3] = set()
        for position in range(0, len(indices), 3):
            if exhausted_time():
                geometry_truncated = True
                break
            triangle: list[Point3] = []
            for value in indices[position : position + 3]:
                try:
                    index = operator.index(cast(Any, value))
                except TypeError as error:
                    raise GoalRegionResolutionError(
                        "agent navmesh triangle index is invalid"
                    ) from error
                if isinstance(value, bool) or not 0 <= index < len(normalized):
                    raise GoalRegionResolutionError("agent navmesh triangle index is invalid")
                face_vertex = normalized[index]
                if face_vertex is None:
                    raise GoalRegionResolutionError("agent navmesh triangle vertex is invalid")
                triangle.append(face_vertex)
            first, second, third = triangle
            points = (
                closest_triangle_point(point3(reference_center), first, second, third),
                closest_triangle_point(envelope_center, first, second, third),
                point3(sum(vertex[index] for vertex in triangle) / 3 for index in range(3)),
            )
            for candidate in points:
                candidate = point3(candidate)
                if admitted(candidate) and candidate != original and candidate != projected:
                    interior.add(candidate)
                    add_candidate(candidate)
            assert geometry_search is not None
            geometry_search["triangles_seen"] = position // 3 + 1
        assert geometry_search is not None
        geometry_search["triangle_candidates_in_region"] = len(interior)
    if geometry_search is not None:
        geometry_search["time_budget_exhausted"] = geometry_truncated
    ordered = sorted(candidates, key=lambda candidate: (stop_envelope(candidate), candidate))
    candidates_seen += len(ordered)
    budget = max_path_queries - queries
    if budget <= 0:
        raise search_miss("goal-region path-query budget exhausted", "path_query_budget", True)
    successful: list[tuple[float, float, Point3]] = []
    for candidate in ordered[:budget]:
        queries += 1
        route = path_length(candidate)
        if route is not None and math.isfinite(route) and route >= 0:
            successful.append((stop_envelope(candidate), route, candidate))
    if not successful:
        if geometry_truncated:
            raise search_miss(
                "goal-region geometry time budget exhausted", "geometry_time_budget", True
            )
        reason = (
            "goal-region path-query budget exhausted"
            if len(ordered) > budget
            else "no agent-specific path to stop-compatible official goal region"
        )
        raise search_miss(
            reason,
            "path_query_budget" if len(ordered) > budget else "candidates_exhausted",
            len(ordered) > budget,
        )
    envelope, route, candidate = min(successful)
    return GoalRegionSelection(
        candidate,
        "agent-navmesh",
        original_status,
        estimated_distance(candidate),
        envelope,
        route,
        vertices_seen,
        candidates_seen,
        queries,
        geometry_truncated or len(ordered) > budget,
        geometry_search,
    )
