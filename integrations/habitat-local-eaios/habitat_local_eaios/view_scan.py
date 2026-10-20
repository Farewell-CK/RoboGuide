"""Explicit bounded observation scans using already deployed base-velocity actions.

Only an admitted model-selected scan emits yaw through the original joint
action vector. No simulator method is called here. Ground and flight endpoints
must expose their actual original control layout; missing support fails closed.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from numbers import Real
from types import SimpleNamespace
from typing import Any

from .model import IntegrationError
from .observation_control import ObservationAcquisitionControl
from .view_motion import ScanRotationObservation

VIEW_SCAN_TOOL = "scan_for_object"
VIEW_SCAN_PROFILE = "bounded-base-yaw-view-search/v0.1"
MAX_SCAN_STEPS = 32
MAX_YAW_RADIANS = math.pi / 2
MAX_ANGULAR_SPEED = 1.5
_LOG = logging.getLogger(__name__)


def view_scan_declaration() -> dict[str, Any]:
    """Publish actual motion bounds without promising a visible or preserved effect."""
    return {
        "profile": VIEW_SCAN_PROFILE,
        "tool": VIEW_SCAN_TOOL,
        "operation": "observation.acquire@v1",
        "max_simulator_steps_per_call": MAX_SCAN_STEPS,
        "max_commanded_yaw_radians_per_call": MAX_YAW_RADIANS,
        "max_angular_speed_radians_per_second": MAX_ANGULAR_SPEED,
        "translation_command": "zero",
        "control_source": "loaded-original-base-velocity-action",
        "camera_teleportation": False,
        "view_search_completion_is_detection": False,
        "original_acquisition_budget_renewed": False,
        "official_success_authority": "habitat-pddl",
    }


def _width(space: Any) -> int:
    """Count only the actual continuous action layout, preserving its declared order."""
    children = getattr(space, "spaces", None)
    if isinstance(children, Mapping):
        return sum(_width(child) for child in children.values())
    shape = getattr(space, "shape", None)
    if (
        not isinstance(shape, tuple)
        or not shape
        or any(type(n) is not int or n <= 0 for n in shape)
    ):
        raise IntegrationError("view scan requires an explicit continuous action layout")
    return math.prod(shape)


def _original_vector_width(space: Any) -> int:
    """Use the deployed HRL codec for complete vectors, including original EmptySpace slots."""
    from habitat_baselines.utils.common import get_num_actions  # type: ignore[import-not-found]

    width = get_num_actions(space)
    if type(width) is not int or not 0 < width <= 16384:
        raise IntegrationError("original action vector has no bounded explicit width")
    return width


def _original_base_types() -> tuple[type[Any], ...]:
    """Resolve exact loaded vendor controls; a same-named substitute does not establish support."""
    from habitat.tasks.rearrange.actions.actions import (  # type: ignore[import-not-found]
        BaseVelAction,
        BaseVelNonCylinderAction,
    )
    from habitat.tasks.rearrange.actions.drone_actions import (  # type: ignore[import-not-found]
        DroneBaseVelAction,
    )

    return (BaseVelAction, BaseVelNonCylinderAction, DroneBaseVelAction)


@dataclass(frozen=True)
class ViewScanSpec:
    """Freeze the actual own-endpoint control object, layout, rate and original clock."""

    action: Any
    action_space: Any
    agent: int
    yaw_index: int
    width: int
    angular_scale: float
    ctrl_freq: float
    normalized_yaw: float

    def evidence(self) -> dict[str, Any]:
        """Expose public readiness facts, never hardware certification or visibility proof."""
        return {
            "agent_id": self.agent,
            "action_class": f"{type(self.action).__module__}.{type(self.action).__name__}",
            "action_vector_width": self.width,
            "yaw_index": self.yaw_index,
            "angular_scale": self.angular_scale,
            "controller_frequency": self.ctrl_freq,
            "commanded_angular_speed": self.normalized_yaw * self.angular_scale,
            "profile": VIEW_SCAN_PROFILE,
        }


def inspect_view_scan(environment: Any, policy: Any, agent: int) -> ViewScanSpec:
    """Validate the original scalar yaw interface before advertising the local scan tool."""
    try:
        actions = environment.task.actions
        action = actions.get(f"agent_{agent}_base_velocity")
        if action is None and agent == 0 and len(environment.sim.agents_mgr) == 1:
            action = actions.get("base_velocity")
        name = type(action).__name__
        if type(action) not in _original_base_types():
            raise ValueError("unsupported original base controller")
        prefix = f"agent_{agent}_" if action._multi_agent else ""
        if action._agent_index != agent or action._action_arg_prefix != prefix:
            raise ValueError("base controller belongs to a different endpoint")
        space = policy._high_level_policy._action_space
        spaces = space.spaces
        base = spaces["base_velocity"]
        children = base.spaces
        if name == "DroneBaseVelAction":
            expected = {f"{prefix}lin_vel": 3, f"{prefix}ang_vel": 1}
            yaw_key, yaw_local = f"{prefix}ang_vel", 0
        else:
            lateral = bool(getattr(action, "_enable_lateral_move", False))
            expected = {f"{prefix}base_vel": 3 if lateral else 2}
            yaw_key, yaw_local = f"{prefix}base_vel", 2 if lateral else 1
        # The original multi-agent projection strips the outer action name,
        # retaining each original action's agent-prefixed argument space.
        if {key: _width(child) for key, child in children.items()} != expected:
            raise ValueError("original base layout differs from the supported interface")
        offset = 0
        for key, child in spaces.items():
            if key == "base_velocity":
                break
            offset += _original_vector_width(child)
        for key, child in children.items():
            if key == yaw_key:
                break
            offset += _width(child)
        width, yaw_index = _original_vector_width(space), offset + yaw_local
        raw_scale, raw_hz = action._ang_speed, environment.sim.ctrl_freq
        if any(
            isinstance(value, bool) or not isinstance(value, Real) for value in (raw_scale, raw_hz)
        ):
            raise ValueError("original control scale is not numeric")
        scale, hz = float(raw_scale), float(raw_hz)
        if not all(math.isfinite(value) and value > 0 for value in (scale, hz)):
            raise ValueError("original control rate is unavailable")
        if not 0 <= yaw_index < width <= 16384:
            raise ValueError("yaw control index is outside the original action vector")
        velocity = min(MAX_ANGULAR_SPEED, MAX_YAW_RADIANS * hz / MAX_SCAN_STEPS, scale)
        return ViewScanSpec(action, space, agent, yaw_index, width, scale, hz, velocity / scale)
    except Exception as error:
        raise IntegrationError(
            "original endpoint has no supported bounded view-scan control"
        ) from error


def _skill_factory(spec: ViewScanSpec, control: ObservationAcquisitionControl, policy: Any) -> Any:
    """Construct a separate local skill on the original wait/budget/HRL machinery."""
    from habitat_baselines.rl.hrl.skills.wait import (  # type: ignore[import-not-found]
        WaitSkillPolicy,
    )

    original_wait = policy._skills[policy._name_to_idx["wait"]]
    if type(original_wait) is not WaitSkillPolicy:
        raise IntegrationError("view scan requires the exact original wait machinery")
    config = SimpleNamespace(
        ignore_grip=True,
        apply_postconds=False,
        force_end_on_timeout=False,
        max_skill_steps=MAX_SCAN_STEPS,
        skill_name=VIEW_SCAN_TOOL,
    )

    class ViewScanSkill(WaitSkillPolicy):  # type: ignore[misc]
        """Use original HRL entry/termination and emit only bounded original base yaw."""

        def _parse_skill_arg(self, skill_name: str, skill_arg: Any) -> Any:
            """Bind the exact requested entity and the remaining original acquisition budget."""
            if skill_name != VIEW_SCAN_TOOL or skill_arg != {
                "target_obj": control.invocation.object_ref
            }:
                raise IntegrationError("view scan skill received a different semantic target")
            remaining = control.snapshot()["remaining_simulator_steps"]
            if remaining <= 0:
                raise IntegrationError("view scan cannot renew an exhausted acquisition")
            self._wait_time = min(MAX_SCAN_STEPS, remaining)
            return skill_arg

        def _internal_act(self, *args: Any, **kwargs: Any) -> Any:
            """Generate one vector; the existing actor and Gym retain all physical stepping."""
            result = super()._internal_act(*args, **kwargs)
            if result.actions.shape[-1] != spec.width:
                raise IntegrationError("view scan action layout changed after readiness")
            result.actions[:, self._wait_ac_idx] = 0.0
            result.actions[:, spec.yaw_index] = spec.normalized_yaw
            return result

    return ViewScanSkill(config, spec.action_space, 1)


class ViewScanBinding:
    """Temporarily install one own-attempt skill and expose only its exact semantic tool."""

    def __init__(
        self,
        environment: Any,
        policy: Any,
        agent: int,
        control: ObservationAcquisitionControl,
        record: Callable[[dict[str, Any]], None],
    ) -> None:
        """Prepare all interfaces before adding a new local skill; peers are never edited."""
        self.spec = inspect_view_scan(environment, policy, agent)
        self.control, self.policy, self.environment, self.record = (
            control,
            policy,
            environment,
            record,
        )
        if control.agent_id != agent:
            raise IntegrationError("view scan belongs to a different acquisition endpoint")
        maps = (
            policy._name_to_idx,
            policy._idx_to_name,
            policy._high_level_policy._skill_name_to_idx,
        )
        if (
            any(not isinstance(mapping, dict) for mapping in maps)
            or VIEW_SCAN_TOOL in maps[0]
            or VIEW_SCAN_TOOL in maps[2]
            or not isinstance(policy._skills, dict)
        ):
            raise IntegrationError("view scan skill registry is unavailable or already bound")
        self.index = max(policy._skills, default=-1) + 1
        if self.index in policy._idx_to_name:
            raise IntegrationError("view scan skill index conflicts with the original registry")
        skill = _skill_factory(self.spec, control, policy)
        policy._skills[self.index] = skill
        policy._name_to_idx[VIEW_SCAN_TOOL] = self.index
        policy._idx_to_name[self.index] = VIEW_SCAN_TOOL
        policy._high_level_policy._skill_name_to_idx[VIEW_SCAN_TOOL] = self.index
        self.skill = skill
        self._last_sequence = 0
        self._motion: ScanRotationObservation | None = None

    def tool(self) -> dict[str, Any]:
        """Describe the real limited scan before selection, without changing the task objective."""
        steps = min(MAX_SCAN_STEPS, self.control.snapshot()["remaining_simulator_steps"])
        return {
            "name": VIEW_SCAN_TOOL,
            "description": (
                "Observe the exact committed object by a bounded in-place yaw sweep "
                f"for at most {steps} "
                "simulator steps and at most 90 degrees using the original base controller. "
                "No translation, camera gimbal or object manipulation is commanded. "
                "This can change the observer's heading and view, cannot remove every occlusion "
                "or correct vertical framing. Detection and preservation of prior views "
                "are not guaranteed. "
                "The actual orientation feedback distinguishes a completed timer from rotation. "
                "After the scan, use the current detection feedback to decide the next action."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target_obj": {"type": "string", "enum": [self.control.invocation.object_ref]}
                },
                "required": ["target_obj"],
                "additionalProperties": False,
            },
        }

    def admit(self, raw: Mapping[str, Any], mapped: Any, call_id: str, sequence: int) -> None:
        """Validate the unchanged original CrabAgent passthrough before any scan skill entry."""
        if (
            raw
            != {
                "name": VIEW_SCAN_TOOL,
                "arguments": {"target_obj": self.control.invocation.object_ref},
            }
            or mapped != raw
            or inspect_view_scan(self.environment, self.policy, self.spec.agent) != self.spec
            or self.spec.action.cur_grasp_mgr.snap_idx is not None
            or self.control.snapshot()["remaining_simulator_steps"] <= 0
            or type(sequence) is not int
            or sequence <= self._last_sequence
            or not isinstance(call_id, str)
            or not 1 <= len(call_id) <= 1024
        ):
            raise IntegrationError(
                "view scan dispatch is detached, exhausted or would transport an object"
            )
        self._last_sequence = sequence
        self._motion = ScanRotationObservation(self.spec.action, call_id, sequence)
        try:
            self.record(
                {
                    "event": "view-scan-dispatch-bound",
                    "view_search": view_scan_declaration(),
                    "invocation_digest": "sha256:" + self.control.invocation.request_key(),
                    "agent_name": f"agent_{self.spec.agent}",
                    "tool_call_id": call_id,
                    "action_sequence": sequence,
                    "raw_selected_action": dict(raw),
                    "local_dispatch": mapped,
                    "original_control": self.spec.evidence(),
                    "condition_holds": self.control.snapshot()["sample"]["condition_holds"],
                    "benchmark_goal_satisfied": None,
                }
            )
        except Exception:  # noqa: BLE001 - evidence storage does not authorize or block physics
            _LOG.exception("view scan dispatch evidence unavailable")

    def observe_physical_step(self) -> None:
        """Sample only an existing step of this exact own skill; peers and idle are excluded."""
        if self._motion is None:
            return
        try:
            if self.policy._cur_skills[0] != self.index:
                return
        except Exception:  # noqa: BLE001 - unexposed skill ownership remains unknown
            return
        self._motion.observe()

    def motion_feedback(self, call_id: str, sequence: int) -> dict[str, Any] | None:
        """Return only evidence for the exact original pending call, never a prior scan."""
        if (
            self._motion is None
            or self._motion.call_id != call_id
            or self._motion.sequence != sequence
        ):
            return None
        return self._motion.snapshot()

    def close(self) -> None:
        """Remove only this attempt's exact skill registration after its feedback hooks close."""
        if self.policy._skills.get(self.index) is self.skill:
            del self.policy._skills[self.index]
            self.policy._idx_to_name.pop(self.index, None)
        for mapping in (
            self.policy._name_to_idx,
            self.policy._high_level_policy._skill_name_to_idx,
        ):
            if mapping.get(VIEW_SCAN_TOOL) == self.index:
                del mapping[VIEW_SCAN_TOOL]
