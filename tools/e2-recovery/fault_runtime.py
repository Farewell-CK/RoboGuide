"""External node-failure injection for the unchanged RoboGuide runtime."""

from __future__ import annotations

import http.client
import json
import re
import shutil
import signal
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
        observe: Callable[[str, bytes, int, bytes], None],
    ) -> None:
        """Configure the loopback reverse proxy and its intercept callback."""
        self.upstream_port = upstream_port
        self.observe = observe
        super().__init__(address, _ProxyHandler)


class _ProxyHandler(BaseHTTPRequestHandler):
    server: _ProxyServer

    def do_GET(self) -> None:
        """Forward bridge health and readiness reads."""
        self._forward(b"")

    def do_POST(self) -> None:
        """Forward workflow calls and observe accepted dispatches after their response."""
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
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
            self.wfile.flush()
            self.server.observe(self.path, body, response.status, payload)
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
    """Inject one crash after local acceptance but before primitive ``k + 1`` takes effect."""

    def __init__(
        self,
        output: Path,
        expected_bridge_port: int,
        actual_bridge_port: int,
        controller_api: str,
        inject_after_steps: int | None,
        target_agent_id: int | None,
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
        self.target_agent_id = target_agent_id
        self.original_start = original_start
        self.timeline: list[dict[str, Any]] = []
        self.node_processes: dict[int, SuppressedExitProcess] = {}
        self.node_configs: dict[int, Path] = {}
        self.node_environments: dict[int, dict[str, str]] = {}
        self.node_binary: Path | None = None
        self.restarted_primary: subprocess.Popen[bytes] | None = None
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
            if self.inject_after_steps is not None:
                rewritten.extend(
                    [
                        "--pre-effect-hold-step",
                        str(self.inject_after_steps + 1),
                        "--pre-effect-hold-seconds",
                        "120",
                    ]
                )
            if self.target_agent_id is not None:
                rewritten.extend(
                    [
                        "--pre-effect-hold-agent-id",
                        str(self.target_agent_id),
                        "--pre-effect-hold-seconds",
                        "120",
                    ]
                )
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
            self._observe,
        )
        self.proxy_thread = threading.Thread(target=self.proxy.serve_forever, daemon=True)
        self.proxy_thread.start()

    def _observe(self, path: str, body: bytes, status: int, payload: bytes) -> None:
        """Trigger only after the target local handle was accepted and returned to its Node."""
        if path != "/v1/executions" or status != 200:
            return
        with self._lock:
            self._request_count += 1
            request_number = self._request_count
        try:
            request = json.loads(body)
            invocation = request["invocation"]
            agent_id = int(invocation["parameters"]["agent_id"])
            local_execution_id = str(json.loads(payload)["execution_id"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.event("injection_request_invalid", error=f"{type(error).__name__}: {error}")
            return
        if self.target_agent_id is None:
            if self.inject_after_steps is None or request_number != self.inject_after_steps + 1:
                return
        elif agent_id != self.target_agent_id:
            return
        with self._lock:
            if self._fault_started:
                return
            self._fault_started = True
        self.event(
            "fault_triggered",
            request_number=request_number,
            completed_steps=request_number - 1,
            target_agent_id=agent_id,
            trigger_mode="agent_id" if self.target_agent_id is not None else "completed_step",
            accepted_invocation=invocation,
            local_execution_id=local_execution_id,
        )
        self.injection_thread = threading.Thread(
            target=self._inject,
            args=(agent_id, invocation, local_execution_id),
            daemon=True,
        )
        self.injection_thread.start()

    def _inject(
        self, agent_id: int, invocation: dict[str, Any], local_execution_id: str
    ) -> None:
        """Crash Dog-A, authorize bounded recovery, and restart the same logical Node."""
        try:
            process = self.node_processes[agent_id]
            process.suppress_exit = True
            settings = tomllib.loads(self.node_configs[agent_id].read_text(encoding="utf-8"))
            node_id = str(settings["node_id"])
            controller_execution_id = self._wait_attempt(
                invocation, node_id, {"Accepted", "Running"}, timeout=20.0
            )
            self.event(
                "local_handle_confirmed",
                agent_id=agent_id,
                node_id=node_id,
                controller_execution_id=controller_execution_id,
                local_execution_id=local_execution_id,
            )
            self._snapshot_graph("pre-fault-graph.json")
            self.event("primary_crash_sent", agent_id=agent_id, pid=process.pid)
            process.process.kill()
            return_code = process.process.wait(timeout=10)
            self.event("primary_exited", agent_id=agent_id, return_code=return_code)
            observed_unknown = self._wait_attempt(
                invocation, node_id, {"Unknown"}, timeout=30.0
            )
            if observed_unknown != controller_execution_id:
                raise RuntimeError("a different execution became Unknown during fault injection")
            self.event(
                "recovery_required",
                controller_execution_id=controller_execution_id,
                node_id=node_id,
            )
            recovery_status, recovery_body = self._controller_json(
                "POST",
                f"/v1/executions/{controller_execution_id}/recover",
                {
                    "schema_version": "roboguide.execution-recovery-command/v0.1",
                    "expected_node_id": node_id,
                    "repeat_authorized": True,
                    "timeout_ms": 120000,
                    "max_replacements": 1,
                },
            )
            self.event(
                "recovery_authorized",
                controller_execution_id=controller_execution_id,
                http_status=recovery_status,
                response=recovery_body,
            )
            if recovery_status != 202:
                raise RuntimeError(f"Controller rejected recovery authorization: {recovery_body}")
            if self.node_binary is None:
                raise RuntimeError("Node binary was not captured")
            self.restarted_primary = self.original_start(
                [str(self.node_binary), str(self.node_configs[agent_id])],
                self.output / f"node-{agent_id}-restarted-primary.log",
                self.node_environments[agent_id],
            )
            self.event(
                "same_owner_restarted",
                agent_id=agent_id,
                node_id=node_id,
                pid=self.restarted_primary.pid,
            )
            self._wait_node_registration(agent_id, node_id)
            self._wait_rebind(invocation, node_id, controller_execution_id)
        except (
            KeyError,
            OSError,
            RuntimeError,
            subprocess.TimeoutExpired,
            TimeoutError,
            ValueError,
        ) as error:
            self.event("injection_failed", error=f"{type(error).__name__}: {error}")
        finally:
            self._fault_finished.set()

    def _wait_node_registration(self, agent_id: int, node_id: str) -> None:
        """Wait until the restarted same-owner Node is visible to Controller inventory."""
        for _ in range(60):
            try:
                _, body = self._controller_json("GET", "/v1/inventory")
                nodes = body.get("nodes", []) if isinstance(body, dict) else []
                if any(node.get("node_id") == node_id for node in nodes):
                    self.event("same_owner_registered", agent_id=agent_id, node_id=node_id)
                    return
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            time.sleep(0.5)
        raise TimeoutError(f"restarted Node {node_id} did not register")

    def _wait_attempt(
        self,
        invocation: dict[str, Any],
        node_id: str,
        statuses: set[str],
        *,
        timeout: float,
    ) -> str:
        """Return the exact Controller attempt once its lifecycle reaches a target status."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status, body = self._controller_json("GET", "/v1/execution-attempts")
            attempts = body.get("attempts", []) if status == 200 and isinstance(body, dict) else []
            for attempt in reversed(attempts):
                if (
                    attempt.get("mission_id") == invocation.get("mission_id")
                    and attempt.get("task_id") == invocation.get("task_id")
                    and attempt.get("role_id") == invocation.get("role_id")
                    and attempt.get("node_id") == node_id
                    and attempt.get("status") in statuses
                ):
                    return str(attempt["execution_id"])
            time.sleep(0.1)
        raise TimeoutError(f"Controller attempt did not reach {sorted(statuses)}")

    def _wait_rebind(
        self, invocation: dict[str, Any], node_id: str, original_execution_id: str
    ) -> None:
        """Record recovery phases until a fresh same-owner attempt is admitted."""
        deadline = time.monotonic() + 120.0
        last_disposition: object = None
        while time.monotonic() < deadline:
            status, view = self._controller_json(
                "GET", f"/v1/executions/{original_execution_id}/recovery"
            )
            if status == 200 and isinstance(view, dict):
                disposition = view.get("disposition")
                if disposition != last_disposition:
                    self.event(
                        "recovery_phase",
                        controller_execution_id=original_execution_id,
                        disposition=disposition,
                        recovery_view=view,
                    )
                    last_disposition = disposition
            try:
                replacement = self._wait_attempt(
                    invocation,
                    node_id,
                    {"Accepted", "Running", "Completed"},
                    timeout=0.25,
                )
            except TimeoutError:
                replacement = original_execution_id
            if replacement != original_execution_id:
                self.event(
                    "rebind_completed",
                    original_execution_id=original_execution_id,
                    replacement_execution_id=replacement,
                    node_id=node_id,
                )
                return
            time.sleep(0.2)
        raise TimeoutError("Controller did not rebind a fresh same-owner execution")

    def _controller_json(
        self, method: str, path: str, body: dict[str, object] | None = None
    ) -> tuple[int, Any]:
        """Call one loopback Controller endpoint with bounded JSON framing."""
        connection = http.client.HTTPConnection(
            "127.0.0.1", int(self.controller_api.rsplit(":", 1)[1]), timeout=5
        )
        payload = None if body is None else json.dumps(body).encode("utf-8")
        headers = {} if payload is None else {"Content-Type": "application/json"}
        try:
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
        finally:
            connection.close()

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
        """Stop the in-process proxy and separately owned restarted primary."""
        if self.injection_thread is not None:
            self.injection_thread.join(timeout=20)
        self._snapshot_graph("post-recovery-graph.json")
        if self.proxy is not None:
            self.proxy.shutdown()
            self.proxy.server_close()
        if self.proxy_thread is not None:
            self.proxy_thread.join(timeout=5)
        if self.restarted_primary is not None:
            if self.restarted_primary.poll() is None:
                self.restarted_primary.send_signal(signal.SIGTERM)
            try:
                self.restarted_primary.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.restarted_primary.kill()
                self.restarted_primary.wait(timeout=5)
            stream = getattr(self.restarted_primary, "_evidence_stream", None)
            if stream is not None:
                stream.close()
        self.event("fault_runtime_finished", injected=self._fault_started)


def copy_database_snapshot(source: Path, destination: Path) -> None:
    """Copy a stopped SQLite database as immutable recovery evidence."""
    if source.exists():
        shutil.copy2(source, destination)
