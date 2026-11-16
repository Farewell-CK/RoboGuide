"""Persistent conversation workflows over bounded reads and explicit Mission commands."""

from __future__ import annotations

import copy
import json
import shlex
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from mission.models import JSONObject, JSONValue

from roboguide_workbench.actions import MissionActions, checked_request
from roboguide_workbench.client import ReadOnlyController, WorkbenchError, identifier
from roboguide_workbench.evidence import QueryStatus, ReadResult
from roboguide_workbench.mission_views import render_mission_list
from roboguide_workbench.persistence import SessionStore
from roboguide_workbench.routing import Query, Router
from roboguide_workbench.session import HELP, Session
from roboguide_workbench.state_queries import choices, discover, fields, read_state
from roboguide_workbench.views import inventory_view, render_mission, render_nodes, safe_text

EXTRA_HELP = """可以直接提问：有哪些设备？dog2 还有多少电？它在哪里？当前有哪些任务？
也可以直接说：让狗1巡检东区；先只规划；把目标改成门口；执行刚才的计划；取消该请求。
Agent 会查能力、补问和调用 MI；原有权限、风险审批与执行准入照常检查。

设备与状态（无需模型）：
  /nodes、/node ID       查看节点；/node #2 查询上次展示的第二个节点
  /capabilities 建图     检索能力；/compare 比较上次展示的节点
  /state-fields [ID]     查看状态对象及可查询字段
  /state ID 电量 位置    查询状态；也支持 battery/position/posture
  /state #2 姿态         查询上次状态目录中的第二个对象；/state 重查当前对象

任务与规划：
  /missions             当前身份可见的任务列表
  /missions active      仅显示当前 Running、Accepted、Cancelling 或 Unknown 任务
  /mission ID 或 #2      查询任务及各步骤
  /explain ID 或 #2      组合读取任务、绑定与恢复依据
  /watch [ID]            后台跟踪；/watches 查看，/unwatch ID 停止跟踪
  /plan 目标             只生成计划；/run 目标 按既有审批策略进入执行流程
  /request [ID]         刷新当前或指定的 Mission Request
  /answer 内容          回答澄清或修改草案；/answer-to 问题ID 内容 指定问题
  /plan-detail          展开计划；/check 检查当前版本
  /execute              授权已展示版本；/approve 独立风险审批
  /choose plan|run       旧提议协议的兼容确认；普通自然语言任务无需此步骤
  /cancel request ID 或 /cancel mission ID  预览取消，/cancel confirm 确认

会话与诊断：
  /status、/sessions     查看当前连接与会话；/new [名称] 新建，/resume 名称 恢复
  /history、/raw         查看历史；/reset 清除当前对象选择
  /observe、/reconcile   核对原命令，不重发原操作
  /feedback [目录]       本地保存脱敏诊断包
  /queue、/dequeue 序号、/edit-queued 序号  查看、撤回或取回待发消息（交互终端）
  /quit                 等原请求收尾后退出

Enter 发送，Alt-Enter 换行，Tab 补全。Ctrl-C 停止显示和清空排队，取消任务请明确说出目标。
Alt-Up 取回末条待发消息编辑；Alt-Backspace 撤回末条。处理轨迹留在对话区。
设备状态缺失、过期或来源冲突会明确显示；历史记录不能代替当前读取。
"""


def state_records(raw: JSONObject) -> list[JSONObject]:
    """Validate the State facade envelope before using a scoped record as query evidence."""
    records = raw.get("records")
    if raw.get("schema") != "roboguide.state-query/v0.1" or not isinstance(records, list):
        raise WorkbenchError("State 查询响应格式无效。")
    if not all(isinstance(item, dict) for item in records):
        raise WorkbenchError("State 查询含无效记录。")
    return cast(list[JSONObject], records)


def diagnostic_text(title: str, raw: JSONObject, mission_id: str, group_id: str) -> str:
    """Render only exact joined Group/Mission facts, leaving missing evidence explicit."""
    if title == "当前 Group 绑定":
        records = state_records(raw)
        lines = []
        for record in records:
            obj, value = record.get("object"), record.get("value")
            if (
                not isinstance(obj, dict)
                or obj.get("object_id") != group_id
                or obj.get("object_type") != "execution_group"
                or not isinstance(value, dict)
                or value.get("mission_id") != mission_id
                or record.get("channel_id") != "group-commitment"
            ):
                raise WorkbenchError("Group 绑定记录身份不匹配。")
            assignments = value.get("assignments")
            if not isinstance(assignments, list):
                raise WorkbenchError("Group 绑定缺少完整 assignment 列表。")
            for assignment in assignments:
                if not isinstance(assignment, dict) or not isinstance(
                    assignment.get("node_id"), str
                ):
                    raise WorkbenchError("Group assignment 无效。")
                lines.append(safe_text(json.dumps(assignment, ensure_ascii=False)))
        return "当前 Group 绑定：\n" + ("\n".join(lines) or "没有返回绑定记录；不推断阻塞原因。")
    if (
        raw.get("schema_version") != "roboguide.mission-recovery-view/v0.1"
        or raw.get("mission_id") != mission_id
        or raw.get("group_id") != group_id
    ):
        raise WorkbenchError("恢复评估身份不匹配。")
    labels = {
        "new_execute_fenced": "新执行受到围栏限制",
        "ownership_retained": "仍保留资源所有权",
        "crash_loop_quarantined": "重复失败隔离",
        "review_due": "需要复核",
    }
    lines = []
    for key, label in labels.items():
        value = raw.get(key)
        if not isinstance(value, bool):
            raise WorkbenchError("恢复评估字段缺失。")
        lines.append(label + ("：是" if value else "：否"))
    return "原恢复评估：\n" + "\n".join(lines)


def render_request(raw: JSONObject) -> str:
    """Show the exact reviewed draft and unanswered questions without claiming execution."""
    record = checked_request(raw)
    lines = [
        f"Request {record.request_id}：{record.lifecycle.value}",
        f"Mission：{record.mission_id}",
        f"提交意图：{record.submission_intent.mode if record.submission_intent else '未知'}",
        f"计划版本：{record.draft_revision}；摘要：{record.draft_digest or '尚无草案'}",
    ]
    if record.assessment:
        lines.append("目标：" + safe_text(record.assessment.objective))
        lines.extend("约束：" + safe_text(text) for text in record.assessment.constraints)
        lines.extend("假设：" + safe_text(text) for text in record.assessment.assumptions)
    for turn in record.dialogue:
        if turn.kind.value == "ClarificationQuestion" and not any(
            answer.in_reply_to == turn.turn_id for answer in record.dialogue
        ):
            lines.append(f"问题 {turn.turn_id}：{safe_text(turn.content)}")
    if record.plan:
        plan = record.plan.to_json()
        # The actual Task/Role/operation/parameter document is shown, not an LLM summary.
        text = json.dumps(plan, ensure_ascii=False, indent=2)
        if len(text) > 64000:
            lines.append("计划超过显示上限；不会设置可执行选择。请缩小计划或使用正式审批界面。")
        else:
            lines.append(
                "计划任务与原始参数（完整计划）：\n" + safe_text(text).replace("\\u000a", "\n")
            )
    lines.extend("提示：" + safe_text(issue) for issue in record.issues)
    if record.approval_required:
        lines.append("仍需风险审批：" + ", ".join(record.approval_reasons))
    if record.lifecycle.value == "AwaitingExecution":
        lines.append("尚未提交执行。查看后可用 /execute，修改用 /answer。")
    elif record.lifecycle.value == "AwaitingApproval":
        lines.append("等待 approver 对当前版本审批：/approve。")
    elif record.lifecycle.value == "Accepted":
        lines.append("请求已被 Controller 接纳；实际执行进度用 /mission 或 /watch。")
    return "\n".join(lines)


class WorkbenchSession(Session):
    """Own durable scope and commands used by direct input and closed native task tools."""

    query_status: QueryStatus = "complete"
    model_label = "未加载"
    config_path: Path | None = None

    def __init__(
        self,
        reader: ReadOnlyController,
        router_factory: Callable[[], Router],
        store: SessionStore,
        actions: MissionActions,
    ) -> None:
        """Restore only reference context; historical responses never become fresh observations."""
        super().__init__(reader, router_factory)
        self.controller = reader
        self.store = store
        self.actions = actions
        context = store.data.get("context", {})
        if not isinstance(context, dict):
            raise WorkbenchError("会话上下文损坏。")
        self.displayed_nodes = self._ids(context.get("nodes", []))
        self.displayed_missions = self._ids(context.get("missions", []))
        self.displayed_states = self._ids(context.get("states", []))
        self.last_state = self._optional_id(context.get("last_state"))
        self.last_node = self._optional_id(context.get("last_node"))
        self.last_mission = self._optional_id(context.get("last_mission"))
        self.request_id = self._optional_id(context.get("request_id"))
        previous = context.get("previous_question", "")
        if not isinstance(previous, str) or len(previous) > 4096:
            raise WorkbenchError("会话问题引用损坏。")
        self.previous_question = previous
        self.shown_draft: JSONObject | None = None
        self.suggestion: tuple[str, str] | None = None
        self.pending_cancel: JSONObject | None = None
        self.reference_checkpoint: JSONObject | None = None
        self.input_references: JSONObject | None = None
        self.input_history: list[JSONObject] | None = None
        self.display_turn_id: str | None = None
        historical = context.get("raw_query")
        if historical is not None:
            if not isinstance(historical, dict) or set(historical) != {"value", "read_at"}:
                raise WorkbenchError("会话历史查询记录损坏。")
            value, read_at = historical["value"], historical["read_at"]
            if (
                not isinstance(value, dict)
                or not isinstance(read_at, str)
                or len(json.dumps(historical, ensure_ascii=False).encode()) > 2 * 1024 * 1024
            ):
                raise WorkbenchError("会话历史查询记录无效或超过上限。")
            self.last_raw, self.last_read_at = copy.deepcopy(value), read_at

    def raw_query(self) -> JSONObject | None:
        """Persist a complete bounded historical result, never a clipped value or fresh evidence."""
        if self.last_raw is None or self.last_read_at is None:
            return None
        historical: JSONObject = {"value": self.last_raw, "read_at": self.last_read_at}
        return (
            copy.deepcopy(historical)
            if len(json.dumps(historical, ensure_ascii=False).encode()) <= 2 * 1024 * 1024
            else None
        )

    def query_references(self) -> JSONObject:
        """Resolve queued questions against their original visible identities, never permission."""
        references: JSONObject = {
            "displayed_nodes": list(self.displayed_nodes),
            "displayed_missions": list(self.displayed_missions),
            "last_node": self.last_node,
            "last_mission": self.last_mission,
            "state_targets": list(self.displayed_states),
            "last_state": self.last_state,
            "previous_question": self.previous_question,
        }
        if self.input_references is not None:
            references.update(self.input_references)
        references["request_id"] = self.request_id
        return references

    @staticmethod
    def _ids(value: JSONValue) -> list[str]:
        """Validate bounded reference lists before using a resumed ordinal."""
        if (
            not isinstance(value, list)
            or len(value) > 128
            or not all(isinstance(v, str) for v in value)
        ):
            raise WorkbenchError("会话对象引用损坏。")
        return [identifier(cast(str, v)) for v in value]

    @staticmethod
    def _optional_id(value: JSONValue) -> str | None:
        """Keep absent references unavailable instead of guessing an object identity."""
        if value is None:
            return None
        if not isinstance(value, str):
            raise WorkbenchError("会话对象身份损坏。")
        return identifier(value)

    def checkpoint(self) -> None:
        """Keep unacknowledged references staged while preserving actual command identities."""
        context: JSONObject = {
            "nodes": list(self.displayed_nodes),
            "missions": list(self.displayed_missions),
            "last_node": self.last_node,
            "last_mission": self.last_mission,
            "states": list(self.displayed_states),
            "last_state": self.last_state,
            "request_id": self.request_id,
            "previous_question": self.previous_question,
            "raw_query": self.raw_query(),
        }
        if self.reference_checkpoint is not None:
            for field in ("nodes", "missions", "last_node", "last_mission", "states", "last_state"):
                if field in self.reference_checkpoint:
                    context[field] = self.reference_checkpoint[field]
            context["raw_query"] = self.reference_checkpoint.get("raw_query")
        self.store.data["context"] = context
        self.store.save()

    def ask(self, question: str) -> str:
        """Record successful or failed turns and route writes only from explicit host commands."""
        question = question.strip()
        if not question or len(question) > 4096:
            raise WorkbenchError("单次问题须为 1–4096 字符。")
        if question.startswith("/") and question.partition(" ")[0] != "/choose":
            self.suggestion = None
        self.query_status = "complete"
        try:
            result = self._turn(question)
        except (ValueError, OSError) as error:
            self.query_status = "failed"
            self._remember(question, "查询/操作未确定：" + str(error))
            raise
        self._remember(question, result)
        return result

    def _remember(self, question: str, result: str) -> None:
        """Audit original dialogue before output; only DisplayTurn may acknowledge its reply."""
        messages = self.store.data.get("messages")
        if not isinstance(messages, list):
            raise WorkbenchError("会话消息记录损坏。")
        turn_id = self.display_turn_id or uuid.uuid4().hex
        messages.extend(
            [
                {
                    "role": "user",
                    "text": question,
                    "at": datetime.now(UTC).isoformat(),
                    "turn_id": turn_id,
                },
                {
                    "role": "assistant",
                    "text": result[:64000],
                    "at": datetime.now(UTC).isoformat(),
                    "turn_id": turn_id,
                    "displayed": False,
                },
            ]
        )
        self.store.data["messages"] = messages[-40:]
        self.previous_question = question
        self.checkpoint()

    def _turn(self, question: str) -> str:
        """Separate original text, proposed action and explicit authorized command."""
        if not question.startswith("/cancel"):
            self.pending_cancel = None
        if question == "/help":
            return EXTRA_HELP
        if question == "/history":
            return safe_text(
                json.dumps(self.store.data.get("messages"), ensure_ascii=False, indent=2)
            ).replace("\\u000a", "\n")
        if question == "/raw" and self.last_raw is None:
            self.query_status = "failed"
            return "尚无可恢复的完整查询原文；请先查询。超过保存上限的原文不会截断后冒充完整结果。"
        if question == "/reset":
            super().reset()
            self.displayed_missions = []
            self.displayed_states = []
            self.last_state = None
            self.request_id = None
            self.shown_draft = None
            self.suggestion = None
            return "已清除对象选择；命令回执和历史仍保留。"
        if question.startswith("/"):
            return self._command(question)
        self.suggestion = None
        if self.router is None:
            self.router = self.router_factory()
        references = self.query_references()
        query = self.router.route(question, references)
        query = Query.parse(
            {
                "kind": query.kind,
                "value": query.value,
                "index": query.index,
                "message": query.message,
            }
        )
        if query.kind in {"plan", "run", "answer", "execute", "approve"}:
            self.suggestion = (query.kind, question)
            if query.kind in {"plan", "run"}:
                return (
                    "已识别任务提议，尚未创建请求。原话："
                    + safe_text(question)
                    + "\n选择：/choose plan 仅生成计划；/choose run 开始执行流程。"
                )
            return f"尚未发出操作。确认 {query.kind} 请用 /choose {query.kind}。"
        if query.kind == "help":
            return HELP + EXTRA_HELP
        if query.kind == "clarify":
            return "需要补充：" + safe_text(query.message or "")
        if query.kind == "unsupported":
            return "此操作尚未开放，未发出命令。"
        if query.value and query.kind in {"node", "mission", "explain"}:
            domain = "nodes" if query.kind == "node" else "missions"
            known = cast(list[JSONValue], references["displayed_" + domain])
            current = references["last_node" if query.kind == "node" else "last_mission"]
            if query.value not in known and query.value != current and query.value not in question:
                raise WorkbenchError("对象 ID 没有用户输入或会话引用依据。")
        if query.index is not None:
            domain = "nodes" if query.kind == "node" else "missions"
            known_ids = cast(list[str], references["displayed_" + domain])
            if not 1 <= query.index <= len(known_ids):
                raise WorkbenchError("原输入时没有这个已展示对象序号。")
            query = Query(query.kind, value=known_ids[query.index - 1])
        if query.kind == "compare":
            return self.read_comparison(tuple(cast(list[str], references["displayed_nodes"])))
        if query.kind in {"missions", "compare"}:
            return self._command("/" + query.kind)
        if query.kind == "active_missions":
            return self.read_missions(active_only=True).text
        if query.kind in {"mission", "explain"}:
            target = (
                self._mission_target("#" + str(query.index))
                if query.index is not None
                else query.value or ""
            )
            return (
                self.read_explanation(target).text
                if query.kind == "explain"
                else self.read_mission(target)
            )
        return self._execute(query)

    def _mission_target(self, text: str) -> str:
        """Resolve a Mission ordinal against the displayed list before any fresh read."""
        if not text:
            if self.last_mission is None:
                raise WorkbenchError("请指定 Mission ID 或先查询 /missions。")
            return self.last_mission
        if text.startswith("#"):
            try:
                index = int(text[1:])
            except ValueError as error:
                raise WorkbenchError("Mission 序号无效。") from error
            if not 1 <= index <= len(self.displayed_missions):
                raise WorkbenchError("没有这个 Mission 序号。")
            return self.displayed_missions[index - 1]
        return identifier(text)

    def _show_request(self, raw: JSONObject) -> str:
        """Bind explicit actions only to a complete displayed server draft."""
        rendered = render_request(raw)
        record = checked_request(raw)
        self.request_id, self.last_mission = record.request_id, record.mission_id
        self.last_raw = raw
        self.last_read_at = datetime.now(UTC).isoformat(timespec="seconds")
        self.shown_draft = (
            raw
            if record.plan
            and len(json.dumps(record.plan.to_json(), ensure_ascii=False, indent=2)) <= 64000
            else None
        )
        return rendered

    def read_mission(self, target: str) -> str:
        """Read an exact validated literal ID, without reinterpreting text as command syntax."""
        target = identifier(target)
        raw = self.controller.mission(target)
        result = render_mission(raw, target)
        self.last_mission, self.last_node = target, None
        self.last_raw = raw
        self.last_read_at = datetime.now(UTC).isoformat(timespec="seconds")
        return result

    def read_missions(self, active_only: bool = False) -> ReadResult:
        """Read one current State cohort and render bounded host-owned Mission aggregates."""
        raw = self.controller.missions()
        result, complete, ids = render_mission_list(raw, active_only=active_only)
        self.displayed_missions = ids
        self.last_mission = ids[0] if len(ids) == 1 else None
        self.last_node = None
        self.last_raw = raw
        self.last_read_at = datetime.now(UTC).isoformat(timespec="seconds")
        component: JSONObject = {
            "source": "current-mission-state",
            "complete": complete,
            "active_only": active_only,
            "displayed_count": len(ids),
            "display_truncated": "完整数量仍为上方聚合" in result,
        }
        self.query_status = "complete" if complete else "partial"
        return ReadResult(result, complete, (component,))

    def read_explanation(self, target: str) -> ReadResult:
        """Retain each validated diagnostic read and its explicit availability independently."""
        result = self.read_mission(target)
        raw = self.last_raw
        assert raw is not None
        group = cast(str, raw["group_id"])
        components: list[JSONObject] = [
            {
                "source": "mission",
                "complete": True,
                "received_at": self.last_read_at,
                "text": result,
            }
        ]
        reads: tuple[tuple[str, Callable[[], JSONObject]], ...] = (
            ("当前 Group 绑定", lambda: self.controller.mission_bindings(group)),
            ("原恢复评估", lambda: self.controller.mission_recovery(target)),
        )
        for title, read in reads:
            component: JSONObject = {
                "source": title,
                "complete": True,
            }
            try:
                text = diagnostic_text(title, read(), target, group)
                if len(text) > 16000:
                    text = text[:16000] + "…（结果已截断，证据不完整）"
                    component["complete"] = False
            except (ValueError, OSError) as error:
                text = title + "不可用：" + safe_text(str(error))
                component["complete"] = False
            component["received_at"] = datetime.now(UTC).isoformat(timespec="seconds")
            component["text"] = text
            components.append(component)
            result += "\n" + text
        result += "\n以上是分别读取的记录，不保证跨接口同一快照；记录未说明的原因保持未知。"
        complete = all(component["complete"] is True for component in components)
        self.query_status = "complete" if complete else "partial"
        return ReadResult(result, complete, tuple(components))

    def read_comparison(self, original: tuple[str, ...]) -> str:
        """Compare a frozen displayed cohort against one fresh complete inventory response."""
        if not 1 <= len(original) <= 4:
            raise WorkbenchError("请先检索并展示 1–4 个节点，再 /compare。")
        raw = self.controller.inventory()
        snapshot = inventory_view(raw)
        by_id = {node.node_id: node for node in snapshot.nodes}
        if any(node not in by_id for node in original):
            raise WorkbenchError("部分原节点已不在最新可见库存中，无法完整比较。")
        self.last_raw = raw
        self.last_read_at = datetime.now(UTC).isoformat(timespec="seconds")
        contract_sets = [set(by_id[node].contracts) for node in original]
        common = set.intersection(*contract_sets)
        difference = [f"共同返回合同：{', '.join(sorted(common)) or '无'}"]
        for node in original:
            unique = set(by_id[node].contracts) - common
            difference.append(
                f"{safe_text(node)} 的其它合同：{safe_text(', '.join(sorted(unique))) or '无'}"
            )
        rendered = (
            "\n".join(difference)
            + "\n"
            + render_nodes([by_id[node] for node in original], snapshot)
        )
        self.displayed_nodes = list(original)
        self.last_node = original[0] if len(original) == 1 else None
        self.last_mission = None
        return rendered

    def _command(self, text: str) -> str:
        """Dispatch explicit commands; model output never enters this method as a write."""
        command, _, argument = text.partition(" ")
        argument = argument.strip()
        if command in {"/state", "/state-fields"}:
            try:
                values = shlex.split(argument)
            except ValueError as error:
                raise WorkbenchError('命令引号或转义不完整；例如 /state "设备ID" 电量。') from error
            target = values[0] if values else "" if command == "/state-fields" else self.last_state
            if target is None:
                raise WorkbenchError("请先 /state-fields 查看设备，或使用 /state 设备ID battery。")
            if target.startswith("#"):
                try:
                    index = int(target[1:])
                except ValueError as error:
                    raise WorkbenchError("请使用 /state #2 battery 这样的设备序号。") from error
                if not 1 <= index <= len(self.displayed_states):
                    raise WorkbenchError("上次展示的状态对象中没有这个序号，请先 /state-fields。")
                target = self.displayed_states[index - 1]
            if command == "/state-fields":
                if len(values) > 1:
                    raise WorkbenchError("用法：/state-fields [设备ID]。")
                raw = self.controller.state_providers()
                state_result, targets = discover(raw, target)
                self.displayed_states = targets
                self.last_state = targets[0] if len(targets) == 1 else None
                self.last_raw = raw
            else:
                requested = fields(values[1:] or ["battery", "position", "posture"])
                snapshot = read_state(self.controller, target, requested, self.controller.timeout)
                state_result = snapshot.render()
                selected_choices = choices(state_result)
                if selected_choices:
                    self.displayed_states = selected_choices
                self.last_state = target
                self.last_raw = {
                    "providers": snapshot.catalog,
                    "inventory": snapshot.inventory,
                    "state": snapshot.state,
                }
            self.last_read_at = datetime.now(UTC).isoformat(timespec="seconds")
            self.query_status = (
                "complete"
                if state_result.complete
                else "partial"
                if any(item.get("complete") is True for item in state_result.components)
                else "failed"
            )
            return state_result.text
        if command == "/status":
            from roboguide_workbench.status import status_line

            return status_line(self)
        if command == "/sessions":
            from roboguide_workbench.sessions import list_sessions

            return list_sessions(self)
        if command == "/feedback":
            from roboguide_workbench.feedback import create_feedback
            from roboguide_workbench.provider_config import load_chat_settings

            secrets = [getattr(self.router, "key", "") or ""]
            if self.config_path is not None:
                import os

                try:
                    provider = load_chat_settings(self.config_path).provider
                    secrets.append(os.environ.get(provider.api_key_env, ""))
                except (ValueError, OSError):
                    pass
            path = create_feedback(
                self, Path(argument).expanduser() if argument else None, secrets=secrets
            )
            return "反馈包已保存到本地：" + safe_text(str(path))
        if command == "/check":
            from roboguide_workbench.preflight import check_plan

            if argument or self.request_id is None or self.shown_draft is None:
                raise WorkbenchError("请先 /request 展示当前完整计划，再使用 /check。")
            current = self.actions.get(self.request_id)
            if any(
                current.get(key) != self.shown_draft.get(key)
                for key in ("draft_revision", "draft_digest", "lifecycle")
            ):
                return "计划或状态已改变，请确认新版本后再检查：\n" + self._show_request(current)
            checked = check_plan(self.controller, checked_request(current))
            self.query_status = "complete" if checked.complete else "partial"
            self.last_check = checked
            return checked.text
        if command == "/cancel":
            from roboguide_workbench.cancellation import CancellationActions, render_preview

            cancellations = CancellationActions(self.controller, self.actions)
            if argument == "confirm":
                selected = self.pending_cancel
                self.pending_cancel = None
                self.shown_draft = None
                self.suggestion = None
                if selected is None:
                    raise WorkbenchError(
                        "请先 /cancel request ID 或 /cancel mission ID 查看取消目标。"
                    )
                result = cancellations.confirm(selected)
                return str(result["text"])
            self.pending_cancel = None
            kind, _, cancel_target = argument.partition(" ")
            if kind not in {"request", "mission"} or not cancel_target.strip():
                raise WorkbenchError(
                    "用法：/cancel request ID、/cancel mission ID；确认用 /cancel confirm。"
                )
            self.pending_cancel = cancellations.preview(kind, cancel_target.strip())
            return render_preview(self.pending_cancel)
        if command == "/choose":
            proposal = self.suggestion
            if proposal is None or argument not in (
                {"plan", "run"} if proposal[0] in {"plan", "run"} else {proposal[0]}
            ):
                raise WorkbenchError("没有匹配的待确认提议。")
            self.suggestion = None
            return self._command(
                "/"
                + argument
                + (" " + proposal[1] if argument in {"plan", "run", "answer"} else "")
            )
        self.suggestion = None
        if command in {"/plan", "/run"}:
            if not argument:
                raise WorkbenchError("请提供任务目标。")
            self.shown_draft = None
            return self._show_request(
                self.actions.command(
                    "create",
                    {
                        "schema_version": "roboguide.mission-request-create/v0.2",
                        "instruction": argument,
                        "submission_mode": "draft_only"
                        if command == "/plan"
                        else "submit_when_ready",
                    },
                )
            )
        if command == "/request":
            target = argument or self.request_id
            if target is None:
                raise WorkbenchError("请提供 Request ID。")
            return self._show_request(self.actions.get(target))
        if command in {"/answer", "/answer-to", "/execute", "/approve"}:
            if self.request_id is None:
                raise WorkbenchError("没有当前 Request，请先 /request。")
            body: JSONObject
            if command in {"/answer", "/answer-to"}:
                if not argument:
                    raise WorkbenchError("请提供回答或修改内容。")
                body = {"text": argument}
                if command == "/answer-to":
                    question_id, _, answer = argument.partition(" ")
                    if not answer.strip():
                        raise WorkbenchError("请提供问题 ID 和回答。")
                    body = {"text": answer, "question_id": identifier(question_id)}
                action = "messages"
            else:
                if argument or self.shown_draft is None:
                    raise WorkbenchError("请先 /request 展示当前完整计划，再执行该命令。")
                old = checked_request(self.shown_draft)
                current = self.actions.get(self.request_id)
                if any(
                    current.get(key) != self.shown_draft.get(key)
                    for key in ("draft_revision", "draft_digest", "lifecycle")
                ):
                    return "计划或状态已改变，未发出命令，请重新查看：\n" + self._show_request(
                        current
                    )
                body = {"draft_revision": old.draft_revision, "draft_digest": old.draft_digest}
                action = command[1:]
            self.shown_draft = None
            return self._show_request(self.actions.command(action, body, self.request_id))
        if command in {"/observe", "/reconcile"}:
            from roboguide_workbench.cancellation import CancellationActions

            cancellation = CancellationActions(self.controller, self.actions)
            if cancellation.owns_latest():
                return str(cancellation.observe()["text"])
            observed = self.actions.observe(reconcile=command == "/reconcile")
            candidate = (
                observed.get("response")
                if observed.get("status") == "complete"
                else observed.get("progress")
            )
            prefix = f"原命令状态：{observed.get('status')}；没有重发。\n"
            if (
                isinstance(candidate, dict)
                and candidate.get("schema_version") == "roboguide.mission-request/v0.5"
            ):
                return prefix + self._show_request(candidate)
            original_request = observed.get("request_id")
            if isinstance(original_request, str):
                return prefix + self._show_request(self.actions.get(original_request))
            return prefix + safe_text(json.dumps(observed, ensure_ascii=False))
        if command == "/missions":
            active_only = argument.strip().casefold() == "active"
            if argument and not active_only:
                raise WorkbenchError("/missions 仅支持可选参数 active。")
            return self.read_missions(active_only=active_only).text
        if command == "/compare":
            if argument:
                raise WorkbenchError("请先检索并展示 1–4 个节点，再 /compare。")
            return self.read_comparison(tuple(self.displayed_nodes))
        if command in {"/mission", "/explain"}:
            target = self._mission_target(argument)
            if command == "/explain":
                return self.read_explanation(target).text
            return self.read_mission(target)
        return super().ask(text)

    def watch(
        self, target: str, emit: Callable[[str], None], *, interval: float = 2, samples: int = 150
    ) -> None:
        """Poll a fixed Mission at most five minutes; interrupt only the local observation."""
        mission_id = self._mission_target(target)
        self.last_mission = mission_id
        self.checkpoint()
        try:
            for index in range(samples):
                raw = self.controller.mission(mission_id)
                emit(render_mission(raw, mission_id))
                self.last_raw = raw
                self.last_read_at = datetime.now(UTC).isoformat(timespec="seconds")
                if raw.get("status") in {"Completed", "Failed", "Cancelled"}:
                    return
                if index + 1 < samples:
                    time.sleep(interval)
        except KeyboardInterrupt:
            emit("已停止本地跟踪；没有发送取消任务命令。")
