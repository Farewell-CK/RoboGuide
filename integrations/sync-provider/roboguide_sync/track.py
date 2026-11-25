"""Parameterized multi-segment geometric tracks and SE(2) progress projection."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .schemas import TrackSegmentSpec, TrackSpec


def wrap_angle(angle: float) -> float:
    """Wrap angle in radians into `(-pi, pi]`."""
    return math.atan2(math.sin(angle), math.cos(angle))


def clamp(val: float, lo: float, hi: float) -> float:
    """Clamp scalar `val` into `[lo, hi]`."""
    return max(lo, min(hi, val))


def rate_limit(target: float, prev: float, max_delta: float) -> float:
    """Bound step change `|next - prev| <= max_delta`."""
    return prev + clamp(target - prev, -max_delta, max_delta)


@dataclass(frozen=True)
class CompiledSegment:
    """Track segment mapped to a normalized interval `[gamma_start, gamma_end]`."""

    index: int
    spec: TrackSegmentSpec
    gamma_start: float
    gamma_end: float

    @property
    def gamma_span(self) -> float:
        """Return normalized progress span of this segment."""
        return self.gamma_end - self.gamma_start


@dataclass(frozen=True)
class TrackSample:
    """Evaluated reference pose and segment context at progress `gamma`."""

    gamma: float
    x: float
    y: float
    yaw: float
    seg_index: int
    seg_kind: str
    seg_local_gamma: float
    seg_length_m: float
    gamma_start: float
    gamma_end: float


class ParameterizedTrack:
    """Continuous normalized track `gamma in [0, 1]` over multi-segment paths."""

    def __init__(self, spec: TrackSpec) -> None:
        """Compile validated track segments into normalized `[0, 1]` intervals."""
        self.spec = spec
        total_len = sum(s.length_m for s in spec.segments)
        if total_len <= 0.0:
            raise ValueError("total track length must be positive")
        self.total_length_m = total_len

        compiled: list[CompiledSegment] = []
        acc = 0.0
        n = len(spec.segments)
        for i, seg in enumerate(spec.segments):
            g0 = acc / total_len
            acc += seg.length_m
            g1 = 1.0 if i == n - 1 else (acc / total_len)
            compiled.append(CompiledSegment(index=i, spec=seg, gamma_start=g0, gamma_end=g1))
        self.segments = tuple(compiled)

    def barrier_boundaries(self) -> list[tuple[str, float, int]]:
        """Return ordered `(stage_name, gamma_threshold, next_seg_index)` sync barriers."""
        out: list[tuple[str, float, int]] = [("START", 0.0, 0)]
        for cs in self.segments[1:]:
            if cs.spec.barrier_before:
                label = cs.spec.label or f"SEG_{cs.index}"
                out.append((f"SEG_{cs.index}_{label}", cs.gamma_start, cs.index))
        return out

    def segment_for_gamma(self, gamma: float) -> CompiledSegment:
        """Return compiled segment covering `gamma in [0, 1]`."""
        g = clamp(gamma, 0.0, 1.0)
        for cs in self.segments:
            if g <= cs.gamma_end or cs.index == len(self.segments) - 1:
                return cs
        return self.segments[-1]

    def evaluate(self, gamma: float) -> TrackSample:
        """Evaluate exact `(x, y, yaw)` and segment metadata at `gamma`."""
        g = clamp(gamma, 0.0, 1.0)
        cs = self.segment_for_gamma(g)
        span = max(1e-9, cs.gamma_span)
        u = clamp((g - cs.gamma_start) / span, 0.0, 1.0)
        s = cs.spec
        if s.kind == "line":
            x = s.start_x + u * (s.end_x - s.start_x)
            y = s.start_y + u * (s.end_y - s.start_y)
            yaw = s.start_yaw
        elif s.kind == "arc":
            ang0 = math.atan2(s.start_y - s.center_y, s.start_x - s.center_x)
            ang = ang0 + u * s.delta_yaw
            x = s.center_x + s.radius_m * math.cos(ang)
            y = s.center_y + s.radius_m * math.sin(ang)
            sign_tan = 1.0 if s.delta_yaw >= 0 else -1.0
            yaw = wrap_angle(ang + sign_tan * (math.pi / 2.0))
        elif s.kind == "turn_in_place":
            x = s.start_x
            y = s.start_y
            yaw = wrap_angle(s.start_yaw + u * s.delta_yaw)
        else:  # hold
            x = s.start_x
            y = s.start_y
            yaw = s.start_yaw
        return TrackSample(
            gamma=g,
            x=x,
            y=y,
            yaw=yaw,
            seg_index=cs.index,
            seg_kind=s.kind,
            seg_local_gamma=u,
            seg_length_m=s.length_m,
            gamma_start=cs.gamma_start,
            gamma_end=cs.gamma_end,
        )

    def project_in_segment(
        self, x: float, y: float, yaw: float, seg_index: int
    ) -> tuple[float, float, float]:
        """Project current pose onto `segments[seg_index]` -> `(gamma_meas, e_lat_body, e_yaw)`."""
        cs = self.segments[max(0, min(seg_index, len(self.segments) - 1))]
        s = cs.spec
        if s.kind == "line":
            dx = s.end_x - s.start_x
            dy = s.end_y - s.start_y
            L = max(1e-6, s.length_m)
            ux, uy = dx / L, dy / L
            rel_x, rel_y = x - s.start_x, y - s.start_y
            s_along = rel_x * ux + rel_y * uy
            u = clamp(s_along / L, 0.0, 1.0)
            gamma_meas = cs.gamma_start + u * cs.gamma_span
            # Signed left-hand lateral deviation (+ = robot to the LEFT of forward tangent)
            e_lat_body = -uy * rel_x + ux * rel_y
            e_yaw = wrap_angle(yaw - s.start_yaw)
            return gamma_meas, e_lat_body, e_yaw

        if s.kind == "arc":
            ang0 = math.atan2(s.start_y - s.center_y, s.start_x - s.center_x)
            ang_cur = math.atan2(y - s.center_y, x - s.center_x)
            sign_tan = 1.0 if s.delta_yaw >= 0 else -1.0
            # Signed progress along arc
            if s.delta_yaw >= 0:
                d_ang = (ang_cur - ang0) % (2.0 * math.pi)
                if d_ang > min(2.0 * math.pi - 0.5, abs(s.delta_yaw) + 0.5):
                    d_ang -= 2.0 * math.pi
            else:
                d_ang = -((ang0 - ang_cur) % (2.0 * math.pi))
                if d_ang < -min(2.0 * math.pi - 0.5, abs(s.delta_yaw) + 0.5):
                    d_ang += 2.0 * math.pi
            u = clamp(d_ang / s.delta_yaw, 0.0, 1.0)
            gamma_meas = cs.gamma_start + u * cs.gamma_span
            ref_ang = ang0 + u * s.delta_yaw
            ref_yaw = wrap_angle(ref_ang + sign_tan * (math.pi / 2.0))
            rx = s.center_x + s.radius_m * math.cos(ref_ang)
            ry = s.center_y + s.radius_m * math.sin(ref_ang)
            e_lat_body = -math.sin(ref_yaw) * (x - rx) + math.cos(ref_yaw) * (y - ry)
            e_yaw = wrap_angle(yaw - ref_yaw)
            return gamma_meas, e_lat_body, e_yaw

        if s.kind == "turn_in_place":
            # Signed progress along intended rotation direction
            rel_yaw = wrap_angle(yaw - s.start_yaw)
            if s.delta_yaw > 0 and rel_yaw < -0.5:
                rel_yaw += 2.0 * math.pi
            elif s.delta_yaw < 0 and rel_yaw > 0.5:
                rel_yaw -= 2.0 * math.pi
            u = clamp(rel_yaw / s.delta_yaw, 0.0, 1.0)
            gamma_meas = cs.gamma_start + u * cs.gamma_span
            e_lat_body = math.hypot(x - s.start_x, y - s.start_y)
            e_yaw = wrap_angle(yaw - (s.start_yaw + u * s.delta_yaw))
            return gamma_meas, e_lat_body, e_yaw

        # hold segment advances via timer in caller; pose error is radial distance
        e_lat_body = math.hypot(x - s.start_x, y - s.start_y)
        e_yaw = wrap_angle(yaw - s.start_yaw)
        return cs.gamma_start, e_lat_body, e_yaw
