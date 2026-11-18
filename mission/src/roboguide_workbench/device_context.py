"""Resolve advisory device names only against actual returned identities and contracts."""

from __future__ import annotations

import re

from mission.controller import InventoryNode
from mission.models import JSONObject

from roboguide_workbench.views import inventory_view, safe_text


def aliases(text: str) -> set[str]:
    """Recognize numbered dog names without inventing an actual Node or physical identity."""
    return {
        "dog" + str(int(number))
        for number in re.findall(r"(?:dog|狗)\s*([0-9]{1,6})(?![0-9])", text, re.IGNORECASE)
    }


def alias_matches(identity: str, text: str) -> bool:
    """Match a natural name to a supplied real identity, requiring the same complete number."""
    return bool(aliases(identity) & aliases(text))


def mentioned(identity: str, text: str) -> bool:
    """Require a complete literal ID rather than accepting a letter inside an unrelated word."""
    return (
        re.search(r"(?<![A-Za-z0-9_-])" + re.escape(identity) + r"(?![A-Za-z0-9_-])", text)
        is not None
    )


def context_nodes(raw: JSONObject, instruction: str) -> tuple[InventoryNode, ...]:
    """Select current exact or advisory aliases without changing task instructions."""
    nodes = inventory_view(raw).nodes
    selected = [
        node
        for node in nodes
        if node.node_id in instruction or alias_matches(node.node_id, instruction)
    ]
    return tuple(selected or nodes)[:16]


def task_context(raw: JSONObject, instruction: str) -> str:
    """Explain actual declared capabilities as advice without claiming Control eligibility."""
    selected = context_nodes(raw, instruction)
    lines = ["当前设备接入情况："]
    for node in selected[:16]:
        contracts = ", ".join(node.contracts) or "没有登记合同"
        lines.append(f"- {safe_text(node.node_id)}：{safe_text(contracts)}")
        if re.search(
            r"巡检|探索|导航|前往|去.+(?:门|区|间)|patrol|navigate|explore",
            instruction,
            re.IGNORECASE,
        ):
            if not any(
                contract.startswith(("mobility.navigate@", "task.delegate@", "patrol."))
                for contract in node.contracts
            ):
                lines.append("  当前未登记导航/巡检或任务委派合同；需要先接入相应本机技能。")
        if re.search(
            r"(?:共享|分享|传|导入).*(?:地图)|(?:地图).*(?:共享|分享|传|导入)", instruction
        ):
            if not any(contract.startswith("spatial.map.") for contract in node.contracts):
                lines.append(
                    "  当前未登记地图处理合同；原点捕获或图像检查不能证明具备地图共享能力。"
                )
        if node.liveness != "Reachable":
            lines.append("  当前上报节点不可达，执行条件仍需恢复后检查。")
    if not selected:
        lines.append("当前没有可见节点，不能确认执行能力。")
    lines.append("以上为当前登记快照；MI 规划、风险审批和 Control 的实际准入检查继续独立进行。")
    return "\n".join(lines)
