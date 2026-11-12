"""Process-local conversation context and fresh execution of closed read-only queries."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from mission.models import JSONObject

from roboguide_workbench.client import WorkbenchError
from roboguide_workbench.routing import Query, Router
from roboguide_workbench.views import (
    DISPLAY_LIMIT,
    inventory_view,
    matching_nodes,
    render_mission,
    render_nodes,
    safe_text,
)

HELP = """可询问：接入了几个节点？各节点有什么功能？第二个节点呢？哪些支持建图？
也可询问：任务 mission-x 到哪一步了？
无需模型的命令：
  /nodes                 集群概览与节点能力
  /node ID 或 /node #2   节点详情（#2 指上轮展示的第二个节点）
  /capabilities 建图      检索能力或合同关键词
  /mission ID            Mission 与 Task 状态
  /raw                   最近一次成功查询的原始结果（历史，不重新查询）
  /reset                 清除本进程的会话引用与原始结果
  /help                  帮助
  /quit                  退出
当前版本只查询；执行、取消和修改操作尚未接入。"""


class Reader(Protocol):
    """Provide the only two server reads available to conversation tools."""

    def inventory(self) -> JSONObject:
        """Return one current inventory response or raise an unavailable error."""
        ...

    def mission(self, mission_id: str) -> JSONObject:
        """Return the exact requested Mission without issuing a command."""
        ...


def command_query(text: str) -> Query:
    """Parse explicit commands with literal arguments and no shell interpretation."""
    command, _, argument = text.partition(" ")
    argument = argument.strip()
    kinds = {
        "/nodes": "cluster",
        "/node": "node",
        "/capabilities": "capability",
        "/mission": "mission",
        "/help": "help",
    }
    if command not in kinds:
        raise WorkbenchError("未知命令。输入 /help 查看可用查询。")
    index = None
    if command == "/node" and argument.startswith("#"):
        try:
            index = int(argument[1:])
        except ValueError as error:
            raise WorkbenchError("请使用 /node #2 这样的节点序号。") from error
        argument = ""
    return Query.parse(
        {"kind": kinds[command], "value": argument or None, "index": index, "message": None}
    )


class Session:
    """Retain bounded displayed references, never reuse old state as fresh query evidence."""

    def __init__(self, reader: Reader, router_factory: Callable[[], Router]) -> None:
        """Keep the model lazy so direct commands require no Provider config or credential."""
        self.reader = reader
        self.router_factory = router_factory
        self.router: Router | None = None
        self.displayed_nodes: list[str] = []
        self.last_node: str | None = None
        self.last_mission: str | None = None
        self.previous_question = ""
        self.last_raw: JSONObject | None = None
        self.last_read_at: str | None = None

    def reset(self) -> None:
        """Forget all conversational references and historical results without server effects."""
        self.displayed_nodes.clear()
        self.last_node = self.last_mission = None
        self.previous_question = ""
        self.last_raw = None
        self.last_read_at = None

    def ask(self, question: str) -> str:
        """Resolve one question, query fresh evidence and update context only after success."""
        question = question.strip()
        if not question or len(question) > 4096:
            raise WorkbenchError("单次问题须为 1–4096 字符。")
        if question == "/raw":
            if self.last_raw is None:
                return "尚无成功查询结果。"
            return f"历史查询，读取于 {self.last_read_at}：\n" + safe_text(
                json.dumps(self.last_raw, ensure_ascii=False)
            )
        if question == "/reset":
            self.reset()
            return "已清除本次会话。"
        if question.startswith("/"):
            query = command_query(question)
        else:
            if self.router is None:
                self.router = self.router_factory()
            query = self.router.route(
                question,
                {
                    "displayed_nodes": list(self.displayed_nodes),
                    "last_node": self.last_node,
                    "last_mission": self.last_mission,
                    "previous_question": self.previous_question,
                },
            )
        # Revalidate injected routers too; only this descriptor reaches any GET.
        query = Query.parse(
            {
                "kind": query.kind,
                "value": query.value,
                "index": query.index,
                "message": query.message,
            }
        )
        if not question.startswith("/") and query.kind in {"node", "mission"}:
            known = (
                [*self.displayed_nodes, self.last_node]
                if query.kind == "node"
                else [self.last_mission]
            )
            if query.value is not None and query.value not in known and query.value not in question:
                raise WorkbenchError("查询 ID 不在当前问题或已有对象引用中，请明确提供原始 ID。")
        if query.kind == "help":
            return HELP
        if query.kind == "clarify":
            self.previous_question = question
            return "需要补充：" + safe_text(query.message or "")
        if query.kind == "unsupported":
            return "当前工作台仅支持集群、节点、能力和任务状态查询。未执行任何操作。"
        if query.kind not in {"cluster", "node", "capability", "mission"}:
            raise WorkbenchError("此意图需要第二版会话入口。")
        result = self._execute(query)
        self.previous_question = question
        return result

    def _execute(self, query: Query) -> str:
        """Resolve previous display order before reading a fresh response from the same source."""
        target = query.value
        if query.index is not None:
            if query.index > len(self.displayed_nodes):
                raise WorkbenchError("上一轮没有这个节点序号，请先查询 /nodes。")
            target = self.displayed_nodes[query.index - 1]
        if query.kind == "mission":
            assert target is not None
            raw = self.reader.mission(target)
            rendered = render_mission(raw, target)
            self.last_mission, self.last_node = target, None
            self.displayed_nodes = []
        else:
            raw = self.reader.inventory()
            snapshot = inventory_view(raw)
            nodes = list(snapshot.nodes)
            if query.kind == "node":
                nodes = [node for node in nodes if node.node_id == target]
                if not nodes:
                    raise WorkbenchError("当前身份的最新库存中未找到该节点；不证明其物理不存在。")
            elif query.kind == "capability":
                assert target is not None
                nodes = matching_nodes(snapshot, target)
            rendered = render_nodes(nodes, snapshot)
            self.displayed_nodes = [node.node_id for node in nodes[:DISPLAY_LIMIT]]
            self.last_node = nodes[0].node_id if len(nodes) == 1 else None
            self.last_mission = None
        self.last_raw = raw
        self.last_read_at = datetime.now(UTC).isoformat(timespec="seconds")
        return (
            f"读取于 {self.last_read_at}（客户端 UTC）；范围：当前 Controller、当前身份可见。\n\n"
            + rendered
        )
