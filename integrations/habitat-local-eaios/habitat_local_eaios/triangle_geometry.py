"""Pure closed-triangle geometry shared by observation and Local How search.

Inputs are finite world-space points validated by the caller. These operations
do not read a simulator, query a pathfinder, or assert physical reachability.
"""

from __future__ import annotations

import math

Point3 = tuple[float, float, float]


def _subtract(first: Point3, second: Point3) -> Point3:
    """Subtract finite mesh vectors without consulting simulator state."""
    return first[0] - second[0], first[1] - second[1], first[2] - second[2]


def _dot(first: Point3, second: Point3) -> float:
    """Compute a Euclidean scalar product for triangle projection."""
    return sum(first[index] * second[index] for index in range(3))


def _interpolate(first: Point3, second: Point3, fraction: float) -> Point3:
    """Return one closed-edge point for a bounded interpolation fraction."""
    return (
        first[0] + fraction * (second[0] - first[0]),
        first[1] + fraction * (second[1] - first[1]),
        first[2] + fraction * (second[2] - first[2]),
    )


def _closest_edge(point: Point3, first: Point3, second: Point3) -> Point3:
    """Project onto a segment, retaining coincident endpoints as valid geometry."""
    edge = _subtract(second, first)
    length = _dot(edge, edge)
    fraction = max(0.0, min(1.0, _dot(_subtract(point, first), edge) / length)) if length else 0.0
    return _interpolate(first, second, fraction)


def closest_triangle_point(point: Point3, first: Point3, second: Point3, third: Point3) -> Point3:
    """Find the closed-triangle minimum, including interior and degenerate edges."""
    ab, ac = _subtract(second, first), _subtract(third, first)
    normal = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    if _dot(normal, normal) <= 1e-24:
        return min(
            (
                _closest_edge(point, first, second),
                _closest_edge(point, second, third),
                _closest_edge(point, third, first),
            ),
            key=lambda candidate: math.dist(point, candidate),
        )
    ap = _subtract(point, first)
    d1, d2 = _dot(ab, ap), _dot(ac, ap)
    if d1 <= 0 and d2 <= 0:
        return first
    bp = _subtract(point, second)
    d3, d4 = _dot(ab, bp), _dot(ac, bp)
    if d3 >= 0 and d4 <= d3:
        return second
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        return _interpolate(first, second, d1 / (d1 - d3))
    cp = _subtract(point, third)
    d5, d6 = _dot(ab, cp), _dot(ac, cp)
    if d6 >= 0 and d5 <= d6:
        return third
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        return _interpolate(first, third, d2 / (d2 - d6))
    va = d3 * d6 - d5 * d4
    if va <= 0 and d4 - d3 >= 0 and d5 - d6 >= 0:
        return _interpolate(second, third, (d4 - d3) / ((d4 - d3) + (d5 - d6)))
    denominator = va + vb + vc
    v, w = vb / denominator, vc / denominator
    return (
        first[0] + ab[0] * v + ac[0] * w,
        first[1] + ab[1] * v + ac[1] * w,
        first[2] + ab[2] * v + ac[2] * w,
    )
