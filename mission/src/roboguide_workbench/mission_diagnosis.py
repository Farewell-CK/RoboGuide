"""Scoped Mission diagnosis from original execution attempts and stop records."""

from __future__ import annotations

from typing import cast

from mission.models import JSONObject

from roboguide_workbench.client import ReadOnlyController, WorkbenchError, identifier
from roboguide_workbench.evidence import ReadResult
from roboguide_workbench.views import render_mission, safe_text

_ATTEMPT_FIELDS = ("execution_id", "group_id", "task_id", "role_id", "node_id", "status")
_IN_FLIGHT = frozenset({"Dispatched", "Accepted", "Running", "Unknown"})
_TERMINAL = frozenset({"Completed", "Failed", "Cancelled"})
_MAX_ATTEMPTS = 16_384
_DISPLAY_ATTEMPTS = 6
_STOP_PHASES = {
    "stop_requested": "已记录停止请求",
    "stop_command_accepted": "已记录取消接收回执",
    "safe_state_observed": "已记录安全状态观测",
    "terminal_process_observed": "已记录原进程终止",
    "stop_confirmed": "已记录停止确认",
    "continuation_prepared": "已准备后续处置",
    "group_stopped": "已记录执行组停止",
    "manual_escalation": "需要人工处理",
}
_CONFIRMED_PHASES = frozenset({"stop_confirmed", "continuation_prepared", "group_stopped"})
_MISSING_SAFETY_CONTEXT = "operation safety declaration or current session is unavailable"
_STOP_REASONS = {_MISSING_SAFETY_CONTEXT: "该操作的安全声明或当时的会话不可用"}
_RECEIPTS = {
    "accepted_at": "取消接收回执",
    "stopped_at": "执行终止记录",
    "safe_proof": "安全状态观测",
}


def _current_attempts(
    raw: JSONObject, mission: str, group: str, tasks: dict[str, str]
) -> list[dict[str, str]]:
    """Validate original identities before selecting any current Group attempt."""
    entries = raw.get("attempts")
    if not isinstance(entries, list) or len(entries) > _MAX_ATTEMPTS:
        raise WorkbenchError("执行尝试列表不可读或超过上限。")
    seen: set[str] = set()
    current: list[dict[str, str]] = []
    for row in entries:
        if not isinstance(row, dict) or row.get("mission_id") != mission:
            raise WorkbenchError("执行尝试与当前 Mission 的身份不匹配。")
        item: dict[str, str] = {}
        for key in _ATTEMPT_FIELDS:
            value = row.get(key)
            if not isinstance(value, str):
                raise WorkbenchError("执行尝试缺少原始身份或状态。")
            item[key] = identifier(value, limit=1024 if key == "execution_id" else 256)
        execution_id = item["execution_id"]
        if execution_id in seen or item["status"] not in _IN_FLIGHT | _TERMINAL:
            raise WorkbenchError("执行尝试身份重复或状态无法识别。")
        seen.add(execution_id)
        if item["group_id"] == group:
            if item["task_id"] not in tasks:
                raise WorkbenchError("当前执行组的尝试无法与 Mission 步骤核对。")
            current.append(item)
    return current


def _timestamp(value: object) -> bool:
    """Recognize original millisecond clocks while refusing boolean or fabricated times."""
    return type(value) is int and value >= 0


def _stop_lines(raw: JSONObject, mission: str, attempt: dict[str, str]) -> list[str]:
    """Describe verified receipt presence without upgrading it to physical evidence."""
    purpose = raw.get("purpose")
    if (
        raw.get("schema_version") != "roboguide.safety-stop-view/v0.2"
        or raw.get("command_receipt_is_stop_proof") is not False
        or not isinstance(purpose, dict)
        or not isinstance(record := purpose.get("record"), dict)
        or not isinstance(identity := record.get("identity"), dict)
    ):
        raise WorkbenchError("原停止记录无法验证。")
    if (
        identity.get("execution_id") != attempt["execution_id"]
        or identity.get("group_id") != attempt["group_id"]
        or identity.get("role_id") != attempt["role_id"]
        or identity.get("task_ref") != {"mission_id": mission, "task_id": attempt["task_id"]}
        or purpose.get("node_id") != attempt["node_id"]
    ):
        raise WorkbenchError("停止记录与原执行身份不匹配。")
    phase = record.get("phase")
    confirmation = record.get("confirmed_at")
    observation = purpose.get("safe_proof")
    if (
        not isinstance(phase, str)
        or phase not in _STOP_PHASES
        or (phase in _CONFIRMED_PHASES and not _timestamp(confirmation))
        or not set(_RECEIPTS).issubset(purpose)
        or any(
            purpose[key] is not None and not _timestamp(purpose[key])
            for key in ("accepted_at", "stopped_at")
        )
        or (observation is not None and (not isinstance(observation, dict) or not observation))
    ):
        raise WorkbenchError("停止阶段或原回执记录格式无法验证。")

    lines = [f"停止记录显示{_STOP_PHASES[phase]}（{phase}）。"]
    reason = purpose.get("reason")
    if isinstance(reason, str) and reason.strip():
        reason_text = safe_text(_STOP_REASONS.get(reason, reason[:400]))
        lines.append(f"Controller 记录的原因是{reason_text}。")
    elif phase not in _CONFIRMED_PHASES:
        lines.append("停止记录没有说明卡住的原因。")
    if phase not in _CONFIRMED_PHASES:
        missing = [label for key, label in _RECEIPTS.items() if purpose[key] is None]
        if missing:
            lines.append("尚待核对" + "、".join(missing) + "，原停止记录中未取得这些证据。")
        lines.append("这次读取无法确认设备已停止。")
    return lines


def read_mission_diagnosis(
    controller: ReadOnlyController, mission_id: str, task_id: str = ""
) -> tuple[ReadResult, JSONObject]:
    """Trace one current Mission and at most one original stop, spending at most three GETs."""
    mission_id = identifier(mission_id)
    task_id = identifier(task_id) if task_id else ""
    mission = controller.mission(mission_id)
    render_mission(mission, mission_id)
    group = cast(str, mission["group_id"])
    status = cast(str, mission["status"])
    tasks = {
        cast(str, row["task_id"]): cast(str, row["status"])
        for row in cast(list[JSONObject], mission["tasks"])
    }
    components: list[JSONObject] = [{"source": "Mission 详情", "complete": True}]
    original: JSONObject = {"mission": mission}
    lines = [f"Mission {safe_text(mission_id)} · {status}"]

    def finish() -> tuple[ReadResult, JSONObject]:
        """Return only evidence from successfully joined reads with a scoped raw view."""
        lines.append("输入 /raw 查看本次取得的原记录。")
        complete = all(component["complete"] is True for component in components)
        return ReadResult("\n".join(lines), complete, tuple(components)), original

    if task_id and task_id not in tasks:
        lines.append(f"这个 Mission 的当前步骤中没有 {safe_text(task_id)}。")
        components.append({"source": "Task 选择", "complete": False})
        return finish()
    try:
        attempts = controller.mission_attempts(mission_id)
        current = _current_attempts(attempts, mission_id, group, tasks)
    except (ValueError, OSError) as error:
        lines.append("执行尝试不可用，无法定位卡点。" + safe_text(str(error)))
        components.append({"source": "执行尝试", "complete": False})
        return finish()
    original["execution_attempts"] = attempts
    components.append({"source": "执行尝试", "complete": True})

    candidates = [
        attempt
        for attempt in current
        if (not task_id or attempt["task_id"] == task_id)
        and (
            attempt["status"] in _IN_FLIGHT
            if status == "Cancelling"
            else attempt["status"] == "Unknown"
        )
    ]
    if not candidates:
        if task_id:
            lines.append(f"当前步骤 {safe_text(task_id)} · {tasks[task_id]}")
        lines.append("当前执行记录里没有关联的待停止或 Unknown 尝试。")
        if status == "Cancelling" and not (task_id and tasks[task_id] in _TERMINAL):
            lines.append("取消中的具体卡点缺少可核对的原执行记录。")
            components.append({"source": "停止记录关联", "complete": False})
        elif status != "Cancelling":
            lines.append("可用 /explain 查看任务、资源绑定和恢复评估。")
        return finish()
    if len(candidates) > 1:
        lines.append(f"有 {len(candidates)} 条可能相关的原执行，选择其中一条需要更多依据。")
        lines.extend(
            f"{safe_text(row['task_id'])} · {safe_text(row['role_id'])} · {row['status']}"
            for row in candidates[:_DISPLAY_ATTEMPTS]
        )
        if len(candidates) > _DISPLAY_ATTEMPTS:
            lines.append(f"以上仅列前 {_DISPLAY_ATTEMPTS} 条。")
        if task_id:
            lines.append("这个步骤仍对应多条执行记录，请查看原记录确认相关尝试。")
        else:
            lines.append("可以用 /diagnose MissionID TaskID 缩小范围。")
        components.append({"source": "停止记录关联", "complete": False})
        return finish()

    choice = candidates[0]
    task = choice["task_id"]
    lines.append(
        f"关注步骤 {safe_text(task)} · {tasks[task]} · 原执行 {choice['status']} · "
        f"{safe_text(choice['node_id'])}"
    )
    try:
        stop = controller.execution_safety_stop(choice["execution_id"])
        lines.extend(_stop_lines(stop, mission_id, choice))
    except (ValueError, OSError) as error:
        lines.append("原停止记录不可用，取消接收和停止状态目前无法核对。" + safe_text(str(error)))
        components.append({"source": "原停止记录", "complete": False})
    else:
        original["safety_stop"] = stop
        components.append({"source": "原停止记录", "complete": True})
    return finish()
