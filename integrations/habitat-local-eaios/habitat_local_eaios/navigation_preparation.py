"""Prepare opt-in local navigation actions before a shared physical step.

This boundary owns no planning, executor selection, resource or recovery decision.
Only an actual selected navigation command is prepared; a miss is bounded local
evidence, never a proof of global physical impossibility or official goal truth.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from .goal_region_navigation import GoalRegionResolutionError, GoalRegionSearchMiss
from .model import IntegrationError

NAVIGATION_PREPARATION_PROFILE = "joint-navigation-preparation/v0.1"
_LOG = logging.getLogger(__name__)


class NavigationPreparationFailure(IntegrationError):
    """Attribute an expected local preparation failure without admitting motion."""

    def __init__(
        self, action_name: str, agent_id: int | None, error: GoalRegionResolutionError
    ) -> None:
        """Retain the original typed cause and bounded search counters."""
        self.action_name = action_name
        self.agent_id = agent_id
        self.reason_code = (
            "bounded_search_miss"
            if isinstance(error, GoalRegionSearchMiss)
            else "navigation_evidence_unavailable"
        )
        self.search = dict(error.search) if isinstance(error, GoalRegionSearchMiss) else None
        self.original_error_type = type(error).__name__
        self.original_message = str(error)
        super().__init__(
            f"local navigation preparation failed at {action_name}: {self.reason_code}: {error}"
        )

    def as_dict(self) -> dict[str, Any]:
        """Expose JSON-safe attribution without claiming route impossibility."""
        return {
            "action_name": self.action_name,
            "agent_id": self.agent_id,
            "reason_code": self.reason_code,
            "error_type": self.original_error_type,
            "error": self.original_message,
            "search": self.search,
            "proves_physical_impossibility": False,
        }


def prepare_navigation_actions(
    actions: Mapping[str, Any],
    decoded: Mapping[str, Any],
    *,
    agent_ids: tuple[int, ...],
) -> Callable[[], None]:
    """Prepare every selected opt-in action, or discard all unconsumed commands.

    The decoded action comes from the same vendor conversion used by Gym. Order
    is preserved, unknown action layouts fail closed, and only declared hooks
    are called. Unexpected implementation exceptions retain their original type.
    Cleanup never replaces the primary error or authorizes a simulator step.
    """
    names = decoded.get("action")
    if isinstance(names, str):
        names = (names,)
    args = decoded.get("action_args")
    if (
        not isinstance(names, (tuple, list))
        or len(names) > 128
        or not all(isinstance(name, str) for name in names)
        or len(names) != len(set(names))
        or not isinstance(args, Mapping)
    ):
        raise IntegrationError("navigation preparation requires the vendor action mapping")
    pending: list[Any] = []

    def discard() -> None:
        """Remove prepared commands only; do not reset meshes, targets or the world."""
        for action in pending:
            try:
                action.discard_prepared_navigation()
            except Exception:  # noqa: BLE001 - cleanup cannot mask the primary failure
                _LOG.exception("navigation command cleanup failed")

    try:
        for name in names:
            if name not in actions:
                raise IntegrationError("navigation preparation references an unknown task action")
            action = actions[name]
            prepare = getattr(action, "prepare_navigation_step", None)
            if not callable(prepare):
                continue
            if not callable(getattr(action, "discard_prepared_navigation", None)):
                raise IntegrationError("navigation preparation action has no command cleanup")
            agent_id = next(
                (value for value in agent_ids if name == f"agent_{value}_oracle_nav_action"),
                None,
            )
            if agent_id is None:
                raise IntegrationError(
                    "navigation preparation hook is outside configured endpoints"
                )
            pending.append(action)
            try:
                prepare(**args)
            except GoalRegionResolutionError as error:
                raise NavigationPreparationFailure(name, agent_id, error) from error
    except BaseException:
        discard()
        raise
    return discard
