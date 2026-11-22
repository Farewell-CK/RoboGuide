"""Bounded tool records for a single displayed Workbench turn."""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from mission.models import JSONObject

from roboguide_workbench.evidence import ReadResult
from roboguide_workbench.views import safe_text

ActivityStatus = Literal[
    "complete", "partial", "unavailable", "needs_context", "refused", "budget", "reused", "unknown"
]
ACTIVITY_KEY = "turn_activity"
MAX_TURNS = 10
MAX_RUNS = 32
MAX_TURN_BYTES = 16_384
MAX_LOG_BYTES = 128_000
_LABELS: dict[ActivityStatus, str] = {
    "complete": "完成",
    "partial": "部分完成",
    "unavailable": "不可用",
    "needs_context": "需补充",
    "refused": "未操作",
    "budget": "预算不足",
    "reused": "已复用",
    "unknown": "原操作结果未知",
}


def _size(value: object) -> int:
    """Measure the actual serialized UTF-8 bound used by the private session."""
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode())


def _display_text(value: str) -> str:
    """Preserve real line breaks while escaping all embedded terminal controls."""
    return "\n".join(safe_text(line) for line in value.split("\n"))


@dataclass(frozen=True)
class ToolRun:
    """Identify a host tool from admission to settlement."""

    call_id: str
    label: str
    target: str
    at: str
    started: float


class TurnActivity:
    """Collect live tool outcomes and freeze a local detail view before display acknowledgement."""

    def __init__(self, turn_id: str, sanitize: Callable[[str], str]) -> None:
        """Own just this turn's presentation and bounded record."""
        self.turn_id, self.sanitize = turn_id, sanitize
        self.rows: list[JSONObject] = []
        self.omitted = 0
        self._sequence = 0
        self.reply: str | None = None

    def begin(self, label: str, target: str = "", *, call_id: str = "") -> ToolRun:
        """Identify actual model calls or allocate a local identity for direct host reads."""
        self._sequence += 1
        return ToolRun(
            call_id or f"local-{self._sequence}",
            label.removeprefix("正在"),
            target,
            datetime.now(UTC).isoformat(timespec="seconds"),
            time.monotonic(),
        )

    def finish(
        self,
        run: ToolRun,
        status: ActivityStatus,
        *,
        result: ReadResult | None = None,
        reference: str = "",
    ) -> None:
        """Record a final status and host-provided evidence without storing arguments or raw I/O."""
        if len(self.rows) >= MAX_RUNS:
            self.omitted += 1
            return
        source = result.detail if result is not None else None
        detail = source if source is not None else result.text if result is not None else ""
        # Redact before truncation so a cut cannot remove the credential's surrounding syntax.
        clean = _display_text(self.sanitize(detail))
        if len(clean) > 1800:
            clean = clean[:1800] + "\n… 摘要已截断"
        row: JSONObject = {
            "call_id": safe_text(self.sanitize(run.call_id))[:160],
            "label": safe_text(run.label)[:80],
            "target": safe_text(self.sanitize(run.target))[:160],
            "at": run.at,
            "ms": max(0, int((time.monotonic() - run.started) * 1000)),
            "status": status,
            "reference": safe_text(self.sanitize(reference))[:160],
            "detail": clean,
        }
        snapshot = self.snapshot()
        if _size({**snapshot, "tools": [*self.rows, row]}) > MAX_TURN_BYTES:
            row["detail"] = "摘要超过本轮详情上限。"
        if _size({**snapshot, "tools": [*self.rows, row]}) > MAX_TURN_BYTES:
            self.omitted += 1
            return
        self.rows.append(row)

    def result(self, run: ToolRun, result: ReadResult, *, reference: str = "") -> None:
        """Reflect available evidence, retaining uncertainty separately from missing data."""
        status: ActivityStatus
        if result.complete:
            status = "complete"
        elif any(item.get("complete") is True for item in result.components):
            status = "partial"
        else:
            status = "unavailable"
        self.finish(run, status, result=result, reference=reference)

    def snapshot(self) -> JSONObject:
        """Produce a public projection of settled calls for the existing session writer."""
        return {"turn_id": self.turn_id, "tools": list(self.rows), "omitted": self.omitted}


def append_activity(previous: object, record: JSONObject) -> list[JSONObject]:
    """Retain a bounded tail without requiring migration of older session files."""
    rows = previous if isinstance(previous, list) else []
    retained = [
        item
        for item in rows[-(MAX_TURNS - 1) :]
        if isinstance(item, dict) and item.get("turn_id") != record["turn_id"]
    ]
    retained.append(record)
    while len(retained) > 1 and _size(retained) > MAX_LOG_BYTES:
        retained.pop(0)
    return retained


def displayed_activity(data: JSONObject) -> list[JSONObject]:
    """Load only activities belonging to acknowledged replies in the selected session."""
    messages, context = data.get("messages"), data.get("context")
    if not isinstance(messages, list) or not isinstance(context, dict):
        return []
    rows = context.get(ACTIVITY_KEY)
    if not isinstance(rows, list):
        return []
    visible = {
        row["turn_id"]
        for row in messages
        if isinstance(row, dict)
        and row.get("role") == "assistant"
        and row.get("displayed") is True
        and isinstance(row.get("turn_id"), str)
    }
    return [
        row
        for row in rows[-MAX_TURNS:]
        if isinstance(row, dict)
        and isinstance(row.get("turn_id"), str)
        and row["turn_id"] in visible
        and isinstance(row.get("tools"), list)
    ]


def activity_summary(trace: TurnActivity) -> str:
    """Collapse settled calls into a count without treating reused or unknown work as success."""
    counts = Counter(row["status"] for row in trace.rows)
    if not counts and not trace.omitted:
        return ""
    parts = [f"{counts[status]} {label}" for status, label in _LABELS.items() if counts[status]]
    if trace.omitted:
        parts.append(f"{trace.omitted} 条未列出")
    return "本轮检查 · " + " · ".join(parts)


def activity_lines(record: JSONObject) -> list[str]:
    """Render a historical detail record without consulting live state or tool adapters."""
    lines: list[str] = []
    tools = record.get("tools")
    for row in tools[:MAX_RUNS] if isinstance(tools, list) else []:
        if not isinstance(row, dict):
            continue
        label, target = row.get("label"), row.get("target")
        status, at, elapsed = row.get("status"), row.get("at"), row.get("ms")
        if not isinstance(label, str) or not isinstance(status, str):
            continue
        target_label = f" · {safe_text(target)}" if isinstance(target, str) and target else ""
        state = next((value for key, value in _LABELS.items() if status == key), "状态未知")
        lines.append(f"{safe_text(label)}{target_label} · {state}")
        if isinstance(at, str) and type(elapsed) is int:
            ref = row.get("reference")
            suffix = f" · {safe_text(ref)}" if isinstance(ref, str) and ref else ""
            lines.append(f"  采集 {safe_text(at)} · {elapsed} ms{suffix}")
        detail = row.get("detail")
        if isinstance(detail, str) and detail:
            lines.extend("  " + line for line in _display_text(detail[:2000]).split("\n"))
        lines.append("")
    if record.get("omitted"):
        lines.append("部分活动超过本轮详情上限。")
    return lines or ["这轮没有已保存的工具活动。"]
