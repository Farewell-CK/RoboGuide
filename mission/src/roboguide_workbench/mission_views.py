"""Host-owned Mission list projections for the read-only chat workspace."""

from __future__ import annotations

from dataclasses import dataclass, field

from mission.models import JSONObject

from roboguide_workbench.client import WorkbenchError, identifier
from roboguide_workbench.views import safe_text

MISSION_LIFECYCLES = frozenset(
    {"Accepted", "Running", "Cancelling", "Completed", "Failed", "Cancelled"}
)
ACTIVE_LIFECYCLES = frozenset({"Accepted", "Running", "Cancelling"})
MAX_ROWS = 128
MAX_OBJECTIVE_CHARS = 160
MAX_PROJECTION_BYTES = 20_000
CHANNEL_OWNERS = {
    "accepted-plan": ("desired", "roboguide:mission-orchestrator"),
    "mission-lifecycle": ("derived", "roboguide:runtime-orchestration"),
}


@dataclass
class MissionSummary:
    """Keep runtime lifecycle evidence independent of optional accepted-plan descriptions."""

    mission_id: str
    lifecycle_values: list[str] = field(default_factory=list)
    lifecycle_invalid: bool = False
    lifecycle_stale: bool = False
    provenance_missing: bool = False
    objective: str | None = None
    objective_conflict: bool = False
    objective_unavailable: bool = False

    def observe(self, record: JSONObject) -> None:
        """Merge only an original channel; metadata defects never erase runtime state."""
        value, stale = record.get("value"), record.get("stale")
        if record.get("channel_id") == "accepted-plan":
            objective = value.get("objective") if isinstance(value, dict) else None
            if not isinstance(objective, str) or stale is True:
                self.objective_unavailable = True
            elif stale is not None and not isinstance(stale, bool):
                self.objective_unavailable = True
            elif self.objective is None:
                self.objective = objective
            elif self.objective != objective:
                self.objective_conflict = True
            return
        if not isinstance(value, dict):
            self.lifecycle_invalid = True
            return
        lifecycle = value.get("lifecycle")
        if not isinstance(lifecycle, str) or lifecycle not in MISSION_LIFECYCLES:
            self.lifecycle_invalid = True
        elif lifecycle not in self.lifecycle_values:
            self.lifecycle_values.append(lifecycle)
        if stale is True:
            self.lifecycle_stale = True
        elif stale is not None and not isinstance(stale, bool):
            self.lifecycle_invalid = True

    @property
    def lifecycle(self) -> str:
        """Count only an unambiguous available runtime lifecycle, never Task or plan details."""
        if (
            self.provenance_missing
            or self.lifecycle_invalid
            or self.lifecycle_stale
            or len(self.lifecycle_values) != 1
        ):
            return "Unknown"
        return self.lifecycle_values[0]

    @property
    def display_lifecycle(self) -> str:
        """Retain actual conflicting values and label unavailable runtime evidence explicitly."""
        if self.provenance_missing:
            return "Unknown（缺少来源身份）"
        if len(self.lifecycle_values) > 1:
            return "Unknown（生命周期冲突：" + "/".join(self.lifecycle_values) + "）"
        if self.lifecycle_stale:
            return "Unknown（生命周期过期）"
        if self.lifecycle_invalid:
            return "Unknown（生命周期无效）"
        if not self.lifecycle_values:
            return "Unknown（缺少生命周期）"
        return self.lifecycle

    @property
    def display_objective(self) -> str:
        """Describe missing/conflicting plan metadata independently of known lifecycle facts."""
        if self.objective_conflict:
            return "目标存在冲突，未选用任一描述"
        if self.objective is None:
            return "目标未提供"
        suffix = "（部分目标元数据不可用）" if self.objective_unavailable else ""
        return _objective(self.objective) + suffix


def _summaries(raw: JSONObject) -> tuple[list[MissionSummary], bool, int, int]:
    """Validate the current cohort without granting other source channels runtime authority."""
    if raw.get("schema") != "roboguide.state-query/v0.1":
        raise WorkbenchError("State 查询响应格式无效。")
    records = raw.get("records")
    if not isinstance(records, list):
        raise WorkbenchError("State 查询缺少完整 records。")
    observed = raw.get("observed_at_ms")
    if observed is not None and (type(observed) is not int or observed < 0):
        raise WorkbenchError("State 观察时间无效。")
    by_id: dict[str, MissionSummary] = {}
    malformed = ignored = 0
    for record in records:
        if not isinstance(record, dict):
            malformed += 1
            continue
        obj = record.get("object")
        if not isinstance(obj, dict) or not isinstance(obj.get("object_type"), str):
            malformed += 1
            continue
        if obj["object_type"] != "mission":
            ignored += 1
            continue
        object_id = obj.get("object_id")
        if not isinstance(object_id, str):
            raise WorkbenchError("任务列表含无效 Mission ID；无法建立当前身份范围。")
        mission_id = identifier(object_id)
        channel = record.get("channel_id")
        if not isinstance(channel, str):
            malformed += 1
            continue
        if channel not in CHANNEL_OWNERS:
            ignored += 1
            continue
        # The live server always supplies this complete original attribution. Explicit
        # foreign sources remain independent; missing attribution cannot borrow the
        # built-in orchestrator's authority, even when a lifecycle value looks valid.
        semantic, source = CHANNEL_OWNERS[channel]
        attribution = (
            (obj.get("class"), "roboguide"),
            (record.get("semantic"), semantic),
            (record.get("source"), source),
        )
        if any(isinstance(actual, str) and actual != expected for actual, expected in attribution):
            ignored += 1
            continue
        summary = by_id.setdefault(mission_id, MissionSummary(mission_id))
        if any(actual != expected for actual, expected in attribution):
            if channel == "mission-lifecycle":
                summary.provenance_missing = True
            else:
                summary.objective_unavailable = True
            malformed += 1
            continue
        summary.observe(record)
    summaries = list(by_id.values())
    complete = not malformed and all(summary.lifecycle != "Unknown" for summary in summaries)
    return summaries, complete, ignored, malformed


def _objective(value: str) -> str:
    """Bound escaped user objectives without silently presenting their prefix as complete."""
    value = safe_text(value)
    if len(value) <= MAX_OBJECTIVE_CHARS:
        return value
    return value[:MAX_OBJECTIVE_CHARS] + "…（目标已截断）"


def render_mission_list(
    raw: JSONObject, *, active_only: bool = False
) -> tuple[str, bool, list[str]]:
    """Render complete runtime aggregates and bounded rows from this one current State response.

    Returned IDs exactly match the rows rendered. Display truncation never changes the
    complete source aggregates; missing runtime evidence remains explicitly Unknown.
    """
    summaries, complete, ignored, malformed = _summaries(raw)
    states = ("Running", "Accepted", "Cancelling", "Completed", "Failed", "Cancelled", "Unknown")
    counts = dict.fromkeys(states, 0)
    for summary in summaries:
        counts[summary.lifecycle] += 1
    active_count = sum(counts[state] for state in ACTIVE_LIFECYCLES)
    rows = (
        [
            summary
            for summary in summaries
            if summary.lifecycle not in {"Completed", "Failed", "Cancelled"}
        ]
        if active_only
        else summaries
    )
    lines = [
        f"当前身份可见 Mission：{len(summaries)} 个。",
        f"Running={counts['Running']}，Accepted={counts['Accepted']}，"
        f"Cancelling={counts['Cancelling']}，Unknown={counts['Unknown']}。",
        f"终态：Completed={counts['Completed']}，Failed={counts['Failed']}，"
        f"Cancelled={counts['Cancelled']}。",
    ]
    if active_only:
        lines.insert(0, f"当前正在运行的 Mission：{counts['Running']} 个（Running）。")
        lines.append(f"当前活动 Mission：{active_count} 个；另有状态未知：{counts['Unknown']} 个。")
        if not rows and complete:
            lines.append("当前响应没有 Running、Accepted 或 Cancelling 的 Mission。")
    shown: list[MissionSummary] = []
    for summary in rows[:MAX_ROWS]:
        candidate = (
            f"{len(shown) + 1}. {safe_text(summary.mission_id)}："
            f"{summary.display_lifecycle} — {summary.display_objective}"
        )
        if len("\n".join((*lines, candidate)).encode()) > MAX_PROJECTION_BYTES:
            break
        shown.append(summary)
        lines.append(candidate)
    if len(rows) > len(shown):
        lines.append(
            f"仅展示当前响应前 {len(shown)}/{len(rows)} 行（最多 {MAX_ROWS} 行及 20 KB）；"
            "完整数量仍为上方聚合。"
        )
    if ignored:
        lines.append(f"另有 {ignored} 条其它对象/来源/通道记录，未作为 Mission 生命周期依据。")
    if malformed:
        lines.append(f"响应含 {malformed} 条格式异常记录，来源不完整；不能排除未识别的活动任务。")
    if counts["Unknown"]:
        lines.append(
            "部分 Mission 生命周期缺失、无效、冲突或过期；Unknown 保留，不能据此断言运行状态。"
        )
    observed = raw.get("observed_at_ms")
    if type(observed) is int:
        lines.append(f"Controller State 观察时间：{observed} ms（原始时钟）。")
    lines.append(
        "范围：当前 Controller、当前身份可见的 State 记录；"
        "Accepted 表示已接纳，Cancelling 表示取消中，均与 Running 分别计数。"
        "Mission 终态不会根据 Task 的 Active 明细改为运行中。"
    )
    return "\n".join(lines), complete, [summary.mission_id for summary in shown]
