"""Versioned, host-owned field decoders for read-only reported State observations."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast

from mission.models import JSONObject, JSONValue


@dataclass(frozen=True)
class StateValue:
    """Keep a qualified field separate from an unknown value and its original source budget."""

    status: str
    value: JSONValue = None
    unit: str = ""
    reason: str = ""
    remaining_ms: float = 0
    details: JSONObject = field(default_factory=dict)


@dataclass(frozen=True)
class StateProfile:
    """Bind one exact schema/object shape to declared fields and a maintained pure decoder."""

    schema: str
    object_class: str
    object_type: str
    fields: tuple[str, ...]
    decode: Callable[[JSONObject, str, float], StateValue]


def number(value: JSONValue) -> bool:
    """Accept finite physical scalars without treating booleans or huge integers as readings."""
    try:
        return type(value) in {int, float} and math.isfinite(cast(float, value))
    except OverflowError:
        return False


def _object(value: JSONValue) -> JSONObject:
    """Treat missing nested data as unavailable rather than creating an empty measurement."""
    return value if isinstance(value, dict) else {}


def _window(age: JSONValue, ttl: JSONValue, retained: float) -> float:
    """Consume the shorter original chassis window and same-Controller/local elapsed time."""
    if not number(age) or cast(float, age) < 0 or type(ttl) is not int or not 0 < ttl <= 60_000:
        return 0
    return min(5000, ttl) - cast(float, age) - retained


def _chassis_value(value: JSONObject, name: str, remaining: float) -> StateValue:
    """Read independent battery/posture values without lending a posture result to battery."""
    if remaining <= 0:
        return StateValue("stale", reason="底盘数据已过期或缺少有效采样窗口")
    if name == "battery":
        percent = value.get("percent")
        if not number(percent) or not 0 <= cast(float, percent) <= 100:
            return StateValue("unknown", reason="电量缺失或不是有效的 0–100 百分比")
        return StateValue("reported", percent, "%", remaining_ms=remaining)
    posture = value.get("posture")
    if posture not in ("standing", "down"):
        return StateValue("unknown", reason="设备没有报告明确的站立或趴下状态")
    return StateValue("reported", posture, remaining_ms=remaining)


def _position(value: JSONObject, remaining: float) -> StateValue:
    """Return a map-qualified reported pose while retaining missing localization assurance."""
    if remaining <= 0:
        return StateValue("stale", reason="地图接口报告已过期或缺少采样年龄")
    map_id, pose = value.get("map_id"), _object(value.get("pose"))
    if (
        not isinstance(map_id, str)
        or not map_id.strip()
        or len(map_id) > 256
        or value.get("mode") != "localization"
        or not all(number(pose.get(key)) for key in ("x", "y", "theta"))
    ):
        return StateValue("unknown", reason="地图、定位模式或有限坐标不完整")
    return StateValue(
        "reported",
        {"map_id": map_id, "x": pose["x"], "y": pose["y"], "yaw": pose["theta"]},
        "m,rad",
        remaining_ms=remaining,
        details={
            "localization_verified": False,
            "shared_frame_verified": False,
            "pose_measurement_time": None,
            "qualification": "报告位置；原定位测量时间、定位质量和共享坐标系未验证",
        },
    )


def decode_physical(value: JSONObject, name: str, retained: float) -> StateValue:
    """Decode existing World physical reports without renewing nested native sample windows."""
    if value.get("schema_version") != "roboguide.world.robot-physical-observation/v0.1":
        return StateValue("unknown", reason="物理观测内容不符合已声明的版本")
    posture = _object(value.get("posture"))
    if name in {"battery", "posture"}:
        sample = _object(value.get(name))
        if sample.get("availability") != "reported_current":
            return StateValue("unknown", reason="该字段没有当前有效的设备上报")
        remaining = sample.get("remaining_at_publisher_ms")
        window = _window(posture.get("aggregate_age_ms"), posture.get("source_stale_ms"), 0)
        if type(remaining) is not int or not 0 < remaining <= window:
            return StateValue("unknown", reason="原始采样窗口缺失或与来源不一致")
        scalar = {"percent": sample.get("percent"), "posture": sample.get("reported_posture")}
        return _chassis_value(scalar, name, remaining - retained)
    mapping = _object(value.get("mapping"))
    if mapping.get("response_availability") != "reported_current_response":
        return StateValue("unknown", reason="没有当前地图位置报告")
    age = mapping.get("response_age_ms")
    remaining = 10_000 - cast(float, age) - retained if number(age) and cast(float, age) >= 0 else 0
    if mapping.get("localization_verified") is not False or any(
        mapping.get(key) is not None
        for key in ("pose_measurement_time", "localization_quality", "map_revision_id")
    ):
        return StateValue("unknown", reason="位置报告不符合原始诊断格式")
    return _position(
        {
            "map_id": mapping.get("map_id"),
            "mode": mapping.get("mode"),
            "pose": mapping.get("reported_pose"),
        },
        remaining,
    )


def decode_lite3(value: JSONObject, name: str, retained: float) -> StateValue:
    """Interpret the exact legacy robot-state profile independently of its transport device."""
    if name in {"battery", "posture"}:
        chassis = _object(value.get("chassis"))
        original = _object(chassis.get("value"))
        if (
            chassis.get("contract_id") != "robonix/primitive/chassis/state"
            or chassis.get("availability") != "current"
            or original.get("stale") is not False
        ):
            return StateValue("unknown", reason="底盘状态来源不可用")
        remaining = _window(chassis.get("age_ms"), original.get("stale_ms"), retained)
        return _chassis_value(
            {
                "percent": _object(original.get("battery")).get("percent"),
                "posture": original.get("posture"),
            },
            name,
            remaining,
        )
    mapping = _object(value.get("mapping"))
    original = _object(mapping.get("value"))
    if mapping.get("availability") != "current" or original.get("has_map") is not True:
        return StateValue("unknown", reason="没有当前地图位置报告")
    age = mapping.get("age_ms")
    remaining = 10_000 - cast(float, age) - retained if number(age) and cast(float, age) >= 0 else 0
    return _position(original, remaining)


DEFAULT_PROFILES = (
    StateProfile(
        "roboguide.world.robot-physical-observation/v0.1",
        "world",
        "robot-physical-observation",
        ("battery", "position", "posture"),
        decode_physical,
    ),
    StateProfile(
        "lite3.robot-state/v0.1",
        "node",
        "robot",
        ("battery", "position", "posture"),
        decode_lite3,
    ),
)
