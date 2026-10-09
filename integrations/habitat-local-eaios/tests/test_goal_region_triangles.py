"""Exercise bounded interior candidates without changing official arrival geometry."""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.goal_region_navigation import (  # noqa: E402
    TRIANGLE_SEARCH_PROFILE,
    GoalRegionResolutionError,
    GoalRegionSearchMiss,
    Point3,
    select_goal_region_point,
)

TRIANGLE: tuple[Point3, ...] = ((-4.0, 0.0, -4.0), (4.0, 0.0, -4.0), (0.0, 0.0, 4.0))


def _arguments(**changes: Any) -> dict[str, Any]:
    """Freeze a disconnected projection and an interior-only supported route."""
    return {
        "original_point": (10.0, 0.0, 10.0),
        "goal_center": (0.0, 0.0, 0.0),
        "reference_offset": (0.0, 0.0, 0.0),
        "radius_m": 1.0,
        "stop_radius_m": 0.25,
        "navmesh_vertices": TRIANGLE,
        "navmesh_indices": (0, 1, 2),
        "path_length": lambda point: 3.0 if point == (0.0, 0.0, 0.0) else None,
        "project_center": lambda _center: (0.0, 3.0, 0.0),
        **changes,
    }


def test_triangle_interior_recovers_vertex_only_miss() -> None:
    """Select a routed interior point when all vertices and projection are unsuitable."""
    with pytest.raises(GoalRegionSearchMiss, match="no agent-specific path"):
        select_goal_region_point(**_arguments(navmesh_indices=None))
    selected = select_goal_region_point(**_arguments())
    assert selected.point == (0.0, 0.0, 0.0)
    assert selected.path_length_m == 3.0
    assert selected.estimated_stop_envelope_distance_m == 0.25
    assert selected.path_queries == 1
    assert selected.geometry_search is not None
    assert selected.geometry_search["triangles_seen"] == 1
    assert selected.geometry_search["profile"] == TRIANGLE_SEARCH_PROFILE
    assert selected.as_dict()["geometry_search"] == selected.geometry_search


def test_valid_original_keeps_exports_lazy() -> None:
    """Do not export vertices or indices when the original route already works."""

    def unavailable() -> list[Any]:
        """Expose any accidental eager geometry export as a failure."""
        raise AssertionError("unused native geometry export")

    selected = select_goal_region_point(
        **_arguments(
            original_point=(0.0, 0.0, 0.0),
            navmesh_vertices=unavailable,
            navmesh_indices=unavailable,
        )
    )
    assert selected.source == "original"
    assert selected.geometry_search is None
    assert "geometry_search" not in selected.as_dict()


def test_vertical_reference_offset_is_applied_to_interiors() -> None:
    """Use actual base/reference geometry instead of assuming a zero offset."""
    selected = select_goal_region_point(
        **_arguments(
            reference_offset=(0.0, 2.0, 0.0),
            navmesh_vertices=tuple((p[0], -2.0, p[2]) for p in TRIANGLE),
            path_length=lambda point: 3.0 if point == (0.0, -2.0, 0.0) else None,
        )
    )
    assert selected.point == (0.0, -2.0, 0.0)
    assert selected.reference_distance_m == 0.0
    assert selected.estimated_stop_envelope_distance_m == 0.25


def test_degenerate_triangle_projects_onto_its_closed_edge() -> None:
    """A finite collinear export can still provide a real interior path candidate."""
    selected = select_goal_region_point(
        **_arguments(navmesh_vertices=((-4.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 0.0, 0.0)))
    )
    assert selected.point == (0.0, 0.0, 0.0)


@pytest.mark.parametrize("indices", [(0, 1), (0, 1, -1), (0, 1, 3), (0, 1, True), (0, 1, 2.0)])
def test_malformed_indices_fail_before_path_query(indices: tuple[object, ...]) -> None:
    """Do not route points inferred from malformed topology or rounded index values."""
    queries: list[Point3] = []
    with pytest.raises(GoalRegionResolutionError, match="triangle index"):
        select_goal_region_point(**_arguments(navmesh_indices=indices, path_length=queries.append))
    assert queries == []


def test_triangle_referencing_malformed_vertex_is_unavailable() -> None:
    """Reject invalid face geometry instead of silently claiming an exhaustive miss."""
    with pytest.raises(GoalRegionResolutionError, match="triangle vertex"):
        select_goal_region_point(
            **_arguments(navmesh_vertices=((*TRIANGLE[:2], (float("nan"), 0.0, 4.0))))
        )


def test_triangle_count_budget_is_explicit() -> None:
    """Bound geometry retention/work and preserve the reason on budget exhaustion."""
    with pytest.raises(GoalRegionSearchMiss) as caught:
        select_goal_region_point(**_arguments(navmesh_indices=(0, 1, 2) * 2, max_triangles=1))
    assert caught.value.search["reason_code"] == "triangle_budget"
    assert caught.value.search["search_truncated"] is True
    geometry = caught.value.search["geometry_search"]
    assert isinstance(geometry, dict)
    assert geometry["triangles_seen"] == 0


def test_geometry_time_budget_never_claims_candidates_exhausted() -> None:
    """An incomplete scan remains a bounded miss rather than a no-route conclusion."""
    times = iter((0.0, 0.0, 0.0, 0.0, 2.0))
    with pytest.raises(GoalRegionSearchMiss) as caught:
        select_goal_region_point(**_arguments(clock=lambda: next(times)))
    assert caught.value.search["reason_code"] == "geometry_time_budget"
    assert caught.value.search["search_truncated"] is True
    geometry = caught.value.search["geometry_search"]
    assert isinstance(geometry, dict)
    assert geometry["time_budget_exhausted"] is True


def test_partial_geometry_can_retain_an_actual_positive_path() -> None:
    """A timed-out second face cannot invalidate the first face's checked path."""
    times = iter((0.0, 0.0, 0.0, 0.0, 0.0, 2.0))
    selected = select_goal_region_point(
        **_arguments(navmesh_indices=(0, 1, 2) * 2, clock=lambda: next(times))
    )
    assert selected.point == (0.0, 0.0, 0.0)
    assert selected.search_truncated is True
    assert selected.geometry_search is not None
    assert selected.geometry_search["triangles_seen"] == 1


def test_duplicate_faces_cannot_consume_duplicate_route_queries() -> None:
    """Equivalent face candidates are queried once under the original path budget."""
    queried: list[Point3] = []

    def route(point: Point3) -> float | None:
        """Retain the candidate identity and return its independent static route."""
        queried.append(point)
        return 3.0 if point == (0.0, 0.0, 0.0) else None

    selected = select_goal_region_point(
        **_arguments(navmesh_indices=(0, 1, 2, 2, 1, 0), path_length=route, max_path_queries=1)
    )
    assert queried == [(0.0, 0.0, 0.0)]
    assert selected.path_queries == 1


def test_interior_intersection_without_actual_path_remains_a_miss() -> None:
    """Positive triangle geometry never substitutes for the required route query."""
    with pytest.raises(GoalRegionSearchMiss) as caught:
        select_goal_region_point(**_arguments(path_length=lambda _point: None))
    assert caught.value.search["reason_code"] == "candidates_exhausted"
    assert caught.value.search["path_queries"] == 1


@pytest.mark.parametrize("distance", [1.932855368354129, 1.9388604134739469])
def test_small_intersection_is_not_stop_compatible(distance: float) -> None:
    """A goal-ball intersection cannot bypass the unchanged conservative stop envelope."""
    assert distance < 2.0
    assert math.hypot(distance, 0.5) > 1.98
    queried: list[Point3] = []
    with pytest.raises(GoalRegionSearchMiss):
        select_goal_region_point(
            **_arguments(
                goal_center=(0.0, distance, 0.0),
                radius_m=2.0,
                stop_radius_m=0.5,
                path_length=queried.append,
                project_center=None,
            )
        )
    assert queried == []


def test_new_candidates_cannot_exceed_remaining_path_budget() -> None:
    """Original and projected misses consume the same budget as interior candidates."""
    queried: list[Point3] = []
    with pytest.raises(GoalRegionSearchMiss) as caught:
        select_goal_region_point(
            **_arguments(
                original_point=(0.1, 0.0, 0.0),
                project_center=lambda _center: (0.2, 0.0, 0.0),
                path_length=queried.append,
                max_path_queries=2,
            )
        )
    assert queried == [(0.1, 0.0, 0.0), (0.2, 0.0, 0.0)]
    assert caught.value.search["path_queries"] == 2
    assert caught.value.search["reason_code"] == "path_query_budget"


@pytest.mark.parametrize("seconds", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_geometry_time_budget_is_rejected(seconds: float) -> None:
    """Require a finite positive bound before exporting any mesh or checking a route."""
    with pytest.raises(GoalRegionResolutionError, match="budget is invalid"):
        select_goal_region_point(**_arguments(max_geometry_seconds=seconds))
