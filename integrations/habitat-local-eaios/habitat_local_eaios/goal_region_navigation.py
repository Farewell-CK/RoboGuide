"""Bounded, deterministic Local How target selection for official distance goals."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

Point3 = tuple[float, float, float]
MAX_NAVMESH_VERTICES = 100_000
MAX_PATH_QUERIES = 256


class GoalRegionResolutionError(RuntimeError):
    """Report missing or exhausted evidence for a navigable goal-region point."""


@dataclass(frozen=True)
class GoalRegionSelection:
    """Retain the selected Local How point and its bounded search provenance."""

    point: Point3
    source: Literal["original", "agent-navmesh"]
    original_status: Literal["reachable", "outside_goal_region", "no_agent_path"]
    reference_distance_m: float
    path_length_m: float
    vertices_seen: int
    candidates_in_region: int
    path_queries: int
    search_truncated: bool

    def as_dict(self) -> dict[str, object]:
        """Return JSON-safe evidence without claiming official goal truth."""
        return {
            "point": list(self.point),
            "source": self.source,
            "original_status": self.original_status,
            "estimated_pddl_reference_distance_m": self.reference_distance_m,
            "path_length_m": self.path_length_m,
            "vertices_seen": self.vertices_seen,
            "candidates_in_region": self.candidates_in_region,
            "path_queries": self.path_queries,
            "search_truncated": self.search_truncated,
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
    navmesh_vertices: Sequence[Sequence[object]] | Callable[[], Sequence[Sequence[object]]],
    path_length: Callable[[Point3], float | None],
    max_vertices: int = MAX_NAVMESH_VERTICES,
    max_path_queries: int = MAX_PATH_QUERIES,
) -> GoalRegionSelection:
    """Prefer a reachable original point, then search the agent's own navmesh.

    The official radius is read from Habitat but is never changed here. The
    current base-to-PDDL-reference offset estimates the reference position at
    a candidate; only the official evaluator can decide actual predicate truth.
    Selection uses no RNG or simulator step. A bounded miss fails explicitly.
    """
    center = point3(goal_center)
    offset = point3(reference_offset)
    original = point3(original_point)
    if not math.isfinite(radius_m) or radius_m <= 0:
        raise GoalRegionResolutionError("official any_at radius is unavailable")
    if max_vertices < 1 or max_path_queries < 1:
        raise GoalRegionResolutionError("goal-region search budget is invalid")
    # Leave a small geometric margin for numeric error. This is not a changed
    # benchmark threshold and cannot guarantee truth after local skill stopping.
    bound = radius_m - min(0.1, radius_m * 0.1)

    def estimated_distance(candidate: Point3) -> float:
        """Estimate the official 3D reference distance at a candidate base."""
        reference = tuple(candidate[index] + offset[index] for index in range(3))
        return math.dist(reference, center)

    queries = 0
    original_distance = estimated_distance(original)
    original_status: Literal["reachable", "outside_goal_region", "no_agent_path"] = (
        "outside_goal_region"
    )
    if original_distance < bound:
        queries += 1
        original_path = path_length(original)
        if original_path is not None and math.isfinite(original_path) and original_path >= 0:
            return GoalRegionSelection(
                original,
                "original",
                "reachable",
                original_distance,
                original_path,
                0,
                0,
                queries,
                False,
            )
        original_status = "no_agent_path"

    vertices = navmesh_vertices() if callable(navmesh_vertices) else navmesh_vertices
    if len(vertices) > max_vertices:
        raise GoalRegionResolutionError("agent navmesh vertex budget exhausted")
    candidates: set[Point3] = set()
    for raw_vertex in vertices:
        try:
            vertex = point3(raw_vertex)
        except GoalRegionResolutionError:
            continue
        if estimated_distance(vertex) < bound and vertex != original:
            candidates.add(vertex)
    ordered = sorted(candidates, key=lambda candidate: (estimated_distance(candidate), candidate))
    budget = max_path_queries - queries
    if budget <= 0:
        raise GoalRegionResolutionError("goal-region path-query budget exhausted")
    successful: list[tuple[float, float, Point3]] = []
    for candidate in ordered[:budget]:
        queries += 1
        route = path_length(candidate)
        if route is not None and math.isfinite(route) and route >= 0:
            successful.append((estimated_distance(candidate), route, candidate))
    if not successful:
        reason = (
            "goal-region path-query budget exhausted"
            if len(ordered) > budget
            else "no agent-specific path to official goal region"
        )
        raise GoalRegionResolutionError(reason)
    distance, route, candidate = min(successful)
    return GoalRegionSelection(
        candidate,
        "agent-navmesh",
        original_status,
        distance,
        route,
        len(vertices),
        len(ordered),
        queries,
        len(ordered) > budget,
    )
