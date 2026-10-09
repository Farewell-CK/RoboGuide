"""Bounded taps of original arm calls, never an IK solver or manipulation policy.

Only an actually dispatched pick/place action opens an observation scope. Each
original call receives its unchanged arguments and runs once. Returned arrays
are copied before caller mutation; lazy results are never consumed. All reads
are optional, and no FK, IK, grasp, simulator or RNG call is added.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from typing import Any

from .model import CanonicalInvocation, CanonicalRelocationInvocation

MANIPULATION_SCHEMA = "roboguide.local-manipulation-observation/v0.1"
MAX_CALLS_PER_STEP = 16
MAX_JOINTS = 64
_MISSING = object()


def _read(reader: Callable[[], Any]) -> Any:
    """Isolate each attribute read, preserving missing evidence as unavailable."""
    try:
        return reader()
    except Exception as error:  # noqa: BLE001 - observation cannot become execution authority
        return {"_status": "unavailable", "reason": type(error).__name__}


def _vector(value: Any, maximum: int = MAX_JOINTS) -> list[float]:
    """Copy bounded finite indexed arrays without consuming a generator."""
    length = len(value)
    if not 0 < length <= maximum:
        raise ValueError("vector exceeds observation bound")
    result = [float(value[index]) for index in range(length)]
    if not all(math.isfinite(item) for item in result):
        raise ValueError("vector is not finite")
    return result


def _text(value: Any) -> str:
    """Copy bounded actual identity without synthesizing missing identifiers."""
    if not isinstance(value, str) or not 0 < len(value) <= 1024:
        raise ValueError("identity unavailable or exceeds bound")
    return value


def _state(controller: Any) -> dict[str, Any]:
    """Read existing geometry and joints, never invoke solver or movement methods."""
    agent = controller.cur_articulated_agent
    manager = controller.cur_grasp_mgr
    return {
        "base_position_world": _read(lambda: _vector(agent.base_pos, 3)),
        "base_transform_columns": _read(
            lambda: [_vector(agent.base_transformation[index], 4) for index in range(4)]
        ),
        "end_effector_position_world": _read(lambda: _vector(agent.ee_transform().translation, 3)),
        "arm_joint_positions": _read(lambda: _vector(agent.arm_joint_pos)),
        "arm_motor_targets": _read(lambda: _vector(agent.arm_motor_pos)),
        "arm_joint_limits": _read(
            lambda: [_vector(agent.arm_joint_limits[index]) for index in range(2)]
        ),
        "motion_type": _read(lambda: _text(str(agent.sim_obj.motion_type))),
        "is_grasped": _read(lambda: _boolean(manager.is_grasped)),
        "snap_object_id": _read(lambda: _object_id(manager.snap_idx)),
        "ee_target_vendor_ik_frame": _read(lambda: _vector(controller.ee_target, 3)),
    }


def _boolean(value: Any) -> bool:
    """Accept an observed boolean without converting unknown to false or true."""
    if type(value) is not bool:
        raise ValueError("grasp flag unavailable")
    return value


def _object_id(value: Any) -> int | None:
    """Preserve a real absolute object ID or a genuine empty grasp."""
    if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
        raise ValueError("object ID unavailable")
    return value


def _invocation_identity(invocation: CanonicalRelocationInvocation) -> dict[str, Any]:
    """Read one supplied immutable attempt without retaining a loop closure."""
    return {
        "mission_id": _read(lambda: _text(invocation.mission_id)),
        "task_id": _read(lambda: _text(invocation.task_id)),
        "group_id": _read(lambda: _text(invocation.group_id)),
        "role_id": _read(lambda: _text(invocation.role_id)),
        "attempt_id": _read(lambda: _text(invocation.attempt_id)),
        "operation": _read(lambda: _text(invocation.operation)),
        "canonical_object": _read(lambda: _text(invocation.object_ref)),
        "canonical_destination": _read(lambda: _text(invocation.destination)),
    }


def _gripper_profile(parent: Any) -> dict[str, Any]:
    """Read fixed configured grasp facts without a live contact or object scan."""
    return {
        "class": _read(lambda: _text(type(parent.grip_ctrlr).__qualname__)),
        "grasp_threshold_m": _read(
            lambda: _vector([parent.grip_ctrlr._config.grasp_thresh_dist], 1)[0]
        ),
    }


class ManipulationDiagnostics:
    """Observe bounded original arm/gripper calls under exact current attempt identity."""

    def __init__(self) -> None:
        """Allocate bounded per-agent current/last records and loss accounting."""
        self._current: dict[int, dict[str, Any]] = {}
        self._last: dict[int, dict[str, Any]] = {}
        self._identity: dict[int, dict[str, Any]] = {}
        self._active: dict[int, Any] = {}
        self._installed: set[tuple[int, str]] = set()
        self._gripper_profiles: dict[tuple[int, str], dict[str, Any]] = {}
        self._calls = 0
        self._dropped = 0
        self._failures = 0
        self._capture_seconds = 0.0
        self._cleared = 0

    def bind(self, invocations: Mapping[int, CanonicalInvocation]) -> None:
        """Clear prior segment identity before binding only exact relocation attempts."""
        self._identity.clear()
        self._cleared += sum(record["call_count"] for record in self._current.values())
        self._current.clear()
        self._last.clear()
        self._identity = {
            agent: _invocation_identity(invocation)
            for agent, invocation in invocations.items()
            if isinstance(invocation, CanonicalRelocationInvocation)
        }

    def _begin(
        self, agent: int, name: str, args: Any, kwargs: Any, action_key: str | None
    ) -> dict[str, Any]:
        """Copy inputs only for original calls inside the dispatched arm scope."""
        current = self._current.setdefault(
            agent, {"call_count": 0, "calls": [], "dropped_calls": 0}
        )
        current["call_count"] += 1
        self._calls += 1
        if current["call_count"] > MAX_CALLS_PER_STEP:
            current["dropped_calls"] += 1
            self._dropped += 1
            return {"_status": "unavailable"}
        record: dict[str, Any] = {"method": name}
        current["calls"].append(record)
        controller = self._active[agent]
        if name.endswith(".step") and name.startswith("arm_"):
            record["gripper_configuration"] = self._gripper_profiles.get((agent, name))
            record["action_argument_key"] = action_key
            record["state_before"] = _read(lambda: _state(controller))
            record["selected_action"] = _read(lambda: _vector(kwargs[action_key], 2))
            record["ee_constraints_vendor_ik_frame"] = _read(
                lambda: [
                    _vector(
                        controller.cur_articulated_agent.params.ee_constraint[controller.ee_index][
                            index
                        ],
                        2,
                    )
                    for index in range(3)
                ]
            )
        elif name == "apply_ee_constraints":
            record["target_before"] = _read(lambda: _vector(controller.ee_target, 3))
        elif name == "_get_coord_for_pddl_idx":
            record["selected_pddl_index"] = _read(lambda: _vector(args, 1)[0])
            record["selected_entity"] = _read(
                lambda: _text(controller._entities[int(args[0])].name)
            )
        elif name in {"calc_ik", "calc_fk", "set_arm_state", "set_desired_ee_pos"}:
            keyword = {
                "calc_ik": "targ_ee",
                "calc_fk": "js",
                "set_arm_state": "joint_pos",
                "set_desired_ee_pos": "ee_pos",
            }[name]
            record["input"] = _read(lambda: _vector(args[0] if args else kwargs[keyword]))
        elif name == "gripper.step":
            record["input"] = _read(lambda: None if args[0] is None else float(args[0]))
        return record

    def _finish(self, agent: int, record: dict[str, Any], result: Any, error: Any) -> None:
        """Copy existing results before caller mutation without extra solver queries."""
        if record.get("_status") == "unavailable":
            return
        record["call_outcome"] = "raised" if error is not None else "returned"
        if error is not None:
            record["exception_type"] = type(error).__name__
        name = record["method"]
        if name in {"calc_fk", "calc_ik", "_get_coord_for_pddl_idx"} and error is None:
            record["returned_value"] = _read(lambda: _vector(result))
        if name == "apply_ee_constraints":
            record["target_after"] = _read(lambda: _vector(self._active[agent].ee_target, 3))
        if name.endswith(".step") and name.startswith("arm_"):
            record["state_after"] = _read(lambda: _state(self._active[agent]))

    def _safe_finish(self, agent: int, record: dict[str, Any], result: Any, error: Any) -> None:
        """Isolate observer faults, preserving every original result and exception."""
        started = time.perf_counter()
        try:
            self._finish(agent, record, result, error)
        except Exception:  # noqa: BLE001 - observation cannot replace original results
            self._failures += 1
            record["observation_error"] = True
        finally:
            self._capture_seconds += time.perf_counter() - started

    def _wrapper(
        self,
        agent: int,
        name: str,
        original: Any,
        controller: Any = None,
        action_key: str | None = None,
    ) -> Callable[..., Any]:
        """Keep metadata in closures and pass every positional/keyword argument intact."""

        def observed(*args: Any, **kwargs: Any) -> Any:
            """Invoke one original call under optional active arm attribution."""
            previous = self._active.get(agent)
            if controller is not None:
                if action_key is None:
                    return original(*args, **kwargs)
                try:
                    selected = kwargs[action_key]
                    active = len(selected) == 2 and float(selected[1]) != 0
                except Exception:  # noqa: BLE001 - unsupported layout remains untouched
                    active = False
                if not active:
                    return original(*args, **kwargs)
                self._active[agent] = controller
            elif agent not in self._active:
                return original(*args, **kwargs)
            record: dict[str, Any] = {"_status": "unavailable"}
            try:
                started = time.perf_counter()
                record = self._begin(agent, name, args, kwargs, action_key)
                self._capture_seconds += time.perf_counter() - started
            except Exception:  # noqa: BLE001 - read failure cannot block manipulation
                self._failures += 1
            try:
                try:
                    result = original(*args, **kwargs)
                except BaseException as error:
                    self._safe_finish(agent, record, None, error)
                    raise
                self._safe_finish(agent, record, result, None)
                return result
            finally:
                if controller is not None:
                    if previous is None:
                        self._active.pop(agent, None)
                    else:
                        self._active[agent] = previous

        return observed

    def install(self, actions: Mapping[str, Any], agent: int) -> list[Callable[[], None]]:
        """Tap configured original arm instances, avoiding double-wrapped shared helpers."""
        restores: list[Callable[[], None]] = []
        for kind in ("pick", "place"):
            action_key = f"agent_{agent}_arm_{kind}_action"
            parent = actions.get(action_key)
            if parent is None:
                continue
            try:
                controller = parent.arm_ctrlr
                self._gripper_profiles[(agent, f"arm_{kind}_action.step")] = _gripper_profile(
                    parent
                )
                targets = [(parent, "step", f"arm_{kind}_action.step", controller)]
                targets.extend(
                    (controller, name, name, None)
                    for name in (
                        "_get_coord_for_pddl_idx",
                        "set_desired_ee_pos",
                        "apply_ee_constraints",
                    )
                )
                targets.extend(
                    (controller._ik_helper, name, name, None)
                    for name in ("set_arm_state", "calc_fk", "calc_ik")
                )
                targets.append((parent.grip_ctrlr, "step", "gripper.step", None))
                for owner, attribute, name, context in targets:
                    key = (id(owner), attribute)
                    original = getattr(owner, attribute, None)
                    if key in self._installed or not callable(original):
                        continue
                    override = vars(owner).get(attribute, _MISSING)
                    setattr(
                        owner,
                        attribute,
                        self._wrapper(
                            agent,
                            name,
                            original,
                            context,
                            action_key if context is not None else None,
                        ),
                    )
                    self._installed.add(key)
                    restores.append(self._restorer(owner, attribute, override))
            except Exception:  # noqa: BLE001 - unsupported action remains diagnostic only
                self._failures += 1
        return restores

    def _restorer(self, owner: Any, name: str, override: Any) -> Callable[[], None]:
        """Restore the exact prior instance override or original class lookup."""

        def restore() -> None:
            """Remove only this observer's override and release installation bookkeeping."""
            if override is _MISSING:
                delattr(owner, name)
            else:
                setattr(owner, name, override)
            self._installed.discard((id(owner), name))

        return restore

    def _document(self, agent: int, current: dict[str, Any]) -> dict[str, Any]:
        """Label copied evidence and its limits without claiming physical feasibility."""
        return {
            "schema_version": MANIPULATION_SCHEMA,
            "invocation": self._identity.get(
                agent, {"_status": "unavailable", "reason": "no canonical relocation attempt bound"}
            ),
            "coordinate_frames": {
                "positions": "habitat-world",
                "solver_vectors": "original-vendor-ik-frame; no observer frame conversion",
            },
            "max_calls_per_agent_per_step": MAX_CALLS_PER_STEP,
            "call_records_complete": current["call_count"] == len(current["calls"])
            and all(
                call.get("call_outcome") in {"returned", "raised"}
                and "observation_error" not in call
                for call in current["calls"]
            ),
            **current,
        }

    def sample(self, agent: int, step: int) -> dict[str, Any]:
        """Consume calls after an actual Gym step; gaps do not fabricate action evidence."""
        current = self._current.pop(agent, None)
        if current is None:
            return {"_status": "unavailable", "reason": "no active arm call observed this step"}
        document = {**self._document(agent, current), "simulator_step": step}
        self._last[agent] = document
        return document

    def boundary(self, agent: int) -> dict[str, Any]:
        """Retain pending original calls even when the physical step failed."""
        return {
            "last_post_step": self._last.get(agent),
            "pending_since_last_post_step": self._document(agent, self._current[agent])
            if agent in self._current
            else None,
        }

    def stats(self) -> dict[str, Any]:
        """Report observer cost, loss and limits separately from execution outcome."""
        return {
            "max_calls_per_agent_per_step": MAX_CALLS_PER_STEP,
            "max_joint_vector_length": MAX_JOINTS,
            "calls_observed": self._calls,
            "calls_dropped": self._dropped,
            "observation_failures": self._failures,
            "pending_calls_cleared_at_bind": self._cleared,
            "capture_seconds": self._capture_seconds,
        }
