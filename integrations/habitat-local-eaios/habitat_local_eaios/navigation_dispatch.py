"""Rearm the original Oracle command latch for an explicitly admitted fresh call.

The original EMOS action suppresses a repeated completed target before it can
compute arrival again. A new acquisition command must not inherit that latch.
Only the command-local flags change; target sampling, world state, arrival
tests, skill budgets and official predicates remain with their original owners.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from .model import IntegrationError
from .stage2_contract import Stage2ExecutionContract

if TYPE_CHECKING:
    from .view_recovery_binding import ObservationViewpointRecovery

_LOG = logging.getLogger(__name__)


def navigation_dispatch_declaration() -> dict[str, Any]:
    """Disclose the scoped command lifecycle separately from physics and sensing."""
    return {
        "profile": "fresh-acquisition-navigation-command/v0.1",
        "operation": "observation.acquire@v1",
        "boundary": "after-guarded-original-tool-mapping-before-skill-entry",
        "reset_fields": ["prev_nav_done", "skill_done"],
        "target_cache_policy": "preserve-original-targets-and-pathfinder",
        "arrival_authority": "original-oracle-action-recomputation",
        "official_success_authority": "habitat-pddl",
    }


def _original_action_type() -> type[Any]:
    """Resolve the already loaded deployment interface without constructing an environment."""
    from habitat.tasks.rearrange.actions.habitat_mas_actions import (  # type: ignore[import-not-found]
        OracleNavDiffBaseAction,
    )

    return OracleNavDiffBaseAction  # type: ignore[no-any-return]


def inspect_navigation_dispatch(environment: Any, agent: int) -> Any:
    """Fail before readiness if the original command-local latch is unavailable."""
    actions = getattr(getattr(environment, "task", None), "actions", None)
    action = actions.get(f"agent_{agent}_oracle_nav_action") if isinstance(actions, dict) else None
    if not isinstance(action, _original_action_type()) or (
        type(vars(action).get("prev_nav_done")) is not bool
        or type(vars(action).get("skill_done")) is not bool
        or not isinstance(getattr(action, "_poss_entities", None), (list, tuple))
        or not isinstance(getattr(action, "_targets", None), dict)
    ):
        raise IntegrationError("acquisition navigation command lifecycle is unavailable")
    return action


class NavigationDispatchLifecycle:
    """Bind each fresh admitted tool call to only its own endpoint's original action."""

    def __init__(
        self,
        environment: Any,
        agent: int,
        contract: Stage2ExecutionContract,
        record: Callable[[dict[str, Any]], None],
        *,
        view_recovery: ObservationViewpointRecovery | None = None,
    ) -> None:
        """Retain one exact acquisition binding without resetting an action or world."""
        if contract.operation != "observation.acquire@v1" or not contract.invocation_digest:
            raise IntegrationError("navigation lifecycle requires an exact acquisition contract")
        self._environment = environment
        self._agent = agent
        self._contract = contract
        self._action = inspect_navigation_dispatch(environment, agent)
        self._record = record
        self._sequence = 0
        self._view_recovery = view_recovery

    def dispatch(
        self,
        mapped: Any,
        raw: Mapping[str, Any],
        tool_call_id: str,
        sequence: int,
        *,
        stationary_scan: tuple[str, int] | None = None,
    ) -> None:
        """Clear only completion latches after an exact raw-to-local navigation mapping.

        No sensor or completion fact is written. The following original action
        must recompute its result, even if the robot has moved since the earlier
        completion. A changed endpoint, malformed mapping or reused sequence
        fails before touching the latches.
        """
        agent_name = f"agent_{self._agent}"
        target = self._contract.expected_destination
        if (
            raw != {"name": "nav_to_obj", "arguments": {"target_obj": target}}
            or mapped
            != {"name": "nav_to_obj", "arguments": {"target_obj": target, "robot": agent_name}}
            or type(sequence) is not int
            or sequence <= self._sequence
            or not isinstance(tool_call_id, str)
            or not tool_call_id
            or inspect_navigation_dispatch(self._environment, self._agent) is not self._action
            or sum(entity.name == target for entity in self._action._poss_entities) != 1
        ):
            raise IntegrationError("acquisition navigation dispatch is malformed or detached")
        if self._view_recovery is not None:
            self._view_recovery.dispatch(tool_call_id, sequence, stationary_scan)
        prior = {key: vars(self._action)[key] for key in ("prev_nav_done", "skill_done")}
        self._action.prev_nav_done = False
        self._action.skill_done = False
        self._sequence = sequence
        try:
            self._record(
                {
                    "event": "fresh-navigation-command",
                    "navigation_dispatch": navigation_dispatch_declaration(),
                    "agent_name": agent_name,
                    "contract": self._contract.as_dict(),
                    "tool_call_id": tool_call_id,
                    "action_sequence": sequence,
                    "raw_selected_action": dict(raw),
                    "local_dispatch": mapped,
                    "previous_command_flags": prior,
                    "new_command_flags": {"prev_nav_done": False, "skill_done": False},
                    "local_skill_completed": None,
                    "benchmark_goal_satisfied": None,
                }
            )
        except Exception:  # noqa: BLE001 - archival loss cannot fabricate an execution result
            _LOG.exception("fresh navigation command evidence unavailable")

    def close(self) -> None:
        """Remove only this attempt's optional viewpoint hooks on every segment exit."""
        if self._view_recovery is not None:
            self._view_recovery.close()

    @property
    def supports_viewpoint_recovery(self) -> bool:
        """Expose loaded optional implementation, never actual geometric feasibility."""
        return self._view_recovery is not None
