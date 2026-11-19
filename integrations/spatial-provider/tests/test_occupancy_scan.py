"""Focused acceptance for bounded finite planar scan projection and two-view gating."""

from __future__ import annotations

import math
from typing import Any

import pytest
from roboguide_spatial_provider.occupancy_freshness import (
    ObservationCoverage,
    OccupancyLayers,
    ProjectionBinding,
    ViewCoverage,
    decide_occupancy_freshness_mask,
)
from roboguide_spatial_provider.occupancy_scan import (
    FiniteLaserScan,
    PlanarGridTransform,
    ScanProjection,
    ScanProjectionPolicy,
    SynchronizedPlanarPose,
    TwoViewRefusal,
    project_finite_planar_scan,
    qualify_two_view_stale_cells,
)


def policy(**changes: Any) -> ScanProjectionPolicy:
    """Build one explicit bounded scan policy with a conservative test baseline."""
    values: dict[str, Any] = {
        "min_range_m": 0.1,
        "max_range_m": 10.0,
        "max_scan_age_ns": 100,
        "interpolation_step_rad": 0.25,
        "max_interpolation_gap_rad": 1.6,
        "interpolation_margin_m": 0.1,
        "free_range_margin_m": 0.35,
        "sensor_origin_margin_m": 0.0,
        "hit_margin_m": 0.0,
        "max_beams": 100,
        "max_interpolated_rays": 1_000,
        "max_trace_steps": 100_000,
        "max_cells": 1_000,
        "min_two_view_baseline_m": 3.0,
        "min_two_view_angle_rad": 0.25,
        "max_two_view_interval_ns": 100,
    }
    values.update(changes)
    return ScanProjectionPolicy(**values)


def grid(
    *,
    resolution: float = 1.0,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
    yaw: float = 0.0,
) -> PlanarGridTransform:
    """Build one exact target-frame planning grid."""
    return PlanarGridTransform("map", resolution, origin_x, origin_y, yaw)


def pose(
    *,
    x: float = 0.5,
    y: float = 0.5,
    yaw: float = 0.0,
    stamp_ns: int = 1_000,
    session: str = "localization-session",
    clock: str = "sensor-clock",
    sensor_frame: str = "laser",
    target_frame: str = "map",
) -> SynchronizedPlanarPose:
    """Build an already synchronized planar pose without any TF acquisition."""
    return SynchronizedPlanarPose(
        target_frame,
        sensor_frame,
        session,
        clock,
        stamp_ns,
        x,
        y,
        yaw,
    )


def scan(
    *,
    view_id: str = "view-a",
    sensor_id: str = "lidar",
    angles: tuple[float, ...] = (0.0,),
    ranges: tuple[float | None, ...] = (4.0,),
    stamp_ns: int = 1_000,
    session: str = "localization-session",
    clock: str = "sensor-clock",
    sensor_frame: str = "laser",
) -> FiniteLaserScan:
    """Build one finite scan whose synchronization identity is explicit."""
    return FiniteLaserScan(
        view_id,
        sensor_id,
        session,
        clock,
        sensor_frame,
        stamp_ns,
        angles,
        ranges,
    )


def project(
    *,
    view_id: str = "view-a",
    sensor_id: str = "lidar",
    angles: tuple[float, ...] = (0.0,),
    ranges: tuple[float | None, ...] = (4.0,),
    stamp_ns: int = 1_000,
    sensor_x: float = 0.5,
    sensor_y: float = 0.5,
    sensor_yaw: float = 0.0,
    session: str = "localization-session",
    clock: str = "sensor-clock",
    sensor_frame: str = "laser",
    planning_grid: PlanarGridTransform | None = None,
    scan_policy: ScanProjectionPolicy | None = None,
    now_ns: int = 1_050,
) -> ScanProjection:
    """Invoke the pure helper with exact matching defaults."""
    selected_policy = scan_policy or policy()
    return project_finite_planar_scan(
        scan=scan(
            view_id=view_id,
            sensor_id=sensor_id,
            angles=angles,
            ranges=ranges,
            stamp_ns=stamp_ns,
            session=session,
            clock=clock,
            sensor_frame=sensor_frame,
        ),
        pose=pose(
            x=sensor_x,
            y=sensor_y,
            yaw=sensor_yaw,
            stamp_ns=stamp_ns,
            session=session,
            clock=clock,
            sensor_frame=sensor_frame,
        ),
        grid=planning_grid or grid(),
        policy=selected_policy,
        now_ns=now_ns,
    )


def test_projection_separates_candidate_free_cells_from_current_hits() -> None:
    """Free rays stop before hits, exclude the sensor cell, and never bridge a wide gap."""
    projection = project(
        angles=(0.0, math.pi / 2),
        ranges=(4.0, 2.0),
        scan_policy=policy(max_interpolation_gap_rad=0.5),
    )

    assert projection.coverage.view_id == "view-a"
    assert projection.coverage.covered_cells == frozenset({(1, 0), (2, 0), (3, 0), (0, 1)})
    assert projection.hit_cells == frozenset({(4, 0), (0, 2)})
    assert (0, 0) not in projection.coverage.covered_cells
    assert projection.rays_traced == 2
    assert projection.unbridged_angular_gaps == 1
    assert projection.valid_from_ns == 1_000
    assert projection.valid_until_ns == 1_100


@pytest.mark.parametrize("bad_range", [math.inf, -math.inf, math.nan])
def test_nonfinite_ranges_are_refused_before_any_cell_can_be_free(bad_range: float) -> None:
    """Infinity and NaN are invalid evidence, never a no-return free ray."""
    with pytest.raises(ValueError, match="finite"):
        scan(ranges=(bad_range,))


def test_nonfinite_angle_is_refused_before_projection() -> None:
    """An invalid angular sample cannot select an arbitrary free direction."""
    with pytest.raises(ValueError, match="finite"):
        scan(angles=(math.nan,))


def test_sensor_range_limits_do_not_invent_hits_or_free_space() -> None:
    """Only a measured in-range return can supply free or occupied evidence."""
    projection = project(
        angles=(0.0, math.pi / 2, math.pi),
        ranges=(0.05, 6.0, 5.0),
        scan_policy=policy(
            min_range_m=0.1,
            max_range_m=5.0,
            max_interpolation_gap_rad=0.5,
        ),
    )

    assert projection.coverage.covered_cells == frozenset()
    assert projection.hit_cells == frozenset()
    assert projection.rays_traced == 0


def test_missing_beam_keeps_angular_gap_unobserved() -> None:
    """A missing beam keeps its index and prevents interpolation across the gap."""
    projection = project(angles=(0.0, 0.01, 0.02), ranges=(4.0, None, 4.0))
    assert projection.rays_traced == 2


def test_scan_pose_identity_stamp_and_time_window_must_match_exactly() -> None:
    """No approximate, cross-session, cross-clock, or cross-frame substitution is admitted."""
    selected_policy = policy()
    with pytest.raises(ValueError, match="stamps differ"):
        project_finite_planar_scan(
            scan=scan(stamp_ns=1_001),
            pose=pose(stamp_ns=1_000),
            grid=grid(),
            policy=selected_policy,
            now_ns=1_050,
        )
    with pytest.raises(ValueError, match="sessions differ"):
        project_finite_planar_scan(
            scan=scan(session="other-session"),
            pose=pose(),
            grid=grid(),
            policy=selected_policy,
            now_ns=1_050,
        )
    with pytest.raises(ValueError, match="clock domains differ"):
        project_finite_planar_scan(
            scan=scan(clock="wall-clock"),
            pose=pose(),
            grid=grid(),
            policy=selected_policy,
            now_ns=1_050,
        )
    with pytest.raises(ValueError, match="sensor frames differ"):
        project_finite_planar_scan(
            scan=scan(sensor_frame="other-laser"),
            pose=pose(),
            grid=grid(),
            policy=selected_policy,
            now_ns=1_050,
        )
    with pytest.raises(ValueError, match="target frame"):
        project_finite_planar_scan(
            scan=scan(),
            pose=pose(target_frame="other-map"),
            grid=grid(),
            policy=selected_policy,
            now_ns=1_050,
        )


def test_future_and_half_open_expired_scans_are_refused() -> None:
    """The source clock cannot run ahead and the validity deadline is exclusive."""
    with pytest.raises(ValueError, match="ahead"):
        project(now_ns=999)
    with pytest.raises(ValueError, match="freshness age"):
        project(now_ns=1_100)


def test_angular_gap_and_interpolation_margin_are_explicitly_bounded() -> None:
    """Only a permitted narrow gap is bridged, and a larger margin can remove that bridge."""
    angles = (0.0, math.pi / 2)
    ranges = (4.0, 2.0)
    unbridged = project(
        angles=angles,
        ranges=ranges,
        scan_policy=policy(max_interpolation_gap_rad=0.5),
    )
    bridged = project(angles=angles, ranges=ranges, scan_policy=policy())
    removed_by_margin = project(
        angles=angles,
        ranges=ranges,
        scan_policy=policy(interpolation_margin_m=1.5),
    )

    assert (1, 1) not in unbridged.coverage.covered_cells
    assert unbridged.unbridged_angular_gaps == 1
    assert (1, 1) in bridged.coverage.covered_cells
    assert bridged.rays_traced == 8
    assert (1, 1) not in removed_by_margin.coverage.covered_cells


def test_hit_margin_blocks_every_cell_near_a_boundary_hit() -> None:
    """A finite endpoint on a cell boundary conservatively enters adjacent hit cells."""
    projection = project(
        ranges=(1.5,),
        scan_policy=policy(hit_margin_m=0.2),
    )

    assert projection.hit_cells == frozenset({(1, 0), (2, 0)})
    assert projection.coverage.covered_cells == frozenset()


def test_hit_margin_and_beam_count_are_bounded() -> None:
    """A margin larger than one cell and an over-budget scan are refused up front."""
    with pytest.raises(ValueError, match="one planning cell"):
        project(scan_policy=policy(hit_margin_m=1.01))
    with pytest.raises(ValueError, match="beam bound"):
        project(angles=(0.0, 0.1), ranges=(2.0, 2.0), scan_policy=policy(max_beams=1))


def test_rotated_grid_uses_the_declared_cell_center_transform() -> None:
    """Candidate and hit cells follow the exact rotated origin used by the planning grid."""
    rotated = grid(resolution=0.5, origin_x=1.0, origin_y=2.0, yaw=math.pi / 2)
    assert rotated.cell_center_m((1, 0)) == pytest.approx((0.75, 2.75))

    projection = project(
        sensor_x=0.75,
        sensor_y=2.75,
        ranges=(1.5,),
        planning_grid=rotated,
    )
    assert projection.coverage.covered_cells == frozenset({(1, -1), (1, -2)})
    assert projection.hit_cells == frozenset({(1, -3)})


def _separated_views(
    *,
    selected_policy: ScanProjectionPolicy | None = None,
    planning_grid: PlanarGridTransform | None = None,
) -> tuple[ScanProjection, ScanProjection]:
    """Return two real projected rays crossing the center of cell (5, 5)."""
    selected_policy = selected_policy or policy()
    first_angle = math.atan2(2.0, 5.0)
    second_angle = math.atan2(5.0, 2.0)
    first = project(
        view_id="view-a",
        angles=(first_angle,),
        ranges=(8.0,),
        sensor_x=0.5,
        sensor_y=3.5,
        stamp_ns=1_000,
        planning_grid=planning_grid,
        scan_policy=selected_policy,
        now_ns=1_002,
    )
    second = project(
        view_id="view-b",
        angles=(second_angle,),
        ranges=(8.0,),
        sensor_x=3.5,
        sensor_y=0.5,
        stamp_ns=1_001,
        planning_grid=planning_grid,
        scan_policy=selected_policy,
        now_ns=1_002,
    )
    return first, second


def test_two_separated_views_qualify_a_shared_stale_cell() -> None:
    """A cell covered by both finite rays needs a real baseline and bearing separation."""
    first, second = _separated_views()
    evidence = qualify_two_view_stale_cells(
        first=first,
        second=second,
        stale_cells={(5, 5)},
        policy=policy(),
        now_ns=1_002,
    )

    assert evidence.refusals == ()
    assert evidence.qualified_cells == frozenset({(5, 5)})
    assert evidence.unqualified_cells == frozenset()
    assert evidence.baseline_m == pytest.approx(math.hypot(3.0, 3.0))


def test_distinct_scan_ids_at_one_origin_do_not_satisfy_two_view_gating() -> None:
    """Changing a view ID or heading alone cannot establish viewpoint parallax."""
    first, _ = _separated_views()
    duplicate_origin = project(
        view_id="view-b",
        angles=(math.pi / 4,),
        ranges=(8.0,),
        sensor_x=first.pose.x_m,
        sensor_y=first.pose.y_m,
        stamp_ns=1_001,
        now_ns=1_002,
    )
    evidence = qualify_two_view_stale_cells(
        first=first,
        second=duplicate_origin,
        stale_cells={(5, 5)},
        policy=policy(),
        now_ns=1_002,
    )

    assert evidence.qualified_cells == frozenset()
    assert TwoViewRefusal.INSUFFICIENT_BASELINE in evidence.refusals


def test_distant_cell_fails_the_per_cell_bearing_angle_gate() -> None:
    """Baseline alone is insufficient when both viewpoints see a cell on the same bearing."""
    selected_policy = policy(min_two_view_angle_rad=0.05, max_range_m=100.0)
    first = project(
        view_id="view-a",
        ranges=(51.0,),
        sensor_x=0.5,
        sensor_y=0.5,
        stamp_ns=1_000,
        scan_policy=selected_policy,
        now_ns=1_002,
    )
    second = project(
        view_id="view-b",
        ranges=(51.0,),
        sensor_x=3.5,
        sensor_y=0.5,
        stamp_ns=1_001,
        scan_policy=selected_policy,
        now_ns=1_002,
    )
    evidence = qualify_two_view_stale_cells(
        first=first,
        second=second,
        stale_cells={(50, 0)},
        policy=selected_policy,
        now_ns=1_002,
    )

    assert evidence.refusals == ()
    assert evidence.qualified_cells == frozenset()
    assert evidence.unqualified_cells == frozenset({(50, 0)})


def test_duplicate_identity_and_stale_pair_window_are_refused() -> None:
    """Two nominal projections cannot borrow identity or exceed the pair time window."""
    selected_policy = policy(max_scan_age_ns=1_000)
    first, second = _separated_views(selected_policy=selected_policy)
    duplicate = ScanProjection(
        sensor_id=second.sensor_id,
        coverage=ViewCoverage(first.coverage.view_id, second.coverage.covered_cells),
        hit_cells=second.hit_cells,
        pose=second.pose,
        grid=second.grid,
        valid_from_ns=second.valid_from_ns,
        valid_until_ns=second.valid_until_ns,
        rays_traced=second.rays_traced,
        unbridged_angular_gaps=second.unbridged_angular_gaps,
    )
    duplicate_result = qualify_two_view_stale_cells(
        first=first,
        second=duplicate,
        stale_cells={(5, 5)},
        policy=selected_policy,
        now_ns=1_002,
    )
    assert TwoViewRefusal.DUPLICATE_VIEW in duplicate_result.refusals

    other_sensor = project(
        view_id="view-other-sensor",
        sensor_id="other-lidar",
        angles=(math.atan2(5.0, 2.0),),
        ranges=(8.0,),
        sensor_x=3.5,
        sensor_y=0.5,
        stamp_ns=1_001,
        now_ns=1_002,
    )
    sensor_result = qualify_two_view_stale_cells(
        first=first,
        second=other_sensor,
        stale_cells={(5, 5)},
        policy=selected_policy,
        now_ns=1_002,
    )
    assert TwoViewRefusal.SENSOR_MISMATCH in sensor_result.refusals

    late = project(
        view_id="view-late",
        angles=(math.atan2(5.0, 2.0),),
        ranges=(8.0,),
        sensor_x=3.5,
        sensor_y=0.5,
        stamp_ns=1_101,
        scan_policy=selected_policy,
        now_ns=1_102,
    )
    interval_result = qualify_two_view_stale_cells(
        first=first,
        second=late,
        stale_cells={(5, 5)},
        policy=selected_policy,
        now_ns=1_102,
    )
    assert TwoViewRefusal.VIEW_INTERVAL_TOO_LARGE in interval_result.refusals


def test_existing_freshness_still_protects_unknown_and_n_owned_cells() -> None:
    """Scan candidates feed freshness without overriding unknown or N-owned layers."""
    first, second = _separated_views()
    coverage = ObservationCoverage(
        ProjectionBinding("localization-session", "sha256:" + "a" * 64, "sensor-clock"),
        max(first.valid_from_ns, second.valid_from_ns),
        min(first.valid_until_ns, second.valid_until_ns),
        (first.coverage, second.coverage),
    )
    layers = OccupancyLayers(
        shared_occupied=frozenset({(5, 5), (7, 6), (6, 7)}),
        localization_occupied=frozenset({(7, 6)}),
        current_occupied=second.hit_cells,
        unknown_cells=frozenset({(5, 5)}),
    )
    decision = decide_occupancy_freshness_mask(
        coverage=coverage,
        layers=layers,
        active_binding=ProjectionBinding(
            "localization-session", "sha256:" + "a" * 64, "sensor-clock"
        ),
        now_ns=1_002,
    )

    assert decision.mask_cells == frozenset()
    assert decision.protected_localization_occupied == frozenset({(7, 6)})
    assert decision.protected_current_occupied == second.hit_cells
    assert decision.unchanged_unknown_cells == frozenset({(5, 5)})
    assert decision.retained_shared_occupied == layers.shared_occupied
