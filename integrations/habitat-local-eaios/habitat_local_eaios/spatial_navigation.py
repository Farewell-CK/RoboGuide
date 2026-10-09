"""Pure spatial-arrival decisions for an opt-in differential-base Local How.

These decisions concern the selected navigation point, not official PDDL truth.
They never select an entity, alter robot ability, or authorize another execution.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from .goal_region_navigation import GoalRegionResolutionError, Point3, point3

SPATIAL_ARRIVAL_PROFILE = "spatial-route-arrival/v0.1"
GOAL_AWARE_ARRIVAL_PROFILE = "live-reference-goal-region/v0.1"
GOAL_AWARE_SELECTION_SCHEMA = "roboguide.habitat-goal-region-navigation/v0.6"
GOAL_AWARE_POINT_RESOLVER = "official-any-at-live-arrival/v0.1"
MAX_ROUTE_POINTS = 4096


@dataclass(frozen=True)
class NavigationGoalRegion:
    """Actual reference and exact entity geometry; never an official verdict."""

    reference_position: Point3
    center: Point3
    radius_m: float

    def reference_distance(self) -> float:
        """Validate finite geometry before checking local completion eligibility."""
        if (
            isinstance(self.radius_m, bool)
            or not math.isfinite(self.radius_m)
            or self.radius_m <= 0
        ):
            raise GoalRegionResolutionError("live goal-region radius is unavailable")
        return math.dist(point3(self.reference_position), point3(self.center))

    @property
    def bound_m(self) -> float:
        """Keep the existing selection margin separate from the official radius."""
        self.reference_distance()
        return self.radius_m - min(0.02, self.radius_m * 0.01)


@dataclass(frozen=True)
class SpatialNavigationDecision:
    """One bounded control decision with explicit local, non-benchmark evidence."""

    branch: Literal["follow-route", "face-entity", "arrived"]
    direction: tuple[float, float]
    distance_m: float
    horizontal_distance_m: float
    vertical_distance_m: float
    goal_reference_distance_m: float | None = None


def heading_error(forward: tuple[float, float], direction: tuple[float, float]) -> float:
    """Measure a planar heading; a coincident target needs no additional turn."""
    if (
        len(forward) != 2
        or len(direction) != 2
        or not all(math.isfinite(value) for value in (*forward, *direction))
        or math.hypot(*forward) <= 1e-8
    ):
        raise GoalRegionResolutionError("spatial navigation forward direction is invalid")
    if math.hypot(*direction) <= 1e-8:
        return 0.0
    cross = forward[0] * direction[1] - forward[1] * direction[0]
    dot = forward[0] * direction[0] + forward[1] * direction[1]
    return math.atan2(abs(cross), dot)


def spatial_navigation_decision(
    *,
    position: Point3,
    final_point: Point3,
    entity_point: Point3,
    route_points: tuple[Point3, ...],
    forward: tuple[float, float],
    distance_threshold_m: float,
    turn_threshold_radians: float,
    goal_region: NavigationGoalRegion | None = None,
) -> SpatialNavigationDecision:
    """Continue the route until spatial arrival, then face the exact entity.

    Finite points, a bounded successful route and unchanged vendor thresholds
    are required. A vertical-only next segment cannot fabricate planar movement
    or completion; it fails explicitly. No world state or RNG is accessed.
    """
    position, final_point, entity_point = map(point3, (position, final_point, entity_point))
    if (
        isinstance(distance_threshold_m, bool)
        or isinstance(turn_threshold_radians, bool)
        or not math.isfinite(distance_threshold_m)
        or distance_threshold_m <= 0
        or not math.isfinite(turn_threshold_radians)
        or not 0 < turn_threshold_radians <= math.pi
        or not 0 < len(route_points) <= MAX_ROUTE_POINTS
    ):
        raise GoalRegionResolutionError("spatial navigation route or thresholds are invalid")
    heading_error(forward, (1.0, 0.0))
    points = tuple(point3(point) for point in route_points)
    if math.dist(points[-1], final_point) > 0.05:
        raise GoalRegionResolutionError("spatial navigation route misses the selected point")
    delta = tuple(final_point[index] - position[index] for index in range(3))
    distance = math.dist(position, final_point)
    horizontal = math.hypot(delta[0], delta[2])
    vertical = abs(delta[1])
    goal_distance = None if goal_region is None else goal_region.reference_distance()
    goal_reached = goal_region is None or (
        goal_distance is not None and goal_distance < goal_region.bound_m
    )
    if distance < distance_threshold_m and goal_reached:
        direction = (entity_point[0] - position[0], entity_point[2] - position[2])
        branch: Literal["follow-route", "face-entity", "arrived"] = (
            "arrived"
            if heading_error(forward, direction) < turn_threshold_radians
            else "face-entity"
        )
    else:
        next_point = next(
            (
                point
                for point in points[1:]
                if math.hypot(point[0] - position[0], point[2] - position[2]) > 1e-5
            ),
            None,
        )
        if next_point is None:
            raise GoalRegionResolutionError("spatial navigation route has no planar waypoint")
        direction = (next_point[0] - position[0], next_point[2] - position[2])
        branch = "follow-route"
    return SpatialNavigationDecision(
        branch, direction, distance, horizontal, vertical, goal_distance
    )
