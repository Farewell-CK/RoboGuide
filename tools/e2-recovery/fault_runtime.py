"""External node-failure injection for the unchanged RoboGuide runtime."""

from __future__ import annotations

import http.client
import json
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
import tomllib
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def json_write(path: Path, value: object) -> None:
    """Atomically write one evidence document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class SuppressedExitProcess:
    """Hide one deliberate primary-node exit from the generic runner only."""

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        """Retain the real child while controlling only its visible liveness."""
        self.process = process
        self.suppress_exit = False
        self.cleanup_started = False

    def poll(self) -> int | None:
        """Report the injected exit as live until normal runner cleanup begins."""
        result = self.process.poll()
        if self.suppress_exit and not self.cleanup_started and result is not None:
            return None
        return result

    def send_signal(self, selected_signal: int) -> None:
        """Delegate cleanup signals without failing on the already-dead primary."""
        self.cleanup_started = True
        if self.process.poll() is None:
            self.process.send_signal(selected_signal)

    def wait(self, timeout: float | None = None) -> int:
        """Return the real process status."""
        return self.process.wait(timeout=timeout)

    def kill(self) -> None:
        """Delegate a forced cleanup when still needed."""
        self.cleanup_started = True
        if self.process.poll() is None:
            self.process.kill()

    def __getattr__(self, name: str) -> Any:
        """Expose ordinary ``Popen`` attributes to the existing runner."""
        return getattr(self.process, name)


class _ProxyServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        upstream_port: int,
        intercept: Callable[[str, bytes], bool],
    ) -> None:
        """Configure the loopback reverse proxy and its intercept callback."""
        self.upstream_port = upstream_port
        self.intercept = intercept
        super().__init__(address, _ProxyHandler)


class _ProxyHandler(BaseHTTPRequestHandler):
    server: _ProxyServer

    def do_GET(self) -> None:
        """Forward bridge health and readiness reads."""
        self._forward(b"")

    def do_POST(self) -> None:
        """Forward workflow calls except the single injected dispatch."""
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if self.server.intercept(self.path, body):
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()
            return
        self._forward(body)

    def _forward(self, body: bytes) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.upstream_port, timeout=10)
        headers = {"Content-Type": self.headers.get("Content-Type", "application/json")}
        try:
            connection.request(self.command, self.path, body=body or None, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            self.send_response(response.status)
            self.send_header("Content-Type", response.getheader("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
        except OSError as error:
            payload = json.dumps({"error": f"upstream unavailable: {error}"}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        finally:
            connection.close()

    def log_message(self, message: str, *arguments: Any) -> None:
        """Keep proxy traffic in structured evidence rather than stderr."""


class FaultRuntime:
    """Wrap process launch and inject one crash before primitive ``k + 1``."""

    def __init__(
        self,
        output: Path,
        expected_bridge_port: int,
        actual_bridge_port: int,
        controller_api: str,
        inject_after_steps: int | None,
        original_start: Callable[
            [list[str], Path, dict[str, str]], subprocess.Popen[bytes]
        ],
    ) -> None:
        """Configure one isolated proxy, process registry, and evidence timeline."""
        self.output = output
        self.expected_bridge_port = expected_bridge_port
        self.actual_bridge_port = actual_bridge_port
        self.controller_api = controller_api
        self.inject_after_steps = inject_after_steps
        self.original_start = original_start
        self.timeline: list[dict[str, Any]] = []
        self.node_processes: dict[int, SuppressedExitProcess] = {}
        self.node_configs: dict[int, Path] = {}
        self.node_environments: dict[int, dict[str, str]] = {}
        self.node_binary: Path | None = None
        self.standby: subprocess.Popen[bytes] | None = None
        self.proxy: _ProxyServer | None = None
        self.proxy_thread: threading.Thread | None = None
        self.injection_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._request_count = 0
        self._fault_started = False
        self._fault_finished = threading.Event()

    def event(self, kind: str, **details: object) -> None:
        """Append and persist one timestamped injection fact."""
        with self._lock:
            self.timeline.append({"time_unix": time.time(), "event": kind, **details})
            json_write(self.output / "fault-timeline.json", self.timeline)

    def start_process(
        self, command: list[str], log_path: Path, environment: dict[str, str]
    ) -> subprocess.Popen[bytes]:
        """Launch ordinary services while interposing only at the bridge boundary."""
        rewritten = list(command)
        if "coherent_local_eaios.generic_main" in rewritten:
            index = rewritten.index("--port") + 1
            if int(rewritten[index]) != self.expected_bridge_port:
                raise RuntimeError("unexpected generic bridge port")
            rewritten[index] = str(self.actual_bridge_port)
            process = self.original_start(rewritten, log_path, environment)
            self._start_proxy()
            self.event(
                "bridge_proxy_started",
                listen_port=self.expected_bridge_port,
                upstream_port=self.actual_bridge_port,
            )
            return process
        if Path(rewritten[0]).name == "roboguide-node":
            config_path = Path(rewritten[1])
            settings = tomllib.loads(config_path.read_text(encoding="utf-8"))
            operation = str(settings["operations"][0]["operation"])
            match = re.fullmatch(r"coherent\.agent-(\d+)-primitive@v1", operation)
            if match is None:
                raise RuntimeError(f"unexpected Node operation {operation!r}")
            agent_id = int(match.group(1))
            process = self.original_start(rewritten, log_path, environment)
            wrapped = SuppressedExitProcess(process)
            self.node_processes[agent_id] = wrapped
            self.node_configs[agent_id] = config_path
            self.node_environments[agent_id] = dict(environment)
            self.node_binary = Path(rewritten[0])
            return wrapped  # type: ignore[return-value]
        return self.original_start(rewritten, log_path, environment)

    def _start_proxy(self) -> None:
        self.proxy = _ProxyServer(
            ("127.0.0.1", self.expected_bridge_port),
            self.actual_bridge_port,
            self._intercept,
        )
        self.proxy_thread = threading.Thread(target=self.proxy.serve_forever, daemon=True)
        self.proxy_thread.start()

    def _intercept(self, path: str, body: bytes) -> bool:
        if path != "/v1/executions":
            return False
        with self._lock:
            self._request_count += 1
            request_number = self._request_count
        if self.inject_after_steps is None or request_number != self.inject_after_steps + 1:
            return False
        try:
            request = json.loads(body)
            invocation = request["invocation"]
            agent_id = int(invocation["parameters"]["agent_id"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.event("injection_request_invalid", error=f"{type(error).__name__}: {error}")
            return False
        with self._lock:
            if self._fault_started:
                return False
            self._fault_started = True
        self.event(
            "fault_triggered",
            request_number=request_number,
            completed_steps=request_number - 1,
            target_agent_id=agent_id,
            blocked_invocation=invocation,
        )
        self.injection_thread = threading.Thread(
            target=self._inject, args=(agent_id,), daemon=True
        )
        self.injection_thread.start()
        self._fault_finished.wait(timeout=30)
        return True

    def _inject(self, agent_id: int) -> None:
        try:
            process = self.node_processes[agent_id]
            process.suppress_exit = True
            self._snapshot_graph("pre-fault-graph.json")
            self.event("primary_sigterm_sent", agent_id=agent_id, pid=process.pid)
            process.process.send_signal(signal.SIGTERM)
            return_code = process.process.wait(timeout=10)
            self.event("primary_exited", agent_id=agent_id, return_code=return_code)
            self._fault_finished.set()
            time.sleep(2)
            standby_config = self._standby_config(agent_id)
            if self.node_binary is None:
                raise RuntimeError("Node binary was not captured")
            self.standby = self.original_start(
                [str(self.node_binary), str(standby_config)],
                self.output / f"node-{agent_id}-standby.log",
                self.node_environments[agent_id],
            )
            self.event("standby_started", agent_id=agent_id, pid=self.standby.pid)
            self._wait_standby_registration(agent_id, standby_config)
        except (
            KeyError,
            OSError,
            RuntimeError,
            subprocess.TimeoutExpired,
            TimeoutError,
            ValueError,
        ) as error:
            self.event("injection_failed", error=f"{type(error).__name__}: {error}")
            self._fault_finished.set()

    def _standby_config(self, agent_id: int) -> Path:
        source = self.node_configs[agent_id]
        text = source.read_text(encoding="utf-8")
        settings = tomllib.loads(text)
        old_node = str(settings["node_id"])
        old_system = str(settings["local_systems"][0]["id"])
        old_resource = str(settings["resources"][0]["id"])
        old_lock = str(settings["operations"][0]["local_locks"][0])
        replacements = {
            old_node: f"{old_node}-standby-f1",
            old_system: f"{old_system}-standby-f1",
            old_resource: f"{old_resource}-standby-f1",
            old_lock: f"{old_lock}-standby-f1",
            str(settings["state_directory"]): str(
                self.output / f"node-state-{old_node}-standby-f1"
            ),
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        path = self.output / f"agent-{agent_id}-node-standby.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def _wait_standby_registration(self, agent_id: int, config_path: Path) -> None:
        standby_id = str(tomllib.loads(config_path.read_text(encoding="utf-8"))["node_id"])
        for _ in range(60):
            try:
                connection = http.client.HTTPConnection(
                    "127.0.0.1", int(self.controller_api.rsplit(":", 1)[1]), timeout=2
                )
                connection.request("GET", "/v1/inventory")
                response = connection.getresponse()
                body = json.loads(response.read())
                connection.close()
                nodes = body.get("nodes", []) if isinstance(body, dict) else []
                if any(node.get("node_id") == standby_id for node in nodes):
                    self.event("standby_registered", agent_id=agent_id, node_id=standby_id)
                    return
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            time.sleep(0.5)
        raise TimeoutError(f"standby Node {standby_id} did not register")

    def _snapshot_graph(self, filename: str) -> None:
        database = self.output / "coherent-generic.sqlite3"
        if not database.exists():
            return
        import sqlite3

        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT graph_json, step_count FROM graph_state WHERE singleton=1"
            ).fetchone()
        if row is not None:
            json_write(
                self.output / filename,
                {"graph": json.loads(str(row[0])), "primitive_steps": int(row[1])},
            )

    def finish(self) -> None:
        """Stop the in-process proxy and separately owned standby."""
        if self.injection_thread is not None:
            self.injection_thread.join(timeout=20)
        self._snapshot_graph("post-recovery-graph.json")
        if self.proxy is not None:
            self.proxy.shutdown()
            self.proxy.server_close()
        if self.proxy_thread is not None:
            self.proxy_thread.join(timeout=5)
        if self.standby is not None:
            if self.standby.poll() is None:
                self.standby.send_signal(signal.SIGTERM)
            try:
                self.standby.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.standby.kill()
                self.standby.wait(timeout=5)
            stream = getattr(self.standby, "_evidence_stream", None)
            if stream is not None:
                stream.close()
        self.event("fault_runtime_finished", injected=self._fault_started)


def copy_database_snapshot(source: Path, destination: Path) -> None:
    """Copy a stopped SQLite database as immutable recovery evidence."""
    if source.exists():
        shutil.copy2(source, destination)
