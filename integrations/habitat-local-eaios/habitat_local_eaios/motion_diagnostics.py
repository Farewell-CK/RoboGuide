"""Bounded, opt-in taps of original local motion calls, never motion authority.

Inputs are copied before the original call and returns before the caller can
mutate them. The original is invoked once with its exact arguments, return
identity and exceptions. No path query, simulator command, serialization or
file write is added. Missing reads remain unavailable; observed positions
never prove physical collision, arrival or official benchmark success.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from typing import Any

from .model import CanonicalMobilityInvocation

MOTION_SCHEMA = "roboguide.local-motion-observation/v0.1"
MAX_CALLS_PER_STEP = 8
_MISSING = object()


def _read(accessor: Callable[[], Any]) -> Any:
    """Isolate read failures without evaluating or changing physical state."""
    try:
        return accessor()
    except Exception as error:  # noqa: BLE001 - diagnostics never alter execution
        return {"_status": "unavailable", "reason": type(error).__name__}


def _point(value: Any) -> list[float]:
    """Copy a finite three-component position before its original object changes."""
    if len(value) != 3:
        raise ValueError("position must have three components")
    result = [float(value[index]) for index in range(3)]
    if any(isinstance(value[index], bool) for index in range(3)) or not all(
        math.isfinite(component) for component in result
    ):
        raise ValueError("position must be finite")
    return result


def _flag(value: Any) -> bool:
    """Read a genuine boolean instead of coercing unavailable evidence to true."""
    if type(value) is not bool:
        raise ValueError("flag must be boolean")
    return value


def _scalar(value: Any) -> float:
    """Read one finite capability scalar without selecting a replacement."""
    if isinstance(value, bool):
        raise ValueError("capability scalar cannot be boolean")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("capability scalar must be finite")
    return number


def _profile_scalar(action: Any, name: str, *, mesh: bool = False) -> Any:
    """Isolate access to a configured or active-mesh scalar, including parent reads."""
    return _read(
        lambda: _scalar(
            getattr(action.pathfinder.nav_mesh_settings if mesh else action.config, name)
        )
    )


def _bounded_text(value: Any) -> Any:
    """Preserve short identity fields, marking longer or missing values unavailable."""
    if isinstance(value, str) and 0 < len(value) <= 1024:
        return value
    return {"_status": "unavailable", "reason": "identity missing or exceeds 1024 characters"}


def _restore(action: Any, name: str, original: Any) -> None:
    """Restore the exact instance override or remove this class-method override."""
    if original is _MISSING:
        delattr(action, name)
    else:
        setattr(action, name, original)


class MotionDiagnostics:
    """Retain bounded call evidence with explicit Task/Role/attempt attribution."""

    def __init__(self) -> None:
        """Allocate only per-agent current/last records and scalar loss counters."""
        self._current: dict[int, dict[str, Any]] = {}
        self._last: dict[int, dict[str, Any]] = {}
        self._identity: dict[int, dict[str, Any]] = {}
        self._profiles: dict[int, dict[str, Any]] = {}
        self._calls = 0
        self._dropped = 0
        self._failures = 0
        self._capture_seconds = 0.0
        self._pending_calls_cleared_at_bind = 0

    def bind(self, invocations: Mapping[int, CanonicalMobilityInvocation]) -> None:
        """Freeze supplied canonical identity without selecting or dispatching an executor."""
        # Invalidate the old segment even if the new attribution cannot be read.
        self._identity.clear()
        self._pending_calls_cleared_at_bind += sum(
            current["call_count"] for current in self._current.values()
        )
        self._current.clear()
        self._last.clear()
        self._profiles.clear()
        self._identity = {
            agent: {
                "mission_id": _bounded_text(invocation.mission_id),
                "task_id": _bounded_text(invocation.task_id),
                "group_id": _bounded_text(invocation.group_id),
                "role_id": _bounded_text(invocation.role_id),
                "attempt_id": _bounded_text(invocation.attempt_id),
                "operation": _bounded_text(invocation.operation),
                "canonical_destination": _bounded_text(invocation.destination),
            }
            for agent, invocation in invocations.items()
        }

    def _profile(self, action: Any) -> dict[str, Any]:
        """Read actual action/mesh scalars once per segment, without rebuilding a mesh."""
        fields = ("agent_height", "agent_radius", "agent_max_climb", "agent_max_slope")
        return {
            "authority": "observed deployment navigation model, not hardware certification",
            "action_class": _bounded_text(f"{type(action).__module__}.{type(action).__qualname__}"),
            "action_config": {name: _profile_scalar(action, name) for name in fields},
            "active_navmesh_settings": {
                name: _profile_scalar(action, name, mesh=True)
                for name in (*fields, "cell_size", "cell_height")
            },
        }

    def _begin(self, agent: int, action: Any, name: str, args: Any, kwargs: Any) -> dict[str, Any]:
        """Copy pre-call evidence only while the per-step call budget permits it."""
        current = self._current.setdefault(
            agent, {"call_count": 0, "calls": [], "dropped_calls": 0}
        )
        current["call_count"] += 1
        self._calls += 1
        if current["call_count"] > MAX_CALLS_PER_STEP:
            current["dropped_calls"] += 1
            self._dropped += 1
            return {"_status": "unavailable", "reason": "per-step call budget exhausted"}
        if agent not in self._profiles:
            self._profiles[agent] = self._profile(action)
        record: dict[str, Any] = {"method": name}
        current["calls"].append(record)
        if name == "step_filter":
            record["requested_start"] = _read(
                lambda: _point(args[0] if args else kwargs["start_pos"])
            )
            record["requested_end"] = _read(
                lambda: _point(args[1] if len(args) > 1 else kwargs["end_pos"])
            )
            record["pathfinder_loaded"] = _read(lambda: _flag(action.pathfinder.is_loaded))
            record["configured_sliding"] = _read(lambda: _flag(action.config.allow_dyn_slide))
        else:
            record["base_position_before"] = _read(
                lambda: _point(action.cur_articulated_agent.base_pos)
            )
            record["base_update_sliding"] = _read(lambda: _flag(action._allow_dyn_slide))
        return record

    def _finish(self, record: dict[str, Any], action: Any, result: Any, error: Any) -> None:
        """Copy the original return or error, avoiding unsupported causal labels."""
        if record.get("_status") == "unavailable":
            return
        record["call_outcome"] = "raised" if error is not None else "returned"
        if error is not None:
            record["exception_type"] = type(error).__name__
        if record["method"] == "step_filter":
            record["returned_end"] = (
                _read(lambda: _point(result))
                if error is None
                else {"_status": "unavailable", "reason": "original call raised"}
            )
            start, end, returned = (
                record["requested_start"],
                record["requested_end"],
                record["returned_end"],
            )
            if all(isinstance(point, list) for point in (start, end, returned)):
                record["derived_comparison"] = {
                    "basis": "copied endpoints; not a collision or arrival measurement",
                    "requested_distance_m": math.dist(start, end),
                    "returned_distance_m": math.dist(start, returned),
                    "request_changed_by_filter": end != returned,
                }
        else:
            record["base_position_after"] = _read(
                lambda: _point(action.cur_articulated_agent.base_pos)
            )

    def _wrapper(self, action: Any, agent: int, name: str, original: Any) -> Callable[..., Any]:
        """Keep closure metadata separate from every original positional/keyword argument."""

        def observed(*args: Any, **kwargs: Any) -> Any:
            """Invoke the physical method once, isolating all observer failures."""
            record: dict[str, Any] = {"_status": "unavailable"}
            try:
                started = time.perf_counter()
                record = self._begin(agent, action, name, args, kwargs)
                self._capture_seconds += time.perf_counter() - started
            except Exception:  # noqa: BLE001 - pre-read cannot block motion
                self._failures += 1
            try:
                result = original(*args, **kwargs)
            except BaseException as error:
                self._safe_finish(record, action, None, error)
                raise
            self._safe_finish(record, action, result, None)
            return result

        return observed

    def install(self, action: Any, agent: int) -> list[Callable[[], None]]:
        """Wrap supported instance methods transactionally, leaving other actions unchanged."""
        restores: list[Callable[[], None]] = []
        try:
            attributes = vars(action)
            for name in ("step_filter", "update_base"):
                original = getattr(action, name, None)
                if not callable(original):
                    continue
                override = attributes.get(name, _MISSING)
                observed = self._wrapper(action, agent, name, original)

                def restore(*, _name: str = name, _override: Any = override) -> None:
                    """Restore one original method after the terminal evidence is saved."""
                    _restore(action, _name, _override)

                # Add restoration before assignment so a failing setter can be undone.
                restores.append(restore)
                setattr(action, name, observed)
        except Exception:  # noqa: BLE001 - unsupported instances retain physical behavior
            self._failures += 1
            for restore_installed in reversed(restores):
                try:
                    restore_installed()
                except Exception:  # noqa: BLE001 - preserve unsupported original layout
                    pass
            return []
        return restores

    def _observe_finish(self, record: dict[str, Any], action: Any, result: Any, error: Any) -> None:
        """Count observer overhead separately from original motion execution time."""
        started = time.perf_counter()
        try:
            self._finish(record, action, result, error)
        except Exception:  # noqa: BLE001 - post-read cannot mask a physical error or result
            self._failures += 1
            record["observation_error"] = {
                "_status": "unavailable",
                "reason": "post-call capture failed",
            }
        finally:
            self._capture_seconds += time.perf_counter() - started

    def _safe_finish(self, record: dict[str, Any], action: Any, result: Any, error: Any) -> None:
        """Fence timing/callback failures as well as ordinary post-call read failures."""
        try:
            self._observe_finish(record, action, result, error)
        except Exception:  # noqa: BLE001 - even bookkeeping cannot mask the physical result
            self._failures += 1
            record["observation_error"] = {"_status": "unavailable", "reason": "observer failed"}

    def _document(self, agent: int, current: dict[str, Any]) -> dict[str, Any]:
        """Attach frozen attribution and distinguish missing invocation identity."""
        return {
            "schema_version": MOTION_SCHEMA,
            "agent_id": agent,
            "invocation": self._identity.get(
                agent, {"_status": "unavailable", "reason": "no canonical invocation supplied"}
            ),
            "navigation_profile": self._profiles.get(agent),
            "max_calls_per_step": MAX_CALLS_PER_STEP,
            "call_records_complete": current["call_count"] == len(current["calls"])
            and all(
                call.get("call_outcome") in {"returned", "raised"}
                and "observation_error" not in call
                for call in current["calls"]
            ),
            **current,
        }

    def sample(self, agent: int, step: int) -> dict[str, Any]:
        """Consume only actual calls since the preceding post-step sample."""
        current = self._current.pop(agent, None)
        if current is None:
            return {"_status": "unavailable", "reason": "no motion call observed this step"}
        document = {**self._document(agent, current), "simulator_step": step}
        self._last[agent] = document
        return document

    def boundary(self, agent: int) -> dict[str, Any]:
        """Preserve pending calls from a failed step without inventing its simulator number."""
        return {
            "last_post_step": self._last.get(
                agent, {"_status": "unavailable", "reason": "no post-step motion sample"}
            ),
            "pending_since_last_post_step": (
                self._document(agent, self._current[agent]) if agent in self._current else None
            ),
        }

    def stats(self) -> dict[str, Any]:
        """Expose bounded recording loss and time without interpreting execution success."""
        return {
            "max_calls_per_agent_per_step": MAX_CALLS_PER_STEP,
            "calls_observed": self._calls,
            "calls_dropped": self._dropped,
            "observation_failures": self._failures,
            "pending_calls_cleared_at_bind": self._pending_calls_cleared_at_bind,
            "capture_seconds": self._capture_seconds,
        }
