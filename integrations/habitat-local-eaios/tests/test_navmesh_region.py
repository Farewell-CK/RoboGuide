"""Static component geometry cannot fabricate routes, eligibility, or success."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios import navmesh_region as regions  # noqa: E402
from habitat_local_eaios.goal_region_navigation import Point3, point3  # noqa: E402
from habitat_local_eaios.preassignment_feasibility import preassignment_digest  # noqa: E402


class MeshReader:
    """Supply one closed component without exposing any physics or RNG route."""

    def __init__(self, *, height: float = 0.0) -> None:
        """Use a broad triangle whose interior can contain a small goal ball."""
        self.vertices: list[Any] = [(-5.0, height, -5.0), (5.0, height, -5.0), (0.0, height, 5.0)]
        self.indices: list[Any] = [0, 1, 2]
        self.component = 7
        self.snap = (0.0, height, 0.0)
        self.exports = 0

    def get_island(self, start: Point3) -> int:
        """Return this exact starting component independently of floor labels."""
        del start
        return self.component

    def snap_point(self, start: Point3, component: int) -> Point3:
        """Observe projection without moving a real robot or sampling RNG."""
        del start
        assert component == self.component
        return self.snap

    def build_navmesh_vertices(self, component: int) -> list[Any]:
        """Export this complete component once, under the reader's size budget."""
        assert component == self.component
        self.exports += 1
        return self.vertices

    def build_navmesh_vertex_indices(self, component: int) -> list[Any]:
        """Keep explicit triangles so vertex-only false negatives are impossible."""
        assert component == self.component
        return self.indices


def _analysis(
    *,
    height: float = 0.0,
    center: Point3 = (0.0, 0.0, 0.0),
    reference_offset: Point3 = (0.0, 0.0, 0.0),
    budget: int = 100,
) -> dict[str, Any]:
    """Inspect controlled static geometry without any simulator dependency."""
    reader = MeshReader(height=height)
    start = (0.0, height, 0.0)
    reference = tuple(start[i] + reference_offset[i] for i in range(3))
    mesh = regions.ComponentMesh.read(reader, start)
    return regions.analyze_region(
        mesh, start, point3(reference), center, 2.0, regions.RegionBudget(budget)
    )


def test_triangle_interior_prevents_vertex_only_negative() -> None:
    """A small goal ball may intersect a face while every vertex lies outside."""
    result = _analysis()
    assert result["status"] == "intersects"
    assert result["minimum_reference_distance_m"] == 0.0
    assert result["closest_base_position"] == [0.0, 0.0, 0.0]
    assert result["complete"] is True
    assert result["triangles_examined"] == 1
    assert all(sum(c * c for c in p) > 4.0 for p in MeshReader().vertices)


def test_same_floor_disconnected_region_is_scoped_negative() -> None:
    """Equal heights cannot hide a horizontally disconnected target region."""
    result = _analysis(center=(20.0, 0.0, 0.0))
    assert result["status"] == "disjoint"
    assert result["orientation_safe_distance_lower_bound_m"] > 2.001
    assert result["scope"] == "reset_static_start_component"
    assert "physical_reachability" not in result


def test_adjacent_floor_inside_goal_sphere_retains_intersection() -> None:
    """Floor transitions are unnecessary when the official ball reaches this mesh."""
    result = _analysis(center=(0.0, 1.5, 0.0))
    assert result["status"] == "intersects"
    assert result["minimum_reference_distance_m"] == 1.5


def test_reference_rotation_cannot_create_false_negative() -> None:
    """A snapshot offset pointing away does not prove all future orientations miss."""
    reader = MeshReader()
    reader.vertices = [(0.0, 0.0, 0.0)] * 3
    mesh = regions.ComponentMesh.read(reader, (0.0, 0.0, 0.0))
    result = regions.analyze_region(
        mesh, (0.0, 0.0, 0.0), (-1.5, 0.0, 0.0), (2.5, 0.0, 0.0), 2.0, regions.RegionBudget()
    )
    assert result["minimum_reference_distance_m"] == 4.0
    assert result["orientation_safe_distance_lower_bound_m"] == 1.0
    assert result["status"] == "unknown"


@pytest.mark.parametrize("height", [1.9995, 2.0, 2.0005])
def test_contact_and_numerical_margin_are_unknown(height: float) -> None:
    """Strict official thresholds and floating contact never authorize exclusion."""
    assert _analysis(height=height)["status"] == "unknown"


def test_work_budget_never_publishes_partial_minimum() -> None:
    """Incomplete full-region scans cannot turn a bounded miss into disjoint."""
    result = _analysis(center=(20.0, 0.0, 0.0), budget=0)
    assert result["status"] == "unknown" and result["reason_code"] == "triangle_budget"
    assert result["complete"] is False and result["triangles_examined"] == 0
    assert result["minimum_reference_distance_m"] is None


def test_time_budget_and_shared_budget_are_explicit() -> None:
    """Wall-clock limits and later goals retain unknown without renewed work."""
    mesh = regions.ComponentMesh.read(MeshReader(), (0.0, 0.0, 0.0))
    ticks = iter((0.0, 1.0, 1.1))
    result = regions.analyze_region(
        mesh,
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
        (20.0, 0.0, 0.0),
        2.0,
        regions.RegionBudget(),
        clock=lambda: next(ticks),
    )
    assert result["reason_code"] == "analysis_time_budget"
    assert result["complete"] is False
    shared = regions.RegionBudget(1)
    for expected in ("disjoint", "unknown"):
        result = regions.analyze_region(
            mesh, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (20.0, 0.0, 0.0), 2.0, shared
        )
        assert result["status"] == expected


@pytest.mark.parametrize(
    "fault",
    [
        "component",
        "snap",
        "vertices",
        "indices",
        "float_index",
        "bool_index",
        "nonfinite",
        "truncated",
    ],
)
def test_incomplete_or_malformed_export_cannot_be_negative(fault: str) -> None:
    """Reject incomplete arrays, fabricated indices, missing components and projections."""
    reader = MeshReader()
    if fault == "component":
        reader.component = -1
    elif fault == "snap":
        reader.snap = (2.0, 0.0, 0.0)
    elif fault == "vertices":
        reader.vertices *= regions.MAX_COMPONENT_VERTICES
    elif fault == "indices":
        reader.indices[0] = 3
    elif fault == "float_index":
        reader.indices[0] = 0.0
    elif fault == "bool_index":
        reader.indices[0] = True
    elif fault == "nonfinite":
        reader.vertices[0] = (float("nan"), 0.0, 0.0)
    else:
        reader.indices.pop()
    with pytest.raises((ValueError, RuntimeError)):
        regions.ComponentMesh.read(reader, (0.0, 0.0, 0.0))


@pytest.mark.parametrize(
    ("point", "vertices", "expected"),
    [
        ((0.0, 2.0, 0.0), ((-5.0, 0.0, -5.0), (5.0, 0.0, -5.0), (0.0, 0.0, 5.0)), (0.0, 0.0, 0.0)),
        ((2.0, 0.0, 2.0), ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)), (0.5, 0.0, 0.5)),
        ((1.0, 1.0, 1.0), ((0.0, 0.0, 0.0), (2.0, 2.0, 0.0), (0.0, 0.0, 2.0)), (1.0, 1.0, 1.0)),
        ((1.0, 1.0, 0.0), ((0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (1.0, 0.0, 0.0)), (1.0, 0.0, 0.0)),
    ],
)
def test_projection_covers_interior_edge_slope_and_degeneracy(
    point: Point3,
    vertices: tuple[Point3, Point3, Point3],
    expected: Point3,
) -> None:
    """Direct geometry checks avoid mirroring only the observer's classification."""
    assert regions.closest_triangle_point(point, *vertices) == pytest.approx(expected)


def test_mesh_content_identity_changes_with_geometry() -> None:
    """A stale or altered component cannot retain its geometry content digest."""
    reader = MeshReader()
    before = regions.ComponentMesh.read(reader, (0.0, 0.0, 0.0))
    reader.vertices[0] = (-4.0, 0.0, -5.0)
    after = regions.ComponentMesh.read(reader, (0.0, 0.0, 0.0))
    assert before.digest != after.digest
    assert before.digest == preassignment_digest(
        {"vertices": [list(p) for p in before.vertices], "indices": [0, 1, 2]}
    )
