"""Validate current projections and render operator facts without model-generated claims."""

from __future__ import annotations

import json
import unicodedata
from collections import Counter
from typing import cast

from mission.controller import InventoryNode, InventorySnapshot, MissionControllerError
from mission.models import JSONObject

from roboguide_workbench.client import WorkbenchError, identifier

DISPLAY_LIMIT = 128
CAPABILITIES = {"mobility": "移动", "transport": "搬运", "compute": "计算", "observation": "观测"}
CONTRACT_LABELS = {
    "spatial.map.build@v0": "建图",
    "spatial.map.import@v0": "导入地图",
    "spatial.map.publish@v0": "发布地图",
    "spatial.localization.verify@v0": "验证定位",
    "mobility.navigate@v1": "导航",
    "mobility.move@v1": "移动",
    "object.relocate@v1": "搬运物体",
    "manipulation.grasp@v1": "抓取",
    "compute.infer@v1": "推理计算",
    "compute.prepare@v1": "准备计算产物",
    "observation.verify@v1": "验证观测条件",
    "observation.acquire@v1": "主动获取观测",
    "safety.observe@v1": "观测安全条件",
}
# Vocabulary aids only: a match still requires the actual returned kind or contract spelling.
SEARCH_TERMS = {
    "建图": ("spatial.map.build@",),
    "地图": ("spatial.map.",),
    "定位": ("spatial.localization.",),
    "导航": ("mobility.navigate@",),
    "移动": ("mobility",),
    "搬运": ("transport", "object.relocate@"),
    "抓取": ("manipulation.grasp@",),
    "计算": ("compute",),
    "推理": ("compute.infer@",),
    "观测": ("observation",),
}
TASK_STATES = {
    "Pending",
    "Ready",
    "Active",
    "Blocked",
    "AwaitingSatisfaction",
    "Completed",
    "Failed",
    "SatisfactionRejected",
    "Cancelled",
}
MISSION_STATES = {"Accepted", "Running", "Cancelling", "Completed", "Failed", "Cancelled"}


def safe_text(value: str) -> str:
    """Escape terminal controls and invisible formatting in all external display strings."""
    return "".join(
        f"\\u{ord(char):04x}" if unicodedata.category(char).startswith("C") else char
        for char in value
    )


def inventory_view(raw: JSONObject) -> InventorySnapshot:
    """Validate complete inventory including current quantitative resource extensions.

    The shared legacy MI parser receives its original field set. Optional current resource
    metadata remains in the raw response and is validated here; no shared contract is changed.
    """
    normalized = cast(JSONObject, json.loads(json.dumps(raw)))
    nodes = normalized.get("nodes")
    if not isinstance(nodes, list):
        raise WorkbenchError("库存响应缺少节点列表；不能计算节点数量。")
    for raw_node in nodes:
        if not isinstance(raw_node, dict):
            raise WorkbenchError("节点资源响应格式错误。")
        raw_resources = raw_node.get("resources")
        if not isinstance(raw_resources, list):
            raise WorkbenchError("节点资源响应格式错误。")
        for resource in raw_resources:
            if not isinstance(resource, dict):
                raise WorkbenchError("资源格式错误。")
            if "allocation_mode" in resource or "resource_class" in resource:
                resource_class = resource.get("resource_class")
                if (
                    resource.get("allocation_mode") != "divisible"
                    or not isinstance(resource_class, str)
                    or not resource_class.strip()
                    or resource.get("kind") != "compute"
                ):
                    raise WorkbenchError("资源分配声明格式错误。")
                del resource["allocation_mode"]
                del resource["resource_class"]
    try:
        snapshot = InventorySnapshot.from_json(normalized)
    except (MissionControllerError, ValueError, TypeError) as error:
        raise WorkbenchError("库存响应不符合当前读取合同；未生成部分统计。") from error
    identities = [identifier(node.node_id) for node in snapshot.nodes]
    if len(set(identities)) != len(identities):
        raise WorkbenchError("库存存在重复节点 ID，不能计算可信数量。")
    for node in snapshot.nodes:
        if len(set(node.contracts)) != len(node.contracts):
            raise WorkbenchError("库存存在重复合同。")
        if len({resource.resource_id for resource in node.resources}) != len(node.resources):
            raise WorkbenchError("节点存在重复资源 ID。")
        if len({cap.kind for cap in node.capabilities}) != len(node.capabilities):
            raise WorkbenchError("节点存在重复能力类别。")
    return snapshot


def _age(now: int, received: int) -> str:
    """Compare only Controller-local observation times; future values remain unknown."""
    return "时间关系未知" if received > now else f"{now - received} ms 前"


def node_lines(node: InventoryNode, observed: int) -> list[str]:
    """Describe registration and last observations without inferring live eligibility."""
    capabilities = (
        "、".join(
            f"{CAPABILITIES[cap.kind]}（报告{'可用' if cap.available else '不可用'}）"
            for cap in node.capabilities
        )
        or "未声明"
    )
    contracts = (
        "、".join(
            f"{CONTRACT_LABELS[contract]} [{contract}]"
            if contract in CONTRACT_LABELS
            else safe_text(contract)
            for contract in node.contracts
        )
        or "未返回"
    )
    resources = (
        "、".join(
            f"{safe_text(resource.resource_id)}: {resource.kind}, 容量 {resource.capacity}"
            for resource in node.resources
        )
        or "未声明"
    )
    return [
        f"能力：{capabilities}",
        f"当前投影中的可用合同：{contracts}",
        f"最近健康报告：{node.reported_health}（接收于 {_age(observed, node.received_at_ms)}）",
        f"最近可达观测：{node.liveness}（{_age(observed, node.liveness_observed_at_ms)}）",
        f"注册资源：{resources}；容量不代表剩余量。",
    ]


def matching_nodes(snapshot: InventorySnapshot, term: str) -> list[InventoryNode]:
    """Match only actual contract/kind spellings using explicit display vocabulary aliases."""
    terms = SEARCH_TERMS.get(term.strip(), (term.strip().casefold(),))
    return [
        node
        for node in snapshot.nodes
        if any(
            needle in actual.casefold()
            for needle in terms
            for actual in (*node.contracts, *(cap.kind for cap in node.capabilities))
        )
    ]


def render_nodes(nodes: list[InventoryNode], snapshot: InventorySnapshot) -> str:
    """Render a numbered bounded view whose exact ID ordering can be retained for follow-ups."""
    lines = [f"当前身份可见的已注册节点：{len(snapshot.nodes)} 个；本次匹配：{len(nodes)} 个。"]
    for index, node in enumerate(nodes[:DISPLAY_LIMIT], 1):
        lines.append(f"\n{index}. {safe_text(node.node_id)}")
        lines.extend("   " + line for line in node_lines(node, snapshot.observed_at_ms))
    if len(nodes) > DISPLAY_LIMIT:
        lines.append(f"只展示前 {DISPLAY_LIMIT} 个；统计覆盖完整响应。请按节点 ID 查询详情。")
    if not nodes:
        lines.append("本次视图没有匹配项；缺少声明不证明不具备能力。")
    lines.append(
        f"\nController 观察时间：{snapshot.observed_at_ms} ms（保留原时钟数值）。\n"
        "接口未提供新鲜度判定策略；最近报告不等于当前在线、可调度或物理就绪。"
    )
    return "\n".join(lines)


def render_mission(raw: JSONObject, mission_id: str) -> str:
    """Validate exact Mission/Task identities and expose only reported lifecycle facts."""
    status, group, tasks = raw.get("status"), raw.get("group_id"), raw.get("tasks")
    if (
        raw.get("mission_id") != mission_id
        or not isinstance(status, str)
        or status not in MISSION_STATES
        or not isinstance(group, str)
        or not group.strip()
        or not isinstance(tasks, list)
    ):
        raise WorkbenchError("任务响应缺失或身份不匹配；任务状态未知。")
    rows: list[tuple[str, str]] = []
    for task in tasks:
        if not isinstance(task, dict):
            raise WorkbenchError("Task 状态响应不完整；未生成部分统计。")
        task_id, task_status = task.get("task_id"), task.get("status")
        if (
            not isinstance(task_id, str)
            or not isinstance(task_status, str)
            or task_status not in TASK_STATES
        ):
            raise WorkbenchError("Task 状态响应不完整；未生成部分统计。")
        rows.append((identifier(task_id), task_status))
    if len({task_id for task_id, _ in rows}) != len(rows):
        raise WorkbenchError("任务响应含重复 Task ID。")
    counts = Counter(state for _, state in rows)
    lines = [
        f"Mission {safe_text(mission_id)}：{status}",
        f"Group：{safe_text(identifier(group))}",
        f"Task 共 {len(rows)} 个："
        + ("、".join(f"{s}={n}" for s, n in sorted(counts.items())) or "无"),
        *(f"  {safe_text(task)}：{state}" for task, state in rows[:DISPLAY_LIMIT]),
    ]
    if len(rows) > DISPLAY_LIMIT:
        lines.append(f"仅展示前 {DISPLAY_LIMIT} 个 Task；统计覆盖完整响应。")
    for field in ("isolation", "failure_state"):
        if raw.get(field) is not None:
            detail = safe_text(json.dumps(raw[field], ensure_ascii=False))
            suffix = "…（已截断，/raw 查看原响应）" if len(detail) > 4096 else ""
            lines.append(f"{field}（原始记录）：{detail[:4096]}{suffix}")
    lines.append("AwaitingSatisfaction 表示等待目标验证；本地完成与 Mission 完成分别判定。")
    lines.append("这里展示已记录状态；未提供的失败原因保持未知。")
    return "\n".join(lines)
