"""Natural Responses conversations over host-owned reads and explicit Mission commands."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import replace
from typing import cast

from mission.models import JSONObject, JSONValue

from roboguide_workbench.agent_loop import AgentConversation
from roboguide_workbench.agent_tools import ReadToolAdapter
from roboguide_workbench.answers import (
    EvidenceConversation,
    bounded_history,
    host_read_answer,
    is_active_missions_question,
    is_inventory_question,
    local_response,
)
from roboguide_workbench.client import WorkbenchError
from roboguide_workbench.evidence import (
    READ_COSTS,
    OriginalOperationPending,
    ReadResult,
    command_count,
    new_pending_command,
)
from roboguide_workbench.mission_diagnosis import cancellation_diagnosis_intent, diagnosis_ordinal
from roboguide_workbench.mission_views import cancelling_missions
from roboguide_workbench.plan_view import same_plan
from roboguide_workbench.routing import Query, ResponsesRouter
from roboguide_workbench.session import LOCAL_SNAPSHOT_COMMANDS
from roboguide_workbench.task_tools import TaskTools
from roboguide_workbench.task_tools import definitions as task_definitions
from roboguide_workbench.views import safe_text
from roboguide_workbench.workspace import WorkbenchSession, render_request

_DIAGNOSIS_CHOICES = 6


class ConversationSession(WorkbenchSession):
    """Keep grounded explanations separate from authoritative Mission commands and approvals."""

    answer_mode = "agent"
    evidence: list[JSONObject]
    tool_limit = 12
    call_limit = 8
    turn_seconds = 120.0
    stream_output: Callable[[str], None] | None = None
    stream_enabled = True
    progress: Callable[[str], None] = staticmethod(lambda message: None)

    def _turn(self, question: str) -> str:
        """Keep commands verbatim and route natural text through native read and task tools."""
        if not question.startswith("/cancel"):
            self.pending_cancel = None
        if question in LOCAL_SNAPSHOT_COMMANDS or question == "/help":
            return super()._turn(question)
        if self.answer_mode in {"agent", "grounded"} and not question.startswith("/"):
            diagnosis = self._natural_diagnosis(question)
            if diagnosis is not None:
                return diagnosis
        self.diagnosis_selection = False
        if question.startswith("/"):
            if question == "/reset":
                self.evidence = []
            return super()._turn(question)
        self.suggestion = None
        if self.answer_mode in {"agent", "grounded"}:
            return self._agent_turn(question)
        return self._legacy_turn(question)

    def _natural_diagnosis(self, question: str) -> str | None:
        """Resolve a plain read-only question or its displayed ordinal before using a model."""
        intent = cancellation_diagnosis_intent(question)
        ordinal = diagnosis_ordinal(question)
        if intent is None and ordinal is None:
            return None
        references = self.query_references()
        selecting = references.get("diagnosis_selection") is True
        if intent is None and not selecting:
            return None

        target: str | None = None
        if ordinal is not None and selecting:
            displayed = self._ids(references.get("displayed_missions", []))
            if not 1 <= ordinal <= len(displayed):
                return "请回复刚才列表里显示的序号。"
            target = displayed[ordinal - 1]
        elif intent == "focused":
            target = self._optional_id(references.get("last_mission"))
        self.suggestion = None
        self.diagnosis_selection = False
        self.evidence = []

        cost = READ_COSTS["read_mission_diagnosis"] + (1 if target is None else 0)
        original_timeout = self.controller.timeout
        self.controller.timeout = min(original_timeout, self.turn_seconds / cost)
        try:
            if target is None:
                self.progress("正在查看哪些任务还在取消中")
                trace = self.activity
                run = trace.begin("查找取消中的任务") if trace is not None else None
                try:
                    matches, complete = cancelling_missions(self.controller.missions())
                except (ValueError, OSError):
                    if trace is not None and run is not None:
                        trace.finish(run, "unavailable")
                    raise
                if trace is not None and run is not None:
                    trace.finish(
                        run,
                        "complete" if complete else "partial",
                        result=ReadResult(f"查到 {len(matches)} 个处于取消中的任务。"),
                        reference="Controller · Mission 列表",
                    )
                if not self.task_dialogue.pinned:
                    self.task_dialogue.clear()
                if not matches:
                    if not complete:
                        self.query_status = "partial"
                        return "当前任务列表有缺口，暂时无法确认哪些任务在取消中。"
                    return "当前没有查到处于取消中的任务。"
                if len(matches) != 1 or not complete:
                    shown = matches[:_DIAGNOSIS_CHOICES]
                    self.displayed_missions = [item.mission_id for item in shown]
                    self.last_mission = None
                    self.diagnosis_selection = True
                    self.query_status = "complete" if complete else "partial"
                    head = f"查到 {len(matches)} 个处于取消中的任务。"
                    if not complete:
                        head += "当前列表有缺口。"
                    if len(matches) > len(shown):
                        head += f"先列出前 {len(shown)} 个。"
                    rows = [
                        f"{i}. {item.display_objective} · {safe_text(item.mission_id)}"
                        for i, item in enumerate(shown, 1)
                    ]
                    return "\n".join([head, *rows, "想查哪个？回复序号就行。"])
                target = matches[0].mission_id
            if self.tool_limit < cost:
                self.query_status = "failed"
                return "本轮读取预算不足，暂时无法完成任务诊断。"
            self.progress("正在核对原执行与停止记录")
            result = self.diagnose_mission(target, record_activity=True)
            if not self.task_dialogue.pinned:
                self.task_dialogue.clear()
            return result.text
        except (ValueError, OSError) as error:
            self.query_status = "failed"
            return "暂时无法完成任务诊断。" + safe_text(str(error))
        finally:
            self.controller.timeout = original_timeout

    def _agent_turn(self, question: str) -> str:
        """Use original user text with host-checked tools rather than generated task commands."""
        self.evidence = []
        if self.router is None:
            self.router = self.router_factory()
        if not isinstance(self.router, ResponsesRouter):
            # Explicit injected legacy routers retain their protocol; actual Responses
            # providers never silently downgrade when a native request fails.
            return super()._turn(question)
        references = self.query_references()
        references["controller"] = self.controller.endpoint
        history = (
            self.input_history
            if self.input_history is not None
            else bounded_history(self.store.data.get("messages"))
        )
        adapter = ReadToolAdapter(self, question, references)
        tasks = TaskTools(self, question)

        def invoke_read(name: str, arguments: JSONObject, remaining: float) -> ReadResult:
            """Share validated identities with task tools while keeping visible selection staged."""
            returned = adapter.read(name, arguments, remaining)
            tasks.learn_missions(adapter.task_missions(returned))
            return returned

        def invoke_task(
            name: str,
            arguments: JSONObject,
            original: str,
            user_turns: tuple[str, ...],
            remaining: float,
        ) -> ReadResult:
            """Publish actual new receipt IDs to subsequent reads without guessing targets."""
            before = command_count(self.store.data)
            try:
                returned = tasks.invoke(name, arguments, original, remaining, user_turns=user_turns)
            except (ValueError, OSError) as error:
                if new_pending_command(self.store.data, before):
                    raise OriginalOperationPending(str(error)) from error
                raise
            adapter.learn_task(tasks.known_nodes)
            return returned

        self.progress("正在思考")
        result = AgentConversation(
            self.router, tools=self.tool_limit, calls=self.call_limit, seconds=self.turn_seconds
        ).answer(
            question,
            adapter.references,
            history,
            invoke_read,
            tool_definitions=adapter.definitions() + task_definitions(),
            progress=self.progress,
            act=invoke_task,
            task_read_context=tasks.cache_context,
            task_work_key=tasks.work_key,
            on_delta=self.stream_output if self.stream_enabled else None,
            activity=self.activity,
        )
        self.query_status, self.evidence = result.status, result.evidence
        if not tasks.owns_selection:
            if not result.evidence and re.search(r"[?？]|请.+(?:提供|补充|告诉)", result.text):
                # Compatibility with providers which answer a task clarification directly.
                self.task_dialogue.pinned = True
            elif result.evidence and not self.task_dialogue.pinned:
                self.task_dialogue.clear()
        self._retain_agent_turn(question, result.items)
        if result.proposal is not None:
            if result.proposal_text is None:
                raise WorkbenchError("操作提议缺少完整原文。")
            self.suggestion = (result.proposal.kind, result.proposal_text)
            options = (
                "/choose plan 仅规划；/choose run 开始执行流程"
                if result.proposal.kind in {"plan", "run"}
                else "/choose " + result.proposal.kind
            )
            return (
                "待确认的原文：\n"
                + safe_text(result.proposal_text).replace("\\u000a", "\n")
                + "\n确认后按以上原文继续。\n"
                + options
            )
        text = result.text
        task_answer = tasks.answer(result.evidence)
        read_answer = adapter.read_answer(result.evidence, include_nonstate=task_answer is not None)
        if task_answer is not None or read_answer is not None:
            text = "\n\n".join(
                part for part in (read_answer.text if read_answer else None, task_answer) if part
            )
            if read_answer is not None and not read_answer.complete:
                self.query_status = (
                    "partial"
                    if any(item.complete for item in tasks.results)
                    or any(item.get("complete") is True for item in read_answer.components)
                    else "failed"
                )
            if result.status != "complete":
                text += "\n本轮其它操作、读取或回复未完成；未确认部分保持未知。"
        presentation: str | None = None
        if self.activity is not None and (task_answer is not None or read_answer is not None):
            # Keep every plan and clarification in the visible result; only advisory
            # capability descriptions and detailed diagnostic evidence can be folded.
            presentation = "\n\n".join(
                part
                for part in (
                    tasks.answer(result.evidence, compact=True),
                    (read_answer.summary or read_answer.text) if read_answer else None,
                )
                if part
            )
            if result.status != "complete":
                presentation += "\n本轮其它操作、读取或回复未完成；未确认部分保持未知。"
        if result.evidence:
            summary = adapter.reference_summary(result.evidence)
            if summary:
                text += "\n\n" + summary
                if presentation is not None:
                    presentation += "\n\n" + summary
            mission = self.last_mission
            adapter.publish(result.evidence, publish_raw=not tasks.owns_selection)
            if tasks.owns_selection:
                self.last_mission = mission
        if self.activity is not None:
            self.activity.reply = presentation
        return text

    def _retain_agent_turn(self, question: str, items: list[JSONObject]) -> None:
        """Keep bounded public tool transcripts as historical audit data without permissions."""
        context = self.store.data.get("context")
        if not isinstance(context, dict):
            return
        previous = context.get("agent_turns")
        turns = list(previous) if isinstance(previous, list) else []
        public = [item for item in items if item.get("type") != "reasoning"]
        current: JSONObject = {"question": question, "items": cast(JSONValue, public)}
        if len(json.dumps(current, ensure_ascii=False).encode()) > 48_000:
            current = {
                "question": question,
                "items": [],
                "omitted": "transcript exceeded audit limit",
            }
        turns.append(current)
        while turns and (
            len(turns) > 10 or len(json.dumps(turns, ensure_ascii=False).encode()) > 128_000
        ):
            turns.pop(0)
        context["agent_turns"] = cast(JSONValue, turns)

    def _legacy_turn(self, question: str) -> str:
        """Retain the old query protocol only for an explicitly selected compatibility mode."""
        # Keep basic conversation independent of Provider credentials and Controller reachability.
        # The full-turn matcher intentionally leaves compound requests (for example,
        # ``你好，查节点``) to the evidence router.
        direct = local_response(question)
        if direct is not None:
            self.query_status = "complete"
            return direct
        if is_active_missions_question(question):
            return self._read_host_query(Query("active_missions"))
        if is_inventory_question(question):
            return self._read_host_query(Query("cluster"))
        if self.answer_mode == "facts":
            return super()._turn(question)
        if self.router is None:
            self.router = self.router_factory()
        if not isinstance(self.router, ResponsesRouter):
            return super()._turn(question)
        references = self.query_references()
        references["controller"] = self.controller.endpoint
        context = self.store.data.get("context")
        if isinstance(context, dict) and context.get("request_context") is not None:
            references["request_context_historical"] = context["request_context"]
        self.progress("正在查询并整理依据")
        comparison_targets: dict[Query, tuple[str, ...]] = {}

        def resolve(query: Query) -> Query:
            """Freeze this batch's typed references before its first read can change a list."""
            if query.kind == "compare":
                comparison_targets[query] = tuple(cast(list[str], references["displayed_nodes"]))
            if query.index is not None:
                domain = "nodes" if query.kind == "node" else "missions"
                displayed = cast(list[str], references["displayed_" + domain])
                if not 1 <= query.index <= len(displayed):
                    raise WorkbenchError("本批查询没有这个已展示对象序号。")
                return replace(query, value=displayed[query.index - 1], index=None)
            if query.value and query.kind in {"node", "mission", "explain"}:
                domain = "nodes" if query.kind == "node" else "missions"
                known = [
                    *cast(list[str], references["displayed_" + domain]),
                    references["last_node" if query.kind == "node" else "last_mission"],
                ]
                if query.value not in known and query.value not in question:
                    raise WorkbenchError("对象 ID 没有当前问题或查询结果依据。")
            return query

        def read(query: Query, remaining: float) -> str | ReadResult:
            """Perform a resolved closed read under the remaining original turn budget."""
            original_timeout = self.controller.timeout
            self.controller.timeout = min(
                original_timeout, remaining / (3 if query.kind == "explain" else 1)
            )
            self.progress("正在读取 " + query.kind)
            try:
                result: str | ReadResult
                if query.kind == "compare":
                    result = self.read_comparison(comparison_targets[query])
                elif query.kind in {"missions", "active_missions"}:
                    result = self.read_missions(active_only=query.kind == "active_missions")
                elif query.kind == "explain":
                    result = self.read_explanation(query.value or "")
                elif query.kind == "mission":
                    result = self.read_mission(query.value or "")
                else:
                    result = self._execute(query)
                references["displayed_nodes"] = list(self.displayed_nodes)
                references["displayed_missions"] = list(self.displayed_missions)
                return result
            finally:
                self.controller.timeout = original_timeout

        result = EvidenceConversation(
            self.router,
            tools=min(4, self.tool_limit),
            calls=min(3, self.call_limit),
            seconds=min(60, self.turn_seconds),
        ).answer(
            question,
            references,
            self.input_history
            if self.input_history is not None
            else bounded_history(self.store.data.get("messages")),
            read,
            resolve=resolve,
        )
        self.query_status = result.status
        if result.evidence:
            evidence_log = self.store.data["context"]
            if isinstance(evidence_log, dict):
                evidence_log["last_evidence"] = cast(JSONValue, result.evidence)
            self.evidence = result.evidence
        proposal = result.proposal
        if proposal:
            if proposal.kind in {"plan", "run", "answer", "execute", "approve"}:
                self.suggestion = (proposal.kind, question)
                options = (
                    "/choose plan 或 /choose run"
                    if proposal.kind in {"plan", "run"}
                    else "/choose " + proposal.kind
                )
                return "尚未发出操作。原话：" + safe_text(question) + "\n请选择：" + options
            if proposal.kind == "clarify":
                return "需要补充：" + safe_text(proposal.message or "")
            return "此问题尚未执行操作；使用 /help 查看支持范围。"
        return result.text

    def _read_host_query(self, query: Query) -> str:
        """Perform one obvious current read under the configured turn budget."""
        self.progress("正在读取 " + query.kind)
        original_timeout = self.controller.timeout
        self.controller.timeout = min(original_timeout, self.turn_seconds)
        try:
            returned = (
                self.read_missions(active_only=True)
                if query.kind == "active_missions"
                else self._execute(query)
            )
            result = host_read_answer(query, returned)
        except (ValueError, OSError, RuntimeError) as error:
            self.query_status = "failed"
            return "当前状态读取失败，状态未知；未根据历史记录推断。\n原因：" + safe_text(
                str(error)
            )
        finally:
            self.controller.timeout = original_timeout
        self.query_status = result.status
        self.evidence = result.evidence
        evidence_log = self.store.data.get("context")
        if isinstance(evidence_log, dict):
            evidence_log["last_evidence"] = cast(JSONValue, result.evidence)
        return result.text

    def checkpoint(self) -> None:
        """Persist structured evidence with references through the sole foreground writer."""
        old_context = self.store.data.get("context")
        old_request = old_context.get("request_context") if isinstance(old_context, dict) else None
        agent_turns = old_context.get("agent_turns") if isinstance(old_context, dict) else None
        super().checkpoint()
        context = self.store.data["context"]
        if isinstance(context, dict):
            if agent_turns is not None:
                context["agent_turns"] = agent_turns
            if hasattr(self, "evidence"):
                context["last_evidence"] = cast(JSONValue, self.evidence)
            if self.request_id:
                context["request_context"] = getattr(self, "request_context", old_request)
            self.store.save()

    def _show_request(self, raw: JSONObject) -> str:
        """Expand new plans completely and keep acknowledged unchanged progress reads compact."""
        previous = self.acknowledged_draft
        full = super()._show_request(raw)
        self.request_context: JSONObject = {
            key: raw.get(key)
            for key in ("request_id", "lifecycle", "assessment", "draft_revision", "draft_digest")
        }
        plan = raw.get("plan")
        if not isinstance(plan, dict):
            return full
        summary = render_request(raw, include_plan=False) if same_plan(previous, raw) else full
        if (
            previous
            and previous.get("request_id") == raw.get("request_id")
            and previous.get("draft_revision") != raw.get("draft_revision")
        ):
            summary += (
                f"\n版本变化：{previous.get('draft_revision')} → {raw.get('draft_revision')}\n"
            )
            old = previous.get("plan")
            if isinstance(old, dict):
                changed = [key for key in set(old) | set(plan) if old.get(key) != plan.get(key)]
                if changed:
                    summary += "修改部分：" + ", ".join(sorted(changed))
        return summary
