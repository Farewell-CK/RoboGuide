"""Generic EAIOS-neutral HTTP chassis adapter (`GET /pose`, `POST /cmd_vel`, `POST /estop`)."""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from typing import Any

from ..track import clamp
from .base import RobotMotionAdapter, RobotPose, SensorSnapshot


class HttpChassisAdapter(RobotMotionAdapter):
    """HTTP JSON chassis adapter (`/pose`, `/cmd_vel`, `/estop`) for any EAIOS."""

    adapter_kind = "http-chassis"

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 0.4,
        max_pose_age_ms: float = 500.0,
    ) -> None:
        """Initialize the HTTP chassis client."""
        self.base_url = base_url.rstrip("/")
        self.timeout_s = float(timeout_s)
        self.max_pose_age_ms = float(max_pose_age_ms)
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._last_sensors = SensorSnapshot(posture="unknown", healthy=False, detail="not polled")

    def _req(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Issue one HTTP JSON request to the chassis server."""
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        with self._opener.open(req, timeout=self.timeout_s) as resp:
            if resp.status < 200 or resp.status >= 300:
                raise RuntimeError(f"HTTP {resp.status} from {path}")
            raw = resp.read().decode("utf-8")
            parsed = json.loads(raw) if raw.strip() else {}
            return dict(parsed) if isinstance(parsed, dict) else {}

    def start(self) -> None:
        """Initialize adapter state."""

    def close(self) -> None:
        """Stop chassis safely on shutdown."""
        self.emergency_stop()

    def read_pose(self) -> RobotPose | None:
        """Fetch latest SE(2) pose from `/pose` with fail-closed staleness check."""
        try:
            d = self._req("GET", "/pose")
        except Exception as exc:
            self._last_sensors = SensorSnapshot(
                posture="unknown", healthy=False, detail=f"pose error: {exc}"
            )
            return None

        try:
            age_raw = d.get("age_ms")
            if age_raw is not None:
                if isinstance(age_raw, bool):
                    raise TypeError("age_ms must not be bool")
                age_ms = float(age_raw)
                if not math.isfinite(age_ms) or age_ms > self.max_pose_age_ms:
                    self._last_sensors = SensorSnapshot(
                        posture=str(d.get("posture", "unknown")),
                        healthy=False,
                        detail=f"stale chassis pose ({age_ms:.1f}ms > {self.max_pose_age_ms}ms)",
                    )
                    return None

            x = float(d["x"])
            y = float(d["y"])
            yaw = float(d["yaw"])
            vx = float(d.get("vx", 0.0))
            vy = float(d.get("vy", 0.0))
            wz = float(d.get("wz", 0.0))
            lmin_raw = d.get("lidar_front_min_m")
            lmin = float(lmin_raw) if lmin_raw is not None else None
            bat_raw = d.get("battery_pct")
            bat = float(bat_raw) if bat_raw is not None else None
        except (KeyError, TypeError, ValueError):
            self._last_sensors = SensorSnapshot(
                posture="unknown", healthy=False, detail="malformed /pose"
            )
            return None
        if not (
            math.isfinite(x)
            and math.isfinite(y)
            and math.isfinite(yaw)
            and math.isfinite(vx)
            and math.isfinite(vy)
            and math.isfinite(wz)
            and (lmin is None or math.isfinite(lmin))
            and (bat is None or math.isfinite(bat))
        ):
            self._last_sensors = SensorSnapshot(
                posture="unknown", healthy=False, detail="non-finite /pose fields"
            )
            return None

        self._last_sensors = SensorSnapshot(
            posture=str(d.get("posture", "standing")),
            lidar_front_min_m=lmin,
            battery_pct=bat,
            healthy=bool(d.get("healthy", True)),
            detail=str(d.get("detail", "ok")),
        )
        return RobotPose(
            x=x,
            y=y,
            yaw=yaw,
            vx=vx,
            vy=vy,
            wz=wz,
            stamp_mono=time.monotonic(),
        )

    def read_sensors(self) -> SensorSnapshot:
        """Return latest cached sensor snapshot."""
        return self._last_sensors

    def send_velocity(self, vx: float, vy: float, wz: float) -> None:
        """Send clamped body-frame velocity command."""
        payload = {
            "vx": round(clamp(float(vx), -0.50, 0.50), 4),
            "vy": round(clamp(float(vy), -0.25, 0.25), 4),
            "wz": round(clamp(float(wz), -1.20, 1.20), 4),
        }
        try:
            self._req("POST", "/cmd_vel", payload)
        except Exception:
            pass

    def emergency_stop(self) -> None:
        """Send `/estop` and fallback zero velocity burst."""
        for _ in range(3):
            try:
                self._req("POST", "/estop", {"reason": "roboguide_sync_estop"})
                break
            except Exception:
                pass
            try:
                self._req("POST", "/cmd_vel", {"vx": 0.0, "vy": 0.0, "wz": 0.0})
            except Exception:
                pass

    def safe_park_wait(self, settle_s: float = 1.8) -> None:
        """Stop chassis and wait cleanly for `settle_s` seconds."""
        self.emergency_stop()
        time.sleep(max(0.0, settle_s))

    def check_ready(self, operation: str) -> tuple[bool, str]:
        """Verify live pose freshness, sensor health, and standing posture."""
        pose = self.read_pose()
        if pose is None:
            return False, self._last_sensors.detail
        s = self._last_sensors
        if not s.healthy:
            return False, s.detail
        if s.posture != "standing":
            return False, f"chassis posture is {s.posture!r}, expected standing"
        return True, f"http-chassis ready for {operation}"
