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
MAX_CANDIDATES = 512
MAX_ROUTE_QUERIES = 64
MAX_SCAN_CANDIDATES = 32
LOCAL_GRID_HALF_WIDTH_M = 1.2
LOCAL_GRID_SPACING_M = 0.15


def sphere_may_intersect_camera_frustum(
    camera_point: tuple[float, float, float],
    horizontal_tangent: float,
    vertical_tangent: float,
    radius_m: float,
) -> bool:
    """Return a conservative camera-frustum intersection for one object sphere.

    The camera looks along ``-z``.  This is only a necessary geometric
    condition: it ignores occlusion, detector pixels and semantic predicate
    truth.  The caller must provide finite camera-frame coordinates and the
    actual sensor field of view; missing optics are rejected by the caller.
    """
    if (
        len(camera_point) != 3
        or not all(math.isfinite(value) for value in camera_point)
        or not math.isfinite(horizontal_tangent)
        or not math.isfinite(vertical_tangent)
        or horizontal_tangent <= 0
        or vertical_tangent <= 0
        or not math.isfinite(radius_m)
        or radius_m < 0
    ):
        raise IntegrationError("observation camera geometry is malformed")
    x, y, z = camera_point
    normals = (
        (1.0, 0.0, horizontal_tangent),
        (-1.0, 0.0, horizontal_tangent),
        (0.0, 1.0, vertical_tangent),
        (0.0, -1.0, vertical_tangent),
        (0.0, 0.0, 1.0),
    )
    point = (x, y, z)
    return all(
        normal[0] * point[0] + normal[1] * point[1] + normal[2] * point[2]
        <= radius_m * math.sqrt(sum(value * value for value in normal)) + 1e-9
        for normal in normals
    )


def local_grid_candidates(
    start: Point3,
    *,
    half_width_m: float = LOCAL_GRID_HALF_WIDTH_M,
    spacing_m: float = LOCAL_GRID_SPACING_M,
) -> tuple[Point3, ...]:
    """Enumerate a deterministic bounded local ground grid around one endpoint.

    These are requests to the existing mesh projection, not simulator poses.
    The grid is deliberately finite and does not infer that an unexamined
    point is unreachable or unsafe.
    """
    if (
        not math.isfinite(half_width_m)
        or half_width_m <= 0
        or not math.isfinite(spacing_m)
        or spacing_m <= 0
    ):
        raise IntegrationError("observation local candidate grid is malformed")
    count = int(math.floor(half_width_m / spacing_m))
    if count > (math.isqrt(MAX_CANDIDATES + 1) - 1) // 2:
        raise IntegrationError("observation local candidate grid exceeds its bound")
    candidates = [
        (
            start[0] + ix * spacing_m,
            start[1],
            start[2] + iz * spacing_m,
        )
        for ix in range(-count, count + 1)
        for iz in range(-count, count + 1)
        if ix != 0 or iz != 0
    ]
    candidates.sort(
        key=lambda point: (
            math.dist(start, point),
            math.atan2(point[2] - start[2], point[0] - start[0]),
            point[0],
            point[2],
        )
    )
    if len(candidates) > MAX_CANDIDATES:
        raise IntegrationError("observation local candidate grid exceeds its bound")
    return tuple(candidates)


class ObservationRecoveryUnavailable(IntegrationError):
    """A local bounded recovery could not be prepared; this is not a Provider failure."""


class ViewpointSearchMiss(IntegrationError):
    """Retain bounded query progress without claiming physical infeasibility."""

    def __init__(self, message: str, progress: dict[str, int]) -> None:
        """Preserve attempted geometry counts for an unavailable local search."""
        super().__init__(message)
        self.progress = progress


def preparation_reselection_declaration() -> dict[str, Any]:
    """Declare model feedback for one proven pre-physics miss within existing budgets."""
    return {
        "profile": "bounded-pre-dispatch-preparation-feedback/v0.1",
        "eligible_failure": "bounded-viewpoint-search-miss",
        "failure_owner": "local-capability-preparation",
        "boundary": "after-guarded-navigation-mapping-before-skill-entry",
        "reselection_budget": "shared-original-canonical-attempt-contract-recovery-budget",
        "search_budget": "original-nonrenewable-viewpoint-search-budget",
        "requires_exact_invocation_and_call": True,
        "requires_zero_physical_steps": True,
        "provider_errors_retried": False,
        "unknown_errors_retried": False,
        "model_actions_rewritten": False,
        "official_success_authority": "habitat-pddl",
    }


def validate_preparation_reselection(value: object) -> None:
    """Refuse absent, widened or Boolean-coerced preparation authority declarations."""
    expected = preparation_reselection_declaration()
    if (
        not isinstance(value, dict)
        or value != expected
        or any(type(value[key]) is not type(item) for key, item in expected.items())
    ):
        raise IntegrationError("actual action preparation feedback contract is invalid")


def view_recovery_declaration(*, optics: bool = True) -> dict[str, Any]:
    """Publish a finite local search with unchanged semantic and execution budgets."""
    declaration = {
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

    if optics:
        declaration.update(
            profile="bounded-observation-viewpoint/v0.2",
            candidate_generation="bounded-local-ground-grid",
            max_candidates=MAX_CANDIDATES,
            grid_half_width_m=LOCAL_GRID_HALF_WIDTH_M,
            grid_spacing_m=LOCAL_GRID_SPACING_M,
            max_route_queries=MAX_ROUTE_QUERIES,
            max_scan_candidates=MAX_SCAN_CANDIDATES,
            max_displacement_m=RADII_M[-1],
            max_route_length_m=1.5,
            camera_check="actual-own-semantic-camera-bounding-sphere-frustum",
            occlusion_and_pixel_detection="unknown-until-existing-observation",
            other_task_effects_preserved=False,
        )
        del declaration["candidate_radii_m"]
        del declaration["directions_per_radius"]
    return declaration


@dataclass(frozen=True)
class ViewpointSelection:
    """One actual route-supported candidate, not a physical arrival or visibility proof."""

    point: Point3
    route_length_m: float
    heading_radians: float
    candidates_examined: int
    viewpoint_evidence: dict[str, Any] | None = None
    route_queries: int = 0
    scan_candidates: int = 0


def select_viewpoint(
    start: Point3,
    entity: Point3,
    project: Callable[[Point3], Point3],
    route_length: Callable[[Point3], float | None],
    permits_scan: Callable[[Point3, float], bool],
    previous: tuple[Point3, ...] = (),
    clock: Callable[[], float] = time.monotonic,
    candidate_points: Callable[[Point3], tuple[Point3, ...]] | None = None,
    permits_viewpoint: Callable[[Point3, float], bool] | None = None,
    viewpoint_evidence: Callable[[Point3, float], dict[str, Any]] | None = None,
) -> ViewpointSelection:
    """Search bounded projected points without RNG or straight-line fallback.

    Production callers provide a finite local mesh candidate generator and a
    camera-frustum necessary-condition callback.  Unit callers may omit those
    callbacks and retain the original radial stencil.  A miss is explicitly a
    bounded search miss, never proof of impossibility. Projection, camera
    geometry, actual route and original footprint must all agree. Exceptions
    remain unavailable instead of skipping malformed evidence to obtain success.
    """
    start, entity = point3(start), point3(entity)
    if len(previous) > MAX_RECOVERIES:
        raise IntegrationError("observation viewpoint history exceeds its attempt budget")
    excluded = tuple(point3(point) for point in previous)
    began = clock()
    count = routes = scans = 0

    def miss(message: str) -> ViewpointSearchMiss:
        """Keep actual counters when a finite search stops without a supported point."""
        return ViewpointSearchMiss(
            message,
            {
                "candidates_examined": count,
                "route_queries": routes,
                "scan_candidates": scans,
            },
        )

    if candidate_points is None:
        candidates = tuple(
            (
                start[0] + radius * math.cos(direction * 2 * math.pi / DIRECTIONS),
                start[1],
                start[2] + radius * math.sin(direction * 2 * math.pi / DIRECTIONS),
            )
            for radius in RADII_M
            for direction in range(DIRECTIONS)
        )
    else:
        candidates = candidate_points(start)
    if not isinstance(candidates, tuple) or len(candidates) > MAX_CANDIDATES:
        raise IntegrationError("observation viewpoint candidates exceed their bound")
    for desired in candidates:
        if clock() - began >= MAX_SEARCH_SECONDS:
            raise miss("observation viewpoint search time budget exhausted")
        count += 1
        desired = point3(desired)
        point = point3(project(desired))
        distance = math.dist(start, point)
        if (
            math.dist(desired, point) > 0.2
            or not 0.25 <= distance <= RADII_M[-1] + 1e-6
            or any(math.dist(point, prior) < 0.15 for prior in excluded)
        ):
            continue
        heading = math.atan2(-(entity[2] - point[2]), entity[0] - point[0])
        if permits_viewpoint is not None and not permits_viewpoint(point, heading):
            continue
        if routes >= MAX_ROUTE_QUERIES:
            raise miss("observation viewpoint route query budget exhausted")
        routes += 1
        length = route_length(point)
        if length is None:
            continue
        if isinstance(length, bool) or not math.isfinite(length) or length < distance - 1e-4:
            raise IntegrationError("observation viewpoint route evidence is malformed")
        if length > 1.5:
            continue
        if scans >= MAX_SCAN_CANDIDATES:
            raise miss("observation viewpoint scan query budget exhausted")
        scans += 1
        if permits_scan(point, heading):
            if clock() - began >= MAX_SEARCH_SECONDS:
                raise miss("observation viewpoint search time budget exhausted")
            return ViewpointSelection(
                point,
                length,
                heading,
                count,
                None if viewpoint_evidence is None else viewpoint_evidence(point, heading),
                routes,
                scans,
            )
    raise miss("bounded observation viewpoint search found no supported candidate")
