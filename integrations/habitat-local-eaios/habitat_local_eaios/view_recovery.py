"""Bounded candidate search for an explicitly selected same-entity navigation.

Candidates are Local How geometry, never new semantic destinations or evidence
of detection, physical safety or official success. Missing map/control evidence
refuses recovery; it never relaxes the supplied controller's checks.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .goal_region_navigation import Point3, point3
from .model import IntegrationError

MAX_RECOVERIES = 2
RADII_M = (0.4, 0.7, 1.0)
DIRECTIONS = 8
MAX_SEARCH_SECONDS = 1.0
MAX_ROUTE_POINTS = 4096
MAX_FOOTPRINT_POINTS = 16


class ObservationRecoveryUnavailable(IntegrationError):
    """A local bounded recovery could not be prepared; this is not a Provider failure."""


def view_recovery_declaration() -> dict[str, Any]:
    """Publish a finite local search with unchanged semantic and execution budgets."""
    return {
        "profile": "bounded-observation-viewpoint/v0.1",
        "operation": "observation.acquire@v1",
        "activation": "per-endpoint-loaded-noncylindrical-footprint",
        "trigger": "fresh-selected-navigation-after-observed-stationary-scan",
        "tool": "nav_to_obj",
        "target_policy": "same-committed-entity",
        "max_recoveries_per_attempt": MAX_RECOVERIES,
        "candidate_radii_m": list(RADII_M),
        "directions_per_radius": DIRECTIONS,
        "max_search_seconds": MAX_SEARCH_SECONDS,
        "max_route_points": MAX_ROUTE_POINTS,
        "max_footprint_points": MAX_FOOTPRINT_POINTS,
        "candidate_check": "unchanged-original-scan-footprint-and-native-route",
        "missing_evidence": "local-recovery-unavailable",
        "original_acquisition_budget_renewed": False,
        "physical_safety_or_visibility_guaranteed": False,
        "official_success_authority": "habitat-pddl",
    }


@dataclass(frozen=True)
class ViewpointSelection:
    """One actual route-supported candidate, not a physical arrival or visibility proof."""

    point: Point3
    route_length_m: float
    heading_radians: float
    candidates_examined: int


def select_viewpoint(
    start: Point3,
    entity: Point3,
    project: Callable[[Point3], Point3],
    route_length: Callable[[Point3], float | None],
    permits_scan: Callable[[Point3, float], bool],
    previous: tuple[Point3, ...] = (),
    clock: Callable[[], float] = time.monotonic,
) -> ViewpointSelection:
    """Search a fixed local stencil without RNG, unbounded exports or straight-line fallback.

    A miss is explicitly a bounded search miss, never proof of impossibility.
    Projection, actual route and original footprint must all agree. Exceptions
    remain unavailable instead of skipping malformed evidence to obtain success.
    """
    start, entity = point3(start), point3(entity)
    if len(previous) > MAX_RECOVERIES:
        raise IntegrationError("observation viewpoint history exceeds its attempt budget")
    excluded = tuple(point3(point) for point in previous)
    began = clock()
    count = 0
    for radius in RADII_M:
        for direction in range(DIRECTIONS):
            if clock() - began >= MAX_SEARCH_SECONDS:
                raise IntegrationError("observation viewpoint search time budget exhausted")
            count += 1
            angle = direction * 2 * math.pi / DIRECTIONS
            desired = (
                start[0] + radius * math.cos(angle),
                start[1],
                start[2] + radius * math.sin(angle),
            )
            point = point3(project(desired))
            distance = math.dist(start, point)
            if (
                math.dist(desired, point) > 0.2
                or not 0.25 <= distance <= RADII_M[-1] + 1e-6
                or any(math.dist(point, prior) < 0.15 for prior in excluded)
            ):
                continue
            length = route_length(point)
            if length is None:
                continue
            if isinstance(length, bool) or not math.isfinite(length) or length < distance - 1e-4:
                raise IntegrationError("observation viewpoint route evidence is malformed")
            if length > 1.5:
                continue
            heading = math.atan2(-(entity[2] - point[2]), entity[0] - point[0])
            if permits_scan(point, heading):
                if clock() - began >= MAX_SEARCH_SECONDS:
                    raise IntegrationError("observation viewpoint search time budget exhausted")
                return ViewpointSelection(point, length, heading, count)
    raise IntegrationError("bounded observation viewpoint search found no supported candidate")
