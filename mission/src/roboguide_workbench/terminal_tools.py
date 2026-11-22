"""Keyboard views over acknowledged Workbench objects and tool activity."""

from __future__ import annotations

import shlex
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from mission.models import JSONObject
from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.utils import get_cwidth

from roboguide_workbench.activity import activity_lines
from roboguide_workbench.views import safe_text

COMMAND_HINTS = {
    "/nodes": "查看节点列表",
    "/node": "查看节点能力",
    "/missions": "查看任务列表",
    "/mission": "查看一个任务",
    "/compare": "比较节点",
    "/explain": "解释任务和绑定",
    "/diagnose": "诊断任务执行与停止记录",
    "/watch": "跟踪任务状态变化",
    "/watches": "查看跟踪列表",
    "/unwatch": "停止本地跟踪",
    "/plan": "创建仅规划请求",
    "/plan-detail": "查看完整计划",
    "/run": "开始执行流程",
    "/request": "查看原任务请求",
    "/answer": "补充或修改任务",
    "/answer-to": "回复指定问题",
    "/execute": "执行已展示的当前计划",
    "/approve": "审批已展示的当前计划",
    "/choose": "选择已展示的提议",
    "/observe": "核对原操作回执",
    "/reconcile": "核对原命令与任务状态",
    "/details": "查看已展示回合的工具记录",
    "/history": "查看会话历史",
    "/help": "显示帮助",
    "/raw": "查看原始证据",
    "/reset": "清除当前对象选择",
    "/quit": "退出会话",
    "/status": "查看会话和处理状态",
    "/state": "查询设备上报状态",
    "/state-fields": "查看设备状态字段",
    "/sessions": "查看会话列表",
    "/new": "新建会话",
    "/resume": "恢复会话",
    "/check": "检查当前计划条件",
    "/cancel": "查看或确认取消目标",
    "/feedback": "保存本地反馈包",
    "/queue": "查看待发消息",
    "/dequeue": "撤回待发消息",
    "/edit-queued": "取回待发消息编辑",
}
_OBJECTS = {
    "/node": ("displayed_nodes", "last_node"),
    "/mission": ("displayed_missions", "last_mission"),
    "/diagnose": ("displayed_missions", "last_mission"),
    "/explain": ("displayed_missions", "last_mission"),
    "/watch": ("displayed_missions", "last_mission"),
    "/state": ("state_targets", "last_state"),
    "/state-fields": ("state_targets", "last_state"),
    "/request": ("", "request_id"),
}
_SHELL_WORDS = frozenset({"/diagnose", "/state", "/state-fields"})


class WorkbenchCompleter(Completer):
    """Describe commands and suggest only scope-local objects previously shown to the user."""

    def __init__(self, references: Callable[[], JSONObject]) -> None:
        """Read the terminal owner's last visible scope without contacting any tool."""
        self.references = references

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        """Complete a command or its first object argument without submitting the input."""
        text = document.text_before_cursor
        if not text.startswith("/") or document.text_after_cursor or "\n" in text:
            return
        command, separator, argument = text.partition(" ")
        if not separator:
            for name, hint in COMMAND_HINTS.items():
                if name.startswith(command.lower()) or command[1:] in hint:
                    yield Completion(name, -len(text), display_meta=hint)
            return
        if command not in _OBJECTS or any(char.isspace() for char in argument):
            return
        list_key, last_key = _OBJECTS[command]
        references = self.references()
        listed = references.get(list_key)
        candidates = list(listed) if isinstance(listed, list) else []
        candidates.append(references.get(last_key))
        seen: set[str] = set()
        for item in candidates:
            if (
                not isinstance(item, str)
                or not item
                or item.startswith("#")
                or item in seen
                or safe_text(item) != item
                or not item.casefold().startswith(argument.casefold())
            ):
                continue
            seen.add(item)
            insertion = shlex.quote(item) if command in _SHELL_WORDS else item
            if insertion == argument:
                continue
            yield Completion(insertion, -len(argument), display=item, display_meta="会话已显示")


def wrap_cells(line: str, width: int) -> list[str]:
    """Keep complete text while wrapping by terminal cells, including Chinese characters."""
    result: list[str] = []
    current: list[str] = []
    cells = 0
    for char in line:
        char_cells = get_cwidth(char)
        if current and cells + char_cells > width:
            result.append("".join(current))
            current, cells = [], 0
        current.append(char)
        cells += char_cells
    result.append("".join(current))
    return result


@dataclass
class DetailPager:
    """Navigate a snapshot of already displayed turns without altering the live draft."""

    records: list[JSONObject]
    index: int = 0
    offset: int = 0

    def change_turn(self, delta: int) -> None:
        """Clamp movement to available history with newest at index zero."""
        self.index = max(0, min(len(self.records) - 1, self.index + delta))
        self.offset = 0

    @staticmethod
    def page_size(height: int) -> int:
        """Keep space for the original composer while paging through each detail row."""
        return max(1, min(8, height // 3) - 1)

    def render(self, width: int, height: int, scroll: int = 0) -> str:
        """Render a bounded page of historical evidence, including on small terminals."""
        width = max(2, width - 1)
        page = self.page_size(height)
        physical = [
            chunk
            for line in activity_lines(self.records[-1 - self.index])
            for chunk in wrap_cells(line, width)
        ]
        self.offset = max(0, min(max(0, len(physical) - page), self.offset + scroll))
        header = (
            f"详情 {self.index + 1}/{len(self.records)} · ↑↓ 翻阅 · ←→ 回合 · Esc"
            if width < 54
            else f"本轮检查详情 {self.index + 1}/{len(self.records)} · ←→ 回合 · ↑↓ 翻阅 · Esc 返回"
        )
        return "\n".join(
            [wrap_cells(header, width)[0], *physical[self.offset : self.offset + page]]
        )
