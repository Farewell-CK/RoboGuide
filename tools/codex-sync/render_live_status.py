#!/usr/bin/env python3
"""Render a public live-status summary from completed E2 recovery evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
RECOVERY_EVENT_ORDER = (
    "fault_triggered",
    "local_handle_confirmed",
    "recovery_required",
    "recovery_authorized",
    "same_owner_restarted",
    "same_owner_registered",
    "rebind_completed",
)


@dataclass(frozen=True)
class RunEvidence:
    run_id: str
    finished_at: datetime
    fault_profile: str
    public_task: str
    task_success: bool
    full_fault_run_success: bool
    injection_valid: bool
    infrastructure_ok: bool
    recovery_protocol: str
    agent_id: int | None
    agent_class: str | None
    node_id: str | None
    action: str | None
    roboguide_commit: str | None
    model: str | None
    goal_satisfied: int | None
    goal_total: int | None
    failure_category: str
    timeline_events: tuple[str, ...]


def _json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _json_array(path: Path) -> list[Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError(f"expected a JSON array: {path}")
    return value


def _safe_text(value: Any, *, limit: int = 180) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()
    text = re.sub(r"\s+", " ", text).replace("|", "\\|")
    return text[:limit]


def _git_head(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _ordered_subsequence(events: Iterable[str], expected: tuple[str, ...]) -> bool:
    iterator = iter(events)
    return all(any(event == target for event in iterator) for target in expected)


def _recovery_protocol(
    verdict: dict[str, Any], timeline: list[dict[str, Any]]
) -> str:
    profile = str(verdict.get("fault_profile") or "unknown")
    if profile == "f0-clean":
        return "NOT_APPLICABLE"
    if not verdict.get("injection_valid") or not verdict.get("infrastructure_ok"):
        return "NOT_EVALUABLE"

    events = tuple(
        str(item.get("event"))
        for item in timeline
        if isinstance(item, dict) and item.get("event")
    )
    if not _ordered_subsequence(events, RECOVERY_EVENT_ORDER):
        return "FAIL"

    phases = [
        item
        for item in timeline
        if isinstance(item, dict) and item.get("event") == "recovery_phase"
    ]
    awaiting_stop = any(
        item.get("disposition") == "AwaitingStop"
        and (item.get("recovery_view") or {}).get("execution_status") == "Unknown"
        for item in phases
    )
    confirmed_stop = any(
        item.get("disposition") == "Superseded"
        and (item.get("recovery_view") or {}).get("execution_status") == "Cancelled"
        and bool(
            ((item.get("recovery_view") or {}).get("stop_intent") or {}).get(
                "release_authorized"
            )
        )
        for item in phases
    )
    rebind = next(
        (
            item
            for item in timeline
            if isinstance(item, dict) and item.get("event") == "rebind_completed"
        ),
        None,
    )
    if not (awaiting_stop and confirmed_stop and rebind):
        return "FAIL"

    old_id = rebind.get("original_execution_id")
    new_id = rebind.get("replacement_execution_id")
    attempts = verdict.get("controller_attempts") or []
    statuses = {
        item.get("execution_id"): item.get("status")
        for item in attempts
        if isinstance(item, dict)
    }
    if not old_id or not new_id:
        return "FAIL"
    if statuses.get(old_id) != "Cancelled" or statuses.get(new_id) != "Completed":
        return "FAIL"
    return "PASS"


def _fault_target(
    verdict: dict[str, Any], timeline: list[dict[str, Any]]
) -> tuple[int | None, str | None, str | None, str | None]:
    trigger = next(
        (
            item
            for item in timeline
            if isinstance(item, dict) and item.get("event") == "fault_triggered"
        ),
        {},
    )
    invocation = trigger.get("accepted_invocation") or {}
    parameters = invocation.get("parameters") or {}
    agent_id = trigger.get("target_agent_id", parameters.get("agent_id"))
    agent_class = parameters.get("agent_class")
    action = parameters.get("action")

    handle = next(
        (
            item
            for item in timeline
            if isinstance(item, dict) and item.get("event") == "local_handle_confirmed"
        ),
        {},
    )
    if agent_id is None:
        agent_id = handle.get("agent_id")
    node_id = handle.get("node_id")

    if agent_class is None and agent_id is not None:
        graph = ((verdict.get("pre_fault_graph") or {}).get("graph") or {})
        for node in graph.get("nodes") or []:
            if isinstance(node, dict) and node.get("id") == agent_id:
                agent_class = node.get("class_name")
                break
    return agent_id, agent_class, node_id, action


def _goal_counts(verdict: dict[str, Any]) -> tuple[int | None, int | None]:
    goal = ((verdict.get("base_task_verdict") or {}).get("official_goal_check") or {})
    satisfied = goal.get("satisfied")
    unsatisfied = goal.get("unsatisfied")
    if not isinstance(satisfied, list) or not isinstance(unsatisfied, dict):
        return None, None
    return len(satisfied), len(satisfied) + len(unsatisfied)


def _failure_category(verdict: dict[str, Any], recovery_protocol: str) -> str:
    if verdict.get("task_success"):
        return "none"
    reason = str((verdict.get("base_task_verdict") or {}).get("failure_reason") or "")
    if reason.startswith("planning failed"):
        if recovery_protocol == "PASS":
            return "planning/provider failure after Recovery completed"
        if not verdict.get("injection_valid"):
            return "planning/provider failure before valid fault injection"
        return "planning/provider failure"
    if reason.startswith("controller Running"):
        return "Controller remained Running; Recovery did not complete"
    if not verdict.get("infrastructure_ok"):
        return "infrastructure failure"
    if not verdict.get("injection_valid"):
        return "fault injection was not reached or was invalid"
    return "task unsuccessful; inspect private evidence"


def load_run(verdict_path: Path, results_root: Path) -> RunEvidence:
    verdict = _json_object(verdict_path)
    timeline_path = verdict_path.with_name("fault-timeline.json")
    timeline_raw = _json_array(timeline_path) if timeline_path.exists() else []
    timeline = [item for item in timeline_raw if isinstance(item, dict)]
    provenance_path = verdict_path.with_name("provenance.json")
    provenance = _json_object(provenance_path) if provenance_path.exists() else {}
    fault_spec_path = verdict_path.with_name("fault-spec.json")
    fault_spec = _json_object(fault_spec_path) if fault_spec_path.exists() else {}
    base = verdict.get("base_task_verdict") or {}

    agent_id, agent_class, node_id, action = _fault_target(verdict, timeline)
    goal_satisfied, goal_total = _goal_counts(verdict)
    recovery_protocol = _recovery_protocol(verdict, timeline)
    events = tuple(
        str(item.get("event")) for item in timeline if item.get("event") is not None
    )
    return RunEvidence(
        run_id=verdict_path.parent.relative_to(results_root).as_posix(),
        finished_at=datetime.fromtimestamp(verdict_path.stat().st_mtime, timezone.utc),
        fault_profile=str(verdict.get("fault_profile") or "unknown"),
        public_task=str(
            base.get("public_task")
            or fault_spec.get("public_task")
            or provenance.get("public_task")
            or "unknown"
        ),
        task_success=bool(verdict.get("task_success")),
        full_fault_run_success=bool(verdict.get("full_fault_run_success")),
        injection_valid=bool(verdict.get("injection_valid")),
        infrastructure_ok=bool(verdict.get("infrastructure_ok")),
        recovery_protocol=recovery_protocol,
        agent_id=agent_id if isinstance(agent_id, int) else None,
        agent_class=str(agent_class) if agent_class else None,
        node_id=str(node_id) if node_id else None,
        action=str(action) if action else None,
        roboguide_commit=str(provenance.get("roboguide_commit") or "") or None,
        model=str(provenance.get("model") or "") or None,
        goal_satisfied=goal_satisfied,
        goal_total=goal_total,
        failure_category=_failure_category(verdict, recovery_protocol),
        timeline_events=events,
    )


def discover_runs(results_root: Path) -> list[RunEvidence]:
    runs = [load_run(path, results_root) for path in results_root.rglob("fault-verdict.json")]
    return sorted(runs, key=lambda run: (run.finished_at, run.run_id))


def _active_section(active_state_path: Path | None) -> list[str]:
    if active_state_path is None:
        return [
            "- 状态：`未声明`",
            "- 说明：没有提供可信的独立 active-run 状态文件；不得据此推断实验正在运行。",
        ]
    state = _json_object(active_state_path)
    if state.get("schema") != "roboguide.codex-sync-active-run/v0.1":
        raise ValueError("unsupported active-run state schema")
    status = state.get("status")
    if status not in {"idle", "running", "blocked"}:
        raise ValueError("active-run status must be idle, running, or blocked")
    lines = [f"- 状态：`{status}`"]
    for key, label in (
        ("public_task", "任务"),
        ("run_id", "运行 ID"),
        ("fault_profile", "故障条件"),
        ("roboguide_commit", "目标 commit"),
        ("observed_at", "观测时间"),
    ):
        if state.get(key):
            lines.append(f"- {label}：`{_safe_text(state[key])}`")
    return lines


def render_markdown(
    runs: list[RunEvidence],
    *,
    generated_at: datetime,
    generator_head: str,
    active_state_path: Path | None,
) -> str:
    completed = len(runs)
    task_successes = sum(run.task_success for run in runs)
    protocol_passes = sum(run.recovery_protocol == "PASS" for run in runs)
    protocol_failures = sum(run.recovery_protocol == "FAIL" for run in runs)
    protocol_not_evaluable = sum(run.recovery_protocol == "NOT_EVALUABLE" for run in runs)
    latest = runs[-1] if runs else None
    timestamp = generated_at.astimezone(LOCAL_TIMEZONE).isoformat(timespec="seconds")

    lines = [
        "# RoboGuide Recovery Live Status",
        "",
        f"生成时间：{timestamp}",
        "",
        "本文件由已完成运行的 `fault-verdict.json`、`fault-timeline.json` 和 `provenance.json` 白名单字段生成。它不复制原始日志、Provider 响应、凭据或聊天记录。",
        "",
        "## 当前实验",
        "",
        "- 目标：验证 RoboGuide 在执行前 Node loss 后的 confirmed-stop、same-owner Recovery Protocol；Task Success 单独统计。",
        "- 当前范围：官方 `env4/task17` 机制 Pilot；不代表备用机器人接替或跨任务泛化。",
        f"- 同步工作树基线（生成前）：`{generator_head}`",
        "",
        "## 正在运行",
        "",
        *_active_section(active_state_path),
        "",
        "## 汇总",
        "",
        "| 指标 | 数值 |",
        "| --- | ---: |",
        f"| 已产生机器 verdict 的独立运行 | {completed} |",
        f"| Task Success | {task_successes} |",
        f"| Task Failure | {completed - task_successes} |",
        f"| Recovery Protocol PASS | {protocol_passes} |",
        f"| Recovery Protocol FAIL | {protocol_failures} |",
        f"| Recovery Protocol NOT_EVALUABLE | {protocol_not_evaluable} |",
        "",
        "注：clean 运行的 Recovery Protocol 为 `NOT_APPLICABLE`，不计入上述三项 Recovery 数量。",
        "",
        "## 最新完成运行",
        "",
    ]
    if latest is None:
        lines.extend(["暂无完成运行。", ""])
    else:
        target = "未记录"
        if latest.agent_id is not None:
            label = f"agent {latest.agent_id}"
            if latest.agent_class:
                label += f" ({_safe_text(latest.agent_class)})"
            if latest.node_id:
                label += f" / {_safe_text(latest.node_id)}"
            target = label
        goal = "未记录"
        if latest.goal_satisfied is not None and latest.goal_total is not None:
            goal = f"{latest.goal_satisfied}/{latest.goal_total}"
        lines.extend(
            [
                f"- 运行：`{_safe_text(latest.run_id)}`",
                f"- 完成时间：`{latest.finished_at.astimezone(LOCAL_TIMEZONE).isoformat(timespec='seconds')}`",
                f"- 任务 / 条件：`{_safe_text(latest.public_task)}` / `{_safe_text(latest.fault_profile)}`",
                f"- 实际故障机器人：`{target}`",
                f"- 故障动作：`{_safe_text(latest.action) if latest.action else '未记录'}`",
                f"- Recovery Protocol：`{latest.recovery_protocol}`",
                f"- Task Success：`{'PASS' if latest.task_success else 'FAIL'}`",
                f"- 官方目标：`{goal}`",
                f"- injection_valid / infrastructure_ok：`{str(latest.injection_valid).lower()}` / `{str(latest.infrastructure_ok).lower()}`",
                f"- 实验代码 commit：`{latest.roboguide_commit or '未记录'}`",
                f"- 模型：`{_safe_text(latest.model) if latest.model else '未记录'}`",
                "",
                "### 最近证据摘要",
                "",
                f"- 结论：Recovery Protocol `{latest.recovery_protocol}`；Task Success `{'PASS' if latest.task_success else 'FAIL'}`。两者不得合并表述。",
                f"- 失败分类：{_safe_text(latest.failure_category)}。",
                f"- 事件链：`{' -> '.join(_safe_text(event) for event in latest.timeline_events)}`",
                "",
            ]
        )

    lines.extend(
        [
            "## 最近运行",
            "",
            "| 完成时间 | 运行 | 条件 | Recovery | Task |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for run in reversed(runs[-5:]):
        lines.append(
            "| "
            + " | ".join(
                (
                    run.finished_at.astimezone(LOCAL_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S"),
                    _safe_text(run.run_id, limit=90),
                    _safe_text(run.fault_profile, limit=40),
                    run.recovery_protocol,
                    "PASS" if run.task_success else "FAIL",
                )
            )
            + " |"
        )
    if not runs:
        lines.append("| - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## 判定边界",
            "",
            "- `Recovery Protocol PASS` 要求有序事件链、`AwaitingStop/Unknown`、可信 `Cancelled`、授权释放、Rebind，以及旧/新 Attempt 分别为 `Cancelled/Completed`。",
            "- `Task Success` 只读取官方任务 verdict；Recovery 成功不等于任务成功。",
            "- `NOT_EVALUABLE` 表示故障注入无效或基础设施异常，不得计为 Recovery 成功或失败。",
            "- 本页仅汇总服务器本地已有证据；不会修改实验结果，也不会触发实验进程。",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output", type=Path, default=Path("docs/codex-sync/LIVE_STATUS.md")
    )
    parser.add_argument("--active-state", type=Path)
    parser.add_argument(
        "--generated-at",
        help="ISO-8601 timestamp for reproducible tests; defaults to current UTC time",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo = args.repo.resolve()
    results_root = args.results_root.resolve()
    if not results_root.is_dir():
        raise SystemExit(f"results root is not a directory: {results_root}")
    generated_at = (
        datetime.fromisoformat(args.generated_at.replace("Z", "+00:00"))
        if args.generated_at
        else datetime.now(timezone.utc)
    )
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)

    markdown = render_markdown(
        discover_runs(results_root),
        generated_at=generated_at,
        generator_head=_git_head(repo),
        active_state_path=args.active_state.resolve() if args.active_state else None,
    )
    output = args.output if args.output.is_absolute() else repo / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    temporary.write_text(markdown, encoding="utf-8", newline="\n")
    os.replace(temporary, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
