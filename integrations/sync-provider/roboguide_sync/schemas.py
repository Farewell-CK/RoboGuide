"""Strict EAIOS-neutral schemas and validators for multi-robot sync and choreography."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

SYNC_PHASE_SCHEMA = "roboguide.sync-phase/v1"
SYNC_TRACK_SCHEMA = "roboguide.sync-track/v1"
CHOREOGRAPHY_SCHEMA = "roboguide.choreography/v1"
SYNC_ENDPOINT_SCHEMA = "roboguide.sync-endpoint/v1"
SAFE_STATE_OBSERVATION_SCHEMA = "roboguide.safe-state-observation/v0.2"
EXECUTION_SAFETY_SCHEMA = "roboguide.execution-safety/v0.1"

OP_SYNC_FOLLOW = "mobility.sync-follow@v1"
OP_CHOREOGRAPHY_PLAY = "choreography.play@v1"
SUPPORTED_OPERATIONS = frozenset({OP_SYNC_FOLLOW, OP_CHOREOGRAPHY_PLAY})

VALID_PHASE_IDS = frozenset({"IDLE_HEARTBEAT", "ALIGN", "BARRIER", "FOLLOW", "DONE", "ESTOP"})
VALID_BARRIER_STATES = frozenset({"NONE", "WAITING", "READY", "RELEASED"})
VALID_SEGMENT_KINDS = frozenset({"line", "arc", "turn_in_place", "hold"})
HARD_MAX_LINEAR_SPEED_MPS = 0.50
HARD_MAX_ANGULAR_SPEED_RAD_S = 1.20
MAX_NEIGHBORS = 32
MAX_BARRIER_ROLES = 64
MAX_KEYFRAMES = 10_000
HOST_PORT_RE = re.compile(r"^([A-Za-z0-9._-]+|\[[0-9a-fA-F:]+\]):(\d{1,5})$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


class SchemaError(ValueError):
    """Raised when an invocation, neighbor map, track, choreography, or phase packet is invalid."""


def canonical_json(value: Any) -> str:
    """Serialize finite JSON deterministically."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def _finite(val: Any, name: str) -> float:
    """Validate and convert `val` to a finite `float`."""
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise SchemaError(f"{name} must be a finite number")
    out = float(val)
    if not math.isfinite(out):
        raise SchemaError(f"{name} must be finite")
    return out


def _finite_vec(val: Any, name: str, min_len: int, max_len: int = 3) -> tuple[float, ...]:
    """Validate an array of `min_len..max_len` finite numbers."""
    if not isinstance(val, (list, tuple)) or not (min_len <= len(val) <= max_len):
        raise SchemaError(f"{name} must be an array of {min_len}..{max_len} finite numbers")
    return tuple(_finite(v, f"{name}[{i}]") for i, v in enumerate(val))


def _nonempty_str(val: Any, name: str, max_len: int = 256) -> str:
    """Validate a trimmed non-empty string bounded by `max_len`."""
    if not isinstance(val, str) or not val.strip() or len(val) > max_len:
        raise SchemaError(f"{name} must be a non-empty string (<= {max_len} chars)")
    return val.strip()


def _parse_json_or_obj(raw: Any, name: str, expected_type: type) -> Any:
    """Decode an object or bounded JSON string of `expected_type`."""
    if isinstance(raw, str):
        if len(raw) > 262_144:
            raise SchemaError(f"{name} JSON string exceeds 256 KiB")
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SchemaError(f"{name} invalid JSON: {exc}") from exc
    if not isinstance(raw, expected_type):
        raise SchemaError(f"{name} must be a {expected_type.__name__} or JSON string")
    return raw


@dataclass(frozen=True)
class NeighborSpec:
    """Validated peer neighbor in a sparse coordination graph."""

    role_id: str
    host: str
    port: int
    weight: float


@dataclass(frozen=True)
class TrackSegmentSpec:
    """One parameterized geometric/hold segment along a normalized track."""

    kind: str
    start_x: float
    start_y: float
    start_yaw: float
    end_x: float
    end_y: float
    end_yaw: float
    length_m: float
    delta_yaw: float = 0.0
    radius_m: float = 0.0
    center_x: float = 0.0
    center_y: float = 0.0
    hold_duration_s: float = 0.0
    barrier_before: bool = False
    label: str = ""


@dataclass(frozen=True)
class TrackSpec:
    """Validated `roboguide.sync-track/v1` specification."""

    schema_version: str
    segments: tuple[TrackSegmentSpec, ...]
    v_ref: float = 0.18
    v_max: float = 0.25
    lat_fence_m: float = 0.45
    min_sep_m: float = 0.75
    lidar_stop_m: float = 0.55
    align_tol_m: float = 0.15
    align_yaw_tol_rad: float = 0.18
    group_roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class SyncParams:
    """Closed-loop consensus, barrier, and dual-tier watchdog parameters."""

    k_sync: float = 0.80
    lead_wait_m: float = 0.30
    tier1_hold_ms: float = 400.0
    tier2_timeout_ms: float = 2000.0
    barrier_timeout_s: float = 45.0
    control_hz: float = 20.0
    done_drain_s: float = 1.5
    turn_min_wz: float = 0.45
    turn_vx_bias: float = 0.05
    midcut_gamma: float | None = None
    skip_align: bool = False
    done_hold_s: float = 0.0


@dataclass(frozen=True)
class KeyframeSpec:
    """Time-indexed choreography keyframe."""

    t_s: float
    x: float
    y: float
    yaw: float
    action: str = ""


@dataclass(frozen=True)
class ChoreographySpec:
    """Validated `roboguide.choreography/v1` time-indexed motion pack."""

    schema_version: str
    keyframes: tuple[KeyframeSpec, ...]
    v_max: float = 0.35
    lidar_stop_m: float = 0.55
    fault_policy: str = "isolate-local-stop"
    pack_id: str = ""
    pack_sha256: str = ""


@dataclass(frozen=True)
class NormalizedExecutionRequest:
    """Canonical operation request extracted from either RoboGuide Node payload shape."""

    attempt_id: str
    execution_generation: int
    budget_ms: int
    operation: str
    objective: str
    mission_id: str
    task_id: str
    group_id: str
    role_id: str
    parameters: dict[str, Any] = field(default_factory=dict)


def extract_execution_intent(body: dict[str, Any]) -> NormalizedExecutionRequest:
    """Parse canonical Node HTTP execution payload (`attempt_id`, `invocation`, `budget_ms`)."""
    if not isinstance(body, dict):
        raise SchemaError("request body must be a JSON object")
    attempt_id = _nonempty_str(body.get("attempt_id"), "attempt_id", 256)
    budget = body.get("budget_ms", 300_000)
    if isinstance(budget, bool) or not isinstance(budget, int) or not (100 <= budget <= 3_600_000):
        raise SchemaError("budget_ms must be an integer in 100..3600000")

    inv = body.get("invocation")
    if not isinstance(inv, dict):
        raise SchemaError("invocation object required")

    # Support nested `invocation.intent` (tests) AND flat RoboGuide canonical `invocation`
    intent_raw = inv.get("intent")
    intent: dict[str, Any] = intent_raw if isinstance(intent_raw, dict) else inv

    op_raw = intent.get("operation")
    if isinstance(op_raw, str):
        operation = op_raw.strip()
    elif isinstance(op_raw, dict) and set(op_raw) >= {"namespace", "name", "version"}:
        operation = f"{op_raw['namespace']}.{op_raw['name']}@{op_raw['version']}"
    else:
        raise SchemaError("canonical operation string or object required")
    if operation not in SUPPORTED_OPERATIONS:
        raise SchemaError(f"unsupported operation: {operation}")

    objective = _nonempty_str(intent.get("objective", operation), "objective", 8192)
    params = intent.get("parameters", {})
    if not isinstance(params, dict):
        raise SchemaError("parameters must be a JSON object")

    gen_raw = body.get("execution_generation", inv.get("execution_generation", 1))
    if isinstance(gen_raw, bool) or not isinstance(gen_raw, int) or gen_raw < 1:
        raise SchemaError("execution_generation must be an integer >= 1")

    mission_id = str(inv.get("mission_id") or params.get("mission_id") or "local-mission")
    task_id = str(inv.get("task_id") or params.get("task_id") or attempt_id)
    group_id = str(inv.get("group_id") or params.get("group_id") or mission_id)
    role_id = str(inv.get("role_id") or params.get("context_role_id") or "robot")

    return NormalizedExecutionRequest(
        attempt_id=attempt_id,
        execution_generation=gen_raw,
        budget_ms=budget,
        operation=operation,
        objective=objective,
        mission_id=mission_id,
        task_id=task_id,
        group_id=group_id,
        role_id=role_id,
        parameters=dict(params),
    )


def parse_host_port(endpoint: Any) -> tuple[str, int]:
    """Validate and split `host:port` (including IPv6 bracketed hosts)."""
    ep = _nonempty_str(endpoint, "endpoint", 256)
    m = HOST_PORT_RE.match(ep)
    if not m:
        raise SchemaError(f"endpoint must be host:port, got {ep!r}")
    host = m.group(1)
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    port = int(m.group(2))
    if not (1 <= port <= 65535):
        raise SchemaError(f"port out of range 1..65535: {port}")
    return host, port


def parse_neighbors(raw: Any) -> tuple[NeighborSpec, ...]:
    """Parse and weight-normalize sparse graph neighbors (`[{role_id, endpoint, weight?}]`)."""
    arr = _parse_json_or_obj(raw, "neighbors", list)
    if len(arr) > MAX_NEIGHBORS:
        raise SchemaError(f"neighbors count {len(arr)} exceeds max {MAX_NEIGHBORS}")
    seen_roles: set[str] = set()
    parsed: list[tuple[str, str, int, float]] = []
    weight_sum = 0.0
    for idx, item in enumerate(arr):
        if not isinstance(item, dict):
            raise SchemaError(f"neighbors[{idx}] must be an object")
        role_id = _nonempty_str(item.get("role_id"), f"neighbors[{idx}].role_id", 128)
        if role_id in seen_roles:
            raise SchemaError(f"duplicate neighbor role_id: {role_id}")
        seen_roles.add(role_id)
        host, port = parse_host_port(item.get("endpoint"))
        w = _finite(item.get("weight", 1.0), f"neighbors[{idx}].weight")
        if w <= 0.0:
            raise SchemaError(f"neighbors[{idx}].weight must be > 0")
        parsed.append((role_id, host, port, w))
        weight_sum += w
    if not parsed:
        return ()
    return tuple(
        NeighborSpec(role_id=r, host=h, port=p, weight=w / weight_sum) for r, h, p, w in parsed
    )


def parse_track_spec(raw: Any) -> TrackSpec:
    """Parse and validate `roboguide.sync-track/v1`."""
    obj = _parse_json_or_obj(raw, "track_spec", dict)
    sv = obj.get("schema_version", SYNC_TRACK_SCHEMA)
    if sv != SYNC_TRACK_SCHEMA:
        raise SchemaError(f"track_spec.schema_version must be {SYNC_TRACK_SCHEMA!r}")

    segs_raw = obj.get("segments")
    if not isinstance(segs_raw, list) or not segs_raw or len(segs_raw) > 128:
        raise SchemaError("track_spec.segments must be a non-empty list (<= 128)")

    v_max = _finite(obj.get("v_max", 0.25), "track_spec.v_max")
    v_ref = _finite(obj.get("v_ref", min(0.18, v_max)), "track_spec.v_ref")
    if not (0.0 < v_ref <= v_max <= HARD_MAX_LINEAR_SPEED_MPS):
        raise SchemaError(f"track_spec requires 0 < v_ref <= v_max <= {HARD_MAX_LINEAR_SPEED_MPS}")

    lat_fence_m = _finite(obj.get("lat_fence_m", 0.45), "track_spec.lat_fence_m")
    min_sep_m = _finite(obj.get("min_sep_m", 0.75), "track_spec.min_sep_m")
    lidar_stop_m = _finite(obj.get("lidar_stop_m", 0.55), "track_spec.lidar_stop_m")
    align_tol_m = _finite(obj.get("align_tol_m", 0.15), "track_spec.align_tol_m")
    align_yaw_tol_rad = _finite(obj.get("align_yaw_tol_rad", 0.18), "track_spec.align_yaw_tol_rad")
    if min_sep_m < 0.0 or lat_fence_m <= 0.0 or lidar_stop_m < 0.0 or align_tol_m <= 0.0:
        raise SchemaError("track_spec geometric tolerances must be non-negative/positive")

    roles_raw = obj.get("group_roles", [])
    if not isinstance(roles_raw, list) or len(roles_raw) > MAX_BARRIER_ROLES:
        raise SchemaError(f"group_roles must be a list of <= {MAX_BARRIER_ROLES} roles")
    group_roles = tuple(_nonempty_str(r, "group_roles[]", 128) for r in roles_raw)

    segs: list[TrackSegmentSpec] = []
    for idx, s in enumerate(segs_raw):
        if not isinstance(s, dict):
            raise SchemaError(f"segments[{idx}] must be an object")
        kind = s.get("kind")
        if kind not in VALID_SEGMENT_KINDS:
            raise SchemaError(f"segments[{idx}].kind invalid: {kind!r}")
        barrier_before = bool(s.get("barrier_before", False))
        label = str(s.get("label", f"seg_{idx}"))[:64]

        if kind == "line":
            start = _finite_vec(s.get("start"), f"segments[{idx}].start", 2, 3)
            end = _finite_vec(s.get("end"), f"segments[{idx}].end", 2, 3)
            sx, sy = start[0], start[1]
            ex, ey = end[0], end[1]
            length = math.hypot(ex - sx, ey - sy)
            if length < 0.05:
                raise SchemaError(f"segments[{idx}] line length < 0.05 m")
            yaw = math.atan2(ey - sy, ex - sx)
            segs.append(
                TrackSegmentSpec(
                    kind="line",
                    start_x=sx,
                    start_y=sy,
                    start_yaw=yaw,
                    end_x=ex,
                    end_y=ey,
                    end_yaw=yaw,
                    length_m=length,
                    barrier_before=barrier_before,
                    label=label,
                )
            )
        elif kind == "arc":
            center = _finite_vec(s.get("center"), f"segments[{idx}].center", 2, 3)
            cx, cy = center[0], center[1]
            rad = _finite(s.get("radius_m"), f"segments[{idx}].radius_m")
            if rad <= 0.05:
                raise SchemaError(f"segments[{idx}] arc radius_m must be > 0.05")
            a0 = _finite(s.get("start_angle"), f"segments[{idx}].start_angle")
            da = _finite(s.get("delta_angle"), f"segments[{idx}].delta_angle")
            if abs(da) < 1e-3:
                raise SchemaError(f"segments[{idx}] arc delta_angle too small")
            a1 = a0 + da
            sign_tan = 1.0 if da > 0 else -1.0
            sx = cx + rad * math.cos(a0)
            sy = cy + rad * math.sin(a0)
            ex = cx + rad * math.cos(a1)
            ey = cy + rad * math.sin(a1)
            syaw = math.atan2(
                math.sin(a0 + sign_tan * math.pi / 2), math.cos(a0 + sign_tan * math.pi / 2)
            )
            eyaw = math.atan2(
                math.sin(a1 + sign_tan * math.pi / 2), math.cos(a1 + sign_tan * math.pi / 2)
            )
            segs.append(
                TrackSegmentSpec(
                    kind="arc",
                    start_x=sx,
                    start_y=sy,
                    start_yaw=syaw,
                    end_x=ex,
                    end_y=ey,
                    end_yaw=eyaw,
                    length_m=abs(da) * rad,
                    delta_yaw=da,
                    radius_m=rad,
                    center_x=cx,
                    center_y=cy,
                    barrier_before=barrier_before,
                    label=label,
                )
            )
        elif kind == "turn_in_place":
            pose = _finite_vec(s.get("pose"), f"segments[{idx}].pose", 2, 3)
            px, py = pose[0], pose[1]
            if "start_yaw" in s and s["start_yaw"] is not None:
                yaw0 = _finite(s["start_yaw"], f"segments[{idx}].start_yaw")
            elif len(pose) >= 3:
                yaw0 = pose[2]
            else:
                yaw0 = 0.0
            dyaw = _finite(s.get("delta_yaw"), f"segments[{idx}].delta_yaw")
            if abs(dyaw) < 0.05:
                raise SchemaError(f"segments[{idx}] turn_in_place delta_yaw too small")
            yaw1 = math.atan2(math.sin(yaw0 + dyaw), math.cos(yaw0 + dyaw))
            eq_len = _finite(
                s.get("equiv_length_m", max(0.35, abs(dyaw) * 0.40)),
                f"segments[{idx}].equiv_length_m",
            )
            if eq_len <= 0.0:
                raise SchemaError(f"segments[{idx}] turn_in_place equiv_length_m must be > 0")
            segs.append(
                TrackSegmentSpec(
                    kind="turn_in_place",
                    start_x=px,
                    start_y=py,
                    start_yaw=yaw0,
                    end_x=px,
                    end_y=py,
                    end_yaw=yaw1,
                    length_m=eq_len,
                    delta_yaw=dyaw,
                    barrier_before=barrier_before,
                    label=label,
                )
            )
        else:  # hold
            pose = _finite_vec(s.get("pose"), f"segments[{idx}].pose", 3, 3)
            px, py, pyaw = pose[0], pose[1], pose[2]
            dur = _finite(s.get("duration_s", 1.0), f"segments[{idx}].duration_s")
            if dur <= 0.0:
                raise SchemaError(f"segments[{idx}] hold duration_s must be > 0")
            eq_len = max(0.10, dur * v_ref)
            segs.append(
                TrackSegmentSpec(
                    kind="hold",
                    start_x=px,
                    start_y=py,
                    start_yaw=pyaw,
                    end_x=px,
                    end_y=py,
                    end_yaw=pyaw,
                    length_m=eq_len,
                    hold_duration_s=dur,
                    barrier_before=barrier_before,
                    label=label,
                )
            )

    return TrackSpec(
        schema_version=sv,
        segments=tuple(segs),
        v_ref=v_ref,
        v_max=v_max,
        lat_fence_m=lat_fence_m,
        min_sep_m=min_sep_m,
        lidar_stop_m=lidar_stop_m,
        align_tol_m=align_tol_m,
        align_yaw_tol_rad=align_yaw_tol_rad,
        group_roles=group_roles,
    )


def parse_sync_params(raw: Any) -> SyncParams:
    """Parse optional `sync_params` object or JSON string."""
    if raw is None or raw == "":
        return SyncParams()
    obj = _parse_json_or_obj(raw, "sync_params", dict)
    k_sync = _finite(obj.get("k_sync", 0.80), "sync_params.k_sync")
    lead_wait_m = _finite(obj.get("lead_wait_m", 0.30), "sync_params.lead_wait_m")
    t1 = _finite(obj.get("tier1_hold_ms", 400.0), "sync_params.tier1_hold_ms")
    t2 = _finite(obj.get("tier2_timeout_ms", 2000.0), "sync_params.tier2_timeout_ms")
    bt = _finite(obj.get("barrier_timeout_s", 45.0), "sync_params.barrier_timeout_s")
    hz = _finite(obj.get("control_hz", 20.0), "sync_params.control_hz")
    dd = _finite(obj.get("done_drain_s", 1.5), "sync_params.done_drain_s")
    min_wz = _finite(obj.get("turn_min_wz", 0.45), "sync_params.turn_min_wz")
    vx_bias = _finite(obj.get("turn_vx_bias", 0.05), "sync_params.turn_vx_bias")
    midcut_raw = obj.get("midcut_gamma")
    midcut = None if midcut_raw is None else _finite(midcut_raw, "sync_params.midcut_gamma")
    done_hold_s = _finite(obj.get("done_hold_s", 0.0), "sync_params.done_hold_s")
    if (
        k_sync < 0.0
        or lead_wait_m <= 0.0
        or t1 <= 0.0
        or t2 < t1
        or bt <= 0.0
        or dd < 0.0
        or not (5.0 <= hz <= 50.0)
    ):
        raise SchemaError("sync_params timing/gain values out of valid bounds")
    if not (0.0 < min_wz <= HARD_MAX_ANGULAR_SPEED_RAD_S) or not (0.0 <= vx_bias <= 0.15):
        raise SchemaError("sync_params turn velocity bounds invalid")
    if not (0.0 <= done_hold_s <= 30.0):
        raise SchemaError("sync_params.done_hold_s must be in [0, 30]")
    if midcut is not None and not (0.0 < midcut < 1.0):
        raise SchemaError("sync_params.midcut_gamma must be in (0, 1)")
    return SyncParams(
        k_sync=k_sync,
        lead_wait_m=lead_wait_m,
        tier1_hold_ms=t1,
        tier2_timeout_ms=t2,
        barrier_timeout_s=bt,
        control_hz=hz,
        done_drain_s=dd,
        turn_min_wz=min_wz,
        turn_vx_bias=vx_bias,
        midcut_gamma=midcut,
        skip_align=bool(obj.get("skip_align", False)),
        done_hold_s=done_hold_s,
    )


def parse_choreography_spec(raw: Any) -> ChoreographySpec:
    """Parse and validate `roboguide.choreography/v1`."""
    obj = _parse_json_or_obj(raw, "choreography_spec", dict)
    sv = obj.get("schema_version", CHOREOGRAPHY_SCHEMA)
    if sv != CHOREOGRAPHY_SCHEMA:
        raise SchemaError(f"choreography_spec.schema_version must be {CHOREOGRAPHY_SCHEMA!r}")

    v_max = _finite(obj.get("v_max", 0.35), "choreography_spec.v_max")
    if not (0.05 <= v_max <= HARD_MAX_LINEAR_SPEED_MPS):
        raise SchemaError(f"choreography_spec.v_max must be in [0.05, {HARD_MAX_LINEAR_SPEED_MPS}]")
    lidar_stop_m = _finite(obj.get("lidar_stop_m", 0.55), "choreography_spec.lidar_stop_m")
    if lidar_stop_m < 0.0:
        raise SchemaError("choreography_spec.lidar_stop_m must be >= 0")
    fault_policy = str(obj.get("fault_policy", "isolate-local-stop"))
    if fault_policy != "isolate-local-stop":
        raise SchemaError(f"unsupported choreography fault_policy: {fault_policy!r}")

    pack_id = str(obj.get("pack_id", ""))[:128]
    pack_sha = str(obj.get("pack_sha256", ""))
    if pack_sha and not HEX64_RE.match(pack_sha):
        raise SchemaError("choreography_spec.pack_sha256 must be 64 lower-hex chars")

    kfs_raw = obj.get("keyframes")
    if not isinstance(kfs_raw, list) or len(kfs_raw) < 2 or len(kfs_raw) > MAX_KEYFRAMES:
        raise SchemaError("choreography_spec.keyframes must contain 2..10000 items")
    kfs: list[KeyframeSpec] = []
    prev_t = -1.0
    for idx, item in enumerate(kfs_raw):
        if not isinstance(item, dict):
            raise SchemaError(f"keyframes[{idx}] must be an object")
        t_s = _finite(item.get("t_s"), f"keyframes[{idx}].t_s")
        if t_s < 0.0 or t_s <= prev_t:
            raise SchemaError(f"keyframes[{idx}].t_s must be strictly increasing >= 0")
        prev_t = t_s
        x = _finite(item.get("x"), f"keyframes[{idx}].x")
        y = _finite(item.get("y"), f"keyframes[{idx}].y")
        yaw = _finite(item.get("yaw"), f"keyframes[{idx}].yaw")
        action = str(item.get("action", ""))[:64]
        kfs.append(KeyframeSpec(t_s=t_s, x=x, y=y, yaw=yaw, action=action))

    return ChoreographySpec(
        schema_version=sv,
        keyframes=tuple(kfs),
        v_max=v_max,
        lidar_stop_m=lidar_stop_m,
        fault_policy=fault_policy,
        pack_id=pack_id,
        pack_sha256=pack_sha,
    )


def _bounded_int(val: Any, name: str, lo: int, hi: int, default: int = 0) -> int:
    """Validate optional non-boolean integer and clamp into `[lo, hi]`."""
    if val is None:
        return default
    if isinstance(val, bool) or not isinstance(val, int):
        raise SchemaError(f"{name} must be an integer in {lo}..{hi}")
    return max(lo, min(hi, val))


def validate_sync_phase_packet(pkt: Any) -> dict[str, Any]:
    """Validate an incoming `roboguide.sync-phase/v1` datagram."""
    if not isinstance(pkt, dict):
        raise SchemaError("packet must be an object")
    if pkt.get("schema") != SYNC_PHASE_SCHEMA:
        raise SchemaError("unexpected schema")
    group_id = _nonempty_str(pkt.get("group_id"), "group_id", 256)
    epoch = pkt.get("epoch")
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
        raise SchemaError("epoch must be int >= 1")
    run_id = _nonempty_str(pkt.get("run_id"), "run_id", 256)
    node_id = _nonempty_str(pkt.get("node_id"), "node_id", 128)
    role_id = _nonempty_str(pkt.get("role_id"), "role_id", 128)
    seq = pkt.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        raise SchemaError("seq must be int >= 0")
    phase_id = pkt.get("phase_id")
    if phase_id not in VALID_PHASE_IDS:
        raise SchemaError(f"invalid phase_id: {phase_id!r}")
    gamma = _finite(pkt.get("gamma", 0.0), "gamma")
    if not (-0.05 <= gamma <= 1.05):
        raise SchemaError(f"gamma out of bounds: {gamma}")
    gamma_dot = _finite(pkt.get("gamma_dot", 0.0), "gamma_dot")
    if not (0.0 <= gamma_dot <= 10.0):
        raise SchemaError(f"gamma_dot out of bounds: {gamma_dot}")
    bstate = pkt.get("barrier_state", "NONE")
    if bstate not in VALID_BARRIER_STATES:
        raise SchemaError(f"invalid barrier_state: {bstate!r}")
    bseen = pkt.get("barrier_seen", [])
    if not isinstance(bseen, list) or len(bseen) > MAX_BARRIER_ROLES:
        raise SchemaError("invalid barrier_seen list")
    wall_ms = pkt.get("wall_ms")
    if isinstance(wall_ms, bool) or not isinstance(wall_ms, int) or wall_ms < 0:
        raise SchemaError("wall_ms must be int >= 0")

    return {
        "schema": SYNC_PHASE_SCHEMA,
        "group_id": group_id,
        "epoch": epoch,
        "run_id": run_id,
        "node_id": node_id,
        "role_id": role_id,
        "seq": seq,
        "echo_seq": pkt.get("echo_seq")
        if isinstance(pkt.get("echo_seq"), int) and not isinstance(pkt.get("echo_seq"), bool)
        else None,
        "phase_id": phase_id,
        "gamma": max(0.0, min(1.0, gamma)),
        "gamma_dot": gamma_dot,
        "barrier_stage": str(pkt.get("barrier_stage", ""))[:64],
        "barrier_state": bstate,
        "barrier_seen": [str(x)[:128] for x in bseen],
        "barrier_released_stage": str(pkt.get("barrier_released_stage", ""))[:64],
        "tier1_hold": bool(pkt.get("tier1_hold", False)),
        "hold_ttl": _bounded_int(pkt.get("hold_ttl"), "hold_ttl", 0, 16, 0),
        "estop": bool(pkt.get("estop", False)),
        "estop_reason": str(pkt.get("estop_reason", ""))[:256],
        "estop_origin": str(pkt.get("estop_origin", ""))[:128],
        "estop_ttl": _bounded_int(pkt.get("estop_ttl"), "estop_ttl", 0, 32, 0),
        "pose_hint": pkt.get("pose_hint")
        if (
            isinstance(pkt.get("pose_hint"), list)
            and len(pkt["pose_hint"]) == 3
            and all(
                not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(float(v))
                for v in pkt["pose_hint"]
            )
        )
        else None,
        "wall_ms": wall_ms,
    }
