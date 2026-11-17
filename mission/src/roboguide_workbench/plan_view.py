"""Readable exact plan documents and version identities for acknowledged terminal views."""

from __future__ import annotations

import json
from typing import cast

import yaml
from mission.models import JSONObject

from roboguide_workbench.views import safe_text

LIFECYCLE_LABELS = {
    "Received": "已收到请求",
    "Interpreting": "正在理解目标",
    "NeedsClarification": "需要补充信息",
    "Drafted": "草案已生成",
    "Reviewing": "正在审查计划",
    "Repairing": "正在修订计划",
    "AwaitingApproval": "等待风险审批",
    "AwaitingExecution": "规划完成，尚未执行",
    "Submitting": "正在提交执行请求",
    "Accepted": "执行请求已接纳",
    "Blocked": "请求受阻",
    "Failed": "请求失败",
    "Cancelled": "请求已取消",
}


def plan_document(plan: JSONObject) -> str:
    """Show every original plan field with readable indentation and safe exact scalar values."""
    # Let the terminal wrap long scalar values. Inserting YAML folds into encoded
    # adapter JSON would split documents that feedback must decode and redact.
    text = yaml.safe_dump(plan, allow_unicode=True, sort_keys=False, width=1_000_000)
    # Preserve invisible characters as JSON escapes rather than changing a plain YAML
    # scalar's value during terminal sanitization. No model summary replaces parameters.
    if any(safe_text(line) != line for line in text.split("\n")):
        return json.dumps(plan, ensure_ascii=True, indent=2)
    return text.rstrip("\n")


def plan_overview(plan: JSONObject) -> str:
    """Offer a bounded reading guide separately from the complete authoritative document."""
    tasks = plan.get("tasks")
    rows = cast(list[JSONObject], tasks) if isinstance(tasks, list) else []
    lines = [f"计划概览：{len(rows)} 个步骤"]
    for index, task in enumerate(rows, 1):
        text = str(task.get("description") or task.get("id") or "步骤")
        text = " ".join(text.split())
        lines.append(f"{index}. {safe_text(text[:160])}" + ("…" if len(text) > 160 else ""))
    return "\n".join(lines)


def same_plan(left: JSONObject | None, right: JSONObject) -> bool:
    """Reuse display only for the same acknowledged Request, revision, digest and whole plan."""
    return bool(
        left is not None
        and isinstance(left.get("plan"), dict)
        and isinstance(right.get("plan"), dict)
        and all(
            left.get(key) == right.get(key)
            for key in ("request_id", "draft_revision", "draft_digest", "plan")
        )
    )
