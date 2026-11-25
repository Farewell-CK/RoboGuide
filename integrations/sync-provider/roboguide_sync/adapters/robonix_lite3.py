"""Robonix Lite3 hardware adapter reusing the F6-B+ field-verified ROS 2 Zenoh bridge."""

from __future__ import annotations

import base64
import json
import math
import subprocess
import threading
import time
import urllib.request
from typing import Any

from ..track import clamp, wrap_angle
from .base import RobotMotionAdapter, RobotPose, SensorSnapshot

# Embedded persistent ROS 2 bridge executed inside the Robonix ROS container ONLY when active.
_ROS_BRIDGE_SCRIPT = r"""
import json, math, struct, sys, threading, time
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from sensor_msgs.msg import JointState, LaserScan, PointCloud2
from tf2_msgs.msg import TFMessage

KNEE_STANDING_THRESHOLD = -2.10

def q_to_yaw(q):
    return math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))

class SyncRosBridge(Node):
    def __init__(self):
        super().__init__("rg_sync_ros_bridge")
        self.lock = threading.Lock()
        self.odom = None
        self.tf_map_odom = None
        self.posture = "unknown"
        self.scan_front_min = None
        self.scan_ts = 0.0
        self.cloud_front_min = None
        self.cloud_ts = 0.0
        self.cmd_vx = 0.0
        self.cmd_vy = 0.0
        self.cmd_wz = 0.0
        self.cmd_ts = 0.0
        self.estop = True
        self.drive_active = False

        qos = qos_profile_sensor_data
        self.pub_vel = self.create_publisher(Twist, "/cmd_vel", 10)
        self.create_subscription(Odometry, "/odom", self._on_odom, 50)
        self.create_subscription(TFMessage, "/tf", self._on_tf, 50)
        self.create_subscription(JointState, "/joint_states", self._on_joints, 20)
        self.create_subscription(LaserScan, "/scanner/scan", self._on_scan, qos)
        self.create_subscription(LaserScan, "/scan", self._on_scan, qos)
        self.create_subscription(PointCloud2, "/livox/lidar", self._on_cloud, qos)
        self.create_timer(0.05, self._on_tick_20hz)

    def _on_odom(self, msg):
        p = msg.pose.pose.position
        yaw = q_to_yaw(msg.pose.pose.orientation)
        tw = msg.twist.twist
        with self.lock:
            self.odom = (time.monotonic(), float(p.x), float(p.y), float(yaw),
                         float(tw.linear.x), float(tw.linear.y), float(tw.angular.z))

    def _on_tf(self, msg):
        now = time.monotonic()
        for tr in msg.transforms:
            if tr.header.frame_id.lstrip("/") == "map" and tr.child_frame_id.lstrip("/") == "odom":
                t = tr.transform.translation
                yaw = q_to_yaw(tr.transform.rotation)
                with self.lock:
                    self.tf_map_odom = (now, float(t.x), float(t.y), float(yaw))

    def _on_joints(self, msg):
        pos = list(msg.position)
        names = list(msg.name)
        if names and len(names) == len(pos):
            knees = [float(p) for n, p in zip(names, pos) if n.endswith("_Joint_2")]
        elif len(pos) >= 12:
            knees = [float(pos[i]) for i in (2, 5, 8, 11)]
        else:
            knees = []
        if knees:
            mk = sum(knees) / len(knees)
            with self.lock:
                self.posture = "standing" if mk > KNEE_STANDING_THRESHOLD else "down"

    def _on_scan(self, msg):
        ang = msg.angle_min
        inc = msg.angle_increment
        rmin_cfg = max(0.22, float(msg.range_min))
        rmax_cfg = min(3.0, float(msg.range_max))
        hits = []
        for r in msg.ranges:
            if abs(ang) <= 0.6109 and math.isfinite(r) and rmin_cfg < r < rmax_cfg:
                hits.append(float(r))
            ang += inc
        front = None
        if len(hits) >= 3:
            hits.sort()
            front = hits[2]
        with self.lock:
            self.scan_front_min = front
            self.scan_ts = time.monotonic()

    def _on_cloud(self, msg):
        if not msg.data or msg.point_step < 12:
            return
        x_off = y_off = z_off = None
        for f in msg.fields:
            if f.name == "x": x_off = f.offset
            elif f.name == "y": y_off = f.offset
            elif f.name == "z": z_off = f.offset
        if x_off is None or y_off is None or z_off is None:
            return
        step = msg.point_step
        buf = memoryview(msg.data)
        n = len(buf) // step
        hits = []
        for i in range(0, n, 8):
            base = i * step
            x = struct.unpack_from("<f", buf, base + x_off)[0]
            y = struct.unpack_from("<f", buf, base + y_off)[0]
            z = struct.unpack_from("<f", buf, base + z_off)[0]
            if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
                continue
            if 0.22 < x < 2.0 and 0.08 <= z <= 0.85 and abs(y) <= 0.70 * x:
                r = math.hypot(x, y)
                if r > 0.22:
                    hits.append(r)
        front = None
        if len(hits) >= 3:
            hits.sort()
            front = hits[2]
        with self.lock:
            self.cloud_front_min = front
            self.cloud_ts = time.monotonic()

    def set_cmd(self, vx, vy, wz, estop=False):
        with self.lock:
            was_active = self.drive_active
            self.estop = bool(estop)
            if self.estop:
                self.cmd_vx = self.cmd_vy = self.cmd_wz = 0.0
                self.drive_active = False
            else:
                self.cmd_vx = max(-0.25, min(0.25, float(vx)))
                self.cmd_vy = max(-0.10, min(0.10, float(vy)))
                self.cmd_wz = max(-0.65, min(0.65, float(wz)))
                self.drive_active = True
            self.cmd_ts = time.monotonic()
        if estop and was_active:
            self.publish_zero_burst()

    def publish_zero_burst(self):
        z = Twist()
        for _ in range(8):
            self.pub_vel.publish(z)
            time.sleep(0.015)

    def _on_tick_20hz(self):
        now = time.monotonic()
        tw_to_pub = None
        with self.lock:
            if self.drive_active:
                if (now - self.cmd_ts) > 0.30 or self.estop:
                    tw_to_pub = Twist()
                    self.drive_active = False
                    self.estop = True
                else:
                    tw = Twist()
                    tw.linear.x = self.cmd_vx
                    tw.linear.y = self.cmd_vy
                    tw.angular.z = self.cmd_wz
                    tw_to_pub = tw
            odom_age_ms = (
                round((now - self.odom[0]) * 1000.0, 1) if self.odom else None
            )
            tf_age_ms = (
                round((now - self.tf_map_odom[0]) * 1000.0, 1)
                if self.tf_map_odom
                else None
            )
            snap = {
                "odom": self.odom[1:] if self.odom else None,
                "odom_age_ms": odom_age_ms,
                "tf_map_odom": self.tf_map_odom[1:] if self.tf_map_odom else None,
                "tf_age_ms": tf_age_ms,
                "posture": self.posture,
                "scan_front_min": self.scan_front_min if (now - self.scan_ts) < 1.0 else None,
                "cloud_front_min": self.cloud_front_min if (now - self.cloud_ts) < 1.0 else None,
            }
        if tw_to_pub is not None:
            self.pub_vel.publish(tw_to_pub)
        sys.stdout.write("ROS_OBS " + json.dumps(snap) + "\n")
        sys.stdout.flush()

def stdin_loop(node):
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
        except Exception:
            continue
        if cmd.get("quit"):
            node.set_cmd(0.0, 0.0, 0.0, estop=True)
            rclpy.shutdown()
            return
        node.set_cmd(
            cmd.get("vx", 0.0),
            cmd.get("vy", 0.0),
            cmd.get("wz", 0.0),
            cmd.get("estop", False),
        )

def main():
    rclpy.init()
    node = SyncRosBridge()
    threading.Thread(target=stdin_loop, args=(node,), daemon=True).start()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            with node.lock:
                was_active = node.drive_active
                node.drive_active = False
            if was_active:
                node.publish_zero_burst()
            node.destroy_node()
        except Exception:
            pass
        if rclpy.ok():
            rclpy.shutdown()

if __name__ == "__main__":
    main()
"""


class MapOdomFuser:
    """Fuses 30-50 Hz `/odom`, `/tf`, and 5 Hz Mapping anchor into 20 Hz map pose."""

    def __init__(self, *, max_pose_age_s: float = 0.8, max_tf_age_s: float = 5.0) -> None:
        """Initialize the pose fuser with staleness ceilings."""
        self._lock = threading.Lock()
        self._max_pose_age_s = float(max_pose_age_s)
        self._max_tf_age_s = float(max_tf_age_s)
        self._t_map_odom: tuple[float, float, float] | None = None
        self._tf_mono: float | None = None
        self._odom: tuple[float, float, float, float, float, float] | None = None
        self._odom_mono: float | None = None
        self._anchor: tuple[float, float, float] | None = None
        self._anchor_mono: float | None = None

    def update_odom(
        self, odom: tuple[float, float, float, float, float, float], now_mono: float | None = None
    ) -> None:
        """Record latest odometry sample and capture timestamp."""
        ts = time.monotonic() if now_mono is None else float(now_mono)
        with self._lock:
            self._odom = odom
            self._odom_mono = ts

    def update_tf_map_odom(
        self, tf_tup: tuple[float, float, float], now_mono: float | None = None
    ) -> None:
        """Record latest `map->odom` transform and capture timestamp."""
        ts = time.monotonic() if now_mono is None else float(now_mono)
        with self._lock:
            self._t_map_odom = tf_tup
            self._tf_mono = ts

    def update_map_pose_anchor(
        self, mx: float, my: float, myaw: float, now_mono: float | None = None
    ) -> None:
        """Update 5 Hz Mapping anchor and derive `map->odom` offset."""
        ts = time.monotonic() if now_mono is None else float(now_mono)
        with self._lock:
            self._anchor = (mx, my, myaw)
            self._anchor_mono = ts
            if self._odom is not None:
                ox, oy, oyaw = self._odom[0], self._odom[1], self._odom[2]
                tyaw = wrap_angle(myaw - oyaw)
                c, s = math.cos(tyaw), math.sin(tyaw)
                self._t_map_odom = (mx - (c * ox - s * oy), my - (s * ox + c * oy), tyaw)
                self._tf_mono = ts

    def get_map_pose(self, now_mono: float | None = None) -> RobotPose | None:
        """Return fused map pose or `None` when odom/TF/anchor are stale."""
        now = time.monotonic() if now_mono is None else float(now_mono)
        with self._lock:
            if (
                self._t_map_odom is not None
                and self._tf_mono is not None
                and (now - self._tf_mono) <= self._max_tf_age_s
                and self._odom is not None
                and self._odom_mono is not None
                and (now - self._odom_mono) <= self._max_pose_age_s
            ):
                tx, ty, tyaw = self._t_map_odom
                ox, oy, oyaw, vx, vy, wz = self._odom
                c, s = math.cos(tyaw), math.sin(tyaw)
                return RobotPose(
                    x=tx + c * ox - s * oy,
                    y=ty + s * ox + c * oy,
                    yaw=wrap_angle(tyaw + oyaw),
                    vx=vx,
                    vy=vy,
                    wz=wz,
                    stamp_mono=self._odom_mono,
                )
            if (
                self._anchor is not None
                and self._anchor_mono is not None
                and (now - self._anchor_mono) <= self._max_pose_age_s
            ):
                return RobotPose(
                    x=self._anchor[0],
                    y=self._anchor[1],
                    yaw=self._anchor[2],
                    stamp_mono=self._anchor_mono,
                )
            return None


class RobonixLite3Adapter(RobotMotionAdapter):
    """Hardware adapter for DeepRobotics Lite3 running Robonix + Zenoh ROS 2."""

    adapter_kind = "robonix-lite3"

    def __init__(
        self,
        *,
        container_name: str = "robonix_lite3_ros",
        mapping_state_url: str = "http://127.0.0.1:8092/api/state",
        expected_map_id: str = "1101_visual_dog2",
    ) -> None:
        """Initialize Lite3 ROS bridge and Mapping anchor endpoints."""
        self.container_name = container_name
        self.mapping_state_url = mapping_state_url
        self.expected_map_id = expected_map_id
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._fuser = MapOdomFuser()
        self._lock = threading.Lock()
        self._tx_lock = threading.Lock()
        self._posture = "unknown"
        self._lidar_front_min: float | None = None
        self._map_mode = "unknown"
        self._map_id = ""
        self._stop_ev = threading.Event()
        self._ros_proc: subprocess.Popen[str] | None = None

    def start(self) -> None:
        """Launch embedded ROS 2 bridge subprocess and anchor poller."""
        if self._ros_proc is not None:
            return
        self._stop_ev.clear()
        b64 = base64.b64encode(_ROS_BRIDGE_SCRIPT.encode("utf-8")).decode("ascii")
        py_exec = (
            "source /opt/ros/humble/setup.bash >/dev/null && "
            f"exec python3 -B -u -c \"import base64; exec(base64.b64decode('{b64}').decode())\""
        )
        cmd = [
            "docker",
            "exec",
            "-i",
            "-e",
            "RMW_IMPLEMENTATION=rmw_zenoh_cpp",
            "-e",
            "ROS_DOMAIN_ID=0",
            self.container_name,
            "bash",
            "-c",
            py_exec,
        ]
        self._ros_proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=self._ros_reader, daemon=True).start()
        threading.Thread(target=self._anchor_poller, daemon=True).start()

    def _ingest_ros_obs(self, obs: dict[str, Any], *, now_mono: float | None = None) -> None:
        """Ingest one `ROS_OBS` sample, backdating capture times by reported age."""
        now = time.monotonic() if now_mono is None else float(now_mono)
        odom = obs.get("odom")
        if odom:
            age_ms = obs.get("odom_age_ms")
            odom_age_s = (
                max(0.0, float(age_ms) / 1000.0)
                if not isinstance(age_ms, bool) and isinstance(age_ms, (int, float))
                else 0.0
            )
            self._fuser.update_odom(tuple(odom), now_mono=now - odom_age_s)
        tf_mo = obs.get("tf_map_odom")
        if tf_mo:
            tf_age_ms = obs.get("tf_age_ms")
            tf_age_s = (
                max(0.0, float(tf_age_ms) / 1000.0)
                if not isinstance(tf_age_ms, bool) and isinstance(tf_age_ms, (int, float))
                else 0.0
            )
            self._fuser.update_tf_map_odom(tuple(tf_mo), now_mono=now - tf_age_s)
        lmins = [
            v for v in (obs.get("scan_front_min"), obs.get("cloud_front_min")) if v is not None
        ]
        with self._lock:
            self._posture = str(obs.get("posture", "unknown"))
            self._lidar_front_min = min(lmins) if lmins else None

    def _ros_reader(self) -> None:
        """Read JSON observations from the container ROS bridge stdout."""
        assert self._ros_proc and self._ros_proc.stdout
        for line in self._ros_proc.stdout:
            if self._stop_ev.is_set():
                break
            if not line.startswith("ROS_OBS "):
                continue
            try:
                obs = json.loads(line[8:])
            except Exception:
                continue
            self._ingest_ros_obs(obs)

    def _anchor_poller(self) -> None:
        """Poll local Mapping HTTP state for 5 Hz global pose anchors."""
        while not self._stop_ev.is_set():
            try:
                with self._opener.open(self.mapping_state_url, timeout=0.4) as r:
                    d = json.loads(r.read().decode("utf-8"))
                p = d.get("pose") or (d.get("mapping") or {}).get("pose")
                if isinstance(p, dict) and p.get("x") is not None and p.get("y") is not None:
                    self._fuser.update_map_pose_anchor(
                        float(p["x"]),
                        float(p["y"]),
                        float(p.get("theta", p.get("yaw", 0.0)) or 0.0),
                    )
                with self._lock:
                    self._map_mode = str(d.get("mode", "unknown"))
                    self._map_id = str(d.get("map_id", ""))
            except Exception:
                pass
            time.sleep(0.2)

    def close(self) -> None:
        """Stop chassis and shut down the ROS 2 bridge subprocess."""
        self.emergency_stop()
        self._stop_ev.set()
        if self._ros_proc is not None:
            try:
                with self._tx_lock:
                    if self._ros_proc.stdin and self._ros_proc.poll() is None:
                        self._ros_proc.stdin.write(json.dumps({"quit": True}) + "\n")
                        self._ros_proc.stdin.flush()
                self._ros_proc.wait(timeout=3.0)
            except Exception:
                self._ros_proc.kill()
            self._ros_proc = None

    def read_pose(self) -> RobotPose | None:
        """Return fused SE(2) map pose or `None` if unavailable/stale."""
        return self._fuser.get_map_pose()

    def read_sensors(self) -> SensorSnapshot:
        """Return bridge health, joint posture, and minimum front LiDAR clearance."""
        with self._lock:
            healthy = (
                self._ros_proc is not None
                and self._ros_proc.poll() is None
                and self._posture != "unknown"
            )
            return SensorSnapshot(
                posture=self._posture,
                lidar_front_min_m=self._lidar_front_min,
                healthy=healthy,
                detail=f"map={self._map_id}({self._map_mode}), posture={self._posture}",
            )

    def _send_raw(self, vx: float, vy: float, wz: float, estop: bool) -> None:
        """Write one clamped velocity or estop command to the bridge stdin."""
        with self._tx_lock:
            if self._ros_proc and self._ros_proc.stdin and self._ros_proc.poll() is None:
                try:
                    self._ros_proc.stdin.write(
                        json.dumps(
                            {
                                "vx": clamp(vx, -0.25, 0.25),
                                "vy": clamp(vy, -0.10, 0.10),
                                "wz": clamp(wz, -0.65, 0.65),
                                "estop": estop,
                            }
                        )
                        + "\n"
                    )
                    self._ros_proc.stdin.flush()
                except Exception:
                    pass

    def send_velocity(self, vx: float, vy: float, wz: float) -> None:
        """Send clamped body-frame velocity to the Lite3 bridge."""
        self._send_raw(vx, vy, wz, estop=False)

    def emergency_stop(self) -> None:
        """Immediately halt the Lite3 chassis via the bridge estop gate."""
        self._send_raw(0.0, 0.0, 0.0, estop=True)

    def safe_park_wait(self, settle_s: float = 1.8) -> None:
        """Stop chassis and enforce minimum 1.8 s physical settle window."""
        self.emergency_stop()
        time.sleep(max(1.8, settle_s))

    def check_ready(self, operation: str) -> tuple[bool, str]:
        """Verify localization mode, map ID, pose freshness, and standing posture."""
        pose = self.read_pose()
        sens = self.read_sensors()
        if not sens.healthy:
            return False, sens.detail
        if pose is None:
            return False, "localization pose unavailable"
        if self._map_mode != "localization":
            return False, f"mapping mode is {self._map_mode!r}, expected localization"
        if self.expected_map_id and self._map_id != self.expected_map_id:
            return False, f"map_id {self._map_id!r} != {self.expected_map_id!r}"
        if sens.posture != "standing":
            return False, f"posture is {sens.posture!r}, expected standing"
        return True, f"robonix-lite3 ready for {operation}"
