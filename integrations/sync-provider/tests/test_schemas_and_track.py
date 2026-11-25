"""L0 tests for strict schemas, ParameterizedTrack geometry, and HttpChassisAdapter."""

from __future__ import annotations

import json
import math
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from roboguide_sync.adapters import HttpChassisAdapter, SimAdapter
from roboguide_sync.adapters.robonix_lite3 import (
    _ROS_BRIDGE_SCRIPT,
    MapOdomFuser,
    RobonixLite3Adapter,
)
from roboguide_sync.schemas import (
    CHOREOGRAPHY_SCHEMA,
    OP_CHOREOGRAPHY_PLAY,
    OP_SYNC_FOLLOW,
    SYNC_PHASE_SCHEMA,
    SYNC_TRACK_SCHEMA,
    SchemaError,
    extract_execution_intent,
    parse_choreography_spec,
    parse_neighbors,
    parse_sync_params,
    parse_track_spec,
    validate_sync_phase_packet,
)
from roboguide_sync.track import ParameterizedTrack, wrap_angle


def test_extract_execution_intent_supports_nested_and_flat_canonical_shapes() -> None:
    """Verify nested and flat RoboGuide invocation payload parsing."""
    nested = {
        "attempt_id": "att-1",
        "execution_generation": 2,
        "budget_ms": 120000,
        "invocation": {
            "mission_id": "m1",
            "task_id": "t1",
            "group_id": "g1",
            "role_id": "dog1",
            "intent": {
                "operation": {"namespace": "mobility", "name": "sync-follow", "version": "v1"},
                "objective": "walk abreast",
                "parameters": {"context_id": "ctx-1"},
            },
        },
    }
    req1 = extract_execution_intent(nested)
    assert req1.operation == OP_SYNC_FOLLOW
    assert req1.execution_generation == 2
    assert req1.group_id == "g1"
    assert req1.role_id == "dog1"

    flat = {
        "attempt_id": "att-2",
        "invocation": {
            "operation": OP_CHOREOGRAPHY_PLAY,
            "objective": "dance",
            "parameters": {"mission_id": "m2", "group_id": "g2", "t0_utc_ms": 1700000000000},
        },
    }
    req2 = extract_execution_intent(flat)
    assert req2.operation == OP_CHOREOGRAPHY_PLAY
    assert req2.group_id == "g2"

    with pytest.raises(SchemaError):
        extract_execution_intent(
            {
                "attempt_id": "att-bad",
                "invocation": {"operation": "unknown.op@v1", "objective": "bad", "parameters": {}},
            }
        )


def test_parse_neighbors_normalizes_weights_and_rejects_bad_entries() -> None:
    """Verify neighbor weight normalization and validation errors."""
    peers = parse_neighbors(
        [
            {"role_id": "r2", "endpoint": "127.0.0.1:18202", "weight": 3.0},
            {"role_id": "r3", "endpoint": "[::1]:18203", "weight": 1.0},
        ]
    )
    assert len(peers) == 2
    assert math.isclose(peers[0].weight, 0.75, rel_tol=1e-9)
    assert math.isclose(peers[1].weight, 0.25, rel_tol=1e-9)
    assert peers[1].host == "::1"

    with pytest.raises(SchemaError):
        parse_neighbors(
            [
                {"role_id": "dup", "endpoint": "127.0.0.1:18201"},
                {"role_id": "dup", "endpoint": "127.0.0.1:18202"},
            ]
        )
    with pytest.raises(SchemaError):
        parse_neighbors([{"role_id": "r1", "endpoint": "not-a-host-port"}])


def test_track_spec_and_parameterized_track_geometry() -> None:
    """Verify ParameterizedTrack evaluation, projection signs, barriers, and arc math."""
    with pytest.raises(SchemaError):
        parse_track_spec(
            {
                "schema_version": SYNC_TRACK_SCHEMA,
                "v_max": 0.90,  # exceeds HARD_MAX_LINEAR_SPEED_MPS = 0.50
                "segments": [{"kind": "line", "start": [0, 0], "end": [1, 0]}],
            }
        )

    spec = parse_track_spec(
        {
            "schema_version": SYNC_TRACK_SCHEMA,
            "v_ref": 0.20,
            "v_max": 0.30,
            "group_roles": ["r1", "r2"],
            "segments": [
                {"kind": "line", "start": [0.0, 1.0], "end": [2.0, 1.0], "label": "out"},
                {
                    "kind": "turn_in_place",
                    "pose": [2.0, 1.0, 0.0],
                    "delta_yaw": math.pi,
                    "equiv_length_m": 1.0,
                    "barrier_before": True,
                    "label": "turn",
                },
                {
                    "kind": "line",
                    "start": [2.0, 1.0],
                    "end": [0.0, 1.0],
                    "barrier_before": True,
                    "label": "ret",
                },
            ],
        }
    )
    track = ParameterizedTrack(spec)
    assert math.isclose(track.total_length_m, 5.0, abs_tol=1e-6)
    barriers = track.barrier_boundaries()
    assert [b[0] for b in barriers] == ["START", "SEG_1_turn", "SEG_2_ret"]
    assert math.isclose(barriers[1][1], 0.4, abs_tol=1e-6)
    assert math.isclose(barriers[2][1], 0.6, abs_tol=1e-6)

    s_mid_out = track.evaluate(0.2)
    assert math.isclose(s_mid_out.x, 1.0, abs_tol=1e-5)
    assert math.isclose(s_mid_out.y, 1.0, abs_tol=1e-5)
    assert math.isclose(s_mid_out.yaw, 0.0, abs_tol=1e-5)

    # Projection sign check: offset +0.15 m to LEFT of +x track gives e_lat_body = +0.15
    g_meas, e_lat, e_yaw = track.project_in_segment(1.0, 1.15, 0.1, 0)
    assert math.isclose(g_meas, 0.2, abs_tol=1e-6)
    assert math.isclose(e_lat, 0.15, abs_tol=1e-6)
    assert math.isclose(e_yaw, 0.1, abs_tol=1e-6)

    # Arc evaluation & projection check
    arc_spec = parse_track_spec(
        {
            "schema_version": SYNC_TRACK_SCHEMA,
            "v_ref": 0.20,
            "v_max": 0.30,
            "segments": [
                {
                    "kind": "arc",
                    "center": [0.0, 0.0],
                    "radius_m": 1.0,
                    "start_angle": -math.pi / 2.0,
                    "delta_angle": math.pi / 2.0,
                }
            ],
        }
    )
    arc_track = ParameterizedTrack(arc_spec)
    arc_end = arc_track.evaluate(1.0)
    assert math.isclose(arc_end.x, 1.0, abs_tol=1e-5)
    assert math.isclose(arc_end.y, 0.0, abs_tol=1e-5)
    assert math.isclose(wrap_angle(arc_end.yaw - math.pi / 2.0), 0.0, abs_tol=1e-5)


def test_choreography_sync_params_and_phase_packet_validation() -> None:
    """Verify strict schema validation for sync params, choreography, tracks, and UDP packets."""
    sp = parse_sync_params(
        {"k_sync": 1.2, "tier1_hold_ms": 300.0, "tier2_timeout_ms": 900.0, "done_hold_s": 1.2}
    )
    assert sp.k_sync == 1.2
    assert sp.done_hold_s == pytest.approx(1.2)
    with pytest.raises(SchemaError):
        parse_sync_params({"done_hold_s": -0.1})
    with pytest.raises(SchemaError):
        parse_sync_params({"done_hold_s": 31.0})
    with pytest.raises(SchemaError):
        parse_sync_params({"tier1_hold_ms": 500.0, "tier2_timeout_ms": 200.0})
    with pytest.raises(SchemaError):
        parse_sync_params({"midcut_gamma": 1.0})

    with pytest.raises(SchemaError):
        parse_choreography_spec(
            {
                "schema_version": CHOREOGRAPHY_SCHEMA,
                "keyframes": [
                    {"t_s": 1.0, "x": 0.0, "y": 0.0, "yaw": 0.0},
                    {"t_s": 1.0, "x": 1.0, "y": 0.0, "yaw": 0.0},  # non-strictly-increasing
                ],
            }
        )
    with pytest.raises(SchemaError):
        parse_choreography_spec(
            {
                "schema_version": CHOREOGRAPHY_SCHEMA,
                "pack_sha256": "deadbeef",  # not 64 hex chars
                "keyframes": [
                    {"t_s": 0.0, "x": 0.0, "y": 0.0, "yaw": 0.0},
                    {"t_s": 2.0, "x": 1.0, "y": 0.0, "yaw": 0.0},
                ],
            }
        )

    pkt = validate_sync_phase_packet(
        {
            "schema": SYNC_PHASE_SCHEMA,
            "group_id": "g1",
            "epoch": 1,
            "run_id": "r1",
            "node_id": "n1",
            "role_id": "dog1",
            "seq": 5,
            "phase_id": "FOLLOW",
            "gamma": 0.42,
            "gamma_dot": 0.09,
            "barrier_state": "NONE",
            "wall_ms": 123456,
        }
    )
    assert pkt["phase_id"] == "FOLLOW"
    with pytest.raises(SchemaError):
        validate_sync_phase_packet({"schema": "wrong"})
    bad_ttls: tuple[object, ...] = ("bad", {}, True)
    for bad_ttl in bad_ttls:
        with pytest.raises(SchemaError):
            validate_sync_phase_packet(
                {
                    "schema": SYNC_PHASE_SCHEMA,
                    "group_id": "g1",
                    "epoch": 1,
                    "run_id": "r1",
                    "node_id": "n1",
                    "role_id": "dog1",
                    "seq": 5,
                    "phase_id": "FOLLOW",
                    "hold_ttl": bad_ttl,
                    "wall_ms": 123456,
                }
            )
    with pytest.raises(SchemaError):
        parse_track_spec({"schema_version": SYNC_TRACK_SCHEMA, "segments": [{"kind": "line"}]})
    with pytest.raises(SchemaError):
        parse_track_spec(
            {
                "schema_version": SYNC_TRACK_SCHEMA,
                "segments": [
                    {
                        "kind": "turn_in_place",
                        "pose": [0.0, 0.0],
                        "delta_yaw": 1.0,
                        "equiv_length_m": -1.0,
                    }
                ],
            }
        )
    with pytest.raises(SchemaError):
        validate_sync_phase_packet(
            {
                "schema": SYNC_PHASE_SCHEMA,
                "group_id": "g1",
                "epoch": 1,
                "run_id": "r1",
                "node_id": "n1",
                "role_id": "dog1",
                "seq": 5,
                "phase_id": "ESTOP",
                "estop_ttl": "invalid",
                "wall_ms": 123456,
            }
        )


def test_http_chassis_adapter_roundtrip_and_stale_pose_fence() -> None:
    """Verify HttpChassisAdapter telemetry roundtrip, velocity commands, and fail-closed checks."""
    cmds: list[dict[str, Any]] = []
    state: dict[str, Any] = {
        "pose": {"x": 1.25, "y": -0.5, "yaw": 0.25, "age_ms": 15.0, "lidar_front_min_m": 2.4},
        "cmds": cmds,
        "estops": 0,
    }
    lock = threading.Lock()

    class _ChassisHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003
            """Suppress test HTTP server stderr logging."""

        def _reply(self, payload: object) -> None:
            """Write a JSON OK response."""
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            """Serve mock `/pose` telemetry."""
            with lock:
                if self.path == "/pose":
                    self._reply(state["pose"])
                else:
                    self._reply({"healthy": True, "posture": "standing"})

        def do_POST(self) -> None:  # noqa: N802
            """Record mock `/cmd_vel` and `/estop` calls."""
            ln = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(ln).decode("utf-8")) if ln > 0 else {}
            with lock:
                if self.path == "/cmd_vel":
                    cmds.append(body)
                elif self.path == "/estop":
                    state["estops"] = int(state["estops"]) + 1
            self._reply({"ok": True})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _ChassisHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        port = srv.server_address[1]
        adapter = HttpChassisAdapter(f"http://127.0.0.1:{port}", max_pose_age_ms=200.0)
        pose = adapter.read_pose()
        assert pose is not None
        assert math.isclose(pose.x, 1.25)
        assert math.isclose(pose.y, -0.5)
        assert adapter.read_sensors().lidar_front_min_m == pytest.approx(2.4)

        adapter.send_velocity(0.22, 0.0, 0.1)
        assert len(cmds) == 1
        assert cmds[0]["vx"] == pytest.approx(0.22)

        # Stale & malformed pose fields trigger Fail-Closed None without raising
        with lock:
            state["pose"] = {"x": 1.25, "y": -0.5, "yaw": 0.25, "age_ms": 950.0}
        assert adapter.read_pose() is None
        assert adapter.check_ready(OP_SYNC_FOLLOW)[0] is False

        with lock:
            state["pose"] = {"x": 1.0, "y": 0.0, "yaw": 0.0, "age_ms": "bad"}
        assert adapter.read_pose() is None

        with lock:
            state["pose"] = {"x": 1.0, "y": 0.0, "yaw": 0.0, "lidar_front_min_m": float("nan")}
        assert adapter.read_pose() is None

        adapter.emergency_stop()
        assert int(state["estops"]) >= 1
    finally:
        srv.shutdown()
        srv.server_close()


def test_map_odom_fuser_se2_fusion_staleness_and_idle_cmd_vel_gate() -> None:
    """Verify MapOdomFuser SE(2) math, odom/TF staleness gating, and ROS bridge idle safety."""
    fuser = MapOdomFuser(max_pose_age_s=0.8)
    assert fuser.get_map_pose(now_mono=10.0) is None

    # Rotate 90 deg map<-odom around origin: odom=(1, 0, 0) anchored at map=(2, 3, pi/2)
    fuser.update_odom((1.0, 0.0, 0.0, 0.1, 0.0, 0.0), now_mono=10.0)
    fuser.update_map_pose_anchor(2.0, 3.0, math.pi / 2.0, now_mono=10.0)
    fuser.update_odom((2.0, 0.0, 0.0, 0.2, 0.0, 0.0), now_mono=10.2)

    pose = fuser.get_map_pose(now_mono=10.5)
    assert pose is not None
    assert pose.stamp_mono == pytest.approx(10.2)
    assert math.isclose(pose.x, 2.0, abs_tol=1e-6)
    assert math.isclose(pose.y, 4.0, abs_tol=1e-6)
    assert math.isclose(wrap_angle(pose.yaw - math.pi / 2.0), 0.0, abs_tol=1e-6)

    # Beyond max_pose_age_s (11.1 - 10.2 = 0.9 > 0.8), Fail-Closed returns None
    assert fuser.get_map_pose(now_mono=11.1) is None

    # Frozen TF beyond max_tf_age_s fails closed even if /odom still ticks
    fuser2 = MapOdomFuser(max_pose_age_s=0.8, max_tf_age_s=5.0)
    fuser2.update_tf_map_odom((0.0, 0.0, 0.0), now_mono=1.0)
    fuser2.update_odom((0.5, 0.0, 0.0, 0.0, 0.0, 0.0), now_mono=7.0)
    assert fuser2.get_map_pose(now_mono=7.1) is None

    # _ingest_ros_obs backdates age_ms so cached stale odom inside container fails closed
    lit_ad = RobonixLite3Adapter()
    lit_ad._ingest_ros_obs(
        {
            "odom": [0.1, 0.2, 0.0, 0.0, 0.0, 0.0],
            "odom_age_ms": 1200.0,
            "tf_map_odom": [0.0, 0.0, 0.0],
            "tf_age_ms": 50.0,
            "posture": "standing",
        },
        now_mono=50.0,
    )
    assert lit_ad._fuser.get_map_pose(now_mono=50.0) is None

    # Embedded Lite3 ROS2 bridge parses cleanly and gates cmd_vel + finally burst on drive_active.
    import ast

    ast.parse(_ROS_BRIDGE_SCRIPT)
    assert "self.drive_active = False" in _ROS_BRIDGE_SCRIPT
    assert "if self.drive_active:" in _ROS_BRIDGE_SCRIPT
    assert "was_active = node.drive_active" in _ROS_BRIDGE_SCRIPT
    assert "if tw_to_pub is not None:" in _ROS_BRIDGE_SCRIPT


def test_engines_reverse_strafe_arc_and_budget_exceeded() -> None:
    """Verify reverse/strafe choreography feedforward, arc feedforward, and budget deadline."""
    from roboguide_sync.choreography_engine import ChoreographyEngine
    from roboguide_sync.clock_sync import FixedClockProbe
    from roboguide_sync.peer_manager import PeerChannelManager
    from roboguide_sync.schemas import ChoreographySpec, KeyframeSpec, SyncParams
    from roboguide_sync.sync_engine import SyncFollowEngine
    from roboguide_sync.transport import UdpHub

    # 1. Choreography reverse (-x) and strafe (+y) with yaw=0 uses signed body-frame feedforward
    ch_ad = SimAdapter(x0=0.0, y0=0.0, yaw0=0.0)
    ch_spec = ChoreographySpec(
        schema_version=CHOREOGRAPHY_SCHEMA,
        keyframes=(
            KeyframeSpec(t_s=0.0, x=0.0, y=0.0, yaw=0.0),
            KeyframeSpec(t_s=2.0, x=-0.40, y=0.0, yaw=0.0),
            KeyframeSpec(t_s=4.0, x=-0.40, y=0.30, yaw=0.0),
        ),
        v_max=0.35,
    )
    ch_eng = ChoreographyEngine(
        adapter=ch_ad,
        clock_probe=FixedClockProbe(offset_ms=0.0, synchronized=True),
        spec=ch_spec,
        t0_utc_ms=int(time.time() * 1000) + 100,
        max_clock_offset_ms=20.0,
        cancel_event=threading.Event(),
    )
    st_ch, detail_ch, m_ch, _ = ch_eng.run()
    assert st_ch == "SUCCEEDED", detail_ch
    assert m_ch["max_track_err_m"] < 0.08
    fp_ch = m_ch["final_pose"]
    assert fp_ch is not None and abs(fp_ch["x"] + 0.40) < 0.06 and abs(fp_ch["y"] - 0.30) < 0.06

    # 2. SyncFollowEngine arc segment + budget_ms hard deadline
    hub = UdpHub(bind_host="127.0.0.1", bind_port=0)
    pm = PeerChannelManager(hub, "n-arc")
    try:
        arc_spec = parse_track_spec(
            {
                "schema_version": SYNC_TRACK_SCHEMA,
                "v_ref": 0.30,
                "v_max": 0.40,
                "segments": [
                    {
                        "kind": "arc",
                        "center": [0.0, 0.0],
                        "radius_m": 0.8,
                        "start_angle": -math.pi / 2.0,
                        "delta_angle": math.pi / 2.0,
                    }
                ],
            }
        )
        arc_ad = SimAdapter(x0=0.0, y0=-0.8, yaw0=0.0)
        arc_eng = SyncFollowEngine(
            adapter=arc_ad,
            hub=hub,
            peer_manager=pm,
            node_id="n-arc",
            group_id="g-arc",
            context_id="ctx",
            role_id="r-arc",
            channel_instance_id="ch-arc",
            run_id="run-arc",
            epoch=1,
            track_spec=arc_spec,
            neighbors=(),
            sync_params=SyncParams(done_drain_s=0.1),
            cancel_event=threading.Event(),
            budget_ms=15000,
        )
        st_a, detail_a, m_a, _ = arc_eng.run()
        assert st_a == "SUCCEEDED", detail_a
        assert abs(m_a["final_pose"]["x"] - 0.8) < 0.12 and abs(m_a["final_pose"]["y"]) < 0.12

        # Budget deadline exceeded -> FENCED_STOP:budget_exceeded
        long_spec = parse_track_spec(
            {
                "schema_version": SYNC_TRACK_SCHEMA,
                "v_ref": 0.15,
                "v_max": 0.20,
                "segments": [{"kind": "line", "start": [0.0, 0.0], "end": [5.0, 0.0]}],
            }
        )
        b_ad = SimAdapter(x0=0.0, y0=0.0, yaw0=0.0)
        b_eng = SyncFollowEngine(
            adapter=b_ad,
            hub=hub,
            peer_manager=pm,
            node_id="n-bud",
            group_id="g-bud",
            context_id="ctx",
            role_id="r-bud",
            channel_instance_id="ch-bud",
            run_id="run-bud",
            epoch=1,
            track_spec=long_spec,
            neighbors=(),
            sync_params=SyncParams(skip_align=True),
            cancel_event=threading.Event(),
            budget_ms=200,
        )
        st_b, detail_b, _, _ = b_eng.run()
        assert st_b == "FAILED" and detail_b == "FENCED_STOP:budget_exceeded"
    finally:
        pm.close()
        hub.close()
