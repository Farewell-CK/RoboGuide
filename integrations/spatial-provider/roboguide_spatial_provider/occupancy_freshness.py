"""Pure occupancy-mask decisions from adapter-validated observation coverage.

This module owns no sensor processing, ROS interaction, planning, or map history.  Its
inputs are already validated cell coverage from independently projected observation
views.  The returned mask is a short-lived planning hint and must be evaluated again from
the caller's current binding and clock before every reuse.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

Cell = tuple[int, int]

_MAX_IDENTIFIER_LENGTH = 256
_MAX_CELLS_PER_LAYER = 1_000_000
_MAX_VIEWS = 16
_REQUIRED_DISTINCT_VIEWS = 2
_maximum_timestamp_ns = 2**63 - 1


class MaskRefusal(str, Enum):  # noqa: UP042 - ROS Humble uses Python 3.10.
    """Stable reasons why no freshness mask may be applied."""

    __str__ = str.__str__

    NO_COVERAGE = "no_coverage"
    INSUFFICIENT_DISTINCT_VIEWS = "insufficient_distinct_views"
    LOCALIZATION_SESSION_MISMATCH = "localization_session_mismatch"
    PLANNING_DIGEST_MISMATCH = "planning_digest_mismatch"
    CLOCK_DOMAIN_MISMATCH = "clock_domain_mismatch"
    NOT_YET_VALID = "not_yet_valid"
    EXPIRED = "expired"


def _identifier(value: object, field: str) -> str:
    """Require an exact, non-empty identity that will not be silently normalized."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_IDENTIFIER_LENGTH
        or value.strip() != value
    ):
        raise ValueError(f"{field} must be a non-empty exact string")
    return value


def _timestamp(value: object, field: str) -> int:
    """Require an integer timestamp in one caller-declared clock domain."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer timestamp")
    if not 0 <= value <= _maximum_timestamp_ns:
        raise ValueError(f"{field} is outside the supported timestamp range")
    return value


def _cells(values: object, field: str) -> frozenset[Cell]:
    """Copy and validate a bounded collection of integer planning-grid cells."""
    if not isinstance(values, (set, frozenset, tuple)):
        raise ValueError(f"{field} must be a set, frozenset, or tuple of cells")
    if len(values) > _MAX_CELLS_PER_LAYER:
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
class ProjectionBinding:
    """Identity required to project current observations into a planning revision."""

    localization_session: str
    planning_digest: str
    clock_domain: str

    def __post_init__(self) -> None:
        """Validate exact binding identities without inferring any missing identity."""
        object.__setattr__(
            self,
            "localization_session",
            _identifier(self.localization_session, "localization_session"),
        )
        object.__setattr__(
            self, "planning_digest", _identifier(self.planning_digest, "planning_digest")
        )
        object.__setattr__(self, "clock_domain", _identifier(self.clock_domain, "clock_domain"))


@dataclass(frozen=True)
class ViewCoverage:
    """Adapter-validated free cells from one independently projected view.

    ``view_id`` identifies the exact projected acquisition, not a sensor or the validity
    of its free evidence.  The adapter owns ray/occlusion/calibration checks before this
    boundary.  Identical IDs cannot be used to claim another independent view.
    """

    view_id: str
    covered_cells: frozenset[Cell]

    def __post_init__(self) -> None:
        """Freeze the view identity and its already validated free-cell coverage."""
        object.__setattr__(self, "view_id", _identifier(self.view_id, "view_id"))
        object.__setattr__(self, "covered_cells", _cells(self.covered_cells, "covered_cells"))


@dataclass(frozen=True)
class ObservationCoverage:
    """Time- and session-bound validated coverage from one or more distinct views."""

    binding: ProjectionBinding
    valid_from_ns: int
    valid_until_ns: int
    views: tuple[ViewCoverage, ...]

    def __post_init__(self) -> None:
        """Validate the half-open validity interval and distinct projected acquisitions."""
        if not isinstance(self.binding, ProjectionBinding):
            raise ValueError("binding must be a ProjectionBinding")
        valid_from = _timestamp(self.valid_from_ns, "valid_from_ns")
        valid_until = _timestamp(self.valid_until_ns, "valid_until_ns")
        if valid_from >= valid_until:
            raise ValueError("observation coverage requires a non-empty validity window")
        if not isinstance(self.views, tuple) or len(self.views) > _MAX_VIEWS:
            raise ValueError(f"views must be a tuple of at most {_MAX_VIEWS} entries")
        if any(not isinstance(view, ViewCoverage) for view in self.views):
            raise ValueError("views must contain only ViewCoverage entries")
        if len({view.view_id for view in self.views}) != len(self.views):
            raise ValueError("view_id values must identify distinct projected views")
        object.__setattr__(self, "valid_from_ns", valid_from)
        object.__setattr__(self, "valid_until_ns", valid_until)


@dataclass(frozen=True)
class OccupancyLayers:
    """Explicit occupied and unknown cells, preserving their independent sources."""

    shared_occupied: frozenset[Cell]
    localization_occupied: frozenset[Cell]
    current_occupied: frozenset[Cell]
    unknown_cells: frozenset[Cell] = frozenset()

    def __post_init__(self) -> None:
        """Freeze all supplied layers without promoting unknown or absent cells to free."""
        object.__setattr__(
            self,
            "shared_occupied",
            _cells(self.shared_occupied, "shared_occupied"),
        )
        object.__setattr__(
            self,
            "localization_occupied",
            _cells(self.localization_occupied, "localization_occupied"),
        )
        object.__setattr__(
            self,
            "current_occupied",
            _cells(self.current_occupied, "current_occupied"),
        )
        object.__setattr__(self, "unknown_cells", _cells(self.unknown_cells, "unknown_cells"))


@dataclass(frozen=True)
class OccupancyMaskDecision:
    """Consequence-free mask cells and the evidence retained around them.

    ``mask_cells`` may suppress only matching ``shared_occupied`` cells.  It never
    changes localization, current-obstacle, or unknown layers.  The caller must call
    :func:`decide_occupancy_freshness_mask` again before any reuse at or after
    ``revalidate_at_ns`` or after the active projection binding changes.
    """

    mask_cells: frozenset[Cell]
    retained_shared_occupied: frozenset[Cell]
    protected_localization_occupied: frozenset[Cell]
    protected_current_occupied: frozenset[Cell]
    unchanged_unknown_cells: frozenset[Cell]
    uncovered_shared_occupied: frozenset[Cell]
    refusals: tuple[MaskRefusal, ...]
    revalidate_at_ns: int | None


def decide_occupancy_freshness_mask(
    *,
    coverage: ObservationCoverage | None,
    layers: OccupancyLayers,
    active_binding: ProjectionBinding,
    now_ns: int,
) -> OccupancyMaskDecision:
    """Select shared-only occupied cells supported by two distinct current free views.

    The validity window is ``[valid_from_ns, valid_until_ns)`` in ``clock_domain``.
    Missing coverage, any binding/window refusal, or fewer than two distinct projected
    views yields no mask.  Unknown, localization, and current-occupied cells are always
    excluded from the mask.  The function is pure and performs no clock, storage, ROS,
    or planner side effects.
    """
    if not isinstance(layers, OccupancyLayers):
        raise ValueError("layers must be OccupancyLayers")
    if not isinstance(active_binding, ProjectionBinding):
        raise ValueError("active_binding must be a ProjectionBinding")
    current_ns = _timestamp(now_ns, "now_ns")

    refusals: list[MaskRefusal] = []
    confirmed_free: frozenset[Cell] = frozenset()
    if coverage is None or not coverage.views:
        refusals.append(MaskRefusal.NO_COVERAGE)
    else:
        if len(coverage.views) < _REQUIRED_DISTINCT_VIEWS:
            refusals.append(MaskRefusal.INSUFFICIENT_DISTINCT_VIEWS)
        if coverage.binding.localization_session != active_binding.localization_session:
            refusals.append(MaskRefusal.LOCALIZATION_SESSION_MISMATCH)
        if coverage.binding.planning_digest != active_binding.planning_digest:
            refusals.append(MaskRefusal.PLANNING_DIGEST_MISMATCH)
        if coverage.binding.clock_domain != active_binding.clock_domain:
            refusals.append(MaskRefusal.CLOCK_DOMAIN_MISMATCH)
        if current_ns < coverage.valid_from_ns:
            refusals.append(MaskRefusal.NOT_YET_VALID)
        elif current_ns >= coverage.valid_until_ns:
            refusals.append(MaskRefusal.EXPIRED)
        if not refusals:
            view_count: dict[Cell, int] = {}
            for view in coverage.views:
                for cell in view.covered_cells:
                    view_count[cell] = view_count.get(cell, 0) + 1
            confirmed_free = frozenset(
                cell for cell, count in view_count.items() if count >= _REQUIRED_DISTINCT_VIEWS
            )

    shared = layers.shared_occupied
    protected_localization = shared & layers.localization_occupied
    protected_current = shared & layers.current_occupied
    eligible_shared = shared - protected_localization - protected_current - layers.unknown_cells
    mask_cells = eligible_shared & confirmed_free
    uncovered_shared = eligible_shared - confirmed_free
    revalidate_at = coverage.valid_until_ns if coverage is not None else None

    return OccupancyMaskDecision(
        mask_cells=frozenset(mask_cells),
        retained_shared_occupied=frozenset(shared - mask_cells),
        protected_localization_occupied=frozenset(protected_localization),
        protected_current_occupied=frozenset(protected_current),
        unchanged_unknown_cells=layers.unknown_cells,
        uncovered_shared_occupied=frozenset(uncovered_shared),
        refusals=tuple(refusals),
        revalidate_at_ns=revalidate_at,
    )
