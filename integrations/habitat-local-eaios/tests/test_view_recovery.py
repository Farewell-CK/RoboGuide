"""Alternative observation points require actual bounded map and control evidence."""

from __future__ import annotations

import math
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from habitat_local_eaios import view_recovery_binding as bindings  # noqa: E402
from habitat_local_eaios.model import IntegrationError  # noqa: E402
from habitat_local_eaios.view_recovery import select_viewpoint  # noqa: E402
from habitat_local_eaios.view_recovery_binding import ObservationViewpointRecovery  # noqa: E402
from habitat_local_eaios.view_scan import ViewScanSpec  # noqa: E402
from test_observation_control import _control, _sample  # noqa: E402
from test_view_scan import BASE_TYPES  # noqa: E402

START = (0.0, 0.0, 0.0)
ENTITY = (0.0, 0.5, 2.0)


def test_actual_route_and_original_footprint_both_precede_selection() -> None:
    """A projected point or shortest route alone cannot establish a scan-supported candidate."""
    queries: list[Any] = []

    def footprint(point: Any, yaw: float) -> bool:
        """Reject the first geometrically reachable point using the supplied real-control gate."""
        queries.append((point, yaw))
        return len(queries) == 2

    result = select_viewpoint(
        START, ENTITY, lambda point: point, lambda point: math.dist(START, point), footprint
    )
    assert result.candidates_examined == 2 and len(queries) == 2
    assert result.point != (0.4, 0.0, 0.0)
    assert result.heading_radians == pytest.approx(
        math.atan2(-(ENTITY[2] - result.point[2]), ENTITY[0] - result.point[0])
    )


@pytest.mark.parametrize("failure", ["no-route", "clipped", "other-floor", "collision", "too-long"])
def test_bounded_miss_never_manufactures_a_route_or_loosens_checks(failure: str) -> None:
    """Twenty-four unsupported candidates stop explicitly, without a success fallback."""
    count = 0

    def project(point: Any) -> Any:
        """Expose one queried map point while retaining exact query accounting."""
        nonlocal count
        count += 1
        return (
            START
            if failure == "clipped"
            else (point[0], 4.0, point[2])
            if failure == "other-floor"
            else point
        )

    def route(point: Any) -> float | None:
        """Return absent or excessive actual route evidence instead of a fake direct route."""
        return (
            None
            if failure == "no-route"
            else 2.0
            if failure == "too-long"
            else math.dist(START, point)
        )

    with pytest.raises(IntegrationError, match="no supported candidate"):
        select_viewpoint(START, ENTITY, project, route, lambda point, yaw: failure != "collision")
    assert count == 24


@pytest.mark.parametrize("distance", [True, -1.0, float("nan"), 0.0])
def test_bad_route_evidence_is_not_skipped_to_obtain_a_success(distance: Any) -> None:
    """Malformed, nonfinite or impossibly short native routes stay unavailable."""
    with pytest.raises(IntegrationError, match="malformed"):
        select_viewpoint(
            START, ENTITY, lambda point: point, lambda point: distance, lambda point, yaw: True
        )


def test_previous_points_and_time_budget_bound_the_same_attempt() -> None:
    """History prevents returning to an already selected point; expiry grants no extra queries."""
    first = select_viewpoint(
        START,
        ENTITY,
        lambda point: point,
        lambda point: math.dist(START, point),
        lambda point, yaw: True,
    )
    second = select_viewpoint(
        START,
        ENTITY,
        lambda point: point,
        lambda point: math.dist(START, point),
        lambda point, yaw: True,
        (first.point,),
    )
    assert first.point != second.point and second.candidates_examined == 2
    times = iter([0.0, 1.0])
    with pytest.raises(IntegrationError, match="time budget"):
        select_viewpoint(
            START,
            ENTITY,
            lambda point: pytest.fail("expired search queried geometry"),
            lambda point: 1.0,
            lambda point, yaw: True,
            clock=lambda: next(times),
        )


class Geometry:
    """Supply detached geometry while exercising production selection and hook ownership."""

    def __init__(self, navigation: Any, scan: Any, target: str) -> None:
        """Bind one exact original target with no simulator, RNG, sensor or control interface."""
        self.navigation, self.target_name = navigation, target
        self.target_index, self.start, self.center = 0, START, ENTITY
        self._np = SimpleNamespace(asarray=tuple)

    def project(self, point: Any) -> Any:
        """Return the supplied local mesh candidate without modifying a world."""
        return point

    def route_length(self, point: Any) -> float:
        """Return the independently supplied route length for this fixture's open map."""
        return math.dist(START, point)

    def permits_scan(self, point: Any, yaw: float) -> bool:
        """Expose the original-control result; no adapter may turn false into true."""
        return bool(self.navigation.scan_allowed)

    def target(self, index: int) -> Any:
        """Reject changed semantic entities just as the production geometry witness does."""
        if index != 0 or self.navigation.target_changed:
            raise IntegrationError("changed target")
        return ENTITY

    def path(self, point: Any) -> Any:
        """Expose a successful route or a later genuine route-loss observation."""
        return SimpleNamespace(points=[START, point]) if self.navigation.route_available else None


def setup_recovery(monkeypatch: pytest.MonkeyPatch, record: Any) -> Any:
    """Construct a negative observation attempt with original cache/method sentinels."""
    monkeypatch.setattr(bindings, "NativeViewpointGeometry", Geometry)
    control = _control()
    control.update(_sample(control, 10, False))
    navigation = SimpleNamespace(
        scan_allowed=True,
        route_available=True,
        target_changed=False,
        _targets={0: ("original-point", "original-object")},
        _get_target_for_idx="original-target-method",
        _path_to_point="original-path-method",
        cur_articulated_agent=SimpleNamespace(base_pos=START),
    )
    return ObservationViewpointRecovery(navigation, object(), control, record)  # type: ignore[arg-type]


def test_recovery_preserves_semantic_target_and_restores_all_owned_methods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selected navigation changes a local point, never the entity, cache, peer or budget."""
    rows: list[Any] = []
    recovery = setup_recovery(monkeypatch, rows.append)
    original = dict(vars(recovery.navigation))
    budget = recovery.control.snapshot()["remaining_simulator_steps"]
    recovery.dispatch("new-nav", 2, ("original-stationary-scan", 1))
    point, entity = recovery.navigation._get_target_for_idx(0)
    assert entity == ENTITY and point == (0.4, 0.0, 0.0)
    assert recovery.navigation._targets == original["_targets"]
    assert recovery.navigation._path_to_point(point) == [START, point]
    assert recovery.control.snapshot()["remaining_simulator_steps"] == budget
    assert rows[-1]["tool_call_id"] == "new-nav" and rows[-1]["stationary_scan_call"] == [
        "original-stationary-scan",
        1,
    ]
    assert rows[-1]["benchmark_goal_satisfied"] is None
    recovery.close()
    recovery.close()
    assert vars(recovery.navigation) == original


@pytest.mark.parametrize(
    "failure", ["no-stationary-scan", "collision", "budget", "positive", "unknown"]
)
def test_unsupported_recovery_cannot_reach_navigation_dispatch(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Missing trigger is inert; unavailable geometry or a changed condition refuses motion."""
    rows: list[Any] = []
    recovery = setup_recovery(monkeypatch, rows.append)
    if failure == "no-stationary-scan":
        recovery.dispatch("nav", 1, None)
        assert not rows and not recovery._hooks
        return
    if failure == "collision":
        recovery.navigation.scan_allowed = False
    elif failure == "budget":
        recovery.control.update(_sample(recovery.control, 266, False))
    elif failure == "positive":
        recovery.control.update(_sample(recovery.control, 11, True))
    else:
        recovery.control._sample = None
    with pytest.raises(IntegrationError):
        recovery.dispatch("nav", 2, ("scan", 1))
    assert not recovery._hooks
    assert recovery.navigation._get_target_for_idx == "original-target-method"


def test_attempt_limit_stale_target_and_failed_route_remain_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Release/rebind does not refresh a search budget, and no invalid live route is invented."""
    rows: list[Any] = []
    recovery = setup_recovery(monkeypatch, rows.append)
    recovery.dispatch("nav-1", 2, ("scan-1", 1))
    point = rows[-1]["selected_ground_position"]
    recovery.navigation.target_changed = True
    with pytest.raises(IntegrationError, match="changed target"):
        recovery.navigation._get_target_for_idx(0)
    recovery.navigation.target_changed, recovery.navigation.route_available = False, False
    with pytest.raises(IntegrationError, match="became unavailable"):
        recovery.navigation._path_to_point(point)
    recovery.navigation.route_available = True
    recovery.close()
    recovery.dispatch("nav-2", 4, ("scan-2", 3))
    assert rows[-1]["selected_ground_position"] != point
    with pytest.raises(IntegrationError, match="attempt budget"):
        recovery.dispatch("nav-3", 6, ("scan-3", 5))
    assert not recovery._hooks and recovery._used == 2


def test_evidence_write_failure_cannot_relax_collision_or_change_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Archive failure neither creates a candidate nor leaves method overrides behind."""

    def bad_writer(row: Any) -> None:
        """Represent evidence storage failure independently of geometry and original execution."""
        raise OSError("disk unavailable")

    recovery = setup_recovery(monkeypatch, bad_writer)
    recovery.navigation.scan_allowed = False
    with pytest.raises(IntegrationError, match="no supported candidate"):
        recovery.dispatch("nav", 2, ("scan", 1))
    recovery.navigation.scan_allowed = True
    recovery.dispatch("nav-next", 3, ("scan", 1))
    recovery.close()
    assert recovery.navigation._get_target_for_idx == "original-target-method"


def test_cleanup_preserves_a_later_owner_and_separate_attempts_have_separate_budgets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closing one binding cannot delete another owner's override or transfer its budget."""
    first = setup_recovery(monkeypatch, lambda row: None)
    second = setup_recovery(monkeypatch, lambda row: None)
    first.dispatch("nav-1", 2, ("scan-1", 1))
    first.navigation._get_target_for_idx = "later-owner"
    first.close()
    assert first.navigation._get_target_for_idx == "later-owner"
    assert first.navigation._path_to_point == "original-path-method"
    assert first._used == 1 and second._used == 0
    second.dispatch("nav-2", 2, ("scan-2", 1))
    second.close()
    assert second._used == 1 and second.navigation._get_target_for_idx == "original-target-method"


@pytest.mark.parametrize(
    "control,points,supported",
    [(0, 3, False), (1, 0, False), (1, 3, True), (1, 17, False), (2, 3, False)],
)
def test_other_control_interfaces_keep_their_original_navigation(
    monkeypatch: pytest.MonkeyPatch,
    control: int,
    points: int,
    supported: bool,
) -> None:
    """Only real supported footprint geometry enables recovery; names or flags are insufficient."""
    from habitat_local_eaios import view_scan

    monkeypatch.setattr(view_scan, "_original_base_types", lambda: BASE_TYPES)
    action = BASE_TYPES[control]()
    action._navmesh_offset = [[0, 0]] * points
    assert (
        bindings.supports_viewpoint_recovery(cast(ViewScanSpec, SimpleNamespace(action=action)))
        is supported
    )
    substitute = type(type(action).__name__, (), {"_navmesh_offset": [[0, 0]]})()
    assert (
        bindings.supports_viewpoint_recovery(cast(ViewScanSpec, SimpleNamespace(action=substitute)))
        is False
    )
