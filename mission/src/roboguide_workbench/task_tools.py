"""Native task tools using original user dialogue and the existing durable Mission client."""

from __future__ import annotations

import copy
import re
import time
from typing import cast

from mission.models import JSONObject

from roboguide_workbench.client import ToolRefusal, WorkbenchError
from roboguide_workbench.device_context import context_nodes, mentioned, task_context
from roboguide_workbench.evidence import ReadResult
from roboguide_workbench.task_context import TaskDialogue, new_task_scope
from roboguide_workbench.views import safe_text
from roboguide_workbench.workspace import WorkbenchSession, draft_reference


def definitions() -> list[JSONObject]:
    """Expose bounded original-text actions rather than model-authored MissionPlan bytes."""
    source: JSONObject = {"type": "string", "minLength": 1, "maxLength": 16}
    shapes: tuple[tuple[str, str, JSONObject], ...] = (
        (
            "clarify_task",
            "任务缺少区域、目标或条件时自然追问；保存完整任务上下文，不创建或执行任务。",
            {"question": {"type": "string", "minLength": 1, "maxLength": 2000}},
        ),
        (
            "start_task",
            "开始用户任务；plan 只规划，run 进入执行流程。"
            "宿主保留完整待补充任务，start_turn 仅作线索。",
            {"mode": {"type": "string", "enum": ["plan", "run"]}, "start_turn": source},
        ),
        (
            "answer_request",
            "用用户本次原始补充回答澄清或修改当前 Request。",
            {"start_turn": source},
        ),
        (
            "read_request",
            "查询当前或明确指定的 Request；空 ID 使用本会话当前任务。",
            {"request_id": {"type": "string", "maxLength": 256}},
        ),
        (
            "execute_plan",
            "仅在用户明确要求执行已完整显示的当前计划时调用；服务端重新检查版本。",
            {},
        ),
        (
            "approve_plan",
            "仅在用户明确审批已完整显示版本时调用；服务端 approver 权限独立检查。",
            {},
        ),
        (
            "cancel_task",
            "取消用户明确指定的 Request 或 Mission；正常重新核对原始目标，不证明物理停止。",
            {
                "kind": {"type": "string", "enum": ["request", "mission"]},
                "target": {"type": "string", "minLength": 1, "maxLength": 256},
            },
        ),
        ("observe_operation", "只读核对上一条原命令及未知结果，不重发命令。", {}),
    )
    return [
        {
            "type": "function",
            "name": name,
            "description": description,
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
        }
        for name, description, properties in shapes
    ]


class TaskTools:
    """Keep one foreground action owner, original command IDs and acknowledged-plan bindings."""

    def __init__(self, session: WorkbenchSession, question: str) -> None:
        """Capture the displayed plan before in-turn observations can change it."""
        self.session, self.question = session, question
        references = session.query_references()
        self.dialogue = TaskDialogue.restore(references.get("task_dialogue"))
        self.input_context_id = self.dialogue.context_id
        self.input_context_pending = self.dialogue.pinned and bool(self.dialogue.turns)
        self.started_context_id = session.task_dialogue.context_id
        self.dialogue.append(question, session.input_turn_id or "current")
        self.input_request_id = references.get("request_id")
        self.input_draft = copy.deepcopy(references.get("draft"))
        self.started_request_id = session.request_id
        self.current_request_id = (
            self.input_request_id if isinstance(self.input_request_id, str) else None
        )
        self.original_draft = copy.deepcopy(session.shown_draft)
        self.results: list[ReadResult] = []
        self.known_nodes: set[str] = set()
        self.known_targets = {
            value
            for value in [
                *cast(list[str], references.get("displayed_missions", [])),
                references.get("last_mission"),
                self.input_request_id,
            ]
            if isinstance(value, str)
        }
        self.explicit_requests: set[str] = set()
        self.owns_selection = False

    def _require_input_request(self, *, allow_named_edit: bool = False) -> None:
        """Refuse rebinding queued edits or grants to a Request selected after input."""
        if (
            allow_named_edit
            and self.current_request_id in self.explicit_requests
            and self.session.request_id == self.current_request_id
        ):
            return
        if (
            self.input_request_id != self.started_request_id
            or self.session.request_id != self.started_request_id
        ):
            raise ToolRefusal("输入时看到的任务已变化；请查看当前任务后再明确操作。")

    def learn_missions(self, targets: set[str]) -> None:
        """Admit only this turn's adapter-validated Mission identities for subsequent task tools."""
        self.known_targets.update(targets)

    def work_key(self, name: str, arguments: JSONObject) -> str | None:
        """Bind corrections and duplicate starts to one host-owned pending task."""
        return "start-task:" + self.dialogue.context_id if name == "start_task" else None

    def cache_context(self, name: str, arguments: JSONObject) -> JSONObject:
        """Resolve task reads against their actual target and original command generation."""
        commands = self.session.store.data.get("commands")
        if not isinstance(commands, list):
            raise WorkbenchError("会话命令日志损坏。")
        latest = commands[-1] if commands else None
        if latest is not None and not isinstance(latest, dict):
            raise WorkbenchError("会话命令日志损坏。")
        return {
            "current_request": self.current_request_id,
            "target": arguments.get("request_id") or self.current_request_id
            if name == "read_request"
            else None,
            "command_count": len(commands),
            "command": {
                key: latest.get(key)
                for key in ("service", "idempotency_key", "operation_id", "path", "state")
            }
            if latest is not None
            else None,
        }

    def invoke(
        self,
        name: str,
        arguments: JSONObject,
        original: str,
        remaining: float,
        *,
        user_turns: tuple[str, ...] | None = None,
    ) -> ReadResult:
        """Call the existing commands once under the original remaining time and source text."""
        session = self.session
        deadline = time.monotonic() + remaining
        controller_timeout, mission_timeout = (
            session.controller.timeout,
            session.actions.connection.timeout,
        )
        prefix = ""
        if name in {"execute_plan", "approve_plan"}:
            if re.search(
                r"不要|不执行|不批准|不审批|不同意|先不|别执行|别批准|别审批|勿执行|勿批准",
                self.question,
            ):
                raise ToolRefusal("用户本轮没有明确要求执行或审批该版本，未发出操作。")
        if name == "start_task":
            if (
                self.input_context_pending
                and self.input_context_id != self.started_context_id
                and not new_task_scope(self.question)
            ):
                raise ToolRefusal("输入时的待补充任务已变化；请重新明确本次任务目标。")
            limit = self.dialogue.limit
            if limit == "discussion" or limit == "plan" and arguments["mode"] == "run":
                raise ToolRefusal("用户要求讨论或仅规划，不能开启执行流程；可继续补充或仅规划。")
            original = self.dialogue.instruction()
        if name not in {"read_request", "observe_operation", "clarify_task"} and re.fullmatch(
            r"(?:你好|您好|谢谢|多谢|感谢|好的|好|收到|嗯|hello|hi|thanks|thank you)[。！!,.，\s]*",
            self.question,
            re.IGNORECASE,
        ):
            raise ToolRefusal("普通问候、感谢或应答不是创建、执行或审批任务的要求。")
        if name == "clarify_task":
            session.task_dialogue.pinned = True
            session.checkpoint()
            result = ReadResult("需要补充：" + safe_text(cast(str, arguments["question"])))
            self.results.append(result)
            return result
        try:
            if name == "start_task":
                progress = getattr(session, "progress", None)
                if callable(progress):
                    progress("正在核对目标设备和已登记合同")
                session.controller.timeout = min(controller_timeout, remaining)
                try:
                    raw = session.controller.inventory()
                    prefix = task_context(raw, original) + "\n\n"
                    self.known_nodes.update(node.node_id for node in context_nodes(raw, original))
                except (ValueError, OSError) as error:
                    prefix = "当前设备能力未能核对：" + str(error) + "；执行条件保持未知。\n\n"
                command = "/" + cast(str, arguments["mode"]) + " " + original
            elif name == "answer_request":
                self._require_input_request(allow_named_edit=True)
                if session.request_id is None:
                    raise ToolRefusal("当前没有可补充的任务 Request；请先明确要开始的任务。")
                command = "/answer " + self.question
            elif name == "read_request":
                target = cast(str, arguments["request_id"]) or self.current_request_id
                if target is None:
                    raise ToolRefusal("当前没有任务 Request，请指定实际任务或先提出任务目标。")
                if target != self.current_request_id and not mentioned(target, self.question):
                    raise ToolRefusal("Request ID 必须来自本会话当前任务或用户原话。")
                command = "/request " + target
            elif name == "observe_operation":
                command = "/observe"
            elif name == "cancel_task":
                target = cast(str, arguments["target"])
                if target not in self.known_targets and not mentioned(target, self.question):
                    raise ToolRefusal("取消目标必须来自用户原话或本会话实际任务回执。")
                if re.search(r"不要取消|不取消|不同意|先不|别取消", self.question):
                    raise ToolRefusal("本轮没有明确取消要求，未发送取消。")
                session.controller.timeout = min(controller_timeout, remaining / 3)
                session.actions.connection.timeout = min(mission_timeout, remaining / 3)
                session._command("/cancel " + cast(str, arguments["kind"]) + " " + target)
                command = "/cancel confirm"
            else:
                self._require_input_request()
                if self.input_draft != draft_reference(self.original_draft):
                    raise ToolRefusal(
                        "输入时看到的计划版本已变化；请查看当前计划后再明确执行或审批。"
                    )
                if self.original_draft is None or session.shown_draft != self.original_draft:
                    raise ToolRefusal(
                        "尚无已完整显示且未变化的当前计划；请先查看计划，再明确执行或审批。"
                    )
                expected = "AwaitingExecution" if name == "execute_plan" else "AwaitingApproval"
                if self.original_draft.get("lifecycle") != expected:
                    raise ToolRefusal(
                        "当前计划不处于等待执行或审批的相应阶段；请读取原任务进度，不重复提交。"
                    )
                command = "/execute" if name == "execute_plan" else "/approve"
            budget = deadline - time.monotonic()
            if budget <= 0:
                raise WorkbenchError("任务准备预算已用完，未发出任务操作。")
            phases = {
                "execute_plan": 2,
                "approve_plan": 2,
                "cancel_task": 3,
                "observe_operation": 3,
            }.get(name, 1)
            session.actions.connection.timeout = min(mission_timeout, budget / phases)
            session.controller.timeout = min(controller_timeout, budget / phases)
            text = session._command(command)
            self.current_request_id = session.request_id
            self.owns_selection = True
            if name == "read_request" and mentioned(
                cast(str, self.current_request_id), self.question
            ):
                self.explicit_requests.add(cast(str, self.current_request_id))
            self.known_targets.update(
                value for value in (session.request_id, session.last_mission) if value is not None
            )
            if name not in {"read_request", "observe_operation"}:
                session.task_dialogue.clear()
            text = text.replace(
                "尚未提交执行。查看后可用 /execute，修改用 /answer。",
                "尚未提交执行。可以说“执行这个计划”，或继续补充修改要求。",
            )
            text = text.replace(
                "等待 approver 对当前版本审批：/approve。", "等待有审批权限的用户明确审批当前版本。"
            )
            text = text.replace(
                "请求已被 Controller 接纳；实际执行进度用 /mission 或 /watch。",
                "请求已被 Controller 接纳；可以继续询问实际执行进度。",
            )
            result = ReadResult(prefix + text)
            self.results.append(result)
            return result
        finally:
            session.controller.timeout = controller_timeout
            session.actions.connection.timeout = mission_timeout

    def answer(self, evidence: list[JSONObject]) -> str | None:
        """Keep full actual receipts and unresolved task parts independently of model wording."""
        parts = [result.text for result in self.results]
        names = {cast(str, tool["name"]) for tool in definitions()}
        for item in evidence:
            source, text = item.get("source"), item.get("text")
            if (
                isinstance(source, str)
                and source in names
                and item.get("complete") is not True
                and isinstance(text, str)
            ):
                parts.append(text)
        return "\n\n".join(dict.fromkeys(parts)) if parts else None
