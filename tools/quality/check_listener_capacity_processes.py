"""Exercise real pre-TLS/TCP/Unix limits and transport deadlines without Nodes or model calls."""

from __future__ import annotations

import argparse
import http.client as http_client
import json
import os
import re
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from check_identity_processes import client, metrics_text, pki, port, request
from check_replica_content_processes import digest, save
from check_state_verifier_processes import ROOT, wait_for
from monitoring_listener_processes import ListenerMonitoring


class NoInference(BaseHTTPRequestHandler):
    """Reject any accidental model call; no fixture model answer or execution fact is produced."""

    calls: list[str] = []

    def log_message(self, *_arguments: Any) -> None:
        """Retain only the explicit bounded unexpected-call list."""

    def do_POST(self) -> None:  # noqa: N802
        """Record an unexpected attempt and fail instead of manufacturing a model response."""
        if len(self.calls) < 128:
            self.calls.append(self.path[:256])
        self.send_error(503, "transport acceptance must make zero model calls")

    def do_GET(self) -> None:  # noqa: N802
        """Reject accidental upstream readiness calls instead of inventing backend health."""
        self.do_POST()


def http(origin: str, context: ssl.SSLContext | None, path: str) -> tuple[int, dict[str, Any]]:
    """Read only an actual owned HTTP service and preserve its response status."""
    address = origin.split("://", 1)[1]
    connection: http_client.HTTPConnection
    if context is not None:
        connection = http_client.HTTPSConnection(address, timeout=3, context=context)
    else:
        connection = http_client.HTTPConnection(address, timeout=3)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        value: dict[str, Any] = json.loads(response.read(4 * 1024 * 1024))
        return response.status, value
    finally:
        connection.close()


def unix(path: Path, route: str) -> dict[str, Any]:
    """Read one original private socket without changing credentials, jobs or backend grants."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(3)
        connection.connect(str(path))
        connection.sendall(
            f"GET {route} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n".encode()
        )
        raw = bytearray()
        while True:
            chunk = connection.recv(8192)
            if not chunk:
                break
            raw.extend(chunk)
            assert len(raw) <= 4 * 1024 * 1024
    head, body = bytes(raw).split(b"\r\n\r\n", 1)
    assert head.startswith(b"HTTP/1.0 200") or head.startswith(b"HTTP/1.1 200")
    value: dict[str, Any] = json.loads(body)
    return value


def wait_closed(connection: socket.socket, seconds: float) -> dict[str, Any]:
    """Drain bounded real transport bytes until original EOF; timeout never counts as closure."""
    started = time.monotonic()
    received = 0
    connection.settimeout(seconds)
    while True:
        remaining = seconds - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError("original accepted socket did not close within its bound")
        connection.settimeout(remaining)
        try:
            chunk = connection.recv(8192)
        except (ConnectionResetError, ssl.SSLEOFError):
            chunk = b""
        if not chunk:
            return {"actual_seconds": time.monotonic() - started, "received_bytes": received}
        received += len(chunk)
        assert received <= 64 * 1024


def local_observer(address: tuple[str, int] | Path) -> Callable[[], dict[str, Any]]:
    """Freeze one actual configured endpoint for independent readonly capacity observations."""
    if isinstance(address, Path):
        return lambda: unix(address, "/v1/transport")
    host, number = address
    return lambda: http(f"http://{host}:{number}", None, "/v1/transport")[1]


def local_capacity(
    address: tuple[str, int] | Path, observe: Callable[[], dict[str, Any]]
) -> dict[str, Any]:
    """Occupy two original local workers and require excess refusal plus actual watchdog closure."""
    held: list[socket.socket] = []

    def connect() -> socket.socket:
        """Open only the explicit actual endpoint from this service's deployment declaration."""
        if isinstance(address, Path):
            stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            stream.settimeout(3)
            stream.connect(str(address))
            return stream
        return socket.create_connection(address, timeout=3)

    try:
        for _ in range(2):
            held.append(connect())
        with connect() as excess:
            excess_closed = wait_closed(excess, 1)
        ended = [wait_closed(stream, 4) for stream in held]
        actual: dict[str, Any] = {}

        def returned() -> bool:
            """Observe actual worker completion after EOF without repeating backend work."""
            actual.update(observe())
            return bool(
                actual["limit"] == 2 and actual["rejected_total"] >= 1 and actual["active"] <= 1
            )

        wait_for(returned, "actual local permits returned after original close", 5)
        return {
            "original_held_connections": 2,
            "excess_actual_close": excess_closed,
            "actual_watchdog_closes": ended,
            "actual_observation": actual,
        }
    finally:
        for stream in held:
            stream.close()


def run(output: Path, binary: Path, monitoring_tools: Path | None = None) -> dict[str, Any]:
    """Own actual services and waits; TLS handshakes grant no Node or execution facts."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    files = [
        binary,
        Path(__file__),
        ROOT / "uv.lock",
        ROOT / "tools/observability/observability_profile.py",
        Path(__file__).with_name("monitoring_listener_processes.py"),
        Path(__file__).with_name("monitoring_processes.py"),
        *sorted((ROOT / "mission/src/mission").glob("*.py")),
        *sorted((ROOT / "integrations/memory-provider/roboguide_memory_provider").glob("*.py")),
        ROOT / "integrations/robonix-map-service/robonix-map-service.py",
    ]
    identity = {str(path): digest(path) for path in files}
    save(output / "source-identity.json", identity)
    credentials = output / "pki"
    leaves = pki(credentials)
    context = client(credentials, "admin")
    admin = "sha256:" + leaves["admin"]
    save(
        output / "operator.json",
        {
            "schema_version": "roboguide.operator-authorization/v0.2",
            "registry_id": "actual-capacity-owner",
            "revision": 1,
            "grants": {
                admin: {
                    "principal_id": "actual-capacity-admin",
                    "role": "recovery-admin",
                    "tenant_ids": ["*"],
                },
                "sha256:" + leaves["admin-next"]: {
                    "principal_id": "actual-capacity-metrics",
                    "role": "metrics-reader",
                    "tenant_ids": ["*"],
                },
            },
        },
    )
    save(
        output / "artifact.json",
        {
            "schema_version": "roboguide.artifact-authorization/v0.1",
            "registry_id": "actual-capacity-artifact",
            "revision": 1,
            "grants": {
                admin: {
                    "principal_id": "actual-capacity-admin",
                    "tenant_id": "local",
                    "role": "admin",
                    "node_id": None,
                    "provider_ids": [],
                    "memory_ids": [],
                    "map_ids": [],
                    "artifact_digests": [],
                    "not_before_ms": 0,
                    "expires_at_ms": 0,
                    "revoked": False,
                }
            },
        },
    )
    save(
        output / "mission.json",
        {
            "schema_version": "roboguide.mission-authorization/v0.2",
            "profile_id": "actual-capacity-owner",
            "revision": 1,
            "grants": {
                admin: {"principal_id": "actual-capacity-admin", "role": "admin", "tenant_id": "*"}
            },
        },
    )
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("ROBOGUIDE_")
    }
    environment["TOKIO_WORKER_THREADS"] = "1"
    for plane, profile in (
        ("NODE", None),
        ("OPERATOR", "operator.json"),
        ("ARTIFACT", "artifact.json"),
    ):
        for variable, name in (
            ("SERVER_CERT", "server.crt"),
            ("SERVER_KEY", "server.key"),
            ("CLIENT_CA", "ca.crt"),
        ):
            environment[f"ROBOGUIDE_{plane}_MTLS_{variable}"] = str(credentials / name)
        if profile:
            environment[f"ROBOGUIDE_{plane}_MTLS_ROLE_MAP"] = str(output / profile)
    environment["ROBOGUIDE_NODE_MTLS_NODE_CERT_DIGESTS"] = json.dumps(
        {
            "schema": "roboguide.node-identity-registry/v0.1",
            "registry_id": "actual-empty-registry",
            "records": {},
        }
    )
    environment.update(
        {
            "ROBOGUIDE_NODE_CONNECTION_LIMIT": "2",
            "ROBOGUIDE_ARTIFACT_CONNECTION_LIMIT": "2",
            "ROBOGUIDE_OPERATOR_CONNECTION_LIMIT": "4",
            "ROBOGUIDE_NODE_STREAM_LIMIT": "2",
        }
    )
    node_port, operator_port, artifact_port = port(), port(), port()
    origin = f"https://127.0.0.1:{operator_port}"
    artifacts = f"https://127.0.0.1:{artifact_port}"
    owned: list[tuple[subprocess.Popen[bytes], dict[str, Any]]] = []
    result: dict[str, Any] = {
        "passed": False,
        "embodied_nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "binary_sha256": identity[str(binary)],
    }
    NoInference.calls = []
    trap = ThreadingHTTPServer(("127.0.0.1", 0), NoInference)
    trap_thread = threading.Thread(target=trap.serve_forever, daemon=True)
    trap_thread.start()
    monitoring: ListenerMonitoring | None = None
    with ExitStack() as logs:

        def start(
            name: str, arguments: list[str], configured: dict[str, str]
        ) -> subprocess.Popen[bytes]:
            """Spawn one actual service and retain its original PID and arguments."""
            log = logs.enter_context((output / (name + ".log")).open("wb"))
            process = subprocess.Popen(arguments, cwd=ROOT, env=configured, stdout=log, stderr=log)
            owned.append((process, {"name": name, "pid": process.pid, "arguments": arguments}))
            return process

        try:
            start(
                "controller",
                [
                    str(binary),
                    f"127.0.0.1:{node_port}",
                    str(output / "controller.sqlite3"),
                    f"127.0.0.1:{operator_port}",
                    f"127.0.0.1:{artifact_port}",
                    str(output / "artifacts"),
                ],
                environment,
            )
            wait_for(
                lambda: request(origin, context, "/healthz")[0] == 200,
                "actual bounded Controller",
                20,
            )
            if monitoring_tools is not None:
                monitoring = ListenerMonitoring(
                    output,
                    monitoring_tools,
                    origin,
                    lambda arguments, name: start(name, arguments, dict(environment)),
                )
                monitoring.start()
            held = []
            try:
                for target in (node_port, artifact_port):
                    for _ in range(2):
                        held.append(socket.create_connection(("127.0.0.1", target), timeout=3))
                    with socket.create_connection(("127.0.0.1", target), timeout=3) as excess:
                        wait_closed(excess, 1)
                assert request(origin, context, "/healthz")[0] == 200
                if monitoring is not None:
                    result["native_capacity_firing"] = monitoring.observe_capacity(
                        "firing",
                        {"RoboGuideNodeCapacitySaturated", "RoboGuideArtifactCapacitySaturated"},
                    )
                result["actual_controller_pre_tls_closes"] = [
                    wait_closed(stream, 7) for stream in held
                ]
            finally:
                for stream in held:
                    stream.close()
            assert http(artifacts, context, "/healthz")[0] == 200
            if monitoring is not None:
                result["native_capacity_resolved"] = monitoring.observe_capacity(
                    "resolved",
                    {"RoboGuideNodeCapacitySaturated", "RoboGuideArtifactCapacitySaturated"},
                )
            node_context = client(credentials, "admin")
            node_context.set_alpn_protocols(["h2"])
            with node_context.wrap_socket(
                socket.create_connection(("127.0.0.1", node_port), timeout=3),
                server_hostname="localhost",
            ) as idle:
                result["actual_authenticated_node_without_http2"] = wait_closed(idle, 20)
            text = metrics_text(origin, context)
            for name in ("node", "artifact"):
                assert f"roboguide_{name}_connections_limit 2" in text
                match = re.search(
                    rf"^roboguide_{name}_connections_rejected_total (\d+)$", text, re.M
                )
                assert match and int(match[1]) >= 1
            (output / "actual-controller-metrics.prom").write_text(text)
            # Compose the real Mission service with a local trap that must receive zero calls.
            mission_configuration = (ROOT / "config/mission.toml").read_text()
            mission_configuration = re.sub(
                r"^base_url = .*",
                f'base_url = "http://127.0.0.1:{trap.server_port}"',
                mission_configuration,
                flags=re.M,
            )
            mission_configuration = mission_configuration.replace(
                "requires_openai_auth = true", "requires_openai_auth = false"
            )
            (output / "mission.toml").write_text(mission_configuration)
            mission_port = port()
            settings = f'''[service]
listen_host = "127.0.0.1"
listen_port = {mission_port}
state_db = "{output / "requests.sqlite3"}"
controller_endpoint = "{origin}"
controller_timeout_seconds = 3
artifact_endpoint = "{artifacts}"
max_request_bytes = 1048576
connection_limit = 2
connection_timeout_seconds = 2
approval_required_contracts = []
mtls_server_cert = "{credentials / "server.crt"}"
mtls_server_key = "{credentials / "server.key"}"
mtls_client_ca = "{credentials / "ca.crt"}"
mtls_role_map = "{output / "mission.json"}"
controller_client_cert = "{credentials / "admin.crt"}"
controller_client_key = "{credentials / "admin.key"}"
controller_client_ca = "{credentials / "ca.crt"}"
artifact_client_cert = "{credentials / "admin.crt"}"
artifact_client_key = "{credentials / "admin.key"}"
artifact_client_ca = "{credentials / "ca.crt"}"
'''
            (output / "mission-service.toml").write_text(settings)
            configured = dict(environment)
            configured["PYTHONPATH"] = str(ROOT / "mission/src")
            configured.pop("OPENAI_API_KEY", None)
            start(
                "mission",
                [
                    sys.executable,
                    "apps/mission-service/main.py",
                    "--mission-config",
                    str(output / "mission.toml"),
                    "--service-config",
                    str(output / "mission-service.toml"),
                ],
                configured,
            )
            mission_origin = f"https://127.0.0.1:{mission_port}"
            wait_for(
                lambda: http(mission_origin, context, "/healthz")[0] == 200,
                "actual Mission transport",
                15,
            )
            result["actual_mission"] = local_capacity(
                ("127.0.0.1", mission_port),
                lambda: http(mission_origin, context, "/metrics")[1]["http_connections"],
            )
            inputs = output / "declared-empty-inputs"
            inputs.mkdir(mode=0o700)
            for transport in ("tcp", "unix"):
                root = output / ("memory-" + transport)
                arguments = [
                    sys.executable,
                    "-m",
                    "roboguide_memory_provider",
                    "--root",
                    str(root),
                    "--provider-id",
                    "actual-capacity-" + transport,
                    "--input-root",
                    str(inputs),
                    "--connection-limit",
                    "2",
                    "--connection-timeout-seconds",
                    "2",
                ]
                configured = dict(environment)
                configured["PYTHONPATH"] = str(ROOT / "integrations/memory-provider")
                if transport == "unix":
                    arguments += ["--listen-unix"]
                    endpoint: tuple[str, int] | Path = root / "local.sock"
                else:
                    target = port()
                    arguments += ["--listen-port", str(target)]
                    endpoint = ("127.0.0.1", target)
                observe = local_observer(endpoint)
                start("memory-" + transport, arguments, configured)
                wait_for(
                    lambda observe=observe: observe().get("limit") == 2,
                    "actual Memory transport",
                    15,
                )
                result["actual_memory_" + transport] = local_capacity(endpoint, observe)
            adapter_root = output / "robonix"
            adapter_root.mkdir(mode=0o700)
            target = port()
            configured = dict(environment)
            start(
                "robonix",
                [
                    sys.executable,
                    "integrations/robonix-map-service/robonix-map-service.py",
                    "--port",
                    str(target),
                    "--map-root",
                    str(adapter_root / "maps"),
                    "--artifact-root",
                    str(adapter_root / "artifacts"),
                    "--state-db",
                    str(adapter_root / "state.sqlite3"),
                    "--connection-limit",
                    "2",
                    "--connection-timeout-seconds",
                    "2",
                    "--robonix-endpoint",
                    f"http://127.0.0.1:{trap.server_port}",
                ],
                configured,
            )
            observe = local_observer(("127.0.0.1", target))
            wait_for(lambda: observe().get("limit") == 2, "actual Robonix transport", 15)
            result["actual_robonix"] = local_capacity(("127.0.0.1", target), observe)
            assert not NoInference.calls and {str(path): digest(path) for path in files} == identity
            result["unexpected_model_or_robonix_calls"] = list(NoInference.calls)
            result["passed"] = True
        finally:
            if monitoring is not None:
                monitoring.close()
            for process, record in reversed(owned):
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                        record["forced_cleanup"] = True
                        result["passed"] = False
                record["actual_wait"] = process.returncode
                if process.returncode not in {0, -signal.SIGINT}:
                    result["passed"] = False
            trap.shutdown()
            trap.server_close()
            trap_thread.join(timeout=2)
            result["actual_processes"] = [record for _, record in owned]
            save(output / "result.json", result)
    return result


def main() -> None:
    """Require a frozen real Controller binary and a fresh short evidence root."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--monitoring-tools", type=Path)
    args = parser.parse_args()
    if not run(
        args.output.resolve(),
        args.controller.resolve(strict=True),
        args.monitoring_tools.resolve(strict=True) if args.monitoring_tools else None,
    )["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
