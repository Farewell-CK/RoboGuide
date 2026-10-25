"""Own real independent Replica domains and bounded TLS passthrough, never device facts."""

from __future__ import annotations

import json
import select
import signal
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from check_federation_execution_processes import FederatedCase, registry
from check_identity_processes import request
from check_replica_certificate_processes import create_ca, issue
from check_replica_content_processes import Processes, save
from check_state_verifier_processes import address, wait_for


class RoutingEndpoint(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """Forward actual TLS bytes once per connection using a readonly original leader hint."""

    daemon_threads = False
    allow_reuse_address = True

    def __init__(self, case: ReplicatedCase, endpoint: str, plane: str) -> None:
        """Bind only this explicit owned endpoint; no TLS or HTTP response is manufactured."""
        self.case, self.plane = case, plane
        self.permits = threading.BoundedSemaphore(128)
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.connections: set[socket.socket] = set()
        self.records: list[dict[str, Any]] = []
        host, number = endpoint.rsplit(":", 1)
        super().__init__((host, int(number)), Forward)

    def close(self) -> None:
        """End only owned I/O; original application effects remain under their durable journals."""
        self.stopping.set()
        with self.lock:
            for connection in self.connections:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        self.shutdown()
        self.server_close()
        save(self.case.root / ("actual-router-" + self.plane + ".json"), self.records)

    def process_request(self, request: Any, client_address: Any) -> None:
        """Admit one actual transport before a thread, with no application or TLS interpretation."""
        if not self.permits.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.permits.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        """Return only this actual worker's original permit after all forwarding ends."""
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.permits.release()


class Forward(socketserver.BaseRequestHandler):
    """Retain bounded actual bytes and failures without interpreting TLS or application content."""

    def handle(self) -> None:
        """Freeze one real target and never reconnect or replay an uncertain mutation."""
        server: RoutingEndpoint = self.server  # type: ignore[assignment]
        client: socket.socket = self.request
        client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        record: dict[str, Any] = {"started_monotonic": time.monotonic(), "bytes": [0, 0]}
        upstream: socket.socket | None = None
        try:
            member, status = server.case.current()
            target = server.case.configurations[member][server.plane + "_address"]
            record.update(member=member, epoch=status["epoch"], target=target)
            host, number = target.rsplit(":", 1)
            upstream = socket.create_connection((host, int(number)), timeout=2)
            upstream.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            client.settimeout(2)
            with server.lock:
                server.connections.update((client, upstream))
            while (
                not server.stopping.is_set()
                and time.monotonic() - record["started_monotonic"] < 900
            ):
                readable, _, _ = select.select([client, upstream], [], [], 0.2)
                for source in readable:
                    chunk = source.recv(65536)
                    if not chunk:
                        return
                    index = 0 if source is client else 1
                    record["bytes"][index] += len(chunk)
                    if sum(record["bytes"]) > 64 * 1024 * 1024:
                        raise ValueError("owned routing connection exceeded its byte budget")
                    (upstream if source is client else client).sendall(chunk)
        except (OSError, ValueError) as error:
            record["actual_error"] = str(error)[:1024]
        finally:
            with server.lock:
                server.connections.discard(client)
                if upstream is not None:
                    server.connections.discard(upstream)
                if len(server.records) < 4096:
                    record["elapsed_seconds"] = time.monotonic() - record["started_monotonic"]
                    server.records.append(record)
            if upstream is not None:
                upstream.close()


class ReplicatedCase(FederatedCase):
    """Own one real domain's three infrastructure voters and original CPU Node deployment."""

    def __init__(
        self,
        root: Path,
        supervisor: Path,
        storage: Path,
        members: list[int],
        controller: Path,
        node: Path,
    ) -> None:
        """Keep original independent TLS, CPU files and stable actual transport endpoints."""
        super().__init__(root, 1, 1, state_interval_ms=1000)
        self.binary, self.node_binary = controller, node
        self.supervisor, self.storage = supervisor, storage
        assert 3 <= len(members) <= 9 and len(members) == len(set(members)) and min(members) > 0
        self.member_ids = members
        self.configurations: dict[int, dict[str, Any]] = {}
        self.supervisors: dict[int, subprocess.Popen[bytes]] = {}
        self.routers: list[RoutingEndpoint] = []
        self.routing_threads: list[threading.Thread] = []
        self.ipc = tempfile.TemporaryDirectory(prefix="rg-fed-ha-")

    def exchange(
        self, path: str, body: dict[str, Any] | None = None, who: str = "admin"
    ) -> dict[str, Any]:
        """Send one actual request within the API response budget, without retrying a mutation."""
        status, value = request(
            self.origin,
            self.contexts[who],
            path,
            body,
            {"X-RoboGuide-Tenant": "local"} if path.startswith("/v1/federation/") else {},
            timeout_seconds=30 if body is not None else 5,
        )
        if not 200 <= status < 300:
            raise ValueError(f"actual {path}: HTTP {status}: {value}")
        return value

    def current(self) -> tuple[int, dict[str, Any]]:
        """Read fresh original status as a routing hint, never as application/quorum authority."""
        candidates = []
        for member, config in self.configurations.items():
            if self.supervisors[member].poll() is not None:
                continue
            try:
                value = json.loads((Path(config["storage_directory"]) / "status.json").read_bytes())
                age = time.time_ns() // 1_000_000 - value["observed_unix_ms"]
                if value["epoch"] and value["child_pid"] and 0 <= age <= 5000:
                    candidates.append((value["epoch"]["term"], member, value))
            except (OSError, ValueError, KeyError):
                continue
        if not candidates:
            raise ConnectionError("no fresh actual owned leader routing hint")
        _, member, value = max(candidates, key=lambda row: row[:2])
        return member, value

    def finish_goal(self, mission: str, *, valid_for_ms: int = 10000) -> None:
        """Observe formal completion, then independently recompute one fresh bounded proof."""

        def completed() -> bool:
            """Observe actual original Runtime reports, without substituting CPU output for them."""
            rows = [
                row
                for row in self.exchange("/v1/execution-attempts")["attempts"]
                if row["mission_id"] == mission
            ]
            return bool(rows) and all(row["status"] == "Completed" for row in rows)

        wait_for(completed, "actual original domain Runtime completion before goal proof", 60)
        super().finish_goal(mission, valid_for_ms=valid_for_ms)

    def boot(self) -> None:
        """Configure original Roots and real mTLS voters before enrolling any software Node."""
        self.prepare_cluster()
        for member, config in self.configurations.items():
            path = self.root / f"configuration-{member}.json"
            save(path, config)
            self.supervisors[member] = self.start(
                [str(self.supervisor), str(path)], "supervisor-" + str(member)
            )
        wait_for(lambda: self.current(), "actual independent replicated domain leader", 60)
        for endpoint, plane in [
            (self.grpc, "grpc"),
            (self.http, "http"),
            (self.artifact, "artifact"),
        ]:
            router = RoutingEndpoint(self, endpoint, plane)
            thread = threading.Thread(target=router.serve_forever, daemon=True)
            thread.start()
            self.routers.append(router)
            self.routing_threads.append(thread)
        wait_for(lambda: self.exchange("/healthz"), "actual routed mTLS domain Controller", 30)
        registry(self)
        self.boot_nodes()

    def prepare_cluster(self) -> None:
        """Bind explicit actual process configuration without manufacturing any device inventory."""
        tls = self.root / "tls"
        create_ca(tls)
        members = self.member_ids
        leaves = {member: issue(tls, "actual-member-" + str(member)) for member in members}
        endpoints = {str(member): address() for member in members}
        policies: set[str] = set()
        profiles: set[str] = set()
        owners: dict[str, str] = {}
        schema = subprocess.check_output(
            [str(self.binary), "--application-schema"], text=True
        ).strip()
        identity = self.root / "node-identities.json"
        save(identity, json.loads(self.environment["ROBOGUIDE_NODE_MTLS_NODE_CERT_DIGESTS"]))
        artifact = self.root / "artifact-authorization.json"
        save(
            artifact,
            {
                "schema_version": "roboguide.artifact-authorization/v0.1",
                "registry_id": uuid.uuid4().hex,
                "revision": 1,
                "grants": {
                    "sha256:" + self.leaves["admin"]: {
                        "principal_id": "cohort-administrator",
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
        calls = Processes(self.storage, self.root)
        try:
            for member in members:
                parent = self.root / ("member-" + str(member))
                parent.mkdir(mode=0o700)
                configuration: dict[str, Any] = {
                    "schema": "roboguide.controller-replica-process/v0.9",
                    "coordinated_archive_base": "roboguide.controller-replica-process/v0.6",
                    "replica": {
                        "schema": "roboguide.controller-replica/v0.4",
                        "cluster_id": self.environment["ROBOGUIDE_CONTROL_DOMAIN"],
                        "replica_id": member,
                        "members": endpoints,
                        "initial_voters": members,
                        "application_schema": schema,
                        "application_policy_digest": "pending",
                        "tls": {
                            "ca_cert_path": str(tls / "ca.crt"),
                            "cert_path": str(leaves[member][0]),
                            "key_path": str(leaves[member][0]),
                            "server_name": "localhost",
                            "peer_certificate_digests": {str(k): v[1] for k, v in leaves.items()},
                        },
                    },
                    "controller_binary": str(self.binary),
                    "storage_directory": str(parent),
                    "grpc_address": address(),
                    "http_address": address(),
                    "artifact_address": address(),
                    "controller_environment": {
                        "TOKIO_WORKER_THREADS": "1",
                        "ROBOGUIDE_CONTROLLER_STORAGE_SCHEMA": "1",
                        "ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA": "roboguide.artifact-storage/v3",
                    },
                    "federation": {
                        "schema": "roboguide.federation-process-inputs/v0.1",
                        "domain": self.environment["ROBOGUIDE_CONTROL_DOMAIN"],
                        "authorization": self.environment["ROBOGUIDE_FEDERATION_DOMAIN_GRANTS"],
                        "peers": self.environment.get("ROBOGUIDE_FEDERATION_PEERS"),
                    },
                    "content": {
                        "schema": "roboguide.controller-content-process/v0.1",
                        "root": str(parent / "content"),
                        "bootstrap_owners": {},
                        "socket": str(Path(self.ipc.name) / (str(member) + ".sock")),
                        "child_budget_ms": 120000,
                        "quorum_observation_ms": 15000,
                    },
                }
                for plane, profile in [
                    ("node", identity),
                    ("operator", self.authorization),
                    ("artifact", artifact),
                ]:
                    configuration[plane + "_tls"] = {
                        "schema": "roboguide.controller-transport-tls-inputs/v0.1",
                        "server_cert": str(self.certificates / "server.crt"),
                        "server_key": str(self.certificates / "server.key"),
                        "client_ca": self.environment[f"ROBOGUIDE_{plane.upper()}_MTLS_CLIENT_CA"]
                        if plane != "artifact"
                        else str(self.certificates / "ca.crt"),
                        "profile": str(profile),
                    }
                path = self.root / f"configuration-{member}.json"
                save(path, configuration)
                policy = subprocess.check_output(
                    [str(self.supervisor), "--policy-digest", str(path)], text=True
                ).strip()
                configuration["replica"]["application_policy_digest"] = policy
                policies.add(policy)
                save(path, configuration)
                profile_identity = subprocess.check_output(
                    [str(self.supervisor), "--profile-identity", str(path)], text=True
                ).strip()
                profiles.add(profile_identity)
                owners[str(member)] = calls.run(
                    {
                        "action": "create",
                        "store": {
                            "root": configuration["content"]["root"],
                            "profile": profile_identity,
                            "member": member,
                            "staging_gc": True,
                        },
                    },
                    "original-federation-root",
                )["identity"]["store_owner"]
                self.configurations[member] = configuration
            assert len(policies) == len(profiles) == 1
            for config in self.configurations.values():
                config["content"]["bootstrap_owners"] = owners
        finally:
            calls.cleanup()
            save(self.root / "actual-root-processes.json", [record for _, record in calls.owned])

    def close(self) -> None:
        """Drain actual Node, child and supervisor processes before transport/backend cleanup."""
        if self.closed:
            return
        self.closed = True
        failures = []
        try:
            for process, record in reversed(self.processes):
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                    try:
                        process.wait(timeout=45)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                        record["forced_cleanup"] = True
                        failures.append(record["name"])
                record["native_wait"] = process.returncode
                if record["name"].startswith("supervisor-") and process.returncode != 0:
                    failures.append(record["name"])
            self.backend.close()
            self.thread.join(timeout=5)
            save(self.root / "owned-processes.json", [record for _, record in self.processes])
            save(self.root / "final-owned-cpu-waits.json", self.backend.snapshot())
        finally:
            for router in self.routers:
                router.close()
            for thread in self.routing_threads:
                thread.join(timeout=5)
            self.ipc.cleanup()
            for log in self.logs:
                log.close()
        if failures:
            raise ValueError("actual original process drains failed: " + str(failures))
