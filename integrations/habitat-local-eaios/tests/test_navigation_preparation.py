"""Deterministic preparation, failure attribution and no-partial-motion regressions."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.goal_region_navigation import GoalRegionSearchMiss  # noqa: E402
from habitat_local_eaios.model import IntegrationError  # noqa: E402
from habitat_local_eaios.navigation_preparation import (  # noqa: E402
    NavigationPreparationFailure,
    prepare_navigation_actions,
)


class NavigationAction:
    """Represent a selectable local action whose preparation never moves a robot."""

    def __init__(self, error: Exception | None = None, cleanup_error: bool = False) -> None:
        """Configure a local failure while retaining observed command counts."""
        self.error = error
        self.cleanup_error = cleanup_error
        self.pending = False
        self.moves = 0
        self.preparations = 0
        self.arguments: dict[str, Any] = {}

    def prepare_navigation_step(self, **arguments: Any) -> None:
        """Prepare one exact command or expose the injected cause before motion."""
        self.preparations += 1
        self.arguments = arguments
        self.pending = True
        if self.error is not None:
            raise self.error

    def discard_prepared_navigation(self) -> None:
        """Remove the unconsumed command, optionally exposing a cleanup fault."""
        self.pending = False
        if self.cleanup_error:
            raise OSError("cleanup sentinel")

    def step(self, **arguments: Any) -> None:
        """Emulate a sequential Gym action that can otherwise move before its peer fails."""
        if not self.pending:
            self.prepare_navigation_step(**arguments)
        self.moves += 1
        self.pending = False


def _miss() -> GoalRegionSearchMiss:
    """Return the archived failure shape without embedding an Episode or robot identity."""
    return GoalRegionSearchMiss(
        "no agent-specific path to stop-compatible official goal region",
        {"reason_code": "candidates_exhausted", "path_queries": 2, "search_truncated": False},
    )


def _decoded(agent_ids: tuple[int, ...] = (0, 1)) -> dict[str, Any]:
    """Supply a vendor-shaped selected joint command with exact local target indices."""
    return {
        "action": tuple(f"agent_{agent}_oracle_nav_action" for agent in agent_ids),
        "action_args": {f"agent_{agent}_oracle_nav_action": [agent + 1] for agent in agent_ids},
    }


def test_unprepared_second_action_failure_reproduces_partial_joint_motion() -> None:
    """Model the actual ordering: the first base moves before its sibling resolver fails."""
    first, second = NavigationAction(), NavigationAction(_miss())
    args = _decoded()["action_args"]
    with pytest.raises(GoalRegionSearchMiss):
        first.step(**args)
        second.step(**args)
    assert first.moves == 1 and second.moves == 0


def test_preparation_miss_blocks_all_motion_and_retains_bounded_cause() -> None:
    """Resolve all selected endpoints before Gym, preserving the real miss and source."""
    first, second = NavigationAction(), NavigationAction(_miss())
    with pytest.raises(NavigationPreparationFailure) as caught:
        prepare_navigation_actions(
            {"agent_0_oracle_nav_action": first, "agent_1_oracle_nav_action": second},
            _decoded(),
            agent_ids=(0, 1),
        )
    assert first.moves == second.moves == 0
    assert first.preparations == second.preparations == 1
    assert not first.pending and not second.pending
    evidence = caught.value.as_dict()
    assert evidence["agent_id"] == 1 and evidence["reason_code"] == "bounded_search_miss"
    assert evidence["search"]["path_queries"] == 2
    assert evidence["proves_physical_impossibility"] is False
    assert isinstance(caught.value.__cause__, GoalRegionSearchMiss)


@pytest.mark.parametrize("agent_ids", [(0,), (0, 1), (0, 1, 2, 3)])
def test_prepared_success_dispatches_each_selected_action_once(
    agent_ids: tuple[int, ...],
) -> None:
    """The boundary follows configured endpoints and never hardcodes a dual-robot task."""
    actions = {f"agent_{agent}_oracle_nav_action": NavigationAction() for agent in agent_ids}
    decoded = _decoded(agent_ids)
    discard = prepare_navigation_actions(actions, decoded, agent_ids=agent_ids)
    assert all(action.moves == 0 for action in actions.values())
    for action in actions.values():
        action.step(**decoded["action_args"])
    discard()
    assert all(action.moves == action.preparations == 1 for action in actions.values())
    assert all(action.arguments == decoded["action_args"] for action in actions.values())


def test_unexpected_error_and_cleanup_failure_keep_the_original_exception() -> None:
    """A programming failure is not relabeled as unreachable or masked by cleanup."""
    original = ValueError("implementation sentinel")
    first, second = NavigationAction(cleanup_error=True), NavigationAction(original)
    with pytest.raises(ValueError) as caught:
        prepare_navigation_actions(
            {"agent_0_oracle_nav_action": first, "agent_1_oracle_nav_action": second},
            _decoded(),
            agent_ids=(0, 1),
        )
    assert caught.value is original
    assert not first.pending and not second.pending


@pytest.mark.parametrize(
    "decoded",
    [
        {},
        {"action": ["duplicate", "duplicate"], "action_args": {}},
        {"action": "missing", "action_args": {}},
    ],
)
def test_malformed_selected_action_mapping_fails_before_any_preparation(
    decoded: dict[str, Any],
) -> None:
    """Invalid vendor layout cannot silently bypass preparation or begin motion."""
    action = NavigationAction()
    with pytest.raises(IntegrationError):
        prepare_navigation_actions({"agent_0_oracle_nav_action": action}, decoded, agent_ids=(0,))
    assert action.preparations == action.moves == 0
