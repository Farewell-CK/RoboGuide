"""Focused acceptance for the pure, conservative occupancy freshness mask."""

from __future__ import annotations

import pytest
from roboguide_spatial_provider.occupancy_freshness import (
    MaskRefusal,
    ObservationCoverage,
    OccupancyLayers,
    ProjectionBinding,
    ViewCoverage,
    decide_occupancy_freshness_mask,
)


def binding(
    *,
    session: str = "localization-session-1",
    planning: str = "sha256:" + "a" * 64,
    clock: str = "sensor-steady",
) -> ProjectionBinding:
    """Build an exact active projection identity for a focused freshness decision."""
    return ProjectionBinding(session, planning, clock)


def coverage(
    *,
    views: tuple[ViewCoverage, ...] | None = None,
    valid_from_ns: int = 100,
    valid_until_ns: int = 200,
    projection_binding: ProjectionBinding | None = None,
) -> ObservationCoverage:
    """Build adapter-validated coverage without performing any observation work."""
    first = ViewCoverage("view-a", frozenset({(0, 0), (1, 0), (2, 0), (3, 0)}))
    second = ViewCoverage("view-b", frozenset({(0, 0), (1, 0), (2, 0), (3, 0)}))
    return ObservationCoverage(
        projection_binding or binding(),
        valid_from_ns,
        valid_until_ns,
        (first, second) if views is None else views,
    )


def layers() -> OccupancyLayers:
    """Keep shared residual, localization-owned, current, and unknown cells distinct."""
    return OccupancyLayers(
        shared_occupied=frozenset({(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)}),
        localization_occupied=frozenset({(1, 0)}),
        current_occupied=frozenset({(2, 0)}),
        unknown_cells=frozenset({(3, 0), (5, 0)}),
    )


def test_m_only_residual_is_masked_while_local_current_and_unknown_are_retained() -> None:
    """Only doubly covered shared residual occupancy can be temporarily suppressed."""
    decision = decide_occupancy_freshness_mask(
        coverage=coverage(), layers=layers(), active_binding=binding(), now_ns=150
    )

    assert decision.refusals == ()
    assert decision.mask_cells == frozenset({(0, 0)})
    assert decision.protected_localization_occupied == frozenset({(1, 0)})
    assert decision.protected_current_occupied == frozenset({(2, 0)})
    assert decision.unchanged_unknown_cells == frozenset({(3, 0), (5, 0)})
    assert decision.retained_shared_occupied == frozenset({(1, 0), (2, 0), (3, 0), (4, 0)})
    assert decision.revalidate_at_ns == 200


def test_caller_re_evaluates_expiry_at_the_half_open_deadline() -> None:
    """A mask is never extended by elapsed use and must be evaluated again at expiry."""
    original = decide_occupancy_freshness_mask(
        coverage=coverage(), layers=layers(), active_binding=binding(), now_ns=199
    )
    expired = decide_occupancy_freshness_mask(
        coverage=coverage(), layers=layers(), active_binding=binding(), now_ns=200
    )

    assert original.mask_cells == frozenset({(0, 0)})
    assert original.revalidate_at_ns == 200
    assert expired.mask_cells == frozenset()
    assert expired.refusals == (MaskRefusal.EXPIRED,)
    assert expired.retained_shared_occupied == layers().shared_occupied


def test_no_coverage_leaves_every_shared_occupied_cell_unchanged() -> None:
    """An empty observation input cannot become free evidence by default."""
    decision = decide_occupancy_freshness_mask(
        coverage=None, layers=layers(), active_binding=binding(), now_ns=150
    )

    assert decision.mask_cells == frozenset()
    assert decision.refusals == (MaskRefusal.NO_COVERAGE,)
    assert decision.retained_shared_occupied == layers().shared_occupied


@pytest.mark.parametrize(
    ("active", "refusal"),
    [
        (binding(session="another-session"), MaskRefusal.LOCALIZATION_SESSION_MISMATCH),
        (binding(planning="sha256:" + "b" * 64), MaskRefusal.PLANNING_DIGEST_MISMATCH),
        (binding(clock="wall-unsynchronized"), MaskRefusal.CLOCK_DOMAIN_MISMATCH),
    ],
)
def test_changed_session_planning_or_clock_binding_invalidates_the_mask(
    active: ProjectionBinding, refusal: MaskRefusal
) -> None:
    """Coverage cannot migrate to another localization, planning, or time identity."""
    decision = decide_occupancy_freshness_mask(
        coverage=coverage(), layers=layers(), active_binding=active, now_ns=150
    )

    assert decision.mask_cells == frozenset()
    assert decision.refusals == (refusal,)
    assert decision.retained_shared_occupied == layers().shared_occupied


def test_one_view_is_not_independent_coverage_and_uncovered_cells_are_retained() -> None:
    """A single projection cannot satisfy the two-view evidence threshold."""
    one_view = (ViewCoverage("view-a", frozenset({(0, 0), (4, 0)})),)
    decision = decide_occupancy_freshness_mask(
        coverage=coverage(views=one_view),
        layers=layers(),
        active_binding=binding(),
        now_ns=150,
    )

    assert decision.mask_cells == frozenset()
    assert decision.refusals == (MaskRefusal.INSUFFICIENT_DISTINCT_VIEWS,)
    assert decision.retained_shared_occupied == layers().shared_occupied


def test_shared_cell_missing_from_either_view_remains_occupied() -> None:
    """Per-cell agreement is required even when two distinct views are present."""
    views = (
        ViewCoverage("view-a", frozenset({(0, 0), (4, 0)})),
        ViewCoverage("view-b", frozenset({(0, 0)})),
    )
    decision = decide_occupancy_freshness_mask(
        coverage=coverage(views=views),
        layers=layers(),
        active_binding=binding(),
        now_ns=150,
    )

    assert decision.refusals == ()
    assert decision.mask_cells == frozenset({(0, 0)})
    assert decision.uncovered_shared_occupied == frozenset({(4, 0)})
    assert (4, 0) in decision.retained_shared_occupied


def test_unknown_cell_cannot_be_promoted_to_free_even_when_doubly_covered() -> None:
    """Unknown remains explicit and blocks a mask independently of free coverage."""
    input_layers = OccupancyLayers(
        shared_occupied=frozenset({(0, 0), (2, 0)}),
        localization_occupied=frozenset(),
        current_occupied=frozenset(),
        unknown_cells=frozenset({(0, 0)}),
    )
    decision = decide_occupancy_freshness_mask(
        coverage=coverage(), layers=input_layers, active_binding=binding(), now_ns=150
    )

    assert decision.mask_cells == frozenset({(2, 0)})
    assert decision.unchanged_unknown_cells == frozenset({(0, 0)})
    assert decision.retained_shared_occupied == frozenset({(0, 0)})


def test_duplicate_view_id_or_invalid_validity_window_is_refused_at_construction() -> None:
    """Aliased view IDs and empty intervals cannot cross the validated data boundary."""
    duplicate = ViewCoverage("same-view", frozenset({(0, 0)}))
    with pytest.raises(ValueError, match="distinct projected views"):
        ObservationCoverage(binding(), 100, 200, (duplicate, duplicate))
    with pytest.raises(ValueError, match="non-empty validity window"):
        ObservationCoverage(binding(), 200, 200, ())
