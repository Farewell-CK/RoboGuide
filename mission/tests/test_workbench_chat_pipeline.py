"""Focused regressions for direct chat turns and bounded evidence fallback."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from mission.models import JSONObject
from roboguide_workbench.actions import MissionActions
from roboguide_workbench.answers import EvidenceConversation
from roboguide_workbench.conversation import ConversationSession
from roboguide_workbench.evidence import ReadResult
from roboguide_workbench.persistence import SessionStore
from test_workbench import ScriptedTransport, descriptor, router
from test_workbench_v2 import ObservedController


def _session(
    tmp_path: Path,
    transport: object,
    controller: ObservedController | None = None,
) -> ConversationSession:
    """Compose a private session whose Provider transport is fully deterministic."""
    store = SessionStore(tmp_path / "sessions", "chat", {}, False)
    reader = controller or ObservedController()
    return ConversationSession(
        reader,
        lambda: router(transport),  # type: ignore[arg-type]
        store,
        MissionActions(reader, store),
    )


def test_greeting_and_identity_do_not_build_router_or_read_controller(tmp_path: Path) -> None:
    """Basic conversation remains available without Provider credentials or Controller access."""
    controller = ObservedController()
    store = SessionStore(tmp_path / "sessions", "chat", {}, False)
    session = ConversationSession(
        controller,
        lambda: (_ for _ in ()).throw(AssertionError("local turn must not build a router")),
        store,
        MissionActions(controller, store),
    )
    try:
        assert "RoboGuide" in session.ask("你好")
        assert "对话工作台" in session.ask("你是谁？")
        assert session.router is None and controller.reads == []
    finally:
        store.close()


def test_compound_greeting_is_not_swallowed_as_local_greeting(tmp_path: Path) -> None:
    """A greeting plus a real request still reaches the normal evidence route."""
    transport = ScriptedTransport(
        [
            {"queries": [descriptor("cluster")], "answer": "", "citations": []},
            {"queries": [], "answer": "集群节点见 [E1]", "citations": ["E1"]},
        ]
    )
    session = _session(tmp_path, transport)
    try:
        result = session.ask("你好，查节点")
        assert "模型解释" in result
        assert "inventory" in session.reader.reads
    finally:
        session.store.close()


def test_active_task_question_reads_one_host_projection_without_model(tmp_path: Path) -> None:
    """An obvious active-task question bypasses model history and retains one current citation."""
    transport = ScriptedTransport([])
    controller = ObservedController()
    session = _session(tmp_path, transport, controller)
    try:
        result = session.ask("当前有没有正在运行的任务？")
        assert transport.payloads == []
        assert controller.reads == ["missions"]
        assert "E1" in result and "m1" in result
        assert session.query_status == "complete"
        assert session.evidence[0]["complete"] is True
    finally:
        session.store.close()


class TimeoutAfterRead:
    """Return one valid query, then simulate a Provider timeout after host evidence exists."""

    def __init__(self) -> None:
        """Start with one successful routing response before the timeout."""
        self.calls = 0

    def post_json(
        self, url: str, headers: Mapping[str, str], payload: JSONObject, timeout_seconds: float
    ) -> JSONObject:
        """Keep the first structured request deterministic and fail only the explanation call."""
        self.calls += 1
        if self.calls == 1:
            return {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "queries": [descriptor("cluster")],
                                        "answer": "",
                                        "citations": [],
                                    }
                                ),
                            }
                        ],
                    }
                ],
            }
        raise TimeoutError("provider timed out after current read")


def test_provider_timeout_keeps_current_host_facts(tmp_path: Path) -> None:
    """A failed model follow-up leaves a useful current read instead of an empty explanation."""
    transport = TimeoutAfterRead()
    session = _session(tmp_path, transport)
    try:
        result = session.ask("当前有几个节点？")
        assert "节点" in result
        assert "解释未完成" in result
        assert session.evidence[0]["complete"] is True
        assert session.query_status == "partial"
    finally:
        session.store.close()


def test_large_complete_read_keeps_bounded_projection_and_source_completeness() -> None:
    """Oversized display text is bounded without throwing away a complete source read."""
    transport = ScriptedTransport(
        [
            {"queries": [descriptor("cluster")], "answer": "", "citations": []},
            {"queries": [], "answer": "摘要见 [E1]", "citations": ["E1"]},
        ]
    )

    def read(_query: object, _remaining: float) -> ReadResult:
        """Return a complete source with enough text to require the human projection bound."""
        return ReadResult("前缀" + ("x" * 30_000) + "尾部", True)

    result = EvidenceConversation(router(transport)).answer("摘要", {}, [], read)
    assert result.status == "complete"
    assert result.evidence[0]["complete"] is True
    assert result.evidence[0]["text_truncated"] is True
    assert "结果超过模型证据上限" not in result.evidence[0]["text"]
