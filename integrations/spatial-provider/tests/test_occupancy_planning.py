"""Focused acceptance for conservative planning-only occupancy free coverage."""

from __future__ import annotations

import math

import pytest
from roboguide_spatial_provider.occupancy_planning import (
    CoveragePolicy,
    OccupiedSourceCell,
    TargetExtent,
    plan_free_coverage,
)


def policy(**changes: int) -> CoveragePolicy:
    """Build a small finite coverage policy with explicit test overrides."""
    values = {
        "free_max": 20,
        "occupied_min": 80,
        "max_source_cells": 1024,
        "max_target_cells": 256,
        "max_cells_per_target": 16,
        "max_work_cells": 4096,
        "max_output_cells": 256,
    }
    values.update(changes)
    return CoveragePolicy(**values)


def source(
    width: int,
    height: int,
    *,
    resolution: float = 0.1,
    origin: list[float] | None = None,
    cells: list[int] | None = None,
) -> dict[str, object]:
    """Build one normalized metric occupancy grid without file or worker behavior."""
    return {
        "schema_version": "roboguide.occupancy-grid/v0.1",
        "width": width,
        "height": height,
        "resolution": resolution,
        "origin": origin or [0.0, 0.0, 0.0],
        "cells": [0] * (width * height) if cells is None else cells,
    }


def identity(x: float = 0.0, y: float = 0.0) -> list[list[float]]:
    """Build a rigid source-to-target translation."""
    return [[1.0, 0.0, x], [0.0, 1.0, y], [0.0, 0.0, 1.0]]


def rotated(yaw: float, x: float = 0.0, y: float = 0.0) -> list[list[float]]:
    """Build a rigid source-to-target rotation and translation."""
    cosine, sine = math.cos(yaw), math.sin(yaw)
    return [[cosine, -sine, x], [sine, cosine, y], [0.0, 0.0, 1.0]]


def test_shifted_coarse_source_covers_every_fine_target_cell_without_checkerboard() -> None:
    """A 0.1 m source shifted by 0.025 m covers a continuous 0.05 m target grid."""
    result = plan_free_coverage(
        source(5, 5, origin=[-0.025, -0.025, 0.0]),
        target_from_source=identity(),
        target_extent=TargetExtent(0.0, 0.0, 0.4, 0.4, 0.05),
        policy=policy().__dict__,
    )

    assert result.width == 8
    assert result.height == 8
    assert result.eligible_cells == tuple((column, row) for row in range(8) for column in range(8))
    assert result.outside_cells == ()
    assert result.unknown_cells == ()
    assert result.occupied_cells == ()


def test_one_target_cell_requires_all_adjacent_source_cells_to_be_explicitly_free() -> None:
    """A target cell crossing a source boundary cannot borrow one free neighbor."""
    result = plan_free_coverage(
        source(4, 1, cells=[0, 100, 0, 0]),
        target_from_source=identity(-0.05, 0.0),
        target_extent=TargetExtent(0.0, 0.0, 0.3, 0.1, 0.1),
        policy=policy(),
        include_occupied_provenance=True,
    )

    assert result.eligible_cells == ((2, 0),)
    assert result.occupied_cells == ((0, 0), (1, 0))
    assert result.unknown_cells == ()
    assert result.occupied_provenance is not None
    assert result.occupied_provenance[0].source_cells == (OccupiedSourceCell((1, 0), 100),)


def test_rotated_target_uses_the_full_conservative_inverse_corner_rectangle() -> None:
    """Rotation cannot reduce coverage to one sampled source square."""
    cells = [0] * 25
    cells[12] = 100
    rotated_center_y = 0.25 * math.sqrt(2)
    result = plan_free_coverage(
        source(5, 5, cells=cells),
        target_from_source=rotated(math.pi / 4),
        target_extent=TargetExtent(-0.04, rotated_center_y - 0.04, 0.08, 0.08, 0.08),
        policy=policy(),
        include_occupied_provenance=True,
    )

    assert result.eligible_cells == ()
    assert result.occupied_cells == ((0, 0),)
    assert result.occupied_provenance is not None
    assert len(result.occupied_provenance) == 1
    assert OccupiedSourceCell((2, 2), 100) in result.occupied_provenance[0].source_cells


def test_unknown_and_outside_are_independent_refusals_and_never_free() -> None:
    """Unknown source values and missing grid coverage remain separately visible."""
    result = plan_free_coverage(
        source(1, 1, cells=[-1]),
        target_from_source=identity(),
        target_extent=TargetExtent(0.0, 0.0, 0.2, 0.1, 0.1),
        policy=policy(),
    )

    assert result.eligible_cells == ()
    assert result.unknown_cells == ((0, 0),)
    assert result.outside_cells == ((1, 0),)


def test_occupied_and_unknown_evidence_remain_independent_for_the_same_target() -> None:
    """One target classification can retain both occupied and unknown source causes."""
    result = plan_free_coverage(
        source(2, 2, cells=[100, -1, 0, 0]),
        target_from_source=identity(),
        target_extent=TargetExtent(0.0, 0.0, 0.2, 0.2, 0.2),
        policy=policy(),
        include_occupied_provenance=True,
    )

    assert result.eligible_cells == ()
    assert result.occupied_cells == ((0, 0),)
    assert result.unknown_cells == ((0, 0),)
    assert result.occupied_provenance is not None
    assert result.occupied_provenance[0].source_cells == (OccupiedSourceCell((0, 0), 100),)


def test_exact_threshold_equality_is_free_or_occupied_and_intermediate_is_unknown() -> None:
    """Only configured explicit free values can cover a target planning cell."""
    result = plan_free_coverage(
        source(3, 1, cells=[20, 21, 80]),
        target_from_source=identity(),
        target_extent=TargetExtent(0.0, 0.0, 0.3, 0.1, 0.1),
        policy=policy(),
    )

    assert result.eligible_cells == ((0, 0),)
    assert result.unknown_cells == ((1, 0),)
    assert result.occupied_cells == ((2, 0),)


def test_rotated_source_origin_preserves_grid_geometry() -> None:
    """The source grid's own metric origin rotation participates in the inverse mapping."""
    result = plan_free_coverage(
        source(2, 1, origin=[0.1, 0.0, math.pi / 2]),
        target_from_source=identity(),
        target_extent=TargetExtent(0.0, 0.0, 0.1, 0.2, 0.1),
        policy=policy(),
    )

    assert result.eligible_cells == ((0, 0), (0, 1))
    assert result.outside_cells == ()


@pytest.mark.parametrize(
    "pose",
    [
        [[1.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        [[1.1, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        [[1.0, 1.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        [[-1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
    ],
)
def test_pose_must_be_planar_and_properly_rigid(pose: list[list[float]]) -> None:
    """Scale, shear, reflection, and malformed matrices cannot cross the API boundary."""
    with pytest.raises(ValueError, match="3x3 rigid|proper rigid"):
        plan_free_coverage(
            source(1, 1),
            target_from_source=pose,
            target_extent=TargetExtent(0.0, 0.0, 0.1, 0.1, 0.1),
            policy=policy(),
        )


def test_optional_numpy_pose_is_accepted_without_a_module_import_dependency() -> None:
    """The provider environment can pass a NumPy rigid matrix through the same API."""
    numpy = pytest.importorskip("numpy")
    result = plan_free_coverage(
        source(1, 1),
        target_from_source=numpy.eye(3),
        target_extent=TargetExtent(0.0, 0.0, 0.1, 0.1, 0.1),
        policy=policy(),
    )

    assert result.eligible_cells == ((0, 0),)


def test_per_target_work_and_output_budgets_refuse_before_unbounded_growth() -> None:
    """Both source-cell work and eligible output are closed finite budgets."""
    with pytest.raises(ValueError, match="source-cell work budget"):
        plan_free_coverage(
            source(10, 10),
            target_from_source=identity(),
            target_extent=TargetExtent(0.0, 0.0, 1.0, 1.0, 1.0),
            policy=policy(max_cells_per_target=99),
        )
    with pytest.raises(ValueError, match="output-entry budget"):
        plan_free_coverage(
            source(2, 2),
            target_from_source=identity(),
            target_extent=TargetExtent(0.0, 0.0, 0.2, 0.2, 0.1),
            policy=policy(max_output_cells=3),
        )


def test_policy_mapping_is_closed_and_thresholds_are_consistent() -> None:
    """Unspecified policy fields cannot silently select different safety semantics."""
    with pytest.raises(ValueError, match="unknown spatial contract field"):
        plan_free_coverage(
            source(1, 1),
            target_from_source=identity(),
            target_extent=TargetExtent(0.0, 0.0, 0.1, 0.1, 0.1),
            policy={**policy().__dict__, "unexpected": 1},
        )
    with pytest.raises(ValueError, match="free_max must be lower"):
        plan_free_coverage(
            source(1, 1),
            target_from_source=identity(),
            target_extent=TargetExtent(0.0, 0.0, 0.1, 0.1, 0.1),
            policy=policy(free_max=80, occupied_min=80),
        )
