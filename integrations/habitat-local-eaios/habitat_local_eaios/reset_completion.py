"""Bind an admitted arm reset's completion check to its actual controller target.

The original controller samples its reference on its first physical step, whereas
the original HRL skill samples on its first reset command. These need not be the
same pose. Only the skill's reference is temporarily substituted during its own
original completion check; actuator commands, tolerance and budgets stay original.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from numbers import Real
from typing import Any

from .model import IntegrationError
from .stage2_contract import Stage2ExecutionContract

_LOG = logging.getLogger(__name__)
_MISSING = object()


def reset_completion_declaration() -> dict[str, Any]:
    """Disclose a command-scoped reference binding without claiming physical success."""
    return {
        "profile": "controller-bound-arm-reset/v0.1",
        "operation": "object.relocate@v1",
        "target_source": "own-original-reset-controller._init_joint_pos",
        "completion": "original-skill-check-with-controller-reference",
        "uninitialized_target": "wait-for-original-controller-step",
        "tolerance_and_budget": "unchanged-original-skill",
        "call_identity": "agent-attempt-invocation-tool-call-sequence",
        "actuator_commands_changed": False,
        "official_success_authority": "habitat-pddl",
    }


def _original_types() -> tuple[type[Any], type[Any], type[Any]]:
    """Resolve only the already selected original controller and skill interfaces."""
    from habitat.tasks.rearrange.actions.actions import (  # type: ignore[import-not-found]
        ResetArmAction,
    )
    from habitat.tasks.rearrange.actions.reset_action import (  # type: ignore[import-not-found]
        OracleResetArmAction,
    )
    from habitat_baselines.rl.hrl.skills.reset import (  # type: ignore[import-not-found]
        ResetArmSkill,
    )

    return ResetArmAction, OracleResetArmAction, ResetArmSkill


def _joints(value: Any) -> tuple[float, ...]:
    """Read a finite bounded joint vector without querying sensors or executing actions."""
    try:
        if isinstance(value, (str, bytes, dict)) or not 1 <= len(value) <= 64:
            raise ValueError("invalid joint vector")
        if any(isinstance(item, bool) or not isinstance(item, Real) for item in value):
            raise ValueError("joint vector is not numerical")
        result = tuple(float(item) for item in value)
        if not all(math.isfinite(item) for item in result):
            raise ValueError("nonfinite joint vector")
        return result
    except (TypeError, ValueError, OverflowError) as error:
        raise IntegrationError("original reset controller target is unavailable") from error


def inspect_reset_completion(environment: Any, policy: Any, agent: int) -> tuple[Any, Any, Any]:
    """Refuse unsupported interfaces before advertising the new local reset contract."""
    parent_type, action_type, skill_type = _original_types()
    parent = environment.task.actions.get(f"agent_{agent}_arm_reset_action")
    action = getattr(parent, "arm_ctrlr", None)
    skill = policy._skills.get(policy._name_to_idx.get("reset_arm"))
    if (
        action is None
        or type(parent) is not parent_type
        or type(action) is not action_type
        or type(skill) is not skill_type
        or type(getattr(action, "is_init", None)) is not bool
        or not callable(getattr(skill, "_is_skill_done", None))
        or getattr(environment, "current_episode", None) is None
    ):
        raise IntegrationError("controller-bound reset requires exact original interfaces")
    if action.is_init:
        _joints(action._init_joint_pos)
    return parent, action, skill


class ResetCompletionBinding:
    """Keep one endpoint's actual reset reference inside its immutable execution attempt."""

    def __init__(
        self,
        environment: Any,
        policy: Any,
        agent: int,
        contract: Stage2ExecutionContract,
        record: Callable[[dict[str, Any]], None],
    ) -> None:
        """Validate ownership without sampling a replacement home pose or moving the arm."""
        if (
            not contract.is_relocation
            or contract.attempt_id is None
            or not contract.invocation_digest
            or type(agent) is not int
            or agent < 0
        ):
            raise IntegrationError("controller-bound reset needs an exact relocation attempt")
        self.parent, self.action, self.skill = inspect_reset_completion(environment, policy, agent)
        self._environment, self._episode = environment, environment.current_episode
        self._agent, self._contract, self._record = agent, contract, record
        self._target: tuple[float, ...] | None = None
        self._call: tuple[str, int] | None = None
        self._seen: set[str] = set()
        self._sequence = 0
        self._closed = False
        self._reported: str | None = None
        self._previous: Any = _MISSING
        self._installed = False

    def owns(self, name: str, contract: Stage2ExecutionContract) -> bool:
        """Keep reference qualification inside the exact original endpoint and attempt."""
        return not self._closed and name == f"agent_{self._agent}" and contract == self._contract

    def _current_target(self) -> tuple[float, ...] | None:
        """Reject changed worlds, controllers and already frozen reference targets."""
        if (
            self._closed
            or self._environment.current_episode is not self._episode
            or self._environment.task.actions.get(f"agent_{self._agent}_arm_reset_action")
            is not self.parent
            or self.parent.arm_ctrlr is not self.action
            or type(self.action.is_init) is not bool
        ):
            raise IntegrationError("reset reference no longer belongs to its admitted world")
        if not self.action.is_init:
            if self._target is not None:
                raise IntegrationError("initialized original reset target disappeared")
            return None
        target = _joints(self.action._init_joint_pos)
        if self._target is not None and self._target != target:
            raise IntegrationError("original reset controller target changed during the attempt")
        self._target = target
        return target

    def _emit(self, event: str, **fields: Any) -> None:
        """Retain qualified reference evidence without letting storage replace execution errors."""
        try:
            self._record(
                dict(
                    event=event,
                    profile="controller-bound-arm-reset/v0.1",
                    contract=self._contract.as_dict(),
                    agent_name=f"agent_{self._agent}",
                    tool_call_id=self._call[0] if self._call else None,
                    action_sequence=self._call[1] if self._call else None,
                    **fields,
                )
            )
        except Exception:  # noqa: BLE001 - evidence failure cannot alter arm control
            _LOG.exception("reset reference evidence unavailable")

    def bind_call(self, agent_name: str, action: Any, identity: str, sequence: int) -> None:
        """Admit only a fresh own reset after Guard and the unchanged original tool mapping."""
        self._current_target()
        if (
            not self._installed
            or agent_name != f"agent_{self._agent}"
            or action != {"name": "reset_arm", "arguments": {}}
            or not isinstance(identity, str)
            or not 1 <= len(identity) <= 1024
            or identity in self._seen
            or len(self._seen) >= 65_536
            or type(sequence) is not int
            or sequence <= self._sequence
        ):
            raise IntegrationError("reset command identity or original mapping is invalid")
        self._seen.add(identity)
        self._sequence = sequence
        self._call, self._reported = (identity, sequence), None
        self._emit("reset-reference-command-bound")

    def install(self) -> None:
        """Wrap only the existing completion check, retaining the original tensor and result."""
        if self._closed or self._installed:
            raise IntegrationError("reset completion binding is closed or already installed")
        original = self.skill._is_skill_done
        self._previous = vars(self.skill).get("_is_skill_done", _MISSING)

        def checked(*args: Any, **kwargs: Any) -> Any:
            """Use the actual controller reference only for this admitted reset command."""
            if self._call is None:
                return original(*args, **kwargs)
            target = self._current_target()
            if target is None:
                result = original(*args, **kwargs)
                if self._reported != "unavailable":
                    self._emit("reset-reference-unavailable", reason="controller-not-initialized")
                    self._reported = "unavailable"
                return result & False
            previous = self.skill._init_joint_pos
            if tuple(getattr(previous, "shape", ())) != (1, len(target)):
                raise IntegrationError("reset skill target does not match the original joint space")
            # Keep the controller's actual numerical precision, rather than casting
            # its target into the skill observation array's potentially different dtype.
            raw_target = self.action._init_joint_pos
            if not callable(getattr(raw_target, "copy", None)) or not callable(
                getattr(raw_target, "reshape", None)
            ):
                raise IntegrationError("original reset target has no numerical copy interface")
            aligned = raw_target.copy().reshape((1, len(target)))
            self.skill._init_joint_pos = aligned
            try:
                result = original(*args, **kwargs)
            finally:
                self.skill._init_joint_pos = previous
            if self._reported != "aligned":
                self._emit(
                    "reset-reference-aligned",
                    controller_target=list(target),
                    original_skill_reference=list(_joints(previous[0])),
                    skill_budget_changed=False,
                )
                self._reported = "aligned"
            return result

        self.skill._is_skill_done = checked
        self._installed = True

    def close(self) -> None:
        """Remove this instance hook while retaining actual controller and skill bookkeeping."""
        if self._installed:
            if self._previous is _MISSING:
                vars(self.skill).pop("_is_skill_done", None)
            else:
                self.skill._is_skill_done = self._previous
            self._installed = False
        self._closed = True
