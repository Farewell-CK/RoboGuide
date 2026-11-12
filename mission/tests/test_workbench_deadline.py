"""Actual socket regressions for one original deadline across HTTP parsing and body reads."""

from __future__ import annotations

import socket
import ssl
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import BaseRequestHandler, TCPServer

import pytest
from roboguide_workbench.client import WorkbenchError, request_json

ResponseScript = Callable[[socket.socket, threading.Event], None]


@contextmanager
def response_server(
    script: ResponseScript, *, consume_body: bool = True
) -> Iterator[tuple[str, list[str]]]:
    """Serve controlled original bytes and join all handler threads after the bounded test."""
    calls: list[str] = []
    stopped = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        """Accept actual GET or POST requests without following or retrying them."""

        def do_GET(self) -> None:
            """Run a response script while recording the original method exactly once."""
            calls.append(self.command)
            length = int(self.headers.get("Content-Length", "0"))
            if consume_body:
                self.rfile.read(length)
            try:
                script(self.connection, stopped)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        def do_POST(self) -> None:
            """Use the same response fixture after actually consuming one command body."""
            self.do_GET()

        def log_message(self, format: str, *args: object) -> None:
            """Keep test diagnostics independent from native HTTP server access logs."""

    class Server(ThreadingHTTPServer):
        """Join all request handlers instead of leaving daemon sockets behind after timeout."""

        daemon_threads = False

    server = Server(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        stopped.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
        assert not worker.is_alive()


@contextmanager
def stalled_tls_peer() -> Iterator[str]:
    """Accept actual TCP, but never send TLS handshake bytes before explicit test cleanup."""
    stopped = threading.Event()

    class Handler(BaseRequestHandler):
        """Keep one accepted socket alive until the request's original deadline expires."""

        def handle(self) -> None:
            """Retain the peer connection without consuming or acknowledging TLS records."""
            stopped.wait(2)

    server = TCPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    worker.start()
    try:
        yield f"https://127.0.0.1:{server.server_address[1]}"
    finally:
        stopped.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
        assert not worker.is_alive()


def dribble(parts: list[bytes], *, interval: float = 0.025) -> ResponseScript:
    """Produce repeated progress faster than the idle timeout, but beyond the total budget."""

    def send(connection: socket.socket, stopped: threading.Event) -> None:
        """Send each original segment without extending the client's deadline."""
        for part in parts:
            connection.sendall(part)
            if stopped.wait(interval):
                return

    return send


@pytest.mark.parametrize(
    "parts",
    [
        [bytes([part]) for part in b"HTTP/1.1 200 OK\r\n"],
        [
            b"HTTP/1.1 200 OK\r\n",
            *([b"X-Progress: yes\r\n"] * 25),
            b"Content-Length: 2\r\nConnection: close\r\n\r\n{}",
        ],
        [
            b"HTTP/1.1 200 OK\r\nContent-Length: 25\r\nConnection: close\r\n\r\n",
            *([b" "] * 23),
            b"{}",
        ],
        [
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n",
            *([b"0"] * 20),
            b"2\r\n{}\r\n0\r\n\r\n",
        ],
    ],
    ids=["status-line", "headers", "body", "chunk-size-line"],
)
def test_original_deadline_bounds_slow_progress(parts: list[bytes]) -> None:
    """A peer cannot keep status/header/body/chunk parsing alive by dribbling bytes."""
    with response_server(dribble(parts)) as (endpoint, calls):
        started = time.monotonic()
        with pytest.raises(WorkbenchError, match="超时"):
            request_json(endpoint, "GET", {}, None, 0.12)
        elapsed = time.monotonic() - started
        assert 0.07 <= elapsed < 0.35
        assert calls == ["GET"]


@pytest.mark.parametrize("version", [b"1.0", b"1.1"])
def test_fast_response_and_connection_close_preserve_socket_ownership(version: bytes) -> None:
    """Valid responses still work when HTTP transfers body ownership away from its connection."""

    def send(connection: socket.socket, stopped: threading.Event) -> None:
        """Return one complete response before the original deadline expires."""
        connection.sendall(
            b"HTTP/" + version + b" 200 OK\r\nContent-Length: 11\r\n"
            b'Connection: close\r\n\r\n{"ok":true}'
        )

    with response_server(send) as (endpoint, calls):
        assert request_json(endpoint, "GET", {}, None, 1) == {"ok": True}
        assert calls == ["GET"]


def test_timed_out_post_is_not_replayed() -> None:
    """Deadline expiry only stops the original response wait and grants no command replay."""
    with response_server(dribble([b"HTTP/1.1 200 OK\r\n", *([b"X-Progress: yes\r\n"] * 25)])) as (
        endpoint,
        calls,
    ):
        with pytest.raises(WorkbenchError, match="超时"):
            request_json(endpoint, "POST", {"Content-Type": "application/json"}, b"{}", 0.12)
        assert calls == ["POST"]


def test_concurrent_requests_keep_independent_original_deadlines() -> None:
    """A short foreground request cannot shorten another request's deadline or TLS/socket state."""
    script = dribble(
        [
            b"HTTP/1.1 200 OK\r\n",
            b"X-Progress: yes\r\n",
            b'Content-Length: 11\r\nConnection: close\r\n\r\n{"ok":true}',
        ],
        interval=0.05,
    )
    with response_server(script) as (endpoint, calls):
        with ThreadPoolExecutor(max_workers=2) as executor:
            short = executor.submit(request_json, endpoint, "GET", {}, None, 0.03)
            long = executor.submit(request_json, endpoint, "GET", {}, None, 1)
            with pytest.raises(WorkbenchError, match="超时"):
                short.result(timeout=2)
            assert long.result(timeout=2) == {"ok": True}
        assert calls == ["GET", "GET"]


def test_tcp_connect_time_is_not_renewed_for_actual_tls_handshake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A delayed TCP return leaves only the original remaining time for stalled real TLS I/O."""
    original_connect = socket.socket.connect

    def delayed_connect(stream_socket: socket.socket, address: tuple[str, int]) -> None:
        """Model TCP setup consuming time after the actual local kernel connection succeeds."""
        original_connect(stream_socket, address)
        time.sleep(0.2)

    monkeypatch.setattr(socket.socket, "connect", delayed_connect)
    context = ssl.create_default_context()
    with stalled_tls_peer() as endpoint:
        started = time.monotonic()
        with pytest.raises(WorkbenchError, match="超时"):
            request_json(endpoint, "GET", {}, None, 0.3, context)
        assert 0.25 <= time.monotonic() - started < 0.43


def test_request_headers_do_not_renew_blocked_original_body_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blocked original upload gets no fresh budget after its actual headers were accepted."""
    original_sendall = socket.socket.sendall

    def delayed_headers(stream_socket: socket.socket, data: bytes, flags: int = 0) -> None:
        """Spend header-send time before the real body is blocked by the nonreading peer."""
        original_sendall(stream_socket, data, flags)
        if data.startswith(b"POST "):
            time.sleep(0.2)

    def hold_body(connection: socket.socket, stopped: threading.Event) -> None:
        """Observe actual HTTP headers while refusing to drain the body from kernel buffers."""
        stopped.wait(2)

    monkeypatch.setattr(socket.socket, "sendall", delayed_headers)
    body = b"x" * (16 * 1024 * 1024)
    with response_server(hold_body, consume_body=False) as (endpoint, calls):
        started = time.monotonic()
        with pytest.raises(WorkbenchError, match="超时"):
            request_json(endpoint, "POST", {}, body, 0.3)
        assert 0.25 <= time.monotonic() - started < 0.43
        assert calls == ["POST"]
