"""Bounded projection of finite planar laser scans into current occupancy evidence.

The caller owns ROS subscription, raw TF lookup, calibration, and synchronization.
This module accepts only one already synchronized planar sensor pose and finite scan
samples.  It produces candidate free-cell coverage and actual finite-range hit cells;
neither result is authority to change a persistent map or to claim full cell clearance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from itertools import pairwise

from .occupancy_freshness import Cell, ViewCoverage

_MAX_IDENTIFIER_LENGTH = 256
_MAX_BEAMS = 65_536
_MAX_INTERPOLATED_RAYS = 262_144
_MAX_TRACE_STEPS = 2_000_000
_MAX_CELLS = 1_000_000
_MAX_TIMESTAMP_NS = 2**63 - 1
_MAX_EXACT_GRID_COORDINATE = 2**52
_MAX_RANGE_M = 1_000.0


def _identifier(value: object, field: str) -> str:
    """Require an exact identity that the adapter did not silently normalize."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_IDENTIFIER_LENGTH
        or value.strip() != value
    ):
        raise ValueError(f"{field} must be a non-empty exact string")
    return value


def _integer(value: object, field: str, minimum: int, maximum: int) -> int:
    """Require one bounded integer without accepting booleans."""
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{field} must be an integer in [{minimum}, {maximum}]")
    return value


def _timestamp(value: object, field: str) -> int:
    """Require a nonnegative timestamp that fits the common signed nanosecond range."""
    return _integer(value, field, 0, _MAX_TIMESTAMP_NS)


def _finite(value: object, field: str, *, minimum: float, maximum: float) -> float:
    """Require one finite number in an explicit physical or angular range."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"{field} must be finite and in [{minimum}, {maximum}]")
    return result


def _cells(values: object, field: str) -> frozenset[Cell]:
    """Copy one bounded set of exact planning cells."""
    if not isinstance(values, (set, frozenset, tuple)):
        raise ValueError(f"{field} must be a set, frozenset, or tuple of cells")
    if len(values) > _MAX_CELLS:
        raise ValueError(f"{field} exceeds the supported cell count")
    result: set[Cell] = set()
    for cell in values:
        if not isinstance(cell, tuple) or len(cell) != 2:
            raise ValueError(f"{field} entries must be (column, row) tuples")
        column, row = cell
        if (
            isinstance(column, bool)
            or not isinstance(column, int)
            or isinstance(row, bool)
            or not isinstance(row, int)
        ):
            raise ValueError(f"{field} coordinates must be integers")
        result.add((column, row))
    return frozenset(result)


@dataclass(frozen=True)
class PlanarGridTransform:
    """Exact target-frame transform for planning cell centers.

    Cell ``(column, row)`` has its center at ``origin + R(yaw) * resolution *
    (column + 0.5, row + 0.5)``.  The transform does not localize the sensor.
    """

    frame_id: str
    resolution_m: float
    origin_x_m: float
    origin_y_m: float
    origin_yaw_rad: float

    def __post_init__(self) -> None:
        """Validate the exact metric grid before any scan cell is emitted."""
        object.__setattr__(self, "frame_id", _identifier(self.frame_id, "frame_id"))
        object.__setattr__(
            self,
            "resolution_m",
            _finite(self.resolution_m, "resolution_m", minimum=0.0001, maximum=10.0),
        )
        object.__setattr__(
            self,
            "origin_x_m",
            _finite(self.origin_x_m, "origin_x_m", minimum=-1_000_000.0, maximum=1_000_000.0),
        )
        object.__setattr__(
            self,
            "origin_y_m",
            _finite(self.origin_y_m, "origin_y_m", minimum=-1_000_000.0, maximum=1_000_000.0),
        )
        object.__setattr__(
            self,
            "origin_yaw_rad",
            _finite(
                self.origin_yaw_rad,
                "origin_yaw_rad",
                minimum=-1_000_000.0,
                maximum=1_000_000.0,
            ),
        )

    def world_to_cell_units(self, x_m: float, y_m: float) -> tuple[float, float]:
        """Return exact continuous grid coordinates for a validated world point."""
        dx = x_m - self.origin_x_m
        dy = y_m - self.origin_y_m
        cosine = math.cos(self.origin_yaw_rad)
        sine = math.sin(self.origin_yaw_rad)
        column = (dx * cosine + dy * sine) / self.resolution_m
        row = (-dx * sine + dy * cosine) / self.resolution_m
        if not math.isfinite(column) or not math.isfinite(row):
            raise ValueError("scan point has non-finite grid coordinates")
        if max(abs(column), abs(row)) > _MAX_EXACT_GRID_COORDINATE:
            raise ValueError("scan point exceeds exact grid coordinate representation")
        return column, row

    def cell_center_m(self, cell: Cell) -> tuple[float, float]:
        """Return the exact target-frame center of one bounded planning cell."""
        column, row = cell
        if max(abs(column), abs(row)) > _MAX_EXACT_GRID_COORDINATE:
            raise ValueError("cell exceeds exact grid coordinate representation")
        local_x = (column + 0.5) * self.resolution_m
        local_y = (row + 0.5) * self.resolution_m
        cosine = math.cos(self.origin_yaw_rad)
        sine = math.sin(self.origin_yaw_rad)
        x_m = self.origin_x_m + local_x * cosine - local_y * sine
        y_m = self.origin_y_m + local_x * sine + local_y * cosine
        if not math.isfinite(x_m) or not math.isfinite(y_m):
            raise ValueError("cell center is non-finite")
        return x_m, y_m

    def cells_at_point(self, x_m: float, y_m: float, radius_m: float) -> frozenset[Cell]:
        """Conservatively select cells touched by a bounded square around one hit."""
        cells: set[Cell] = set()
        for dx in (-radius_m, radius_m):
            for dy in (-radius_m, radius_m):
                column, row = self.world_to_cell_units(x_m + dx, y_m + dy)
                cell = (math.floor(column), math.floor(row))
                if max(abs(cell[0]), abs(cell[1])) > _MAX_EXACT_GRID_COORDINATE:
                    raise ValueError("hit cell exceeds exact grid coordinate representation")
                cells.add(cell)
        low_column = min(cell[0] for cell in cells)
        high_column = max(cell[0] for cell in cells)
        low_row = min(cell[1] for cell in cells)
        high_row = max(cell[1] for cell in cells)
        return frozenset(
            (column, row)
            for column in range(low_column, high_column + 1)
            for row in range(low_row, high_row + 1)
        )


@dataclass(frozen=True)
class FiniteLaserScan:
    """One adapter-validated finite planar scan and its exact acquisition identity."""

    view_id: str
    sensor_id: str
    localization_session: str
    clock_domain: str
    frame_id: str
    stamp_ns: int
    angles_rad: tuple[float, ...]
    ranges_m: tuple[float | None, ...]

    def __post_init__(self) -> None:
        """Reject non-finite or ambiguous scans before any ray can become free."""
        object.__setattr__(self, "view_id", _identifier(self.view_id, "view_id"))
        object.__setattr__(self, "sensor_id", _identifier(self.sensor_id, "sensor_id"))
        object.__setattr__(
            self,
            "localization_session",
            _identifier(self.localization_session, "localization_session"),
        )
        object.__setattr__(self, "clock_domain", _identifier(self.clock_domain, "clock_domain"))
        object.__setattr__(self, "frame_id", _identifier(self.frame_id, "frame_id"))
        object.__setattr__(self, "stamp_ns", _timestamp(self.stamp_ns, "stamp_ns"))
        if not isinstance(self.angles_rad, tuple) or not 1 <= len(self.angles_rad) <= _MAX_BEAMS:
            raise ValueError(f"angles_rad must be a tuple of one to {_MAX_BEAMS} finite angles")
        if not isinstance(self.ranges_m, tuple) or len(self.ranges_m) != len(self.angles_rad):
            raise ValueError("ranges_m must contain one finite range or missing sample per angle")
        angles = tuple(
            _finite(angle, f"angles_rad[{index}]", minimum=-1_000_000.0, maximum=1_000_000.0)
            for index, angle in enumerate(self.angles_rad)
        )
        ranges = tuple(
            None
            if value is None
            else _finite(value, f"ranges_m[{index}]", minimum=0.0, maximum=_MAX_RANGE_M)
            for index, value in enumerate(self.ranges_m)
        )
        if any(right <= left for left, right in pairwise(angles)):
            raise ValueError("angles_rad must be strictly increasing")
        object.__setattr__(self, "angles_rad", angles)
        object.__setattr__(self, "ranges_m", ranges)


@dataclass(frozen=True)
class SynchronizedPlanarPose:
    """Exact planar lidar pose supplied by a successful synchronized TF lookup."""

    target_frame_id: str
    sensor_frame_id: str
    localization_session: str
    clock_domain: str
    stamp_ns: int
    x_m: float
    y_m: float
    yaw_rad: float

    def __post_init__(self) -> None:
        """Validate exact bindings and a finite planar transform without acquiring one."""
        object.__setattr__(
            self, "target_frame_id", _identifier(self.target_frame_id, "target_frame_id")
        )
        object.__setattr__(
            self,
            "sensor_frame_id",
            _identifier(self.sensor_frame_id, "sensor_frame_id"),
        )
        object.__setattr__(
            self,
            "localization_session",
            _identifier(self.localization_session, "localization_session"),
        )
        object.__setattr__(self, "clock_domain", _identifier(self.clock_domain, "clock_domain"))
        object.__setattr__(self, "stamp_ns", _timestamp(self.stamp_ns, "stamp_ns"))
        object.__setattr__(
            self, "x_m", _finite(self.x_m, "x_m", minimum=-1_000_000_000.0, maximum=1_000_000_000.0)
        )
        object.__setattr__(
            self, "y_m", _finite(self.y_m, "y_m", minimum=-1_000_000_000.0, maximum=1_000_000_000.0)
        )
        object.__setattr__(
            self,
            "yaw_rad",
            _finite(self.yaw_rad, "yaw_rad", minimum=-1_000_000.0, maximum=1_000_000.0),
        )


@dataclass(frozen=True)
class ScanProjectionPolicy:
    """Explicit bounds for finite ray coverage, interpolation, and two-view evidence."""

    min_range_m: float
    max_range_m: float
    max_scan_age_ns: int
    interpolation_step_rad: float
    max_interpolation_gap_rad: float
    interpolation_margin_m: float
    free_range_margin_m: float
    sensor_origin_margin_m: float
    hit_margin_m: float
    max_beams: int
    max_interpolated_rays: int
    max_trace_steps: int
    max_cells: int
    min_two_view_baseline_m: float
    min_two_view_angle_rad: float
    max_two_view_interval_ns: int

    def __post_init__(self) -> None:
        """Validate every bound before input-sized or geometry-sized work begins."""
        object.__setattr__(
            self,
            "min_range_m",
            _finite(self.min_range_m, "min_range_m", minimum=0.0, maximum=_MAX_RANGE_M),
        )
        object.__setattr__(
            self,
            "max_range_m",
            _finite(self.max_range_m, "max_range_m", minimum=0.0001, maximum=_MAX_RANGE_M),
        )
        if self.min_range_m >= self.max_range_m:
            raise ValueError("sensor maximum range must exceed its minimum range")
        object.__setattr__(
            self,
            "max_scan_age_ns",
            _integer(self.max_scan_age_ns, "max_scan_age_ns", 1, _MAX_TIMESTAMP_NS),
        )
        object.__setattr__(
            self,
            "interpolation_step_rad",
            _finite(
                self.interpolation_step_rad,
                "interpolation_step_rad",
                minimum=0.000001,
                maximum=math.pi,
            ),
        )
        object.__setattr__(
            self,
            "max_interpolation_gap_rad",
            _finite(
                self.max_interpolation_gap_rad,
                "max_interpolation_gap_rad",
                minimum=self.interpolation_step_rad,
                maximum=math.pi,
            ),
        )
        for field in ("interpolation_margin_m", "free_range_margin_m", "sensor_origin_margin_m"):
            object.__setattr__(
                self,
                field,
                _finite(getattr(self, field), field, minimum=0.0, maximum=self.max_range_m),
            )
        object.__setattr__(
            self,
            "hit_margin_m",
            _finite(self.hit_margin_m, "hit_margin_m", minimum=0.0, maximum=10.0),
        )
        object.__setattr__(self, "max_beams", _integer(self.max_beams, "max_beams", 1, _MAX_BEAMS))
        object.__setattr__(
            self,
            "max_interpolated_rays",
            _integer(
                self.max_interpolated_rays,
                "max_interpolated_rays",
                self.max_beams,
                _MAX_INTERPOLATED_RAYS,
            ),
        )
        object.__setattr__(
            self,
            "max_trace_steps",
            _integer(self.max_trace_steps, "max_trace_steps", self.max_beams, _MAX_TRACE_STEPS),
        )
        object.__setattr__(self, "max_cells", _integer(self.max_cells, "max_cells", 1, _MAX_CELLS))
        object.__setattr__(
            self,
            "min_two_view_baseline_m",
            _finite(
                self.min_two_view_baseline_m,
                "min_two_view_baseline_m",
                minimum=0.0,
                maximum=2 * _MAX_RANGE_M,
            ),
        )
        object.__setattr__(
            self,
            "min_two_view_angle_rad",
            _finite(
                self.min_two_view_angle_rad,
                "min_two_view_angle_rad",
                minimum=0.0,
                maximum=math.pi,
            ),
        )
        object.__setattr__(
            self,
            "max_two_view_interval_ns",
            _integer(
                self.max_two_view_interval_ns,
                "max_two_view_interval_ns",
                1,
                _MAX_TIMESTAMP_NS,
            ),
        )


@dataclass(frozen=True)
class ScanProjection:
    """One current scan's candidate free coverage and finite-range hit cells."""

    sensor_id: str
    coverage: ViewCoverage
    hit_cells: frozenset[Cell]
    pose: SynchronizedPlanarPose
    grid: PlanarGridTransform
    valid_from_ns: int
    valid_until_ns: int
    rays_traced: int
    unbridged_angular_gaps: int

    def __post_init__(self) -> None:
        """Freeze a bounded result whose hits remain independent of free evidence."""
        object.__setattr__(self, "sensor_id", _identifier(self.sensor_id, "sensor_id"))
        if not isinstance(self.coverage, ViewCoverage):
            raise ValueError("coverage must be a ViewCoverage")
        if not isinstance(self.pose, SynchronizedPlanarPose):
            raise ValueError("pose must be a SynchronizedPlanarPose")
        if not isinstance(self.grid, PlanarGridTransform):
            raise ValueError("grid must be a PlanarGridTransform")
        hits = _cells(self.hit_cells, "hit_cells")
        valid_from = _timestamp(self.valid_from_ns, "valid_from_ns")
        valid_until = _timestamp(self.valid_until_ns, "valid_until_ns")
        if valid_from >= valid_until:
            raise ValueError("scan projection requires a non-empty validity window")
        if self.pose.stamp_ns != valid_from:
            raise ValueError("projection validity must start at its synchronized pose stamp")
        if self.pose.target_frame_id != self.grid.frame_id:
            raise ValueError("projection pose and grid target frames differ")
        if not self.coverage.covered_cells.isdisjoint(hits):
            raise ValueError("current hit cells must remain outside candidate free coverage")
        object.__setattr__(self, "hit_cells", hits)
        object.__setattr__(self, "valid_from_ns", valid_from)
        object.__setattr__(self, "valid_until_ns", valid_until)
        object.__setattr__(
            self,
            "rays_traced",
            _integer(self.rays_traced, "rays_traced", 0, _MAX_INTERPOLATED_RAYS),
        )
        object.__setattr__(
            self,
            "unbridged_angular_gaps",
            _integer(self.unbridged_angular_gaps, "unbridged_angular_gaps", 0, _MAX_BEAMS),
        )


@dataclass
class _TraceBudget:
    """Mutable bounded counters retained only inside one pure projection call."""

    steps: int = 0
    rays: int = 0


def _world_point(
    pose: SynchronizedPlanarPose, angle_rad: float, range_m: float
) -> tuple[float, float]:
    """Apply the supplied sensor pose to one direction without another TF lookup."""
    world_angle = pose.yaw_rad + angle_rad
    return pose.x_m + range_m * math.cos(world_angle), pose.y_m + range_m * math.sin(world_angle)


def _trace_cells(
    *,
    start_x_m: float,
    start_y_m: float,
    end_x_m: float,
    end_y_m: float,
    grid: PlanarGridTransform,
    origin_cell: Cell,
    policy: ScanProjectionPolicy,
    budget: _TraceBudget,
) -> frozenset[Cell]:
    """Trace the interior of one shortened free segment with bounded grid DDA."""
    start_column, start_row = grid.world_to_cell_units(start_x_m, start_y_m)
    end_column, end_row = grid.world_to_cell_units(end_x_m, end_y_m)
    # A segment starting on a cell boundary belongs to the cell it enters, not one it leaves.
    start_column = math.nextafter(start_column, end_column)
    start_row = math.nextafter(start_row, end_row)
    # Excluding arrival on an exact cell boundary avoids promoting a merely touched cell.
    end_column = math.nextafter(end_column, start_column)
    end_row = math.nextafter(end_row, start_row)

    column = math.floor(start_column)
    row = math.floor(start_row)
    final_column = math.floor(end_column)
    final_row = math.floor(end_row)
    delta_column = end_column - start_column
    delta_row = end_row - start_row
    step_column = 1 if delta_column > 0 else -1 if delta_column < 0 else 0
    step_row = 1 if delta_row > 0 else -1 if delta_row < 0 else 0

    if step_column > 0:
        next_column = (column + 1 - start_column) / delta_column
        column_delta = 1 / delta_column
    elif step_column < 0:
        next_column = (start_column - column) / -delta_column
        column_delta = 1 / -delta_column
    else:
        next_column = math.inf
        column_delta = math.inf
    if step_row > 0:
        next_row = (row + 1 - start_row) / delta_row
        row_delta = 1 / delta_row
    elif step_row < 0:
        next_row = (start_row - row) / -delta_row
        row_delta = 1 / -delta_row
    else:
        next_row = math.inf
        row_delta = math.inf

    cells: set[Cell] = set()
    while True:
        budget.steps += 1
        if budget.steps > policy.max_trace_steps:
            raise ValueError("finite scan exceeds the bounded cell trace budget")
        cell = (column, row)
        if cell != origin_cell:
            cells.add(cell)
            if len(cells) > policy.max_cells:
                raise ValueError("finite scan exceeds the bounded candidate cell count")
        if column == final_column and row == final_row:
            break
        if next_column < next_row:
            column += step_column
            next_column += column_delta
        elif next_row < next_column:
            row += step_row
            next_row += row_delta
        else:
            # A corner crossing enters only the diagonal cell, never both edge neighbours.
            column += step_column
            row += step_row
            next_column += column_delta
            next_row += row_delta
    return frozenset(cells)


def project_finite_planar_scan(
    *,
    scan: FiniteLaserScan,
    pose: SynchronizedPlanarPose,
    grid: PlanarGridTransform,
    policy: ScanProjectionPolicy,
    now_ns: int,
) -> ScanProjection:
    """Project one synchronized finite scan into candidate free and current hit cells.

    The scan pose is never acquired here.  Session, clock, sensor frame, target frame,
    and acquisition stamp must agree exactly.  A scan older than the explicit policy
    age or ahead of the caller's clock is refused before ray work.  Cells between
    adjacent beams are covered only when the finite angular gap and the caller's total
    interpolation bound permit it, and every such ray is shortened by the explicit
    interpolation and free-range margins.  Hit cells are emitted only for finite ranges
    below the maximum range and are always removed from this scan's free candidates.
    Missing and maximum-range samples provide neither free nor occupied evidence.
    """
    if not isinstance(scan, FiniteLaserScan):
        raise ValueError("scan must be a FiniteLaserScan")
    if not isinstance(pose, SynchronizedPlanarPose):
        raise ValueError("pose must be a SynchronizedPlanarPose")
    if not isinstance(grid, PlanarGridTransform):
        raise ValueError("grid must be a PlanarGridTransform")
    if not isinstance(policy, ScanProjectionPolicy):
        raise ValueError("policy must be a ScanProjectionPolicy")
    current_ns = _timestamp(now_ns, "now_ns")
    if policy.hit_margin_m > grid.resolution_m:
        raise ValueError("hit margin cannot exceed one planning cell")
    if scan.localization_session != pose.localization_session:
        raise ValueError("scan and synchronized pose localization sessions differ")
    if scan.clock_domain != pose.clock_domain:
        raise ValueError("scan and synchronized pose clock domains differ")
    if scan.frame_id != pose.sensor_frame_id:
        raise ValueError("scan and synchronized pose sensor frames differ")
    if scan.stamp_ns != pose.stamp_ns:
        raise ValueError("scan and synchronized pose stamps differ")
    if pose.target_frame_id != grid.frame_id:
        raise ValueError("synchronized pose target frame differs from planning grid")
    if len(scan.angles_rad) > policy.max_beams:
        raise ValueError("scan exceeds the configured beam bound")
    if scan.stamp_ns > current_ns:
        raise ValueError("scan acquisition time is ahead of the caller clock")
    age_ns = current_ns - scan.stamp_ns
    if age_ns >= policy.max_scan_age_ns:
        raise ValueError("scan exceeds the configured freshness age")
    valid_until_ns = scan.stamp_ns + policy.max_scan_age_ns
    if valid_until_ns > _MAX_TIMESTAMP_NS:
        raise ValueError("scan validity window exceeds the supported timestamp range")

    origin_column, origin_row = grid.world_to_cell_units(pose.x_m, pose.y_m)
    origin_cell = (math.floor(origin_column), math.floor(origin_row))
    start_range_m = max(policy.min_range_m, policy.sensor_origin_margin_m)
    budget = _TraceBudget()
    covered: set[Cell] = set()
    hits: set[Cell] = set()
    unbridged_gaps = 0
    interpolated_rays = 0

    def valid_range(value: float) -> bool:
        return policy.min_range_m <= value < policy.max_range_m

    def add_free_ray(angle_rad: float, endpoint_range_m: float) -> None:
        free_end_m = endpoint_range_m - policy.free_range_margin_m
        if free_end_m <= start_range_m:
            return
        start_x_m, start_y_m = _world_point(pose, angle_rad, start_range_m)
        end_x_m, end_y_m = _world_point(pose, angle_rad, free_end_m)
        budget.rays += 1
        if budget.rays > policy.max_interpolated_rays:
            raise ValueError("finite scan exceeds the bounded traced-ray count")
        traced = _trace_cells(
            start_x_m=start_x_m,
            start_y_m=start_y_m,
            end_x_m=end_x_m,
            end_y_m=end_y_m,
            grid=grid,
            origin_cell=origin_cell,
            policy=policy,
            budget=budget,
        )
        covered.update(traced)
        if len(covered) > policy.max_cells:
            raise ValueError("finite scan exceeds the bounded candidate cell count")

    for index, (angle, endpoint_range) in enumerate(
        zip(scan.angles_rad, scan.ranges_m, strict=True)
    ):
        if endpoint_range is None or not valid_range(endpoint_range):
            continue
        add_free_ray(angle, endpoint_range)
        endpoint_x, endpoint_y = _world_point(pose, angle, endpoint_range)
        hit_cells = grid.cells_at_point(endpoint_x, endpoint_y, policy.hit_margin_m)
        hits.update(hit_cells)
        if len(hits) > policy.max_cells:
            raise ValueError("finite scan exceeds the bounded current-hit cell count")

        if index + 1 >= len(scan.angles_rad):
            continue
        next_angle = scan.angles_rad[index + 1]
        next_range = scan.ranges_m[index + 1]
        if next_range is None or not valid_range(next_range):
            continue
        angular_gap = next_angle - angle
        if angular_gap > policy.max_interpolation_gap_rad:
            unbridged_gaps += 1
            continue
        subdivisions = max(1, math.ceil(angular_gap / policy.interpolation_step_rad))
        added_rays = subdivisions - 1
        interpolated_rays += added_rays
        if interpolated_rays > policy.max_interpolated_rays:
            raise ValueError("finite scan exceeds the bounded interpolation budget")
        safe_range = min(endpoint_range, next_range) - policy.interpolation_margin_m
        for subdivision in range(1, subdivisions):
            fraction = subdivision / subdivisions
            add_free_ray(angle + angular_gap * fraction, safe_range)

    covered.difference_update(hits)
    return ScanProjection(
        sensor_id=scan.sensor_id,
        coverage=ViewCoverage(scan.view_id, frozenset(covered)),
        hit_cells=frozenset(hits),
        pose=pose,
        grid=grid,
        valid_from_ns=scan.stamp_ns,
        valid_until_ns=valid_until_ns,
        rays_traced=budget.rays,
        unbridged_angular_gaps=unbridged_gaps,
    )


class TwoViewRefusal(str, Enum):  # noqa: UP042 - ROS Humble uses Python 3.10.
    """Stable reasons why no stale cell can be qualified by a scan pair."""

    __str__ = str.__str__

    DUPLICATE_VIEW = "duplicate_view"
    BINDING_MISMATCH = "binding_mismatch"
    SENSOR_MISMATCH = "sensor_mismatch"
    GRID_MISMATCH = "grid_mismatch"
    VIEW_NOT_CURRENT = "view_not_current"
    VIEW_INTERVAL_TOO_LARGE = "view_interval_too_large"
    INSUFFICIENT_BASELINE = "insufficient_baseline"


@dataclass(frozen=True)
class TwoViewStaleEvidence:
    """Stale cells supported by two distinct, separated current scan views."""

    qualified_cells: frozenset[Cell]
    unqualified_cells: frozenset[Cell]
    refusals: tuple[TwoViewRefusal, ...]
    baseline_m: float


def _view_angle_at_cell(first: ScanProjection, second: ScanProjection, cell: Cell) -> float:
    """Return the nonnegative angle between two sensor-to-cell-center bearings."""
    cell_x, cell_y = first.grid.cell_center_m(cell)
    first_x = cell_x - first.pose.x_m
    first_y = cell_y - first.pose.y_m
    second_x = cell_x - second.pose.x_m
    second_y = cell_y - second.pose.y_m
    first_norm = math.hypot(first_x, first_y)
    second_norm = math.hypot(second_x, second_y)
    if first_norm == 0 or second_norm == 0:
        return 0.0
    cosine = (first_x * second_x + first_y * second_y) / (first_norm * second_norm)
    return math.acos(max(-1.0, min(1.0, cosine)))


def qualify_two_view_stale_cells(
    *,
    first: ScanProjection,
    second: ScanProjection,
    stale_cells: object,
    policy: ScanProjectionPolicy,
    now_ns: int,
) -> TwoViewStaleEvidence:
    """Gate shared stale candidates on two actual separated, angularly distinct views.

    Both projections must remain current in one localization session, clock domain,
    target frame, and exact grid transform.  Their acquisitions must fall within the
    configured interval.  A cell must be finite free coverage in both views, absent
    from both current-hit layers, and observed from a viewpoint baseline and bearing
    angle meeting the explicit minima.  The result remains a candidate for the
    existing freshness decision, which retains unknown and localization-owned cells.
    """
    if not isinstance(first, ScanProjection) or not isinstance(second, ScanProjection):
        raise ValueError("first and second must be ScanProjection entries")
    if not isinstance(policy, ScanProjectionPolicy):
        raise ValueError("policy must be a ScanProjectionPolicy")
    current_ns = _timestamp(now_ns, "now_ns")
    candidates = _cells(stale_cells, "stale_cells")
    refusals: list[TwoViewRefusal] = []
    if first.coverage.view_id == second.coverage.view_id:
        refusals.append(TwoViewRefusal.DUPLICATE_VIEW)
    if (
        first.sensor_id != second.sensor_id
        or first.pose.sensor_frame_id != second.pose.sensor_frame_id
    ):
        refusals.append(TwoViewRefusal.SENSOR_MISMATCH)
    if (
        first.pose.localization_session != second.pose.localization_session
        or first.pose.clock_domain != second.pose.clock_domain
        or first.pose.target_frame_id != second.pose.target_frame_id
    ):
        refusals.append(TwoViewRefusal.BINDING_MISMATCH)
    if first.grid != second.grid:
        refusals.append(TwoViewRefusal.GRID_MISMATCH)
    if not (
        first.valid_from_ns <= current_ns < first.valid_until_ns
        and second.valid_from_ns <= current_ns < second.valid_until_ns
    ):
        refusals.append(TwoViewRefusal.VIEW_NOT_CURRENT)
    if abs(first.valid_from_ns - second.valid_from_ns) > policy.max_two_view_interval_ns:
        refusals.append(TwoViewRefusal.VIEW_INTERVAL_TOO_LARGE)

    baseline_m = math.hypot(
        first.pose.x_m - second.pose.x_m,
        first.pose.y_m - second.pose.y_m,
    )
    if baseline_m < policy.min_two_view_baseline_m:
        refusals.append(TwoViewRefusal.INSUFFICIENT_BASELINE)

    qualified: set[Cell] = set()
    if not refusals:
        shared = candidates & first.coverage.covered_cells & second.coverage.covered_cells
        shared -= first.hit_cells
        shared -= second.hit_cells
        for cell in shared:
            if _view_angle_at_cell(first, second, cell) >= policy.min_two_view_angle_rad:
                qualified.add(cell)

    fresh = frozenset(qualified)
    return TwoViewStaleEvidence(
        qualified_cells=fresh,
        unqualified_cells=frozenset(candidates - fresh),
        refusals=tuple(refusals),
        baseline_m=baseline_m,
    )
