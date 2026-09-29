"""Deterministic goal-region selection and opt-in deployment wiring tests."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.emos_stage2 import _configure_goal_region_navigation  # noqa: E402
from habitat_local_eaios.goal_region_navigation import (  # noqa: E402
    GoalRegionResolutionError,
    Point3,
    any_at_conjunct_names,
    select_goal_region_point,
)
from habitat_local_eaios.model import IntegrationError  # noqa: E402


def test_reachable_original_point_is_preserved() -> None:
    """Keep the vendor target when the agent path and official region agree."""
    calls: list[Point3] = []

    def vertices() -> tuple[Point3, ...]:
        """Fail if a preserved original point needlessly enumerates the mesh."""
        raise AssertionError("navmesh enumeration was unnecessary")

    def path_length(point: Point3) -> float | None:
        """Record the one expected read-only route query."""
        calls.append(point)
        return 4.0

    selected = select_goal_region_point(
        original_point=(0.0, 0.0, 0.0),
        goal_center=(0.5, 0.0, 0.0),
        reference_offset=(0.0, 0.0, 0.0),
        radius_m=2.0,
        stop_radius_m=0.5,
        navmesh_vertices=vertices,
        path_length=path_length,
    )
    assert selected.source == "original"
    assert selected.point == (0.0, 0.0, 0.0)
    assert selected.path_queries == 1
    assert calls == [(0.0, 0.0, 0.0)]


def test_unreachable_original_uses_reachable_agent_navmesh_point() -> None:
    """Reject a wrong-floor original point and choose within the 3D goal ball."""
    routes = {
        (0.0, -1.0, 0.0): None,
        (0.5, 0.0, 0.0): 5.0,
        (0.0, 0.0, 0.0): 4.0,
    }

    def path_length(point: Point3) -> float | None:
        """Represent one agent-specific static navmesh's paths."""
        return routes.get(point)

    selected = select_goal_region_point(
        original_point=(0.0, -1.0, 0.0),
        goal_center=(0.0, -0.5, 0.0),
        reference_offset=(0.0, 0.0, 0.0),
        radius_m=1.0,
        stop_radius_m=0.1,
        navmesh_vertices=((0.5, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 2.0, 0.0)),
        path_length=path_length,
    )
    assert selected.source == "agent-navmesh"
    assert selected.point == (0.0, 0.0, 0.0)
    assert selected.path_queries == 3
    assert selected.candidates_in_region == 2


def test_reference_offset_and_height_prevent_false_candidate() -> None:
    """Use the PDDL reference offset and full 3D distance, never X/Z alone."""
    queried: list[Point3] = []

    def path_length(point: Point3) -> float | None:
        """Return paths only for examined, geometrically admitted points."""
        queried.append(point)
        return 1.0

    selected = select_goal_region_point(
        original_point=(0.0, -3.0, 0.0),
        goal_center=(0.0, 1.0, 0.0),
        reference_offset=(0.0, 0.5, 0.0),
        radius_m=1.0,
        stop_radius_m=0.1,
        navmesh_vertices=((0.0, 0.0, 0.0), (0.0, -3.0, 0.2)),
        path_length=path_length,
    )
    assert selected.point == (0.0, 0.0, 0.0)
    assert queried == [(0.0, 0.0, 0.0)]


def test_projected_center_accounts_for_original_skill_stop_radius() -> None:
    """Reject an edge point that can stop outside the official 3D goal ball."""
    routed: list[Point3] = []

    def path_length(point: Point3) -> float | None:
        """Record the projected-center route selected for this agent."""
        routed.append(point)
        return 5.0

    def project_center(center: Point3) -> Point3:
        """Represent the agent navmesh directly below this goal center."""
        return center[0], 0.12575, center[2]

    selected = select_goal_region_point(
        original_point=(-3.9907, 0.12575, 2.61777),
        goal_center=(-3.97234, -1.49818, 3.47991),
        reference_offset=(0.0, 0.0, 0.0),
        radius_m=2.0,
        stop_radius_m=0.5,
        navmesh_vertices=lambda: (_ for _ in ()).throw(AssertionError("unneeded vertex scan")),
        path_length=path_length,
        project_center=project_center,
    )
    assert selected.original_status == "stop_envelope_exceeds_goal"
    assert selected.point == (-3.97234, 0.12575, 3.47991)
    assert selected.estimated_stop_envelope_distance_m < 2.0
    assert routed == [selected.point]


@pytest.mark.parametrize(
    ("vertices", "budget", "message"),
    [
        (((0.0, 0.0, 0.0),), 2, "no agent-specific path"),
        (((0.0, 0.0, 0.0), (0.1, 0.0, 0.0)), 1, "path-query budget exhausted"),
    ],
)
def test_missing_route_fails_explicitly(
    vertices: tuple[Point3, ...], budget: int, message: str
) -> None:
    """Do not pass a straight-line fallback to the original Oracle as a route."""

    def no_path(_point: Point3) -> float | None:
        """Represent a disconnected agent-specific navigation graph."""
        return None

    with pytest.raises(GoalRegionResolutionError, match=message):
        select_goal_region_point(
            original_point=(0.0, 0.0, 0.0),
            goal_center=(0.0, 0.0, 0.0),
            reference_offset=(0.0, 0.0, 0.0),
            radius_m=2.0,
            stop_radius_m=0.0,
            navmesh_vertices=vertices,
            path_length=no_path,
            max_path_queries=budget,
        )


def test_vertex_and_authoritative_radius_fail_closed() -> None:
    """Bound enumeration and reject missing official metric parameters."""

    def path_length(_point: Point3) -> float | None:
        """Force fallback so the search examines its vertex budget."""
        return None

    common: dict[str, Any] = {
        "original_point": (0.0, 0.0, 0.0),
        "goal_center": (0.0, 0.0, 0.0),
        "reference_offset": (0.0, 0.0, 0.0),
        "navmesh_vertices": ((0.0, 0.0, 0.0), (0.1, 0.0, 0.0)),
        "path_length": path_length,
    }
    with pytest.raises(GoalRegionResolutionError, match="vertex budget"):
        select_goal_region_point(**common, radius_m=2.0, stop_radius_m=0.0, max_vertices=1)
    with pytest.raises(GoalRegionResolutionError, match="radius is unavailable"):
        select_goal_region_point(**common, radius_m=float("nan"), stop_radius_m=0.0)


def test_only_direct_conjunctive_any_at_goals_are_admitted() -> None:
    """Avoid reinterpreting disjunctions or unrelated entity predicates."""
    first = {"kind": "predicate", "name": "any_at", "arguments": ["object"]}
    second = {"kind": "predicate", "name": "any_at", "arguments": ["receptacle"]}
    conjunction = {
        "kind": "logical",
        "operator": "and",
        "quantifier": None,
        "operands": [first, second],
    }
    assert any_at_conjunct_names(conjunction) == {"object", "receptacle"}
    assert any_at_conjunct_names({**conjunction, "operator": "or"}) == frozenset()
    assert any_at_conjunct_names({**conjunction, "quantifier": "exists"}) == frozenset()
    assert (
        any_at_conjunct_names({"kind": "predicate", "name": "object_at", "arguments": ["object"]})
        == frozenset()
    )


def test_config_switches_only_expected_vendor_navigation_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require every configured agent to use the declared original action type."""
    module = ModuleType("habitat_local_eaios.goal_region_action")
    module.GoalRegionOracleNavDiffBaseAction = type(  # type: ignore[attr-defined]
        "GoalRegionOracleNavDiffBaseAction", (), {}
    )
    monkeypatch.setitem(sys.modules, module.__name__, module)
    actions = {
        "agent_0_oracle_nav_action": SimpleNamespace(type="OracleNavDiffBaseAction"),
        "agent_1_oracle_nav_action": SimpleNamespace(type="OracleNavDiffBaseAction"),
        "agent_0_pick_action": SimpleNamespace(type="PickAction"),
    }
    config = SimpleNamespace(
        habitat=SimpleNamespace(
            task=SimpleNamespace(actions=actions),
            simulator=SimpleNamespace(agents_order=["agent_0", "agent_1"]),
        )
    )

    @contextmanager
    def read_write(_config: Any) -> Iterator[None]:
        """Mimic the deployment's temporary OmegaConf write scope."""
        yield

    _configure_goal_region_navigation(config, read_write)
    assert all(
        actions[f"agent_{agent_id}_oracle_nav_action"].type == "GoalRegionOracleNavDiffBaseAction"
        for agent_id in (0, 1)
    )
    assert actions["agent_0_pick_action"].type == "PickAction"
    actions["agent_0_oracle_nav_action"].type = "OracleNavDiffBaseAction"
    actions["agent_1_oracle_nav_action"].type = "UnexpectedAction"
    with pytest.raises(IntegrationError, match="agent_1_oracle_nav_action"):
        _configure_goal_region_navigation(config, read_write)
