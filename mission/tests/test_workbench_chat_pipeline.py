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
        assert isinstance(session.reader, ObservedController)
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
        result = session.ask("帮我详细介绍这次返回的节点情况")
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
    assert "结果超过模型证据上限" not in str(result.evidence[0]["text"])


def test_model_active_intent_uses_current_missions_once_and_needs_no_followup(
    tmp_path: Path,
) -> None:
    """Paraphrased activity questions route to the mission projection rather than inventory."""
    transport = ScriptedTransport(
        [{"queries": [descriptor("active_missions")], "answer": "", "citations": []}]
    )
    controller = ObservedController()
    session = _session(tmp_path, transport, controller)
    try:
        result = session.ask("帮我看看集群里还有哪些 Mission 没结束？")
        assert controller.reads == ["missions"]
        assert len(transport.payloads) == 1
        assert "m1" in result and session.query_status == "complete"
    finally:
        session.store.close()


def test_partial_component_availability_survives_large_evidence_projection() -> None:
    """Bounding model context must retain the useful subread when another source is missing."""
    transport = ScriptedTransport(
        [
            {"queries": [descriptor("explain", value="m")], "answer": "", "citations": []},
            {"queries": [], "answer": "不可用", "citations": []},
        ]
    )
    returned = ReadResult(
        "任务证据" * 10_000,
        False,
        ({"source": "mission", "complete": True}, {"source": "bindings", "complete": False}),
    )
    result = EvidenceConversation(router(transport)).answer("解释 m", {}, [], lambda *_: returned)
    assert result.status == "partial"
    assert result.evidence[0]["complete"] is False
    assert result.evidence[0]["has_complete_component"] is True


def test_common_inventory_question_needs_only_one_current_read(tmp_path: Path) -> None:
    """The daily node-count/capability question works even when the Provider is unavailable."""
    controller = ObservedController()
    session = _session(tmp_path, ScriptedTransport([]), controller)
    try:
        result = session.ask("当前接入了几个节点，各自有什么能力？")
        assert controller.reads == ["inventory"]
        assert session.router is None and session.query_status == "complete"
        assert "2 个" in result and "依据：[E1]" in result
    finally:
        session.store.close()


def test_model_mission_list_preserves_incomplete_source_status(tmp_path: Path) -> None:
    """The model path must keep the same Unknown evidence as the direct Mission read."""

    class MissingLifecycle(ObservedController):
        def missions(self) -> JSONObject:
            """Return an accepted target with no usable runtime lifecycle evidence."""
            return {
                "schema": "roboguide.state-query/v0.1",
                "records": [
                    {
                        "object": {"object_type": "mission", "object_id": "missing"},
                        "channel_id": "accepted-plan",
                        "value": {"objective": "巡检"},
                    }
                ],
            }

    transport = ScriptedTransport(
        [
            {"queries": [descriptor("missions")], "answer": "", "citations": []},
            {"queries": [], "answer": "任务正在运行 [E1]", "citations": ["E1"]},
        ]
    )
    session = _session(tmp_path, transport, MissingLifecycle())
    try:
        answer = session.ask("列出任务情况")
        assert session.evidence[0]["complete"] is False
        assert session.query_status != "complete"
        assert "模型解释" not in answer and "Unknown" in answer
    finally:
        session.store.close()
