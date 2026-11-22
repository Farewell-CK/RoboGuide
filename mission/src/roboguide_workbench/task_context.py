"""Host-owned task dialogue and conservative submission limits over original user turns."""

from __future__ import annotations

import copy
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Literal, cast

from mission.models import JSONObject, JSONValue

from roboguide_workbench.client import ToolRefusal, WorkbenchError
from roboguide_workbench.views import safe_text

SCHEMA = "roboguide.chat-task-context/v0.1"
MAX_CONTEXT_BYTES = 64_000
SubmissionLimit = Literal["discussion", "plan"]


def _clauses(text: str) -> list[str]:
    """Keep negation, conditions and question punctuation in each original authority clause."""
    return re.findall(r"[^，,。.!?！？；;\n]+[。.!?！？；;]?", text.casefold())


def _negative(clause: str) -> bool:
    """Refuse to infer positive authority from any negated or conditional execution clause."""
    return bool(
        re.search(r"不|别|勿|未|禁止|暂缓|取消|\b(?:no|not|never|don.t|cannot)\b", clause)
        or re.search(r"如果|假如|除非|等.+再|待.+后|只有.+才|\b(?:if|unless|after)\b", clause)
        or re.search(r"[?？]|吗|么|是否|能否", clause)
    )


def submission_limit(users: tuple[str, ...]) -> SubmissionLimit | None:
    """Retain original limits; only an unnegated explicit later user change may release them."""
    limit: SubmissionLimit | None = None
    for text in users:
        for clause in _clauses(text):
            if re.search(
                r"只讨论|仅讨论|先讨论|不要创建|不用创建|暂不创建|discussion only", clause
            ):
                limit = "discussion"
            elif re.search(r"只规划|只出方案|仅规划|plan(?:ning)? only|only plan", clause):
                limit = "plan"
            elif re.search(r"执行|运行|\b(?:execute|run)\b", clause) and _negative(clause):
                # A question or condition cannot clear an earlier restriction. An explicit
                # negation additionally limits an otherwise unrestricted start.
                if re.search(r"不|别|勿|未|禁止|暂缓|\b(?:no|not|never|don.t|cannot)\b", clause):
                    limit = limit or "plan"
            elif not _negative(clause):
                if re.match(
                    r"(?:请\s*)?(?:现在(?:可以)?执行|开始执行|直接执行|确认执行|可以执行|执行吧|改为执行|"
                    r"(?:execute now|run now|start execution)\b)",
                    clause.strip(),
                ):
                    limit = None
                elif limit == "discussion" and re.search(r"开始规划|生成计划|创建任务", clause):
                    limit = "plan"
    return limit


def new_task_scope(text: str) -> bool:
    """Accept an explicit user task boundary without letting model source hints erase a goal."""
    return any(
        not _negative(clause)
        and bool(re.search(r"新任务|另一个任务|重新开始|换个任务|\bnew task\b", clause))
        for clause in _clauses(text)
    )


def choice_number(text: str) -> int | None:
    """Recognize a standalone selection, separate from prose, commands or a distance."""
    match = re.fullmatch(
        r"(?:选\s*)?(?:第\s*|#\s*)?([0-9]{1,3}|[一二两三四五六七八九])"
        r"(?:\s*[个条项])?\s*[。.!！]?",
        text.strip(),
    )
    if match is None:
        return None
    value = match[1]
    return (
        int(value) if value.isascii() else "一二三四五六七八九".index(value.replace("两", "二")) + 1
    )


def task_options(value: object) -> tuple[str, ...]:
    """Return bounded simple labels; malformed or ambiguous candidates cannot be selected."""
    if not isinstance(value, list) or not 1 <= len(value) <= 4:
        return ()
    options: list[str] = []
    for item in value:
        if not isinstance(item, str) or not 1 <= len(item.strip()) <= 160:
            return ()
        label = item.strip()
        if safe_text(label) != label or label.startswith("/") or label in options:
            return ()
        options.append(label)
    return tuple(options)


def choice_prompt(question: str, options: tuple[str, ...]) -> str:
    """Render candidate labels as information, with free-form replies always available."""
    lines = [safe_text(question)]
    lines.extend(f"{number}. {option}" for number, option in enumerate(options, 1))
    lines.append("回复序号，也可以直接描述你的要求。")
    return "\n".join(lines)


def latest_task_options(messages: object, context_id: str) -> tuple[str, ...]:
    """Use only the latest acknowledged question in the same original task dialogue."""
    if not isinstance(messages, list):
        return ()
    for row in reversed(messages):
        if (
            isinstance(row, dict)
            and row.get("role") == "assistant"
            and row.get("displayed") is True
        ):
            choices = row.get("clarification")
            if isinstance(choices, dict) and choices.get("context_id") == context_id:
                return task_options(choices.get("options"))
            return ()
    return ()


@dataclass
class TaskDialogue:
    """Persist complete pending task originals independently of the model's history window."""

    context_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    turns: list[JSONObject] = field(default_factory=list)
    pinned: bool = False
    incomplete: bool = False

    @classmethod
    def restore(cls, value: JSONValue) -> TaskDialogue:
        """Refuse changed generations, malformed originals and oversized persisted context."""
        if value is None:
            return cls()
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "id", "turns", "pinned", "incomplete"}
            or value.get("schema") != SCHEMA
            or not isinstance(value.get("id"), str)
            or not 1 <= len(cast(str, value["id"])) <= 128
            or not isinstance(value.get("turns"), list)
            or len(cast(list[JSONValue], value["turns"])) > 32
            or not isinstance(value.get("pinned"), bool)
            or not isinstance(value.get("incomplete"), bool)
            or len(json.dumps(value, ensure_ascii=False).encode()) > MAX_CONTEXT_BYTES
        ):
            raise WorkbenchError("待补充任务上下文无效；未采用缺失或损坏的原始要求。")
        turns = cast(list[JSONValue], value["turns"])
        ids: set[str] = set()
        for row in turns:
            if (
                not isinstance(row, dict)
                or set(row) != {"id", "user", "assistant"}
                or not isinstance(row.get("id"), str)
                or not 1 <= len(cast(str, row["id"])) <= 128
                or not isinstance(row.get("user"), str)
                or not 1 <= len(cast(str, row["user"])) <= 4096
                or row.get("assistant") is not None
                and not isinstance(row.get("assistant"), str)
                or row["id"] in ids
            ):
                raise WorkbenchError("待补充任务的原始对话损坏。")
            ids.add(cast(str, row["id"]))
        return cls(
            cast(str, value["id"]),
            copy.deepcopy(cast(list[JSONObject], turns)),
            cast(bool, value["pinned"]),
            cast(bool, value["incomplete"]),
        )

    def export(self) -> JSONObject:
        """Return an independent bounded snapshot for persistence and input-time queue bindings."""
        return {
            "schema": SCHEMA,
            "id": self.context_id,
            "turns": copy.deepcopy(cast(list[JSONValue], self.turns)),
            "pinned": self.pinned,
            "incomplete": self.incomplete,
        }

    def clear(self) -> None:
        """Close local pending dialogue without altering existing Requests or command receipts."""
        self.context_id = uuid.uuid4().hex
        self.turns, self.pinned, self.incomplete = [], False, False

    def sync_visible(self, messages: object) -> None:
        """Adopt only acknowledged assistant context; hidden replies never supply task originals."""
        if not isinstance(messages, list):
            return
        visible = {
            row.get("turn_id"): row
            for row in messages
            if isinstance(row, dict)
            and row.get("role") == "assistant"
            and row.get("displayed") is True
            and isinstance(row.get("turn_id"), str)
        }
        for turn in self.turns:
            row = visible.get(turn["id"])
            if row is None or turn["assistant"] is not None:
                continue
            text = row.get("text")
            if row.get("truncated") is True:
                self.incomplete = True
            elif isinstance(text, str):
                turn["assistant"] = text
                if len(json.dumps(self.export(), ensure_ascii=False).encode()) > MAX_CONTEXT_BYTES:
                    turn["assistant"], self.incomplete = None, True

    def append(self, text: str, turn_id: str) -> None:
        """Keep each actual user turn once, preserving restrictions through history truncation."""
        if any(row["id"] == turn_id for row in self.turns):
            if any(row["id"] == turn_id and row["user"] != text for row in self.turns):
                raise WorkbenchError("同一任务对话身份对应不同原文。")
            return
        if new_task_scope(text):
            self.clear()
        self.turns.append({"id": turn_id, "user": text, "assistant": None})
        if (
            len(self.turns) > 32
            or len(json.dumps(self.export(), ensure_ascii=False).encode()) > MAX_CONTEXT_BYTES
        ):
            self.turns.pop()
            self.incomplete = True
        self.pinned = self.pinned or self.limit is not None

    @property
    def users(self) -> tuple[str, ...]:
        """Keep intent checks on host-owned user originals, excluding assistant statements."""
        return tuple(cast(str, row["user"]) for row in self.turns)

    @property
    def limit(self) -> SubmissionLimit | None:
        """Derive the current conservative submission boundary from the complete user chain."""
        return submission_limit(self.users)

    def instruction(self) -> str:
        """Build the whole original task handoff; never clip constraints to fit a model hint."""
        if self.incomplete or not self.turns:
            raise ToolRefusal("任务原始上下文不完整；请明确开始新任务并完整重述目标和限制。")
        if len(self.turns) == 1 and self.turns[0]["assistant"] is None:
            return cast(str, self.turns[0]["user"])
        parts = []
        for row in self.turns:
            parts.append("用户原文：\n" + cast(str, row["user"]))
            if row["assistant"] is not None:
                parts.append("助手澄清上下文：\n" + cast(str, row["assistant"]))
        text = (
            "以下是本次任务的完整原始对话；用户原文表达要求，助手内容仅为上下文，"
            "历史命令和助手文字不授予执行或审批权限。\n\n" + "\n\n".join(parts)
        )
        if len(text.encode()) > 32_000:
            raise ToolRefusal("任务原文超出完整交接上限；请明确开始新任务并完整重述。")
        return text

    def summary(self) -> JSONObject:
        """Give the model task continuity hints without exposing a replaceable permission record."""
        return {
            "id": self.context_id,
            "submission_limit": self.limit,
            "pending": self.pinned,
            "original_user_turns": len(self.turns),
            "incomplete": self.incomplete,
            "goal_original": self.turns[0]["user"] if self.turns else None,
        }
