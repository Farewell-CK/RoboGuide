"""Pure conservative free-space coverage from one validated occupancy grid.

This module plans from an already normalized metric occupancy grid.  It owns no
registration, map fusion, ROS state, or worker behavior.  A target cell is free only
when every source cell in the conservative inverse-transformed bounding rectangle is
explicitly free.  Source occupancy and unknown evidence are returned separately and
are never promoted by this projection.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .contracts import fields, integer, number
from .occupancy_contracts import grid

Cell = tuple[int, int]


@dataclass(frozen=True)
class CoveragePolicy:
    """Closed thresholds and bounds for one pure coverage calculation.

    ``max_output_cells`` bounds the combined emitted target classifications and
    source occupied-provenance entries.  A caller that requests provenance therefore
    spends the same output budget on both result cells and their source evidence.
    """

    free_max: int
    occupied_min: int
    max_source_cells: int
    max_target_cells: int
    max_cells_per_target: int
    max_work_cells: int
    max_output_cells: int

    def __post_init__(self) -> None:
        """Validate thresholds and every allocation/work bound before planning."""
        free_max = integer(self.free_max, "free_max", 0, 99)
        occupied_min = integer(self.occupied_min, "occupied_min", 1, 100)
        integer(self.max_source_cells, "max_source_cells", 1, 1_000_000)
        max_target_cells = integer(self.max_target_cells, "max_target_cells", 1, 1_000_000)
        max_cells_per_target = integer(self.max_cells_per_target, "max_cells_per_target", 1, 65_536)
        max_work_cells = integer(self.max_work_cells, "max_work_cells", 1, 16_000_000)
        max_output_cells = integer(self.max_output_cells, "max_output_cells", 1, 1_000_000)
        if free_max >= occupied_min:
            raise ValueError("free_max must be lower than occupied_min")
        if max_cells_per_target > max_work_cells:
            raise ValueError("max_cells_per_target exceeds max_work_cells")
        if max_output_cells > max_target_cells:
            raise ValueError("max_output_cells exceeds max_target_cells")

    @classmethod
    def parse(cls, value: Any) -> CoveragePolicy:
        """Parse an exact policy mapping before loading or projecting source cells."""
        raw = fields(value, set(cls.__dataclass_fields__))
        return cls(**{key: integer(raw[key], key, *_BOUNDS[key]) for key in raw})


_BOUNDS = {
    "free_max": (0, 99),
    "occupied_min": (1, 100),
    "max_source_cells": (1, 1_000_000),
    "max_target_cells": (1, 1_000_000),
    "max_cells_per_target": (1, 65_536),
    "max_work_cells": (1, 16_000_000),
    "max_output_cells": (1, 1_000_000),
}


@dataclass(frozen=True)
class TargetExtent:
    """Axis-aligned target planning extent in metres and its cell resolution.

    ``origin_x_m`` and ``origin_y_m`` locate ``(column, row) == (0, 0)`` at
    the lower-left corner.  Columns increase with ``+x`` and rows with ``+y``.
    """

    origin_x_m: float
    origin_y_m: float
    width_m: float
    height_m: float
    resolution_m: float

    def __post_init__(self) -> None:
        """Require a finite positive extent whose dimensions are whole cells."""
        origin_x = number(self.origin_x_m, "origin_x_m", -1_000_000, 1_000_000)
        origin_y = number(self.origin_y_m, "origin_y_m", -1_000_000, 1_000_000)
        width = number(self.width_m, "width_m", 0.0001, 1_000_000)
        height = number(self.height_m, "height_m", 0.0001, 1_000_000)
        resolution = number(self.resolution_m, "resolution_m", 0.0001, 1000)
        width_cells = round(width / resolution)
        height_cells = round(height / resolution)
        tolerance = max(1e-9, resolution * 1e-9)
        if (
            width_cells < 1
            or height_cells < 1
            or abs(width - width_cells * resolution) > tolerance
            or abs(height - height_cells * resolution) > tolerance
        ):
            raise ValueError("target width and height must be positive whole cells")
        object.__setattr__(self, "origin_x_m", origin_x)
        object.__setattr__(self, "origin_y_m", origin_y)
        object.__setattr__(self, "width_m", width)
        object.__setattr__(self, "height_m", height)
        object.__setattr__(self, "resolution_m", resolution)


@dataclass(frozen=True)
class OccupiedSourceCell:
    """One source cell that independently refutes free coverage."""

    cell: Cell
    value: int


@dataclass(frozen=True)
class TargetOccupiedProvenance:
    """Occupied source evidence for one target cell, separate from free results."""

    target_cell: Cell
    source_cells: tuple[OccupiedSourceCell, ...]


@dataclass(frozen=True)
class FreeCoveragePlan:
    """Target cells classified by conservative coverage and independent refusal causes."""

    width: int
    height: int
    eligible_cells: tuple[Cell, ...]
    outside_cells: tuple[Cell, ...]
    unknown_cells: tuple[Cell, ...]
    occupied_cells: tuple[Cell, ...]
    occupied_provenance: tuple[TargetOccupiedProvenance, ...] | None
    evaluated_source_cell_visits: int


def plan_free_coverage(
    source_grid: dict[str, Any],
    *,
    target_from_source: Any,
    target_extent: TargetExtent,
    policy: CoveragePolicy | dict[str, Any],
    include_occupied_provenance: bool = False,
) -> FreeCoveragePlan:
    """Conservatively classify target cells from one normalized source grid.

    Args:
        source_grid: A ``roboguide.occupancy-grid/v0.1`` grid in metres.
        target_from_source: Rigid 3x3 matrix mapping source coordinates to target
            coordinates.  Points use column-vector multiplication.
        target_extent: Axis-aligned target extent and planning-cell resolution.
        policy: :class:`CoveragePolicy` or its exact policy mapping.
        include_occupied_provenance: Return occupied source cells and original values
            separately for each refuted target cell.

    Returns:
        Target cells that are conservatively covered by explicit source free cells,
        plus independent outside, unknown, and occupied refusal sets.

    Raises:
        ValueError: If an input, rigid transform, budget, coordinate range, or output
            limit fails.
    """
    active_policy = policy if isinstance(policy, CoveragePolicy) else CoveragePolicy.parse(policy)
    if not isinstance(target_extent, TargetExtent):
        raise ValueError("target_extent must be a TargetExtent")
    source = grid(source_grid, active_policy.max_source_cells)
    source_width = int(source["width"])
    source_height = int(source["height"])
    source_resolution = float(source["resolution"])
    target_width = round(target_extent.width_m / target_extent.resolution_m)
    target_height = round(target_extent.height_m / target_extent.resolution_m)
    target_cell_count = target_width * target_height
    if target_cell_count > active_policy.max_target_cells:
        raise ValueError("target extent exceeds its cell budget")

    pose = _rigid_pose(target_from_source)
    source_origin = _pose(
        float(source["origin"][0]),
        float(source["origin"][1]),
        float(source["origin"][2]),
    )
    source_grid_from_target = _inverse(_multiply(pose, source_origin))
    cells = source["cells"]

    eligible: list[Cell] = []
    outside: list[Cell] = []
    unknown: list[Cell] = []
    occupied: list[Cell] = []
    provenance: list[TargetOccupiedProvenance] = []
    evaluated = 0
    output_entries = 0
    target_resolution = target_extent.resolution_m

    for row in range(target_height):
        y0 = target_extent.origin_y_m + row * target_resolution
        y1 = y0 + target_resolution
        for column in range(target_width):
            x0 = target_extent.origin_x_m + column * target_resolution
            x1 = x0 + target_resolution
            bounds = _source_bounds(
                source_grid_from_target,
                ((x0, y0), (x1, y0), (x1, y1), (x0, y1)),
                source_resolution,
            )
            first_column, last_column, first_row, last_row = bounds
            target_cell = (column, row)
            is_outside = (
                first_column < 0
                or first_row < 0
                or last_column >= source_width
                or last_row >= source_height
            )
            clipped_first_column = max(first_column, 0)
            clipped_last_column = min(last_column, source_width - 1)
            clipped_first_row = max(first_row, 0)
            clipped_last_row = min(last_row, source_height - 1)
            if (
                clipped_first_column <= clipped_last_column
                and clipped_first_row <= clipped_last_row
            ):
                candidate_count = (clipped_last_column - clipped_first_column + 1) * (
                    clipped_last_row - clipped_first_row + 1
                )
            else:
                candidate_count = 0
            if candidate_count > active_policy.max_cells_per_target:
                raise ValueError("one target cell exceeds its source-cell work budget")
            evaluated += candidate_count
            if evaluated > active_policy.max_work_cells:
                raise ValueError("free coverage exceeds its aggregate source-cell work budget")

            has_unknown = False
            occupied_sources: list[OccupiedSourceCell] = []
            for source_row in range(clipped_first_row, clipped_last_row + 1):
                offset = source_row * source_width
                for source_column in range(clipped_first_column, clipped_last_column + 1):
                    value = int(cells[offset + source_column])
                    if value >= active_policy.occupied_min:
                        occupied_sources.append(
                            OccupiedSourceCell((source_column, source_row), value)
                        )
                    elif not 0 <= value <= active_policy.free_max:
                        has_unknown = True

            is_eligible = (
                not is_outside and not has_unknown and not occupied_sources and candidate_count > 0
            )
            emitted = (
                int(is_outside) + int(has_unknown) + int(bool(occupied_sources)) + int(is_eligible)
            )
            if include_occupied_provenance:
                emitted += len(occupied_sources)
            output_entries += emitted
            if output_entries > active_policy.max_output_cells:
                raise ValueError("free coverage exceeds its output-entry budget")

            if is_outside:
                outside.append(target_cell)
            if has_unknown:
                unknown.append(target_cell)
            if occupied_sources:
                occupied.append(target_cell)
                if include_occupied_provenance:
                    provenance.append(
                        TargetOccupiedProvenance(target_cell, tuple(occupied_sources))
                    )
            if is_eligible:
                eligible.append(target_cell)

    return FreeCoveragePlan(
        width=target_width,
        height=target_height,
        eligible_cells=tuple(eligible),
        outside_cells=tuple(outside),
        unknown_cells=tuple(unknown),
        occupied_cells=tuple(occupied),
        occupied_provenance=tuple(provenance) if include_occupied_provenance else None,
        evaluated_source_cell_visits=evaluated,
    )


def _rigid_pose(value: Any) -> list[list[float]]:
    """Validate one finite planar rigid 3x3 transform without accepting shear or scale."""
    raw = value.tolist() if hasattr(value, "tolist") else value
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        raise ValueError("target_from_source must be a 3x3 rigid matrix")
    matrix: list[list[float]] = []
    for row_index, row in enumerate(raw):
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            raise ValueError("target_from_source must be a 3x3 rigid matrix")
        matrix.append(
            [number(item, f"target_from_source[{row_index}]", -1e12, 1e12) for item in row]
        )
    tolerance = 1e-9
    rotation = matrix[:2]
    if (
        abs(matrix[2][0]) > tolerance
        or abs(matrix[2][1]) > tolerance
        or abs(matrix[2][2] - 1.0) > tolerance
        or abs(rotation[0][0] ** 2 + rotation[1][0] ** 2 - 1.0) > tolerance
        or abs(rotation[0][1] ** 2 + rotation[1][1] ** 2 - 1.0) > tolerance
        or abs(rotation[0][0] * rotation[0][1] + rotation[1][0] * rotation[1][1]) > tolerance
        or abs(rotation[0][0] * rotation[1][1] - rotation[0][1] * rotation[1][0] - 1.0) > tolerance
    ):
        raise ValueError("target_from_source must be a proper rigid planar transform")
    return matrix


def _pose(x: float, y: float, yaw: float) -> list[list[float]]:
    """Construct a planar transform with the same column-vector convention as the API."""
    cosine, sine = math.cos(yaw), math.sin(yaw)
    return [
        [cosine, -sine, x],
        [sine, cosine, y],
        [0.0, 0.0, 1.0],
    ]


def _multiply(left: list[list[float]], right: list[list[float]]) -> list[list[float]]:
    """Multiply two small 3x3 planar matrices."""
    return [
        [sum(left[row][item] * right[item][column] for item in range(3)) for column in range(3)]
        for row in range(3)
    ]


def _inverse(matrix: list[list[float]]) -> list[list[float]]:
    """Invert a validated rigid planar transform using its rotation transpose."""
    rotation = ((matrix[0][0], matrix[0][1]), (matrix[1][0], matrix[1][1]))
    translation = (matrix[0][2], matrix[1][2])
    inverse_x = -(rotation[0][0] * translation[0] + rotation[1][0] * translation[1])
    inverse_y = -(rotation[0][1] * translation[0] + rotation[1][1] * translation[1])
    return [
        [rotation[0][0], rotation[1][0], inverse_x],
        [rotation[0][1], rotation[1][1], inverse_y],
        [0.0, 0.0, 1.0],
    ]


def _source_bounds(
    source_grid_from_target: list[list[float]],
    corners: tuple[tuple[float, float], ...],
    resolution: float,
) -> tuple[int, int, int, int]:
    """Return the inclusive source-cell rectangle bounding inverse target corners."""
    transformed = [
        (
            source_grid_from_target[0][0] * x
            + source_grid_from_target[0][1] * y
            + source_grid_from_target[0][2],
            source_grid_from_target[1][0] * x
            + source_grid_from_target[1][1] * y
            + source_grid_from_target[1][2],
        )
        for x, y in corners
    ]
    x_values = [point[0] / resolution for point in transformed]
    y_values = [point[1] / resolution for point in transformed]
    if max(abs(value) for value in (*x_values, *y_values)) > 10_000_000:
        raise ValueError("planar target coordinates exceed the supported numerical range")
    minimum_x, maximum_x = _snap_cell_bounds(min(x_values), max(x_values))
    minimum_y, maximum_y = _snap_cell_bounds(min(y_values), max(y_values))
    return (
        math.floor(minimum_x),
        math.ceil(maximum_x) - 1,
        math.floor(minimum_y),
        math.ceil(maximum_y) - 1,
    )


def _snap_cell_bounds(minimum: float, maximum: float) -> tuple[float, float]:
    """Remove floating-point residue at exact source cell boundaries."""
    tolerance = 1e-9
    minimum_rounded = round(minimum)
    maximum_rounded = round(maximum)
    if abs(minimum - minimum_rounded) <= tolerance:
        minimum = float(minimum_rounded)
    if abs(maximum - maximum_rounded) <= tolerance:
        maximum = float(maximum_rounded)
    return minimum, maximum
