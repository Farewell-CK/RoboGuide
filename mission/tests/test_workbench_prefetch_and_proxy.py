"""Pin the host-side prefetch contract and the proxy deadline bound.

These two behaviours are easy to break silently:

* prefetch is opt-in (a wrong default changes every request payload), and its cache
  must not leak one controller/session view into another;
* the proxy CONNECT loop must spend the *original* time budget, otherwise a stalled
  proxy turns a 1 s timeout into a multi-minute hang.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable, Iterator

import pytest
from mission.models import JSONObject
from roboguide_workbench import agent_loop as agent_loop
from roboguide_workbench import client
from roboguide_workbench.evidence import ReadResult


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    """Keep the module-level cache from leaking between tests."""
    agent_loop._PREFETCH_CACHE.clear()
    yield
    agent_loop._PREFETCH_CACHE.clear()


def _reader(
    text: str, calls: list[tuple[str, JSONObject]]
) -> Callable[[str, JSONObject, float], ReadResult]:
    """Return a fake read callable that records what was asked for."""

    def read(name: str, arguments: JSONObject, remaining: float) -> ReadResult:
        assert remaining > 0
        calls.append((name, dict(arguments)))
        return ReadResult(text)

    return read


def test_prefetch_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt-in only: an unset/empty switch must not change the request payload."""
    monkeypatch.delenv("ROBOGUIDE_PREFETCH_INVENTORY", raising=False)
    assert agent_loop._prefetch_enabled() is False

    monkeypatch.setenv("ROBOGUIDE_PREFETCH_INVENTORY", "0")
    assert agent_loop._prefetch_enabled() is False

    monkeypatch.setenv("ROBOGUIDE_PREFETCH_INVENTORY", "1")
    assert agent_loop._prefetch_enabled() is True


def test_prefetch_collects_cluster_and_missions() -> None:
    """A successful read produces one labelled block with both sources."""
    calls: list[tuple[str, JSONObject]] = []
    read = _reader("1. mujoco-arm：object.relocate@v1（Online）", calls)

    block = agent_loop._prefetch_block(read, time.monotonic() + 10, ("ctl", 1, "/repo"))

    assert "宿主预取" in block
    assert "mujoco-arm" in block
    assert [name for name, _ in calls] == ["read_cluster", "read_active_missions"]


def test_prefetch_cache_is_isolated_by_key() -> None:
    """A second controller/session must not reuse the first one's snapshot."""
    calls: list[tuple[str, JSONObject]] = []
    read = _reader("snapshot", calls)
    deadline = time.monotonic() + 10

    first = agent_loop._prefetch_block(read, deadline, ("ctl-a", 1, "/repo"))
    again = agent_loop._prefetch_block(read, deadline, ("ctl-a", 1, "/repo"))
    other = agent_loop._prefetch_block(read, deadline, ("ctl-b", 2, "/repo"))

    assert first == again
    assert first == other  # same text here, but it was fetched again for the other key
    assert len(calls) == 4  # two reads per distinct key


def test_prefetch_skips_injection_when_reads_fail() -> None:
    """Unreadable inventory must yield an empty block, never a fabricated one."""

    def read(name: str, arguments: JSONObject, remaining: float) -> ReadResult:
        raise RuntimeError("controller unreachable")

    block = agent_loop._prefetch_block(read, time.monotonic() + 10, ("ctl", 1, "/repo"))
    assert block == ""


@pytest.fixture()
def stalled_proxy() -> Iterator[int]:
    """Accept CONNECT but never answer, then report the port under test."""
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    port = int(server.getsockname()[1])
    stop = threading.Event()

    def serve() -> None:
        while not stop.is_set():
            try:
                conn, _ = server.accept()
            except OSError:
                return
            # Hold the connection open without replying, emulating a stuck proxy.
            threading.Thread(target=lambda c=conn: stop.wait(5) or c.close(), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    try:
        yield port
    finally:
        stop.set()
        server.close()


def test_proxy_connect_honours_original_deadline(
    monkeypatch: pytest.MonkeyPatch, stalled_proxy: int
) -> None:
    """A stalled proxy must fail inside the budget instead of hanging."""
    monkeypatch.setenv("ROBOGUIDE_HTTP_PROXY", f"http://127.0.0.1:{stalled_proxy}")
    http_class, _https_class = client._connection_classes(time.monotonic() + 1.0)
    connection = http_class("example.com", 443)

    started = time.monotonic()
    with pytest.raises((TimeoutError, OSError)):
        # connect() 内部才把代理分支挂到 _create_connection 上（见 _connection_classes）
        connection.connect()
    elapsed = time.monotonic() - started

    assert elapsed < 3.0, f"proxy CONNECT ignored the deadline ({elapsed:.1f}s)"
