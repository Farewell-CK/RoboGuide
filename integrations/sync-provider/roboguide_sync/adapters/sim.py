"""Pure-software kinematic `SimAdapter` for multi-process L0/L1 validation (zero hardware I/O)."""

from __future__ import annotations

import math
import threading
import time

from ..track import clamp, wrap_angle
from .base import RobotMotionAdapter, RobotPose, SensorSnapshot


class SimAdapter(RobotMotionAdapter):
    """Thread-safe kinematic SE(2) robot simulator for L0/L1 testing."""

    adapter_kind = "sim"

    def __init__(
        self,
        *,
        x0: float = 0.0,
        y0: float = 0.0,
        yaw0: float = 0.0,
        speed_scale: float = 1.0,
        start_delay_s: float = 0.0,
        fault_at_s: float | None = None,
        lidar_front_min_m: float = 5.0,
        battery_pct: float = 95.0,
    ) -> None:
        """Initialize simulated robot pose and fault injection settings."""
        self._lock = threading.Lock()
        self.x = float(x0)
        self.y = float(y0)
        self.yaw = wrap_angle(float(yaw0))
        self.vx = 0.0
        self.vy = 0.0
        self.wz = 0.0
        self.speed_scale = float(speed_scale)
        self.start_delay_s = float(start_delay_s)
        self.fault_at_s = float(fault_at_s) if fault_at_s is not None else None
        self.lidar_front_min_m = float(lidar_front_min_m)
        self.battery_pct = float(battery_pct)
        self._last_step_m = time.monotonic()
        self._run_t0_m = self._last_step_m
        self._motion_t0_m: float | None = None
        self._fault_latched = False
        self.estop_calls = 0
        self.last_estop_mono: float | None = None
        self.max_cmd_speed = 0.0

    def start(self) -> None:
        """Reset integration timestamps when starting the simulator."""
        with self._lock:
            now = time.monotonic()
            self._last_step_m = now
            self._run_t0_m = now

    def close(self) -> None:
        """Stop simulated motion on shutdown."""
        self.emergency_stop()

    def prepare_run(self) -> None:
        """Reset velocity commands and startup timer before an execution."""
        with self._lock:
            now = time.monotonic()
            self._last_step_m = now
            self._run_t0_m = now
            self._motion_t0_m = None
            self.vx = self.vy = self.wz = 0.0

    def _integrate_locked(self, now: float) -> None:
        """Advance SE(2) state under lock up to `now`."""
        dt = clamp(now - self._last_step_m, 0.0, 0.25)
        self._last_step_m = now
        if self._fault_latched:
            self.vx = self.vy = self.wz = 0.0
            return
        if self.fault_at_s is not None and self._motion_t0_m is not None:
            if (now - self._motion_t0_m) >= self.fault_at_s:
                self._fault_latched = True
                self.vx = self.vy = self.wz = 0.0
                return
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        v_world_x = (self.vx * c - self.vy * s) * self.speed_scale
        v_world_y = (self.vx * s + self.vy * c) * self.speed_scale
        self.x += v_world_x * dt
        self.y += v_world_y * dt
        self.yaw = wrap_angle(self.yaw + self.wz * self.speed_scale * dt)

    def read_pose(self) -> RobotPose | None:
        """Advance state and return current simulated SE(2) pose."""
        with self._lock:
            now = time.monotonic()
            self._integrate_locked(now)
            return RobotPose(
                x=self.x,
                y=self.y,
                yaw=self.yaw,
                vx=self.vx * self.speed_scale,
                vy=self.vy * self.speed_scale,
                wz=self.wz * self.speed_scale,
                stamp_mono=now,
            )

    def read_sensors(self) -> SensorSnapshot:
        """Advance state and return simulated posture/fault/LiDAR snapshot."""
        with self._lock:
            now = time.monotonic()
            self._integrate_locked(now)
            if self._fault_latched:
                return SensorSnapshot(
                    posture="down",
                    lidar_front_min_m=self.lidar_front_min_m,
                    battery_pct=self.battery_pct,
                    healthy=False,
                    detail="sim_injected_fault",
                )
            if (now - self._run_t0_m) < self.start_delay_s:
                return SensorSnapshot(
                    posture="initializing",
                    lidar_front_min_m=self.lidar_front_min_m,
                    battery_pct=self.battery_pct,
                    healthy=True,
                    detail="sim_startup_delay",
                )
            return SensorSnapshot(
                posture="standing",
                lidar_front_min_m=self.lidar_front_min_m,
                battery_pct=self.battery_pct,
                healthy=True,
                detail="ok",
            )

    def send_velocity(self, vx: float, vy: float, wz: float) -> None:
        """Apply clamped body-frame velocity unless fault-latched."""
        with self._lock:
            now = time.monotonic()
            self._integrate_locked(now)
            if self._fault_latched:
                self.vx = self.vy = self.wz = 0.0
                return
            self.vx = clamp(float(vx), -0.50, 0.50)
            self.vy = clamp(float(vy), -0.25, 0.25)
            self.wz = clamp(float(wz), -1.20, 1.20)
            spd = math.hypot(self.vx, self.vy)
            if spd > self.max_cmd_speed:
                self.max_cmd_speed = spd
            if self._motion_t0_m is None and (
                abs(self.vx) > 1e-3 or abs(self.vy) > 1e-3 or abs(self.wz) > 1e-3
            ):
                self._motion_t0_m = now

    def emergency_stop(self) -> None:
        """Zero all velocities and record stop timestamp."""
        with self._lock:
            now = time.monotonic()
            self._integrate_locked(now)
            self.vx = self.vy = self.wz = 0.0
            self.estop_calls += 1
            self.last_estop_mono = now

    def safe_park_wait(self, settle_s: float = 1.8) -> None:
        """Stop immediately and perform capped simulation settle wait."""
        self.emergency_stop()
        # Simulation tests may use short settle or real wall wait; cap at caller's settle_s
        if settle_s > 0.0:
            time.sleep(min(settle_s, 0.05))

    def check_ready(self, operation: str) -> tuple[bool, str]:
        """Return readiness unless simulated fault is latched."""
        with self._lock:
            if self._fault_latched:
                return False, "sim adapter fault latched"
            return True, f"sim ready for {operation}"
