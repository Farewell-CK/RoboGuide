"""Evidence grounding, budgets, session migration and concurrent observation regressions."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
from mission.models import JSONObject
from roboguide_workbench.actions import MissionActions
from roboguide_workbench.answers import ANSWER_SCHEMA, EvidenceConversation, bounded_history
from roboguide_workbench.persistence import SessionStore
from roboguide_workbench.routing import Query
from roboguide_workbench.terminal import WatchManager
from roboguide_workbench.workspace import WorkbenchSession
from test_workbench import ScriptedTransport, descriptor, no_model, router
from test_workbench_v2 import ObservedController


class Steps(ScriptedTransport):
    """Return successive evidence-query or final-answer messages without network access."""

    def post_json(
        self, url: str, headers: Mapping[str, str], payload: JSONObject, timeout_seconds: float
    ) -> JSONObject:
        """Preserve the exact request for inspecting evidence and history isolation."""
        return super().post_json(url, headers, payload, timeout_seconds)


def test_answer_reads_fresh_facts_and_validates_citations() -> None:
    """The model receives read results only after the host performs the requested query."""
    transport = Steps(
        [
            {"queries": [descriptor("cluster")], "answer": "", "citations": []},
            {"queries": [], "answer": "当前返回两个节点。[E1]", "citations": ["E1"]},
        ]
    )
    calls = []

    def read(query: Query, remaining: float) -> str:
        """Return explicit fixture facts while tracking bounded original reads."""
        calls.append(query.kind)
        assert remaining <= 60
        return "当前身份可见节点：2 个"

    result = EvidenceConversation(router(transport)).answer("几个节点", {}, [], read)
    assert calls == ["cluster"] and "模型解释" in result.text
    assert result.evidence[0]["complete"] is True
    supplied = json.loads(cast(str, transport.payloads[1]["input"]))
    assert supplied["evidence"][0]["id"] == "E1"


def test_provider_schema_refuses_empty_capability_and_hidden_actions() -> None:
    """The Provider schema enforces local selectors before the model proposes invalid reads."""
    import jsonschema  # type: ignore[import-untyped]

    jsonschema.Draft202012Validator.check_schema(ANSWER_SCHEMA)
    jsonschema.validate(
        {"queries": [descriptor("cluster")], "answer": "", "citations": []}, ANSWER_SCHEMA
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"queries": [descriptor("capability")], "answer": "", "citations": []}, ANSWER_SCHEMA
        )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {
                "queries": [{**descriptor("cluster"), "url": "http://other"}],
                "answer": "",
                "citations": [],
            },
            ANSWER_SCHEMA,
        )


def test_partial_evidence_cannot_support_a_successful_answer() -> None:
    """Failure details are visible but cannot be cited as a complete current observation."""
    transport = Steps(
        [
            {"queries": [descriptor("cluster")], "answer": "", "citations": []},
            {"queries": [], "answer": "集群为空。[E1]", "citations": ["E1"]},
        ]
    )

    def fail(query: Query, remaining: float) -> str:
        """Simulate a server permission refusal after a valid read request."""
        raise ValueError("HTTP 403")

    result = EvidenceConversation(router(transport)).answer("几个节点", {}, [], fail)
    assert "403" in result.text and "模型解释" not in result.text
    assert result.evidence[0]["complete"] is False


@pytest.mark.parametrize(
    "ending",
    [
        {"queries": [], "answer": "全都可以执行", "citations": ["invented"]},
        {"queries": [descriptor("cluster")], "answer": "", "citations": []},
        {"queries": [descriptor("run")], "answer": "", "citations": []},
    ],
)
def test_invalid_answer_or_repeated_read_cannot_replace_facts(ending: JSONObject) -> None:
    """Bad citations, loops and data-driven writes fall back to acquired facts."""
    transport = Steps([{"queries": [descriptor("cluster")], "answer": "", "citations": []}, ending])
    calls = []

    def read(query: Query, remaining: float) -> str:
        """Count the only legitimate read before refusing subsequent invalid work."""
        calls.append(query.kind)
        return "节点数量为 1"

    result = EvidenceConversation(router(transport)).answer("多少", {}, [], read)
    assert calls == ["cluster"] and "节点数量为 1" in result.text
    assert "解释未完成" in result.text and result.proposal is None


def test_budget_counts_underlying_explain_reads() -> None:
    """Two composite diagnostics cannot exceed four original HTTP reads."""
    transport = Steps(
        [
            {
                "queries": [descriptor("explain", value="a"), descriptor("explain", value="b")],
                "answer": "",
                "citations": [],
            }
        ]
    )
    calls = []

    def read(query: Query, remaining: float) -> str:
        """Record three-read diagnostic admission."""
        calls.append(query.value)
        return "已知状态"

    result = EvidenceConversation(router(transport)).answer("解释", {}, [], read)
    assert calls == ["a"] and "预算" in result.text


def test_v02_upgrade_copies_pending_receipts_without_touching_original(tmp_path: Path) -> None:
    """Original command keys remain exact and old session bytes remain unchanged."""
    store = SessionStore(tmp_path / "sessions", "old", {"tenant": "a"}, False)
    raw = store.data
    raw["schema"] = "roboguide.workbench-session/v0.2"
    raw["commands"] = [{"state": "pending", "idempotency_key": "original"}]
    store.save()
    old = store.path
    before = old.read_bytes()
    store.close()
    migrated = SessionStore(old.parent, "old", {"tenant": "a"}, True)
    try:
        assert migrated.path.stem == "old-v03"
        assert old.read_bytes() == before
        assert migrated.data["commands"] == raw["commands"]
    finally:
        migrated.close()
    resumed = SessionStore(old.parent, "old-v03", {"tenant": "a"}, True)
    resumed.close()


def test_context_budget_preserves_recent_message_order() -> None:
    """Historical dialogue is bounded and explicitly marked as historical model context."""
    history = bounded_history([{"role": "user", "text": str(i) + "中" * 2000} for i in range(30)])
    assert 0 < len(history) <= 20 and str(history[-1]["text"]).startswith("29")
    assert all(item["historical"] for item in history)
    assert len(json.dumps(history, ensure_ascii=False).encode()) < 25000


def test_watch_runs_beside_queries_without_changing_selection(tmp_path: Path) -> None:
    """A background watch reports changes while another query proceeds and preserves ordinals."""
    store = SessionStore(tmp_path / "sessions", "watch", {}, False)
    reader = ObservedController()
    session = WorkbenchSession(reader, no_model, store, MissionActions(reader, store))
    session.displayed_nodes = ["original"]
    rendered: list[str] = []

    async def run() -> None:
        """Exercise actual asyncio scheduling and worker-backed read completion."""
        manager = WatchManager(session, rendered.append, interval=0.01, samples=5)
        manager.start("m1")
        await asyncio.to_thread(session.ask, "/missions")
        await asyncio.sleep(0.03)
        await manager.stop()
        assert not manager.tasks

    try:
        asyncio.run(run())
        assert len(rendered) == 1 and "m1" in rendered[0]
        assert session.displayed_nodes == ["original"]
        assert store.data["commands"] == []
    finally:
        store.close()


def test_three_watch_limit_and_terminal_shutdown(tmp_path: Path) -> None:
    """Watch capacity remains bounded and terminal facts close the actual observation tasks."""

    class CompleteReader(ObservedController):
        """Return a terminal lifecycle immediately without any mutation port."""

        def mission(self, mission_id: str) -> JSONObject:
            """Provide an exact terminal observation to the production watcher."""
            return {"mission_id": mission_id, "group_id": "g", "status": "Completed", "tasks": []}

    store = SessionStore(tmp_path / "sessions", "watch", {}, False)
    reader = CompleteReader()
    session = WorkbenchSession(reader, no_model, store, MissionActions(reader, store))
    rendered: list[str] = []

    async def run() -> None:
        """Admit three tasks synchronously then observe their independent terminal exits."""
        manager = WatchManager(session, rendered.append, interval=0.01)
        for mid in ("a", "b", "c", "d"):
            manager.start(mid)
        assert len(manager.tasks) == 3 and "最多" in rendered[0]
        await asyncio.gather(*list(manager.tasks.values()))
        assert manager.tasks == {}

    try:
        asyncio.run(run())
        assert len(rendered) == 4 and store.data["commands"] == []
    finally:
        store.close()


def test_migrated_session_refuses_second_copy_and_keeps_original(tmp_path: Path) -> None:
    """A second resume cannot overwrite the already advanced v0.3 session with old state."""
    store = SessionStore(tmp_path / "sessions", "old", {}, False)
    store.data["schema"] = "roboguide.workbench-session/v0.2"
    store.save()
    old = store.path.read_bytes()
    store.close()
    migrated = SessionStore(tmp_path / "sessions", "old", {}, True)
    migrated.data["commands"] = [{"state": "pending", "idempotency_key": "new-original"}]
    migrated.save()
    before = migrated.path.read_bytes()
    migrated.close()
    with pytest.raises(ValueError, match="升级副本"):
        SessionStore(tmp_path / "sessions", "old", {}, True)
    assert (tmp_path / "sessions/old.json").read_bytes() == old
    assert (tmp_path / "sessions/old-v03.json").read_bytes() == before
