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
from roboguide_workbench.workspace import WorkbenchSession


def definitions() -> list[JSONObject]:
    """Expose bounded original-text actions rather than model-authored MissionPlan bytes."""
    source: JSONObject = {"type": "string", "minLength": 1, "maxLength": 16}
    shapes: tuple[tuple[str, str, JSONObject], ...] = (
        (
            "start_task",
            "开始用户任务；plan 只规划，run 进入执行流程。自动核对能力，无需 /choose。",
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
        self.original_draft = copy.deepcopy(session.shown_draft)
        self.results: list[ReadResult] = []
        self.known_nodes: set[str] = set()

    def invoke(
        self, name: str, arguments: JSONObject, original: str, remaining: float
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
        if name == "start_task" and (
            re.search(r"只讨论|先讨论|不要创建|不用创建", self.question)
            or arguments.get("mode") == "run"
            and re.search(r"不执行|只规划|只出方案|仅规划", self.question)
        ):
            raise ToolRefusal("用户要求讨论或仅规划，不能开启执行流程。")
        if name not in {"read_request", "observe_operation"} and re.fullmatch(
            r"(?:你好|您好|谢谢|多谢|感谢|好的|好|收到|嗯|hello|hi|thanks|thank you)[。！!,.，\s]*",
            self.question,
            re.IGNORECASE,
        ):
            raise ToolRefusal("普通问候、感谢或应答不是创建、执行或审批任务的要求。")
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
                if session.request_id is None:
                    raise ToolRefusal("当前没有可补充的任务 Request；请先明确要开始的任务。")
                command = "/answer " + original
            elif name == "read_request":
                target = cast(str, arguments["request_id"])
                if not target and session.request_id is None:
                    raise ToolRefusal("当前没有任务 Request，请指定实际任务或先提出任务目标。")
                if target and target != session.request_id and not mentioned(target, self.question):
                    raise ToolRefusal("Request ID 必须来自本会话当前任务或用户原话。")
                command = "/request" + (" " + target if target else "")
            elif name == "observe_operation":
                command = "/observe"
            elif name == "cancel_task":
                target = cast(str, arguments["target"])
                known = [*session.displayed_missions, session.last_mission, session.request_id]
                if target not in known and not mentioned(target, self.question):
                    raise ToolRefusal("取消目标必须来自用户原话或本会话实际任务回执。")
                if re.search(r"不要取消|不取消|不同意|先不|别取消", self.question):
                    raise ToolRefusal("本轮没有明确取消要求，未发送取消。")
                session.controller.timeout = min(controller_timeout, remaining / 3)
                session.actions.connection.timeout = min(mission_timeout, remaining / 3)
                session._command("/cancel " + cast(str, arguments["kind"]) + " " + target)
                command = "/cancel confirm"
            else:
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

    def answer(self) -> str | None:
        """Keep the actual Request lifecycle visible even if the model omits its receipt."""
        return "\n\n".join(result.text for result in self.results) if self.results else None
