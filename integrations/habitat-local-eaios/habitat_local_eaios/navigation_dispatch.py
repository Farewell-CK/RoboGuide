"""Rearm the original Oracle command latch for an explicitly admitted fresh call.

The original EMOS action suppresses a repeated completed target before it can
compute arrival again. A new acquisition command must not inherit that latch.
Relocation also invalidates only a selected cached point whose original entity
has moved. Sampling remains in the original following skill; world state,
arrival tests, skill budgets and official predicates retain their owners.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from .model import IntegrationError
from .stage2_contract import Stage2ExecutionContract

if TYPE_CHECKING:
    from .view_recovery_binding import ObservationViewpointRecovery

_LOG = logging.getLogger(__name__)
RELOCATION_NAVIGATION_SCHEMA = "roboguide.habitat-local-how-profile/v0.20"
_MAX_CALLS = 65_536


def relocation_navigation_declaration() -> dict[str, Any]:
    """Disclose fresh relocation calls and selected-cache invalidation without changing goals."""
    return {
        "profile": "fresh-relocation-navigation-command/v0.1",
        "operation": "object.relocate@v1",
        "boundary": "after-guarded-original-tool-mapping-before-skill-entry",
        "reset_fields": ["prev_nav_done", "skill_done"],
        "target_cache_policy": "invalidate-selected-entry-only-when-entity-position-changes",
        "call_identity": "agent-attempt-invocation-tool-call-sequence",
        "max_calls_per_attempt": _MAX_CALLS,
        "arrival_authority": "original-oracle-action-recomputation",
        "official_success_authority": "habitat-pddl",
    }


def unwrap_relocation_navigation_profile(
    profile: dict[str, Any], *, enabled: bool, live: bool
) -> dict[str, Any]:
    """Validate the closed new envelope while leaving earlier Local How profiles unchanged."""
    if profile.get("schema_version") != RELOCATION_NAVIGATION_SCHEMA:
        return profile
    declaration = profile.get("relocation_navigation_dispatch")
    expected = relocation_navigation_declaration()
    base = profile.get("base_profile")
    if (
        not enabled
        or not live
        or set(profile) != {"schema_version", "base_profile", "relocation_navigation_dispatch"}
        or declaration != expected
        or not isinstance(declaration, dict)
        or any(type(declaration[key]) is not type(value) for key, value in expected.items())
        or not isinstance(base, dict)
        or base.get("schema_version") == RELOCATION_NAVIGATION_SCHEMA
    ):
        raise IntegrationError("relocation navigation Local How envelope is invalid")
    return base


def _position(value: Any) -> tuple[float, float, float]:
    """Copy a finite original entity position without invoking sensors, navigation or RNG."""
    try:
        if isinstance(value, (str, bytes, dict)) or len(value) != 3:
            raise ValueError("position is not three dimensional")
        result = tuple(float(component) for component in value)
        if not all(math.isfinite(component) for component in result):
            raise ValueError("position is not finite")
        return result[0], result[1], result[2]
    except (TypeError, ValueError, OverflowError) as error:
        raise IntegrationError("navigation target position is unavailable") from error


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
        """Retain one exact supported binding without resetting an action or world."""
        if (
            contract.operation not in {"observation.acquire@v1", "object.relocate@v1"}
            or not contract.invocation_digest
            or (view_recovery is not None and contract.is_relocation)
        ):
            raise IntegrationError("navigation lifecycle requires an exact supported contract")
        self._environment = environment
        self._agent = agent
        self._contract = contract
        self._action = inspect_navigation_dispatch(environment, agent)
        self._record = record
        self._sequence = 0
        self._view_recovery = view_recovery
        self._calls: set[str] = set()
        self._closed = False

    def owns(self, agent_name: str, contract: Stage2ExecutionContract) -> bool:
        """Require the exact endpoint and canonical attempt before installing feedback."""
        return (
            not self._closed and agent_name == f"agent_{self._agent}" and contract == self._contract
        )

    def _stale_cache(self, index: int) -> bool:
        """Check only a selected relocation cache against its current authoritative position."""
        cache = self._action._targets
        if not self._contract.is_relocation or index not in cache:
            return False
        try:
            cached = cache[index]
            if not isinstance(cached, (tuple, list)) or len(cached) != 2:
                raise IntegrationError("navigation target cache is malformed")
            _position(cached[0])
            previous = _position(cached[1])
            current = _position(
                self._environment.task.pddl_problem.sim_info.get_entity_pos(
                    self._action._poss_entities[index]
                )
            )
        except IntegrationError:
            raise
        except Exception as error:
            raise IntegrationError("navigation target position is unavailable") from error
        return previous != current

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
        target = (
            raw.get("arguments", {}).get("target_obj")
            if isinstance(raw.get("arguments"), Mapping)
            else None
        )
        targets = (
            {self._contract.expected_object, self._contract.expected_destination}
            if self._contract.is_relocation
            else {self._contract.expected_destination}
        )
        if (
            self._closed
            or not isinstance(target, str)
            or target not in targets
            or raw != {"name": "nav_to_obj", "arguments": {"target_obj": target}}
            or mapped
            != {"name": "nav_to_obj", "arguments": {"target_obj": target, "robot": agent_name}}
            or type(sequence) is not int
            or sequence <= self._sequence
            or not isinstance(tool_call_id, str)
            or not tool_call_id
            or (self._contract.is_relocation and tool_call_id in self._calls)
            or (self._contract.is_relocation and len(self._calls) >= _MAX_CALLS)
            or inspect_navigation_dispatch(self._environment, self._agent) is not self._action
            or sum(entity.name == target for entity in self._action._poss_entities) != 1
        ):
            raise IntegrationError("navigation dispatch is malformed or detached")
        index = next(
            i for i, entity in enumerate(self._action._poss_entities) if entity.name == target
        )
        stale = self._stale_cache(index)
        if self._view_recovery is not None:
            self._view_recovery.dispatch(tool_call_id, sequence, stationary_scan)
        prior = {key: vars(self._action)[key] for key in ("prev_nav_done", "skill_done")}
        if stale:
            del self._action._targets[index]
        self._action.prev_nav_done = False
        self._action.skill_done = False
        self._sequence = sequence
        if self._contract.is_relocation:
            self._calls.add(tool_call_id)
        try:
            self._record(
                {
                    "event": "fresh-navigation-command",
                    "navigation_dispatch": relocation_navigation_declaration()
                    if self._contract.is_relocation
                    else navigation_dispatch_declaration(),
                    "agent_name": agent_name,
                    "contract": self._contract.as_dict(),
                    "tool_call_id": tool_call_id,
                    "action_sequence": sequence,
                    "raw_selected_action": dict(raw),
                    "local_dispatch": mapped,
                    "previous_command_flags": prior,
                    "new_command_flags": {"prev_nav_done": False, "skill_done": False},
                    "selected_target_cache_invalidated": stale,
                    "local_skill_completed": None,
                    "benchmark_goal_satisfied": None,
                }
            )
        except Exception:  # noqa: BLE001 - archival loss cannot fabricate an execution result
            _LOG.exception("fresh navigation command evidence unavailable")

    def close(self) -> None:
        """Remove only this attempt's optional viewpoint hooks on every segment exit."""
        self._closed = True
        if self._view_recovery is not None:
            self._view_recovery.close()

    @property
    def supports_viewpoint_recovery(self) -> bool:
        """Expose loaded optional implementation, never actual geometric feasibility."""
        return self._view_recovery is not None
