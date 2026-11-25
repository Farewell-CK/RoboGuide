"""Clock-triggered `choreography.play@v1` engine with Fail-Closed gating and fault isolation."""

from __future__ import annotations

import hashlib
import math
import threading
import time
from typing import Any

from .adapters.base import RobotMotionAdapter, RobotPose
from .clock_sync import ClockProbe, ClockSyncError, verify_clock_offset
from .schemas import ChoreographySpec, KeyframeSpec, canonical_json
from .track import clamp, rate_limit, wrap_angle


def interpolate_keyframes(
    keyframes: tuple[KeyframeSpec, ...], t_s: float
) -> tuple[float, float, float, float, float, float, bool]:
    """Interpolate reference `(x, y, yaw, vx_world, vy_world, w_ff, finished)` at `t_s >= 0`."""
    if t_s <= keyframes[0].t_s:
        k0 = keyframes[0]
        return k0.x, k0.y, k0.yaw, 0.0, 0.0, 0.0, False
    if t_s >= keyframes[-1].t_s:
        kn = keyframes[-1]
        return kn.x, kn.y, kn.yaw, 0.0, 0.0, 0.0, True

    # Linear scan over monotonic keyframes
    idx = 0
    for i in range(len(keyframes) - 1):
        if keyframes[i].t_s <= t_s <= keyframes[i + 1].t_s:
            idx = i
            break
    a, b = keyframes[idx], keyframes[idx + 1]
    dt = max(1e-6, b.t_s - a.t_s)
    u = (t_s - a.t_s) / dt
    x = a.x + u * (b.x - a.x)
    y = a.y + u * (b.y - a.y)
    dyaw = wrap_angle(b.yaw - a.yaw)
    yaw = wrap_angle(a.yaw + u * dyaw)
    vx_world = (b.x - a.x) / dt
    vy_world = (b.y - a.y) / dt
    w_ff = dyaw / dt
    return x, y, yaw, vx_world, vy_world, w_ff, False


class ChoreographyEngine:
    """Executes one `choreography.play@v1` run under shared UTC `T0` with zero peer coupling."""

    def __init__(
        self,
        *,
        adapter: RobotMotionAdapter,
        clock_probe: ClockProbe,
        spec: ChoreographySpec,
        t0_utc_ms: int,
        max_clock_offset_ms: float,
        cancel_event: threading.Event,
    ) -> None:
        """Initialize one isolated choreography execution."""
        self.adapter = adapter
        self.clock_probe = clock_probe
        self.spec = spec
        self.t0_utc_ms = int(t0_utc_ms)
        self.max_clock_offset_ms = float(max_clock_offset_ms)
        self.cancel_event = cancel_event

    def run(self) -> tuple[str, str, dict[str, Any], bool]:
        """Verify clock and SHA-256, start at `t0_utc_ms`, and isolate faults."""
        self.adapter.prepare_run()

        # 1. Fail-Closed clock offset verification
        try:
            clk = verify_clock_offset(self.clock_probe, self.max_clock_offset_ms)
        except ClockSyncError as exc:
            self.adapter.emergency_stop()
            return "FAILED", f"clock_gate_rejected:{exc}", {"clock_error": str(exc)}, True

        # 2. Optional choreography pack SHA-256 verification
        if self.spec.pack_sha256:
            digest = hashlib.sha256(
                canonical_json(
                    [
                        {"t_s": k.t_s, "x": k.x, "y": k.y, "yaw": k.yaw, "action": k.action}
                        for k in self.spec.keyframes
                    ]
                ).encode("utf-8")
            ).hexdigest()
            if digest != self.spec.pack_sha256:
                self.adapter.emergency_stop()
                return (
                    "FAILED",
                    f"choreography_sha256_mismatch:{digest}!={self.spec.pack_sha256}",
                    {"expected_sha256": self.spec.pack_sha256, "actual_sha256": digest},
                    True,
                )

        now_ms = int(time.time() * 1000)
        if now_ms - self.t0_utc_ms > 2_000:
            self.adapter.emergency_stop()
            return (
                "FAILED",
                f"t0_utc_ms_expired_by_{now_ms - self.t0_utc_ms}ms",
                {"t0_utc_ms": self.t0_utc_ms, "now_utc_ms": now_ms},
                True,
            )

        # 3. Wait cleanly until shared UTC trigger `t0_utc_ms`
        while True:
            if self.cancel_event.is_set():
                self.adapter.emergency_stop()
                return "CANCELED", "canceled_before_t0", {"t0_utc_ms": self.t0_utc_ms}, True
            rem_s = (self.t0_utc_ms - time.time() * 1000.0) / 1000.0
            if rem_s <= 0.0:
                break
            self.cancel_event.wait(timeout=min(rem_s, 0.02))

        actual_start_ms = int(time.time() * 1000)
        actual_start_mono = time.monotonic()
        t0_offset_s = max(0.0, (actual_start_ms - self.t0_utc_ms) / 1000.0)
        start_skew_ms = actual_start_ms - self.t0_utc_ms

        # 4. 20 Hz local time-indexed trajectory playback (monotonic clock anchored at T0!)
        dt = 0.05
        prev_vx = prev_vy = prev_wz = 0.0
        final_state = "SUCCEEDED"
        detail = ""
        last_pose: RobotPose | None = None
        max_track_err_m = 0.0
        next_tick = actual_start_mono

        try:
            while True:
                if self.cancel_event.is_set():
                    self.adapter.emergency_stop()
                    final_state = "CANCELED"
                    detail = "canceled_during_choreography"
                    break

                now_m = time.monotonic()
                if now_m < next_tick:
                    self.cancel_event.wait(timeout=min(next_tick - now_m, 0.02))
                    continue
                next_tick += dt
                if next_tick < now_m - 0.2:
                    next_tick = now_m + dt

                sensors = self.adapter.read_sensors()
                if not sensors.healthy:
                    # Single-robot fault isolation: halt ONLY this robot cleanly, zero peer impact
                    self.adapter.emergency_stop()
                    final_state = "FAILED"
                    detail = f"isolated_local_fault:{sensors.detail}"
                    break
                if (
                    sensors.lidar_front_min_m is not None
                    and sensors.lidar_front_min_m < self.spec.lidar_stop_m
                ):
                    self.adapter.emergency_stop()
                    final_state = "FAILED"
                    detail = f"isolated_local_obstacle:{sensors.lidar_front_min_m:.3f}m"
                    break

                pose = self.adapter.read_pose()
                if pose is None:
                    self.adapter.emergency_stop()
                    final_state = "FAILED"
                    detail = "isolated_local_pose_unavailable"
                    break
                last_pose = pose

                t_s = t0_offset_s + max(0.0, time.monotonic() - actual_start_mono)
                rx, ry, ryaw, vx_w, vy_w, w_ff, finished = interpolate_keyframes(
                    self.spec.keyframes, t_s
                )
                if finished:
                    self.adapter.emergency_stop()
                    detail = f"choreography completed at T+{t_s:.2f}s"
                    break

                # Compute world-to-body tracking error and feedforward
                ex_w, ey_w = rx - pose.x, ry - pose.y
                max_track_err_m = max(max_track_err_m, math.hypot(ex_w, ey_w))
                c, s = math.cos(pose.yaw), math.sin(pose.yaw)
                ex_b = c * ex_w + s * ey_w
                ey_b = -s * ex_w + c * ey_w
                vx_ff = c * vx_w + s * vy_w
                vy_ff = -s * vx_w + c * vy_w
                e_yaw = wrap_angle(ryaw - pose.yaw)

                vx_tgt = clamp(vx_ff + 1.1 * ex_b, -self.spec.v_max, self.spec.v_max)
                vy_lim = min(0.20, self.spec.v_max)
                vy_tgt = clamp(vy_ff + 0.8 * ey_b, -vy_lim, vy_lim)
                wz_tgt = clamp(w_ff + 1.6 * e_yaw, -1.0, 1.0)

                prev_vx = rate_limit(vx_tgt, prev_vx, 0.8 * dt)
                prev_vy = rate_limit(vy_tgt, prev_vy, 0.5 * dt)
                prev_wz = rate_limit(wz_tgt, prev_wz, 2.0 * dt)
                self.adapter.send_velocity(prev_vx, prev_vy, prev_wz)
        finally:
            self.adapter.emergency_stop()
            try:
                self.adapter.safe_park_wait(1.8)
            except Exception:
                pass

        metrics = {
            "t0_utc_ms": self.t0_utc_ms,
            "actual_start_utc_ms": actual_start_ms,
            "start_skew_ms": start_skew_ms,
            "clock_source": clk.source,
            "clock_offset_ms": clk.offset_ms,
            "max_track_err_m": round(max_track_err_m, 4),
            "final_pose": (
                {
                    "x": round(last_pose.x, 4),
                    "y": round(last_pose.y, 4),
                    "yaw": round(last_pose.yaw, 4),
                }
                if last_pose is not None
                else None
            ),
        }
        return final_state, detail, metrics, True
