"""Closed-loop 20 Hz distributed progress consensus engine (`mobility.sync-follow@v1`)."""

from __future__ import annotations

import math
import threading
import time
from typing import Any

from .adapters.base import RobotMotionAdapter, RobotPose
from .peer_manager import PeerChannelManager
from .schemas import NeighborSpec, SyncParams, TrackSpec
from .track import ParameterizedTrack, clamp, rate_limit, wrap_angle
from .transport import PeerTransport, UdpHub


def compute_consensus_gamma_dot(
    gamma_self: float,
    neighbor_views: list[dict[str, Any]],
    *,
    gamma_ref: float,
    gamma_max: float,
    k_sync: float,
    lead_wait_gamma: float,
) -> tuple[float, float]:
    """Compute distributed consensus progress rate `dot_gamma_i` and neighbor residual."""
    active = [p for p in neighbor_views if p.get("gamma") is not None]
    if not active:
        return clamp(gamma_ref, 0.0, gamma_max), 0.0

    w_sum = 0.0
    dgamma = 0.0
    min_rel = float("inf")
    for p in active:
        w = max(0.0, float(p.get("weight", 1.0)))
        diff = float(p["gamma"]) - float(gamma_self)
        dgamma += w * diff
        w_sum += w
        if p.get("phase_id") != "DONE":
            min_rel = min(min_rel, diff)
    if w_sum > 0.0:
        dgamma /= w_sum

    if min_rel < -lead_wait_gamma:
        return 0.0, dgamma

    raw = gamma_ref + k_sync * dgamma
    return clamp(raw, 0.0, gamma_max), dgamma


class SyncFollowEngine:
    """Executes one `mobility.sync-follow@v1` run over a sparse neighbor graph."""

    def __init__(
        self,
        *,
        adapter: RobotMotionAdapter,
        hub: UdpHub,
        peer_manager: PeerChannelManager,
        node_id: str,
        group_id: str,
        context_id: str,
        role_id: str,
        channel_instance_id: str,
        run_id: str,
        epoch: int,
        track_spec: TrackSpec,
        neighbors: tuple[NeighborSpec, ...],
        sync_params: SyncParams,
        cancel_event: threading.Event,
        budget_ms: int = 300_000,
    ) -> None:
        """Initialize one synchronized path-following engine instance."""
        self.adapter = adapter
        self.hub = hub
        self.peer_manager = peer_manager
        self.node_id = node_id
        self.group_id = group_id
        self.context_id = context_id
        self.role_id = role_id
        self.channel_instance_id = channel_instance_id
        self.run_id = run_id
        self.epoch = epoch
        self.track = ParameterizedTrack(track_spec)
        self.spec = track_spec
        self.neighbors = neighbors
        self.params = sync_params
        self.cancel_event = cancel_event
        self.budget_ms = int(budget_ms)

    def _check_hw_fences(
        self,
        pose: RobotPose,
        e_lat_body: float,
        lidar_front: float | None,
        peer_snapshot: dict[str, Any],
        *,
        check_lateral: bool,
    ) -> str:
        """Evaluate local LiDAR, lateral deviation, and inter-robot separation fences."""
        if lidar_front is not None and lidar_front < self.spec.lidar_stop_m:
            return f"lidar_front_obstacle_{lidar_front:.3f}m"
        if check_lateral and abs(e_lat_body) > self.spec.lat_fence_m:
            return f"lateral_fence_dev_{abs(e_lat_body):.3f}m"
        if self.spec.min_sep_m > 0.0:
            for r_id, p in peer_snapshot["peers"].items():
                ph = p.get("pose_hint")
                if ph is not None:
                    d = math.hypot(pose.x - float(ph[0]), pose.y - float(ph[1]))
                    if d < self.spec.min_sep_m:
                        return f"min_separation_fence_{r_id}_d={d:.3f}m"
        return ""

    def run(self) -> tuple[str, str, dict[str, Any], bool]:
        """Run 20 Hz closed-loop sync control.

        Returns `(terminal_state, detail, metrics, safe_state_confirmed)`.
        """
        self.adapter.prepare_run()
        self.peer_manager.ensure_runtime_session(
            group_id=self.group_id,
            context_id=self.context_id,
            context_role_id=self.role_id,
            channel_instance_id=self.channel_instance_id,
        )

        # Immediately trip adapter on RX ESTOP (< 2 ms propagation!)
        transport = PeerTransport(
            self.hub,
            group_id=self.group_id,
            epoch=self.epoch,
            run_id=self.run_id,
            node_id=self.node_id,
            role_id=self.role_id,
            neighbors=self.neighbors,
            expected_barrier_roles=self.spec.group_roles,
            on_estop=lambda _orig, _reason: self.adapter.emergency_stop(),
        )

        # Track telemetry & metrics
        gamma_self = 0.0
        seg_idx = 0
        gamma_ref = self.spec.v_ref / self.track.total_length_m
        gamma_max = self.spec.v_max / self.track.total_length_m
        lead_wait_gamma = clamp(self.params.lead_wait_m / self.track.total_length_m, 0.004, 0.30)

        seg_barriers: dict[int, str] = {
            next_seg: stage_name
            for stage_name, _g, next_seg in self.track.barrier_boundaries()
            if stage_name != "START"
        }

        dt_ctrl = 1.0 / self.params.control_hz
        phase = "ALIGN"
        active_barrier_stage = "START"
        t_run0 = time.monotonic()
        t_phase0 = t_run0
        done_entered_mono: float | None = None
        done_hold_started_mono: float | None = None
        last_yaw_turn: float | None = None
        turn_accum = 0.0
        prev_vx = prev_vy = prev_wz = 0.0

        barrier_ts_ms: dict[str, int] = {}
        first_tier1_wall_ms: int | None = None
        cut_wall_ms: int | None = None
        estop_wall_ms: int | None = None
        fence_reason = ""
        final_state = "SUCCEEDED"
        safe_confirmed = True
        max_abs_dgamma = 0.0
        sum_sq_dgamma = 0.0
        n_follow_samples = 0
        min_sep_seen = 999.0
        last_pose: RobotPose | None = None

        # Wait briefly for initial pose (e.g. ROS bridge warm-up)
        t_pose_wait = time.monotonic()
        while time.monotonic() - t_pose_wait < 5.0 and not self.cancel_event.is_set():
            if self.adapter.read_pose() is not None:
                break
            time.sleep(0.05)

        next_tick = time.monotonic()
        try:
            while True:
                # 1. Immediate cancellation check
                if self.cancel_event.is_set():
                    self.adapter.emergency_stop()
                    transport.broadcast_estop("http_cancel", gamma_self)
                    self.peer_manager.mark_unready(
                        self.group_id, self.context_id, self.role_id, "http_cancel"
                    )
                    estop_wall_ms = int(time.time() * 1000)
                    final_state = "CANCELED"
                    fence_reason = "http_cancel"
                    break

                # 2. Immediate peer ESTOP check (sub-millisecond event wait / poll)
                if transport.estop_event.is_set():
                    self.adapter.emergency_stop()
                    snap_e = transport.get_neighbor_snapshot()
                    einfo = snap_e["estop_info"] or ("peer", "peer_estop")
                    self.peer_manager.mark_unready(
                        self.group_id, self.context_id, self.role_id, f"peer_estop:{einfo[1]}"
                    )
                    estop_wall_ms = int(time.time() * 1000)
                    final_state = "FAILED"
                    fence_reason = f"FENCED_STOP:peer_estop:{einfo[0]}:{einfo[1]}"
                    break

                now_m = time.monotonic()
                if now_m < next_tick:
                    transport.estop_event.wait(timeout=min(next_tick - now_m, 0.02))
                    continue
                next_tick += dt_ctrl
                if next_tick < now_m - 0.25:
                    next_tick = now_m + dt_ctrl

                if (now_m - t_run0) * 1000.0 > self.budget_ms:
                    self.adapter.emergency_stop()
                    transport.broadcast_estop("execution_budget_exceeded", gamma_self)
                    self.peer_manager.mark_unready(
                        self.group_id, self.context_id, self.role_id, "execution_budget_exceeded"
                    )
                    estop_wall_ms = int(time.time() * 1000)
                    final_state = "FAILED"
                    fence_reason = "FENCED_STOP:budget_exceeded"
                    break

                pose = self.adapter.read_pose()
                sensors = self.adapter.read_sensors()
                if pose is None:
                    self.adapter.emergency_stop()
                    transport.broadcast_estop("pose_unavailable", gamma_self)
                    self.peer_manager.mark_unready(
                        self.group_id, self.context_id, self.role_id, "pose_unavailable"
                    )
                    final_state = "FAILED"
                    fence_reason = "FENCED_STOP:pose_unavailable"
                    break
                last_pose = pose

                if not sensors.healthy and sensors.detail != "sim_startup_delay":
                    self.adapter.emergency_stop()
                    transport.broadcast_estop(f"sensor_unhealthy:{sensors.detail}", gamma_self)
                    self.peer_manager.mark_unready(
                        self.group_id, self.context_id, self.role_id, "sensor_unhealthy"
                    )
                    estop_wall_ms = int(time.time() * 1000)
                    final_state = "FAILED"
                    fence_reason = f"FENCED_STOP:sensor_unhealthy:{sensors.detail}"
                    break

                snap = transport.get_neighbor_snapshot(now_m)
                now_wall_ms = int(time.time() * 1000)
                pose_hint = [round(pose.x, 4), round(pose.y, 4), round(pose.yaw, 4)]

                # Track minimum inter-neighbor distance during active phases
                if phase in ("FOLLOW", "BARRIER") and active_barrier_stage != "START":
                    for p in snap["peers"].values():
                        ph = p.get("pose_hint")
                        if ph is not None:
                            min_sep_seen = min(
                                min_sep_seen,
                                math.hypot(pose.x - float(ph[0]), pose.y - float(ph[1])),
                            )

                # 3. Controlled mid-cut fault injection (test/experiment parity)
                if (
                    self.params.midcut_gamma is not None
                    and phase == "FOLLOW"
                    and gamma_self >= self.params.midcut_gamma
                    and cut_wall_ms is None
                ):
                    cut_wall_ms = now_wall_ms
                    transport.suppress_tx()
                    self.adapter.emergency_stop()
                    self.peer_manager.mark_unready(
                        self.group_id,
                        self.context_id,
                        self.role_id,
                        f"controlled_midcut_gamma={gamma_self:.4f}",
                    )
                    final_state = "FAILED"
                    fence_reason = f"FENCED_CUT_SOURCE:gamma={gamma_self:.4f}"
                    break

                # 4. Update projection onto current track segment & check safety fences
                gamma_meas, e_lat_body, e_yaw = self.track.project_in_segment(
                    pose.x, pose.y, pose.yaw, seg_idx
                )
                hw_err = self._check_hw_fences(
                    pose,
                    e_lat_body,
                    sensors.lidar_front_min_m,
                    snap,
                    check_lateral=(phase == "FOLLOW"),
                )
                if hw_err:
                    self.adapter.emergency_stop()
                    transport.broadcast_estop(hw_err, gamma_self)
                    self.peer_manager.mark_unready(
                        self.group_id, self.context_id, self.role_id, hw_err
                    )
                    estop_wall_ms = now_wall_ms
                    final_state = "FAILED"
                    fence_reason = f"FENCED_STOP:{hw_err}"
                    break

                # Peer link watchdog (active once START barrier has released, ignoring DONE peers)
                tier1_hold = False
                if phase == "FOLLOW" or (phase == "BARRIER" and active_barrier_stage != "START"):
                    active_ages = [
                        p["age_ms"] for p in snap["peers"].values() if p.get("phase_id") != "DONE"
                    ]
                    if self.neighbors and any(a is None for a in active_ages):
                        fence_reason = "FENCED_STOP:neighbor_never_seen"
                    elif active_ages:
                        worst_age = max(a for a in active_ages if a is not None)
                        if worst_age > self.params.tier2_timeout_ms:
                            fence_reason = f"FENCED_STOP:peer_timeout_{worst_age:.1f}ms"
                        elif worst_age > self.params.tier1_hold_ms:
                            tier1_hold = True
                            if first_tier1_wall_ms is None:
                                first_tier1_wall_ms = now_wall_ms
                    if snap["propagated_hold"]:
                        tier1_hold = True
                        if first_tier1_wall_ms is None:
                            first_tier1_wall_ms = now_wall_ms

                    if fence_reason:
                        self.adapter.emergency_stop()
                        transport.broadcast_estop(fence_reason, gamma_self)
                        self.peer_manager.mark_unready(
                            self.group_id, self.context_id, self.role_id, fence_reason
                        )
                        estop_wall_ms = now_wall_ms
                        final_state = "FAILED"
                        break

                # 5. State machine transitions & control law
                vx_tgt = vy_tgt = wz_tgt = 0.0
                g_dot_tx = 0.0

                if phase == "ALIGN":
                    if sensors.posture != "standing":
                        # Still waiting for node/robot startup delay or standing transition
                        transport.send_state(
                            phase_id="ALIGN",
                            gamma=0.0,
                            gamma_dot=0.0,
                            pose_hint=pose_hint,
                        )
                        self.adapter.send_velocity(0.0, 0.0, 0.0)
                        continue
                    s0 = self.track.segments[0].spec
                    ex = s0.start_x - pose.x
                    ey = s0.start_y - pose.y
                    dist_goal = math.hypot(ex, ey)
                    e_yaw0 = wrap_angle(s0.start_yaw - pose.yaw)
                    if self.params.skip_align or (
                        dist_goal <= self.spec.align_tol_m
                        and abs(e_yaw0) <= self.spec.align_yaw_tol_rad
                    ):
                        prev_vx = prev_vy = prev_wz = 0.0
                        self.adapter.send_velocity(0.0, 0.0, 0.0)
                        barrier_ts_ms["aligned_wall_ms"] = now_wall_ms
                        phase = "BARRIER"
                        active_barrier_stage = "START"
                        t_phase0 = now_m
                        transport.mark_local_barrier_ready("START", 0.0)
                        continue
                    if (now_m - t_phase0) > self.params.barrier_timeout_s:
                        self.adapter.emergency_stop()
                        transport.broadcast_estop("align_timeout", 0.0)
                        self.peer_manager.mark_unready(
                            self.group_id, self.context_id, self.role_id, "align_timeout"
                        )
                        final_state = "FAILED"
                        fence_reason = "FENCED_STOP:align_timeout"
                        break
                    if dist_goal > self.spec.align_tol_m:
                        th_goal = math.atan2(ey, ex)
                        e_th = wrap_angle(th_goal - pose.yaw)
                        if abs(e_th) > 0.45:
                            wz_tgt = math.copysign(max(self.params.turn_min_wz, 0.50), e_th)
                            vx_tgt = self.params.turn_vx_bias
                        else:
                            vx_tgt = clamp(0.55 * dist_goal, 0.08, self.spec.v_ref)
                            wz_tgt = clamp(1.40 * e_th, -0.55, 0.55)
                    else:
                        wz_hi = max(0.60, self.params.turn_min_wz)
                        wz_tgt = math.copysign(
                            clamp(abs(e_yaw0) * 1.4, self.params.turn_min_wz, wz_hi), e_yaw0
                        )
                        vx_tgt = self.params.turn_vx_bias if abs(e_yaw0) > 0.25 else 0.0
                    prev_vx = rate_limit(vx_tgt, prev_vx, 0.45 * dt_ctrl)
                    prev_vy = rate_limit(vy_tgt, prev_vy, 0.25 * dt_ctrl)
                    prev_wz = rate_limit(wz_tgt, prev_wz, 1.50 * dt_ctrl)
                    self.adapter.send_velocity(prev_vx, prev_vy, prev_wz)
                    transport.send_state(
                        phase_id="ALIGN",
                        gamma=0.0,
                        gamma_dot=0.0,
                        pose_hint=pose_hint,
                    )
                    continue

                elif phase == "BARRIER":
                    prev_vx = prev_vy = prev_wz = 0.0
                    self.adapter.send_velocity(0.0, 0.0, 0.0)
                    released = transport.mark_local_barrier_ready(active_barrier_stage, gamma_self)
                    if released:
                        barrier_ts_ms[f"release_{active_barrier_stage}_wall_ms"] = now_wall_ms
                        phase = "FOLLOW"
                        t_phase0 = now_m
                        cs = self.track.segments[seg_idx]
                        if cs.spec.kind == "turn_in_place":
                            turn_accum = 0.0
                            last_yaw_turn = pose.yaw
                        transport.send_state(
                            phase_id="FOLLOW",
                            gamma=gamma_self,
                            gamma_dot=gamma_ref,
                            barrier_stage=active_barrier_stage,
                            barrier_state="RELEASED",
                            barrier_released_stage=active_barrier_stage,
                            pose_hint=pose_hint,
                        )
                    elif (now_m - t_phase0) > self.params.barrier_timeout_s:
                        self.adapter.emergency_stop()
                        transport.broadcast_estop(
                            f"barrier_timeout:{active_barrier_stage}", gamma_self
                        )
                        self.peer_manager.mark_unready(
                            self.group_id,
                            self.context_id,
                            self.role_id,
                            f"barrier_timeout:{active_barrier_stage}",
                        )
                        final_state = "FAILED"
                        fence_reason = f"FENCED_STOP:barrier_timeout:{active_barrier_stage}"
                        break
                    continue

                elif phase == "FOLLOW":
                    cs = self.track.segments[seg_idx]
                    spec_seg = cs.spec

                    if spec_seg.kind in ("line", "arc"):
                        gamma_self = max(cs.gamma_start, min(cs.gamma_end, gamma_meas))
                        rem_m = (
                            (cs.gamma_end - gamma_self) / max(1e-9, cs.gamma_span)
                        ) * spec_seg.length_m
                        seg_done = rem_m <= 0.035 or gamma_self >= cs.gamma_end - 1e-4
                    elif spec_seg.kind == "turn_in_place":
                        if last_yaw_turn is None:
                            last_yaw_turn = pose.yaw
                        dy = wrap_angle(pose.yaw - last_yaw_turn)
                        sgn_turn = 1.0 if spec_seg.delta_yaw >= 0 else -1.0
                        turn_accum = max(0.0, turn_accum + sgn_turn * dy)
                        last_yaw_turn = pose.yaw
                        u_turn = clamp(turn_accum / abs(spec_seg.delta_yaw), 0.0, 1.0)
                        gamma_self = cs.gamma_start + u_turn * cs.gamma_span
                        rem_rad = abs(spec_seg.delta_yaw) - turn_accum
                        e_end = abs(wrap_angle(pose.yaw - spec_seg.end_yaw))
                        seg_done = (rem_rad <= 0.07) or (u_turn >= 0.90 and e_end <= 0.08)
                    else:  # hold
                        seg_done = gamma_self >= cs.gamma_end - 1e-4

                    if seg_done:
                        gamma_self = cs.gamma_end
                        if seg_idx >= len(self.track.segments) - 1:
                            gamma_self = 1.0
                            done_entered_mono = now_m
                            barrier_ts_ms["done_wall_ms"] = now_wall_ms
                            phase = "DONE"
                            prev_vx = prev_vy = prev_wz = 0.0
                            self.adapter.send_velocity(0.0, 0.0, 0.0)
                            transport.send_state(
                                phase_id="DONE",
                                gamma=1.0,
                                gamma_dot=0.0,
                                pose_hint=pose_hint,
                            )
                            continue
                        seg_idx += 1
                        turn_accum = 0.0
                        last_yaw_turn = pose.yaw
                        if seg_idx in seg_barriers:
                            active_barrier_stage = seg_barriers[seg_idx]
                            prev_vx = prev_vy = prev_wz = 0.0
                            self.adapter.send_velocity(0.0, 0.0, 0.0)
                            phase = "BARRIER"
                            t_phase0 = now_m
                            transport.mark_local_barrier_ready(active_barrier_stage, gamma_self)
                        continue

                    dot_g, dgamma = compute_consensus_gamma_dot(
                        gamma_self,
                        list(snap["peers"].values()),
                        gamma_ref=gamma_ref,
                        gamma_max=gamma_max,
                        k_sync=self.params.k_sync,
                        lead_wait_gamma=lead_wait_gamma,
                    )
                    max_abs_dgamma = max(max_abs_dgamma, abs(dgamma))
                    sum_sq_dgamma += dgamma * dgamma
                    n_follow_samples += 1

                    if spec_seg.kind in ("line", "arc"):
                        v_long = clamp(dot_g * self.track.total_length_m, 0.0, self.spec.v_max)
                        rem_m = (
                            (cs.gamma_end - gamma_self) / max(1e-9, cs.gamma_span)
                        ) * spec_seg.length_m
                        if rem_m < 0.25 and v_long > 0.0:
                            v_long = min(v_long, max(0.06, rem_m * 0.65))
                        vx_tgt = v_long
                        vy_tgt = clamp(-0.25 * e_lat_body, -0.06, 0.06)
                        w_ff = 0.0
                        if spec_seg.kind == "arc" and spec_seg.radius_m > 1e-6:
                            w_ff = (1.0 if spec_seg.delta_yaw >= 0 else -1.0) * (
                                v_long / spec_seg.radius_m
                            )
                        wz_tgt = clamp(w_ff - 1.35 * e_yaw - 0.85 * e_lat_body, -0.80, 0.80)
                        g_dot_tx = dot_g
                    elif spec_seg.kind == "turn_in_place":
                        if dot_g <= 1e-6:
                            vx_tgt = vy_tgt = wz_tgt = 0.0
                            g_dot_tx = 0.0
                        else:
                            sgn = 1.0 if spec_seg.delta_yaw >= 0 else -1.0
                            omega_hi = max(0.65, self.params.turn_min_wz)
                            omega_mag = clamp(
                                dot_g * abs(spec_seg.delta_yaw) / max(1e-6, cs.gamma_span),
                                self.params.turn_min_wz,
                                omega_hi,
                            )
                            wz_tgt = sgn * omega_mag
                            vx_tgt = self.params.turn_vx_bias
                            g_dot_tx = dot_g
                    else:  # hold
                        vx_tgt = vy_tgt = wz_tgt = 0.0
                        g_dot_tx = dot_g
                        gamma_self = min(cs.gamma_end, gamma_self + dot_g * dt_ctrl)

                    if tier1_hold:
                        prev_vx = prev_vy = prev_wz = 0.0
                        self.adapter.send_velocity(0.0, 0.0, 0.0)
                        g_dot_tx = 0.0
                    else:
                        prev_vx = rate_limit(vx_tgt, prev_vx, 0.55 * dt_ctrl)
                        prev_vy = rate_limit(vy_tgt, prev_vy, 0.30 * dt_ctrl)
                        prev_wz = rate_limit(wz_tgt, prev_wz, 1.80 * dt_ctrl)
                        self.adapter.send_velocity(prev_vx, prev_vy, prev_wz)

                    transport.send_state(
                        phase_id="FOLLOW",
                        gamma=gamma_self,
                        gamma_dot=g_dot_tx,
                        tier1_hold=tier1_hold,
                        hold_ttl=max(2, len(self.neighbors) * 2) if tier1_hold else 0,
                        pose_hint=pose_hint,
                    )

                elif phase == "DONE":
                    self.adapter.send_velocity(0.0, 0.0, 0.0)
                    transport.send_state(
                        phase_id="DONE",
                        gamma=1.0,
                        gamma_dot=0.0,
                        pose_hint=pose_hint,
                    )
                    all_peers_done = all(
                        p.get("phase_id") == "DONE"
                        or (p.get("gamma") is not None and p["gamma"] >= 0.995)
                        for p in snap["peers"].values()
                    )
                    if all_peers_done or (
                        done_entered_mono is not None
                        and (now_m - done_entered_mono) >= self.params.done_drain_s
                    ):
                        if done_hold_started_mono is None:
                            done_hold_started_mono = now_m
                        if (now_m - done_hold_started_mono) >= self.params.done_hold_s:
                            break

        finally:
            self.adapter.emergency_stop()
            try:
                self.adapter.safe_park_wait(1.8)
            except Exception:
                pass
            # Silence PeerChannel on terminal transition (ADR-0027 authority rule).
            if final_state == "SUCCEEDED":
                self.peer_manager.silence_on_terminal(self.group_id, self.context_id, self.role_id)
            snap_final = transport.get_neighbor_snapshot()
            transport.close()

        rms_dgamma = math.sqrt(sum_sq_dgamma / n_follow_samples) if n_follow_samples > 0 else 0.0
        metrics = {
            "group_id": self.group_id,
            "context_id": self.context_id,
            "role_id": self.role_id,
            "run_id": self.run_id,
            "duration_s": round(time.monotonic() - t_run0, 3),
            "final_gamma": round(gamma_self, 5),
            "max_abs_dgamma": round(max_abs_dgamma, 5),
            "rms_dgamma": round(rms_dgamma, 5),
            "min_sep_m": round(min_sep_seen, 4) if min_sep_seen < 900.0 else None,
            "barrier_timestamps_ms": barrier_ts_ms,
            "first_tier1_hold_wall_ms": first_tier1_wall_ms,
            "cut_wall_ms": cut_wall_ms,
            "estop_wall_ms": estop_wall_ms,
            "fence_reason": fence_reason,
            "final_pose": (
                {
                    "x": round(last_pose.x, 4),
                    "y": round(last_pose.y, 4),
                    "yaw": round(last_pose.yaw, 4),
                }
                if last_pose is not None
                else None
            ),
            "udp_stats": snap_final["stats"],
        }
        detail = (
            fence_reason
            or f"sync-follow completed (gamma={gamma_self:.4f}, rms_dgamma={rms_dgamma:.4f})"
        )
        return final_state, detail, metrics, safe_confirmed
