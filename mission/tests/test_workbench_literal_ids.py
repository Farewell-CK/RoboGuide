"""Literal Mission identities remain exact across query routing and actual HTTP reads."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast
from urllib.parse import quote, unquote, urlsplit

import pytest
from mission.models import JSONObject
from roboguide_workbench.actions import MissionActions
from roboguide_workbench.client import ReadOnlyController, WorkbenchError
from roboguide_workbench.conversation import ConversationSession
from roboguide_workbench.persistence import SessionStore
from test_workbench import ScriptedTransport, descriptor, router


@contextmanager
def mission_reader(ids: tuple[str, ...]) -> Iterator[tuple[ReadOnlyController, list[str]]]:
    """Serve distinct literal IDs and record exact production-client HTTP paths."""
    reads: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        """Return closed Controller query projections without accepting any mutations."""

        def do_GET(self) -> None:
            """Keep the received literal Mission separate from command ordinal syntax."""
            reads.append(self.path)
            path = urlsplit(self.path).path
            raw: JSONObject
            if self.path == "/v1/state/records?object_type=mission":
                raw = {
                    "schema": "roboguide.state-query/v0.1",
                    "records": [
                        {
                            "object": {"object_type": "mission", "object_id": target},
                            "channel_id": "mission-lifecycle",
                            "value": {"lifecycle": "Running"},
                        }
                        for target in ids
                    ],
                }
            elif self.path.startswith("/v1/state/records?object_type=execution_group"):
                raw = {"schema": "roboguide.state-query/v0.1", "records": []}
            elif path.endswith("/recovery"):
                target = unquote(path.removeprefix("/v1/missions/").removesuffix("/recovery"))
                assert target in ids
                raw = {
                    "schema_version": "roboguide.mission-recovery-view/v0.1",
                    "mission_id": target,
                    "group_id": "g1",
                    "new_execute_fenced": True,
                    "ownership_retained": True,
                    "crash_loop_quarantined": False,
                    "review_due": False,
                }
            else:
                target = unquote(path.removeprefix("/v1/missions/"))
                assert target in ids
                raw = {
                    "mission_id": target,
                    "group_id": "g1",
                    "status": "Running",
                    "tasks": [],
                }
            body = json.dumps(raw).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            """Suppress fixture access output while keeping captured request paths inspectable."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = False
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    worker.start()
    try:
        yield ReadOnlyController(f"http://127.0.0.1:{server.server_port}"), reads
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        assert not worker.is_alive()


def query_session(
    tmp_path: Path, reader: ReadOnlyController, query: JSONObject, answer_mode: str
) -> ConversationSession:
    """Use finite model responses and a private store with no Mission mutation listener."""
    replies: list[JSONObject] = (
        [
            {"queries": [query], "answer": "", "citations": []},
            {"queries": [], "answer": "任务状态见原始依据。[E1]", "citations": ["E1"]},
        ]
        if answer_mode == "grounded"
        else [query]
    )
    transport = ScriptedTransport(replies)
    store = SessionStore(tmp_path / "sessions", "literal", {}, False)
    session = ConversationSession(
        reader, lambda: router(transport), store, MissionActions(reader, store)
    )
    session.answer_mode = answer_mode
    return session


@pytest.mark.parametrize("answer_mode", ["grounded", "facts"])
@pytest.mark.parametrize("kind", ["mission", "explain"])
@pytest.mark.parametrize("selector", ["value", "index"])
@pytest.mark.parametrize("target", ["#1", " padded "])
def test_structured_mission_queries_keep_the_exact_discovered_id(
    tmp_path: Path, answer_mode: str, kind: str, selector: str, target: str
) -> None:
    """A discovered literal ID survives value or ordinal routing without command reparsing."""
    with mission_reader(("other", target)) as (reader, reads):
        query = descriptor(kind, value=target) if selector == "value" else descriptor(kind, index=2)
        session = query_session(tmp_path, reader, query, answer_mode)
        try:
            session.ask("/missions")
            assert session.displayed_missions == ["other", target]
            result = session.ask("查看第二个任务的当前状态")
            expected = "/v1/missions/" + quote(target, safe="")
            assert reads[1] == expected and "/v1/missions/other" not in reads
            assert reads.count(expected) == 1
            assert session.last_mission == target and session.query_status == "complete"
            assert f"Mission {target}：Running" in result
            if answer_mode == "grounded":
                evidence = session.evidence[0]
                assert json.loads(cast(str, evidence["query"])) == [kind, target, None]
                assert evidence["complete"] is True
            if kind == "explain":
                assert reads[-1] == expected + "/recovery" and len(reads) == 4
            else:
                assert len(reads) == 2
        finally:
            session.store.close()


def test_direct_mission_ordinals_resolve_once_into_literal_ids(tmp_path: Path) -> None:
    """The direct CLI retains #N selection even when its selected ID itself starts with #."""
    with mission_reader(("other", "#1")) as (reader, reads):
        session = query_session(tmp_path, reader, descriptor("mission", value="#1"), "grounded")
        try:
            session.ask("/missions")
            assert "Mission other：Running" in session.ask("/mission #1")
            assert "Mission #1：Running" in session.ask("/mission #2")
            assert reads[1:] == ["/v1/missions/other", "/v1/missions/%231"]
        finally:
            session.store.close()


@pytest.mark.parametrize("answer_mode", ["grounded", "facts"])
def test_typed_mission_reads_do_not_relax_model_identity_scope(
    tmp_path: Path, answer_mode: str
) -> None:
    """Unknown IDs absent from user input and observed references remain refused before I/O."""
    with mission_reader(("other", "#1")) as (reader, reads):
        session = query_session(
            tmp_path, reader, descriptor("mission", value="invented"), answer_mode
        )
        try:
            session.ask("/missions")
            if answer_mode == "grounded":
                assert "没有当前问题或查询结果依据" in session.ask("查看第二个任务的状态")
            else:
                with pytest.raises(WorkbenchError, match="没有用户输入或会话引用依据"):
                    session.ask("查看第二个任务的状态")
            assert session.query_status == "failed" and len(reads) == 1
        finally:
            session.store.close()
