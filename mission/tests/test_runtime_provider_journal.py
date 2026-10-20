"""Actual loopback wire/SQL checks using explicitly invalid provider protocol fixtures."""

from __future__ import annotations

import base64
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from mission.runtime_replanning_http import JournaledRuntimeTransport
from mission.runtime_work_store import RuntimeWorkError, RuntimeWorkStore


def test_invalid_original_wire_response_is_saved_and_never_requested_again(tmp_path: Path) -> None:
    """Retain actual malformed response bytes before parsing and reuse them after worker restart."""
    path = tmp_path / "runtime.sqlite3"
    store = RuntimeWorkStore(path)
    store.admit("work", {}, {}, 10000)
    calls = []

    class Handler(BaseHTTPRequestHandler):
        """Serve one malformed protocol reply, with no model or device claims."""

        def log_message(self, format: str, *args: Any) -> None:
            """Keep credentials and request bodies out of fixture logging."""

        def do_POST(self) -> None:  # noqa: N802
            """Observe the real SQL intent before returning actual invalid response bytes."""
            length = int(self.headers["Content-Length"])
            self.rfile.read(length)
            with sqlite3.connect(path) as connection:
                assert connection.execute("SELECT status FROM effects").fetchone() == ("pending",)
            calls.append(1)
            raw = b'{"deliberately-invalid":'
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/responses"
    try:
        transport = JournaledRuntimeTransport(store)
        with pytest.raises(RuntimeWorkError, match="durable runtime ticket"):
            transport.post_json(url, {}, {}, 1)
        with transport.bind("work", "provider-generate"), pytest.raises(json.JSONDecodeError):
            transport.post_json(url, {"Authorization": "unit-secret-not-persisted"}, {}, 1)
        row = store.effects("work")[0]
        assert row["status"] == "completed"
        assert (
            base64.b64decode(row["response"]["raw_response_base64"]) == b'{"deliberately-invalid":'
        )
        assert "unit-secret-not-persisted" not in json.dumps(row)
        store.close()
        store = RuntimeWorkStore(path)
        restored = JournaledRuntimeTransport(store)
        with restored.bind("work", "provider-generate"), pytest.raises(json.JSONDecodeError):
            restored.post_json(url, {}, {}, 1)
        assert calls == [1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        store.close()
    assert not thread.is_alive()
