"""Long-running multi-agent demo server.

This is a thin wrapper around run_multi_agent_demo's mock fixtures
that keeps the uvicorn server alive so a browser can be pointed at
``http://127.0.0.1:<port>/`` to inspect the live UI.

Usage::

    python tests/run_demo_server.py            # auto-pick a free port
    python tests/run_demo_server.py 8080       # bind to a specific port

Then open ``http://127.0.0.1:<port>/`` in a browser.
"""
from __future__ import annotations

import socket
import sys
import threading
import time
import urllib.request

import uvicorn

# Reuse the mock fixtures + the patched transport module from the
# one-shot demo so the behaviour is identical.
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import run_multi_agent_demo as _fixtures  # noqa: E402

import robonix_client.app as client_app  # noqa: E402


def _free_port(preferred: int | None = None) -> int:
    s = socket.socket()
    if preferred:
        try:
            s.bind(("127.0.0.1", preferred))
            port = preferred
            s.close()
            return port
        except OSError:
            s.close()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_ready(port: int, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/agents", timeout=1
            ):
                return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("uvicorn did not become ready in time")


def main() -> None:
    port = _free_port(int(sys.argv[1]) if len(sys.argv) > 1 else None)
    _fixtures._register_agents(port)  # pre-seed so /api/agents has 3 entries

    config = uvicorn.Config(
        client_app.app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_ready(port)

    print(f"\n✓ 多智能体演示服务已启动:")
    print(f"    http://127.0.0.1:{port}/")
    print(f"  已注册智能体: {', '.join(_fixtures.PERSONAS.keys())}")
    print(f"  静态快照: {_fixtures._save_overview_snapshot(port)}")
    print(f"\n按 Ctrl+C 停止服务。\n")
    try:
        while not server.should_exit:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        thread.join(timeout=3)


if __name__ == "__main__":
    main()
