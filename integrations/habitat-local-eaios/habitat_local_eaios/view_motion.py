"""Observe an exact scan's actual orientation separately from its command and timer."""

from __future__ import annotations

import math
from typing import Any

MIN_ROTATION_RADIANS = 1e-5


def motion_feedback_declaration() -> dict[str, Any]:
    """Declare read-only post-step evidence without granting collision or success authority."""
    return {
        "profile": "observed-own-scan-rotation/v0.1",
        "source": "original-own-articulated-agent-rotation",
        "minimum_rotation_radians": MIN_ROTATION_RADIANS,
        "missing_evidence": "unavailable",
        "skill_timer_completion_is_rotation": False,
        "collision_constraints": "unchanged",
        "official_success_authority": "habitat-pddl",
    }


def _rotation(action: Any) -> tuple[float, ...] | None:
    """Read the existing own-agent quaternion; missing or malformed evidence stays unknown."""
    try:
        value = action.cur_articulated_agent.sim_obj.rotation
        raw = (*value.vector, value.scalar)
        if len(raw) != 4 or any(isinstance(item, bool) for item in raw):
            return None
        parts = tuple(float(item) for item in raw)
        norm = math.sqrt(sum(item * item for item in parts))
        if not math.isfinite(norm) or abs(norm - 1) > 1e-3:
            return None
        return tuple(item / norm for item in parts)
    except Exception:  # noqa: BLE001 - evidence reads never change the original execution
        return None


class ScanRotationObservation:
    """Keep a bounded scalar observation for one exact selected scan, never a pose controller."""

    def __init__(self, action: Any, call_id: str, sequence: int) -> None:
        """Freeze the pre-dispatch orientation without changing simulator state or RNG."""
        self._action, self.call_id, self.sequence = action, call_id, sequence
        self._start = _rotation(action)
        self._last = self._start
        self._samples = 0
        self._unavailable = 0
        self._maximum = 0.0

    def observe(self) -> None:
        """Observe one existing successful Gym step, never act, step, render or query a sensor."""
        self._samples += 1
        self._last = _rotation(self._action)
        if self._start is None or self._last is None:
            self._unavailable += 1
            return
        dot = abs(sum(self._start[index] * self._last[index] for index in range(4)))
        angle = 2 * math.acos(min(1.0, dot))
        self._maximum = max(self._maximum, angle)

    def snapshot(self) -> dict[str, Any]:
        """Separate observed rotation, fully observed stationarity and unavailable samples."""
        rotated = self._maximum > MIN_ROTATION_RADIANS
        complete = self._start is not None and self._samples > 0 and self._unavailable == 0
        return {
            "profile": "observed-own-scan-rotation/v0.1",
            "source": "original-own-articulated-agent-rotation",
            "tool_call_id": self.call_id,
            "action_sequence": self.sequence,
            "status": "rotation-observed"
            if rotated
            else "no-rotation-observed"
            if complete
            else "unavailable",
            "rotation_observed": True if rotated else False if complete else None,
            "post_step_samples": self._samples,
            "unavailable_samples": self._unavailable,
            "max_rotation_from_start_radians": self._maximum
            if self._start is not None and self._samples > 0
            else None,
            "minimum_rotation_radians": MIN_ROTATION_RADIANS,
            "physical_blocking_cause": "unavailable",
            "detection_satisfied": None,
            "benchmark_goal_satisfied": None,
        }
