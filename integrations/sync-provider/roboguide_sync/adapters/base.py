"""Abstract EAIOS-neutral motion & sensing interface."""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass(frozen=True)
class RobotPose:
    """SE(2) planar pose + twist snapshot in shared map frame."""

    x: float
    y: float
    yaw: float
    vx: float = 0.0
    vy: float = 0.0
    wz: float = 0.0
    stamp_mono: float = 0.0


@dataclass(frozen=True)
class SensorSnapshot:
    """Chassis health, posture, and front obstacle clearance summary."""

    posture: str = "standing"
    lidar_front_min_m: float | None = None
    battery_pct: float | None = None
    healthy: bool = True
    detail: str = ""


class RobotMotionAdapter(abc.ABC):
    """Hardware/EAIOS interface for robot runtimes (`sim`, `http-chassis`, `robonix-lite3`)."""

    adapter_kind: str = "base"

    @abc.abstractmethod
    def start(self) -> None:
        """Initialize transport/bridges when service starts."""

    @abc.abstractmethod
    def close(self) -> None:
        """Stop chassis safely and release local resources."""

    def prepare_run(self) -> None:  # noqa: B027
        """Optional hook called right before starting a motion execution."""

    @abc.abstractmethod
    def read_pose(self) -> RobotPose | None:
        """Return latest SE(2) map pose, or `None` if unavailable/stale."""

    @abc.abstractmethod
    def read_sensors(self) -> SensorSnapshot:
        """Return latest posture, obstacle distance, battery, and health."""

    @abc.abstractmethod
    def send_velocity(self, vx: float, vy: float, wz: float) -> None:
        """Publish bounded body-frame velocity command (m/s, rad/s)."""

    @abc.abstractmethod
    def emergency_stop(self) -> None:
        """Immediately halt all chassis velocity (burst zero commands)."""

    @abc.abstractmethod
    def safe_park_wait(self, settle_s: float = 1.8) -> None:
        """Hold zero velocity cleanly for `settle_s` seconds (robot physical settle rule)."""

    @abc.abstractmethod
    def check_ready(self, operation: str) -> tuple[bool, str]:
        """Return `(ready, detail)` for capability readiness probes."""
