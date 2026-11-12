"""Regressions for frozen query references, partial diagnostics and CLI failure status."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest
from mission.config import load_settings
from mission.models import JSONObject
from roboguide_workbench.actions import MissionActions
from roboguide_workbench.conversation import ConversationSession
from roboguide_workbench.persistence import SessionStore
from test_workbench import (
    ROOT,
    ScriptedTransport,
    descriptor,
    http_fixture,
    inventory,
    node,
    router,
)
from test_workbench_v2 import ObservedController


def session_for(
    tmp_path: Path, transport: ScriptedTransport, reader: ObservedController | None = None
) -> ConversationSession:
    """Compose a private session with a finite model script and no mutation service."""
    store = SessionStore(tmp_path / "sessions", "queries", {}, False)
    controller = reader or ObservedController()
    return ConversationSession(
        controller, lambda: router(transport), store, MissionActions(controller, store)
    )


def test_two_ordinals_in_same_model_batch_keep_original_objects(tmp_path: Path) -> None:
    """Reading the first Node must not remove the second reference from the pending batch."""
    transport = ScriptedTransport(
        [
            {
                "queries": [descriptor("node", index=1), descriptor("node", index=2)],
                "answer": "",
                "citations": [],
            },
            {"queries": [], "answer": "已读取两个节点。[E1][E2]", "citations": ["E1", "E2"]},
        ]
    )
    session = session_for(tmp_path, transport)
    try:
        session.ask("/nodes")
        result = session.ask("分别查询第一个和第二个节点")
        assert session.query_status == "complete" and "模型解释" in result
        assert all(e["complete"] is True for e in session.evidence)
        assert json.loads(cast(str, session.evidence[0]["query"])) == ["node", "a", None]
        assert json.loads(cast(str, session.evidence[1]["query"])) == ["node", "b", None]
        assert "1. a" in cast(str, session.evidence[0]["text"])
        assert "1. b" in cast(str, session.evidence[1]["text"])
    finally:
        session.store.close()


def test_refresh_before_ordinal_uses_display_that_model_actually_saw(tmp_path: Path) -> None:
    """A new inventory order cannot reinterpret an ordinal already selected in this batch."""

    class ReorderedController(ObservedController):
        """Return a changed ordering after the initial user-visible inventory."""

        def inventory(self) -> JSONObject:
            """Observe a real refresh without granting its order to earlier model selectors."""
            self.reads.append("inventory")
            return (
                inventory(node("a"), node("b"))
                if len(self.reads) == 1
                else inventory(node("b"), node("a"))
            )

    transport = ScriptedTransport(
        [
            {
                "queries": [descriptor("cluster"), descriptor("node", index=2)],
                "answer": "",
                "citations": [],
            },
            {"queries": [], "answer": "第二项是原节点 b。[E2]", "citations": ["E2"]},
        ]
    )
    session = session_for(tmp_path, transport, ReorderedController())
    try:
        session.ask("/nodes")
        session.ask("刷新集群，然后查看第二个节点")
        assert json.loads(cast(str, session.evidence[1]["query"])) == ["node", "b", None]
        assert "1. b" in cast(str, session.evidence[1]["text"])
    finally:
        session.store.close()


def test_next_model_batch_can_reference_its_new_query_results(tmp_path: Path) -> None:
    """Freezing one batch must still allow later model calls to use newly returned references."""
    transport = ScriptedTransport(
        [
            {"queries": [descriptor("cluster")], "answer": "", "citations": []},
            {"queries": [descriptor("node", index=2)], "answer": "", "citations": []},
            {"queries": [], "answer": "第二个节点是 b。[E2]", "citations": ["E2"]},
        ]
    )
    session = session_for(tmp_path, transport)
    try:
        session.ask("列出集群，然后查看第二个节点")
        assert session.query_status == "complete"
        assert json.loads(cast(str, session.evidence[1]["query"])) == ["node", "b", None]
    finally:
        session.store.close()


def test_comparison_keeps_the_original_cohort_after_a_node_read(tmp_path: Path) -> None:
    """An implicit comparison scope is as immutable as explicit indices within one batch."""
    transport = ScriptedTransport(
        [
            {
                "queries": [descriptor("node", index=1), descriptor("compare")],
                "answer": "",
                "citations": [],
            },
            {"queries": [], "answer": "两个节点的比较见依据。[E2]", "citations": ["E2"]},
            {"queries": [descriptor("node", index=2)], "answer": "", "citations": []},
            {"queries": [], "answer": "第二个节点是 b。[E1]", "citations": ["E1"]},
        ]
    )
    session = session_for(tmp_path, transport)
    try:
        session.ask("/nodes")
        session.ask("查看第一个节点，再比较这两个节点")
        comparison = session.evidence[1]
        assert session.query_status == "complete" and comparison["complete"] is True
        assert "a 的其它合同" in cast(str, comparison["text"])
        assert "b 的其它合同" in cast(str, comparison["text"])
        assert session.displayed_nodes == ["a", "b"] and session.last_node is None
        assert "模型解释" in session.ask("第二个节点呢？")
        assert json.loads(cast(str, session.evidence[0]["query"])) == ["node", "b", None]
    finally:
        session.store.close()


def test_complete_diagnostic_can_still_support_a_grounded_answer(tmp_path: Path) -> None:
    """Three valid original subreads retain a usable complete diagnostic rather than always fail."""

    class RecoveryController(ObservedController):
        """Return a fully typed original recovery view without adding physical assertions."""

        def mission_recovery(self, mission_id: str) -> JSONObject:
            """Supply all required fields with exact Mission and Group provenance."""
            return {
                "schema_version": "roboguide.mission-recovery-view/v0.1",
                "mission_id": mission_id,
                "group_id": "g1",
                "new_execute_fenced": True,
                "ownership_retained": True,
                "crash_loop_quarantined": False,
                "review_due": False,
            }

    transport = ScriptedTransport(
        [
            {"queries": [descriptor("explain", value="m1")], "answer": "", "citations": []},
            {"queries": [], "answer": "原恢复评估保留所有权。[E1]", "citations": ["E1"]},
        ]
    )
    session = session_for(tmp_path, transport, RecoveryController())
    try:
        assert "模型解释" in session.ask("解释 m1 的状态")
        assert session.query_status == "complete" and session.evidence[0]["complete"] is True
        components = cast(list[JSONObject], session.evidence[0]["components"])
        assert len(components) == 3 and all(c["complete"] is True for c in components)
    finally:
        session.store.close()


def test_missing_recovery_stays_partial_and_cannot_support_explanation(tmp_path: Path) -> None:
    """A successful Mission read cannot launder a failed recovery query into complete evidence."""
    transport = ScriptedTransport(
        [
            {"queries": [descriptor("explain", value="m1")], "answer": "", "citations": []},
            {"queries": [], "answer": "没有任何恢复阻塞。[E1]", "citations": ["E1"]},
        ]
    )
    session = session_for(tmp_path, transport)
    try:
        result = session.ask("解释 m1 的状态")
        assert session.query_status == "partial" and "模型解释" not in result
        evidence = session.evidence[0]
        assert evidence["complete"] is False and "不可用" in result
        components = cast(list[JSONObject], evidence["components"])
        assert [c["complete"] for c in components] == [True, True, False]
        assert components[-1]["source"] == "原恢复评估"
        assert "Running" in result
        assert "没有任何恢复阻塞" not in result
    finally:
        session.store.close()


def test_direct_diagnostic_propagates_partial_status(tmp_path: Path) -> None:
    """Direct commands expose the same availability without parsing error display strings."""
    session = session_for(tmp_path, ScriptedTransport([]))
    try:
        assert "不可用" in session.ask("/explain m1")
        diagnostic_status = session.query_status
        assert diagnostic_status == "partial"
        session.ask("/nodes")
        assert session.query_status == "complete"
    finally:
        session.store.close()


def test_direct_diagnostic_cli_returns_nonzero_for_invalid_subreads(tmp_path: Path) -> None:
    """Direct diagnostic CLI keeps valid Mission facts but refuses invalid auxiliary evidence."""
    raw = json.dumps(ObservedController().mission("m1")).encode()
    with http_fixture(raw) as (endpoint, reads):
        result = subprocess.run(
            [
                str(Path(sys.executable).parent / "roboguide"),
                "chat",
                "--controller",
                endpoint,
                "--config",
                str(tmp_path / "no-provider-file.toml"),
                "--session-dir",
                str(tmp_path / "sessions"),
                "--ask",
                "/explain m1",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    assert result.returncode == 2 and "Running" in result.stdout
    assert "当前 Group 绑定不可用" in result.stdout and "原恢复评估不可用" in result.stdout
    assert len(reads) == 3 and all(method == "GET" for method, _, _ in reads)


@pytest.mark.parametrize("status", [200, 403, 503])
def test_installed_natural_language_cli_has_truthful_exit_status(
    tmp_path: Path, status: int
) -> None:
    """Actual CLI processes distinguish empty inventory from refused or unavailable reads."""
    reply = {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(
                            {"queries": [descriptor("cluster")], "answer": "", "citations": []}
                        ),
                    }
                ],
            }
        ],
    }
    # A second call returns an explanation; only valid observations may support it.
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    calls: list[str] = []

    class Provider(BaseHTTPRequestHandler):
        """Serve a finite deterministic query/answer sequence over actual local HTTP."""

        def do_POST(self) -> None:
            """Record calls without receiving credentials or touching any remote service."""
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            calls.append(self.path)
            current = (
                reply
                if len(calls) == 1
                else {
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": json.dumps(
                                        {
                                            "queries": [],
                                            "answer": "当前节点数量为零。[E1]",
                                            "citations": ["E1"],
                                        }
                                    ),
                                }
                            ],
                        }
                    ],
                }
            )
            raw = json.dumps(current).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format: str, *args: object) -> None:
            """Suppress fixture access logs so CLI error status remains inspectable."""

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=provider.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        with http_fixture(json.dumps(inventory()).encode(), status) as (endpoint, reads):
            settings = load_settings(ROOT / "config/mission.toml", repository_root=ROOT)
            config = (
                (ROOT / "config/mission.toml")
                .read_text()
                .replace(settings.provider.base_url, f"http://127.0.0.1:{provider.server_port}")
                .replace("requires_openai_auth = true", "requires_openai_auth = false")
            )
            path = tmp_path / "model.toml"
            path.write_text(config)
            result = subprocess.run(
                [
                    str(Path(sys.executable).parent / "roboguide"),
                    "chat",
                    "--controller",
                    endpoint,
                    "--config",
                    str(path),
                    "--session-dir",
                    str(tmp_path / "sessions"),
                    "--ask",
                    "集群有几个节点？",
                ],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                env={k: v for k, v in os.environ.items() if k != settings.provider.api_key_env},
                timeout=10,
                check=False,
            )
            assert result.returncode == (0 if status == 200 else 2), result.stderr
            assert len(reads) == 1 and len(calls) == 2
            if status == 200:
                assert "已注册节点：0 个" in result.stdout and "模型解释" in result.stdout
            else:
                assert f"HTTP {status}" in result.stdout and "模型解释" not in result.stdout
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
