"""Third-generation conversation composition over the unchanged explicit command owner."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from typing import cast

from mission.models import JSONObject, JSONValue

from roboguide_workbench.answers import (
    EvidenceConversation,
    bounded_history,
    host_read_answer,
    is_active_missions_question,
    is_inventory_question,
    local_response,
)
from roboguide_workbench.client import WorkbenchError
from roboguide_workbench.evidence import ReadResult
from roboguide_workbench.routing import Query, ResponsesRouter
from roboguide_workbench.views import safe_text
from roboguide_workbench.workspace import WorkbenchSession


class ConversationSession(WorkbenchSession):
    """Keep grounded explanations separate from authoritative Mission commands and approvals."""

    answer_mode = "grounded"
    tool_limit = 4
    call_limit = 3
    turn_seconds = 60.0
    progress: Callable[[str], None] = staticmethod(lambda message: None)

    def _turn(self, question: str) -> str:
        """Use original commands verbatim; only natural-language reads enter evidence reasoning."""
        if not question.startswith("/cancel"):
            self.pending_cancel = None
        if question == "/help":
            return super()._turn(question) + (
                "\n第三版：/plan-detail 展开完整计划；异步终端可用 /watches、/unwatch ID。\n"
                "解释引用本轮证据；/history 是历史，不能代替当前状态。"
            )
        if question == "/plan-detail":
            if self.shown_draft is None:
                raise WorkbenchError("请先 /request 查看当前计划。")
            return safe_text(
                json.dumps(self.shown_draft.get("plan"), ensure_ascii=False, indent=2)
            ).replace("\\u000a", "\n")
        if question.startswith("/"):
            if question == "/reset":
                self.evidence = []
            return super()._turn(question)
        self.suggestion = None
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
        if self.shown_draft:
            references["draft"] = {
                key: self.shown_draft.get(key)
                for key in ("draft_revision", "draft_digest", "lifecycle", "assessment")
            }
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
            self.router, tools=self.tool_limit, calls=self.call_limit, seconds=self.turn_seconds
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
        super().checkpoint()
        context = self.store.data["context"]
        if isinstance(context, dict):
            if hasattr(self, "evidence"):
                context["last_evidence"] = cast(JSONValue, self.evidence)
            if self.request_id:
                context["request_context"] = getattr(self, "request_context", old_request)
            self.store.save()

    def _show_request(self, raw: JSONObject) -> str:
        """Summarize the host-validated plan and show changes while retaining its exact identity."""
        previous = getattr(self, "displayed_plan_history", None)
        full = super()._show_request(raw)
        self.displayed_plan_history = raw
        self.request_context: JSONObject = {
            key: raw.get(key)
            for key in ("request_id", "lifecycle", "assessment", "draft_revision", "draft_digest")
        }
        plan = raw.get("plan")
        if not isinstance(plan, dict):
            return full
        tasks = plan.get("tasks")
        rows = tasks if isinstance(tasks, list) else []
        summary = full.split("计划任务与原始参数", 1)[0]
        summary += "\n计划步骤（完整参数用 /plan-detail 展开）：\n"
        for task in rows:
            if not isinstance(task, dict):
                continue
            summary += safe_text(json.dumps(task, ensure_ascii=False)) + "\n"
        if previous and previous.get("draft_digest") != raw.get("draft_digest"):
            summary += f"版本变化：{previous.get('draft_revision')} → {raw.get('draft_revision')}\n"
            old = previous.get("plan")
            if isinstance(old, dict):
                changed = [key for key in set(old) | set(plan) if old.get(key) != plan.get(key)]
                summary += "修改部分：" + ", ".join(sorted(changed)) + "\n"
        if raw.get("approval_required"):
            summary += "仍需原风险审批。\n"
        return summary + "执行仍绑定上述原版本；/execute 授权，/answer 修改。"
