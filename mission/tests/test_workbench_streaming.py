"""Actual HTTP streaming, completion evidence and frontend display acknowledgements."""

from __future__ import annotations

import io
import json
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

import pytest
from mission.models import JSONObject
from roboguide_workbench.agent_loop import AgentConversation
from roboguide_workbench.cli import display_question
from roboguide_workbench.client import WorkbenchError
from roboguide_workbench.provider_config import model_settings
from roboguide_workbench.routing import ResponsesRouter
from roboguide_workbench.streaming import post_stream
from test_workbench_agent_conversation import session_for
from test_workbench_agent_loop import Responses, _call, _message, _no_read, _response


def frame(item: JSONObject) -> bytes:
    """Preserve Unicode and exact SSE data boundaries in actual loopback bytes."""
    return b"data: " + json.dumps(item, ensure_ascii=False).encode() + b"\n\n"


@contextmanager
def streaming_server(
    parts: list[bytes], observed: threading.Event | None = None
) -> Iterator[tuple[str, list[JSONObject]]]:
    """Hold the completion frame until the client has actually consumed its first text delta."""
    requests: list[JSONObject] = []

    class Handler(BaseHTTPRequestHandler):
        """Expose one bounded synthetic Provider endpoint without secrets or remote inference."""

        def log_message(self, format: str, *args: object) -> None:
            """Keep private fixture transport logs out of test diagnostics."""

        def do_POST(self) -> None:
            """Send incomplete and completed evidence independently over real HTTP."""
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(sum(map(len, parts))))
            self.end_headers()
            for index, part in enumerate(parts):
                self.wfile.write(part)
                self.wfile.flush()
                if index == 0 and observed is not None:
                    assert observed.wait(5), (
                        "client buffered the whole response instead of streaming"
                    )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/responses", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_delta_arrives_before_the_actual_completion_frame() -> None:
    """Real response bytes reach the callback while the server still holds completion."""
    observed = threading.Event()
    parts = [
        frame({"type": "response.output_text.delta", "delta": "你好，"}),
        frame({"type": "response.output_text.delta", "delta": "世界。"}),
        frame({"type": "response.completed", "response": _response(_message("你好，世界。"))}),
    ]
    deltas: list[str] = []

    def emit(delta: str) -> None:
        """Witness real incremental delivery before allowing the server to finish."""
        deltas.append(delta)
        observed.set()

    with streaming_server(parts, observed) as (url, requests):
        result = post_stream(url, {}, {"model": "fixture"}, 5, emit)
    assert deltas == ["你好，", "世界。"] and result["status"] == "completed"
    assert len(requests) == 1 and requests[0]["stream"] is True


@pytest.mark.parametrize(
    "failure",
    ["missing-completion", "failed", "incomplete", "mismatch", "duplicate", "partial-json"],
)
def test_stream_cannot_turn_partial_or_changed_output_into_tools(failure: str) -> None:
    """Only exact completed responses may enter the ordinary host tool decoder."""
    parts = [
        frame(
            {
                "type": "response.output_item.added",
                "item": _call("start_task", {"mode": "run", "start_turn": "current"}),
            }
        )
    ]
    if failure == "failed":
        parts.append(frame({"type": "response.failed"}))
    elif failure == "incomplete":
        parts.append(frame({"type": "response.incomplete"}))
    elif failure == "partial-json":
        parts.append(b'data: {"type": "response.completed"')
    elif failure == "mismatch":
        parts.extend(
            [
                frame({"type": "response.output_text.delta", "delta": "已取消"}),
                frame({"type": "response.completed", "response": _response(_message("未取消"))}),
            ]
        )
    elif failure == "duplicate":
        parts.extend(
            [frame({"type": "response.completed", "response": _response(_message("你好"))})] * 2
        )
    with streaming_server(parts) as (url, requests):
        with pytest.raises(WorkbenchError):
            post_stream(url, {}, {}, 5, lambda _: None)
    assert len(requests) == 1


def test_incomplete_streamed_tool_arguments_never_reach_the_read_owner() -> None:
    """A real failed SSE response cannot authorize a plausible pending read call."""
    parts = [
        frame({"type": "response.output_item.added", "item": _call("read_cluster")}),
        frame({"type": "response.failed"}),
    ]
    from test_workbench_agent_loop import TOOLS

    with streaming_server(parts) as (url, requests):
        settings = model_settings(
            {"base_url": url.rsplit("/", 1)[0], "name": "fixture", "requires_auth": False}
        )
        router = ResponsesRouter(settings, {})
        result = AgentConversation(router).answer(
            "查节点", {}, [], _no_read, tool_definitions=TOOLS, on_delta=lambda _: None
        )
    assert result.status == "failed" and result.evidence == [] and len(requests) == 1


@pytest.mark.parametrize("output_format", ["text", "stream-json"])
def test_streamed_drafts_only_appear_in_events_and_only_final_reply_is_acknowledged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output_format: str
) -> None:
    """When provisional text differs, plain output still prints only the final answer."""

    class StreamTransport(Responses):
        """Use the production frontend callback with a finite local Provider response."""

        def post_stream_json(
            self, url: str, headers: object, payload: JSONObject, seconds: float, emit: object
        ) -> JSONObject:
            """Deliver public tokens and inspect persisted display state while they are pending."""
            assert callable(emit)
            emit("你好呀")
            assert all(
                row.get("displayed") is not True
                for row in cast(list[JSONObject], session.store.data["messages"])
                if row.get("role") == "assistant"
            )
            return self.post_json(url, cast(dict[str, str], headers), payload, seconds)

    transport = StreamTransport([_response(_message("你好"))])
    session = session_for(tmp_path, transport)
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    try:
        display_question(session, "你好", output_format=output_format)
        if output_format == "stream-json":
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            assert [row["type"] for row in events] == [
                "assistant.delta",
                "assistant.completed",
                "turn.completed",
            ]
            assert events[0]["delta"] == "你好呀"
        else:
            assert output.getvalue() == "你好\n"
        messages = cast(list[JSONObject], session.store.data["messages"])
        assert messages[-1]["displayed"] is True and messages[-1]["text"] == "你好"
    finally:
        session.store.close()
