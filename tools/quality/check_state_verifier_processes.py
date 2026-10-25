"""Run real POSIX computation through Node Protocol, State gates, CAS and verification.

This is software process conformance with zero robots. Local execution comes from actual child
exit and output files. Verification independently rereads configured source files. Physical
continuity and hardware identity remain unverified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import select
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, BinaryIO

ROOT = Path(__file__).resolve().parents[2]


def save(path: Path, value: Any) -> None:
    """Atomically replace an owned configuration or result file."""
    temporary = path.with_suffix(path.suffix + ".pending")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    temporary.replace(path)


_endpoint_ports: set[int] = set()
_endpoint_writer = threading.Lock()


def address() -> str:
    """Select a distinct free Linux listener outside the actual kernel client-port range."""
    low, high = map(int, Path("/proc/sys/net/ipv4/ip_local_port_range").read_text().split())
    if not 1 <= low <= high <= 65535:
        raise ValueError("actual kernel ephemeral port range is invalid")
    before = max(0, low - 1024)
    after_start = max(1024, high + 1)
    available = before + max(0, 65536 - after_start)
    if not available:
        raise ValueError("actual kernel leaves no unprivileged non-ephemeral listener ports")
    with _endpoint_writer:
        for _ in range(512):
            choice = secrets.randbelow(available)
            port = 1024 + choice if choice < before else after_start + choice - before
            if port in _endpoint_ports:
                continue
            with socket.socket() as listener:
                try:
                    listener.bind(("127.0.0.1", port))
                except OSError:
                    continue
                _endpoint_ports.add(port)
                return f"127.0.0.1:{port}"
    raise RuntimeError("no distinct free non-ephemeral listener found within the original bound")


def request(
    origin: str, path: str, body: Any = None, *, method: str | None = None
) -> tuple[int, Any]:
    """Exchange bounded public HTTP requests without transparently retrying a mutation."""
    payload = (
        None if body is None else body if isinstance(body, bytes) else json.dumps(body).encode()
    )
    call = urllib.request.Request(
        origin + path, data=payload, method=method or ("GET" if body is None else "POST")
    )
    call.add_header(
        "Content-Type",
        "application/octet-stream" if isinstance(body, bytes) else "application/json",
    )
    try:
        response = urllib.request.urlopen(call, timeout=3)  # noqa: S310
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("software process response exceeds its bound")
        return response.status, json.loads(raw)


def wait_for(predicate: Any, reason: str, seconds: float = 20) -> Any:
    """Poll an actual bounded process observation without manufacturing a requested outcome."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except (OSError, urllib.error.URLError, sqlite3.Error):
            pass
        time.sleep(0.05)
    raise TimeoutError(reason)


def compute(directory: Path, output: Path) -> None:
    """Perform actual chunked file hashing in a separate OS process with source-change detection."""
    rows = []
    for path in sorted((directory / "inputs").iterdir()):
        before = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(32768):
                digest.update(chunk)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ino,
        ):
            raise RuntimeError("input changed during computation")
        rows.append({"name": path.name, "size": before.st_size, "sha256": digest.hexdigest()})
    save(output, {"schema_version": "roboguide.software-file-result/v0.1", "files": rows})


def verify(directory: Path, output: Path) -> None:
    """Independently recompute file digests in another OS process."""
    observed = json.loads(output.read_bytes())
    expected = []
    for path in sorted((directory / "inputs").iterdir()):
        with path.open("rb") as stream:
            expected.append(
                {
                    "name": path.name,
                    "size": path.stat().st_size,
                    "sha256": hashlib.file_digest(stream, "sha256").hexdigest(),
                }
            )
    if observed != {"schema_version": "roboguide.software-file-result/v0.1", "files": expected}:
        raise ValueError("independent file verification rejected the produced artifact")


class ComputeServer(ThreadingHTTPServer):
    """Expose actual owned POSIX computation through the configured Local How boundary."""

    def __init__(
        self, directory: Path, *, capacity: int = 1, hold_before_read: bool = False
    ) -> None:
        """Own declared process slots and optional actual kernel read barriers for conformance."""
        if not 1 <= capacity <= 8:
            raise ValueError("actual process-slot declaration must stay in 1..8")
        super().__init__(("127.0.0.1", 0), ComputeHandler)
        self.directory = directory
        self.capacity = capacity
        self.hold_before_read = hold_before_read
        self.read_permits: dict[str, int] = {}
        self.lock = threading.RLock()
        self.jobs: dict[str, tuple[subprocess.Popen[bytes], Path, dict[str, Any]]] = {}
        self.invocations: list[dict[str, Any]] = []

    def release_read(self, identity: str) -> None:
        """Permit one original owned child to read; completion still requires its actual exit."""
        with self.lock:
            descriptor = self.read_permits.pop(identity)
            try:
                os.write(descriptor, b"1")
            finally:
                os.close(descriptor)

    @property
    def origin(self) -> str:
        """Return only the owned loopback local-provider route."""
        return f"http://127.0.0.1:{self.server_address[1]}"

    def close(self) -> None:
        """Stop only child processes and listener owned by this conformance invocation."""
        self.shutdown()
        self.server_close()
        for descriptor in self.read_permits.values():
            os.close(descriptor)
        self.read_permits.clear()
        for process, _, _ in self.jobs.values():
            stop(process)


class ComputeHandler(BaseHTTPRequestHandler):
    """Report actual file, configuration and process observations."""

    def respond(self, value: Any, status: int = 200) -> None:
        """Write a bounded local workflow response."""
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        """Observe actual queue availability and the deployment-owned file admission switch."""
        server: ComputeServer = self.server  # type: ignore[assignment]
        if self.path == "/health":
            self.respond({"state": "ONLINE", "detail": "owned POSIX worker process"})
        elif self.path == "/capability":
            self.respond({"state": "READY", "detail": "Python file hashing is loaded"})
        elif self.path == "/admission":
            self.respond(json.loads((server.directory / "admission.json").read_bytes()))
        else:
            self.respond({"error": "unknown fixed route"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        """Start, observe or terminate a bounded real process under its exact invocation."""
        server: ComputeServer = self.server  # type: ignore[assignment]
        body = json.loads(self.rfile.read(min(int(self.headers["Content-Length"]), 65536)))
        with server.lock:
            if self.path == "/execute":
                invocation = body["invocation"]
                identity = body["attempt_id"]
                if identity in server.jobs:
                    self.respond({"execution_id": identity})
                    return
                if (
                    sum(process.poll() is None for process, _, _ in server.jobs.values())
                    >= server.capacity
                    or len(server.jobs) >= 8
                ):
                    self.respond({"error": "actual software queue is occupied/full"}, 409)
                    return
                output = server.directory / (str(uuid.uuid4()) + ".result.json")
                command = [
                    sys.executable,
                    str(Path(__file__)),
                    "--compute",
                    str(server.directory),
                    str(output),
                ]
                read_fd, write_fd = os.pipe() if server.hold_before_read else (-1, -1)
                if read_fd >= 0:
                    command.extend(["--read-permit", str(read_fd)])
                try:
                    process = subprocess.Popen(
                        command,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        pass_fds=(read_fd,) if read_fd >= 0 else (),
                    )
                except BaseException:
                    if write_fd >= 0:
                        os.close(write_fd)
                    raise
                finally:
                    if read_fd >= 0:
                        os.close(read_fd)
                if write_fd >= 0:
                    server.read_permits[identity] = write_fd
                server.jobs[identity] = (process, output, invocation)
                server.invocations.append({"attempt_id": identity, "invocation": invocation})
                save(server.directory / "actual-invocations.json", server.invocations)
                self.respond({"execution_id": identity})
            elif self.path in {"/status", "/cancel"}:
                identity = body["execution_id"]
                process, output, _ = server.jobs[identity]
                if self.path == "/cancel" and process.poll() is None:
                    process.terminate()
                code = process.poll()
                state = (
                    "RUNNING"
                    if code is None
                    else "COMPLETED"
                    if code == 0 and output.is_file()
                    else "CANCELLED"
                    if code < 0
                    else "FAILED"
                )
                self.respond(
                    {
                        "state": state,
                        "detail": f"actual child exit={code}",
                        "execution_id": identity,
                    }
                )
            else:
                self.respond({"error": "unknown fixed operation"}, 404)

    def log_message(self, format: str, *args: Any) -> None:
        """Keep repeated actual process polling out of console output."""


def node_config(directory: Path, node_id: str, grpc: str, origin: str) -> str:
    """Declare software compute, State export and fixed HTTP workflows."""
    return f'''schema = "roboguide.node-config/v0.7"
node_id = "{node_id}"
server_endpoint = "http://{grpc}"
state_directory = {json.dumps(str(directory / "node-state"))}
reconnect_delay_ms = 100
[[local_systems]]
id = "posix-compute"
runtime_name = "posix-file-computation"
runtime_version = "0.1"
[local_systems.metadata]
assurance = "software-process"
[local_systems.health]
state_pointer = "/state"
detail_pointer = "/detail"
online = ["ONLINE"]
degraded = ["DEGRADED"]
offline = ["OFFLINE"]
[local_systems.health.step]
id = "health"
connection = "compute-http"
[local_systems.health.step.operation]
kind = "http"
method = "GET"
path = "/health"
[[connections]]
driver = "http"
id = "compute-http"
local_system = "posix-compute"
endpoint = "{origin}"
timeout_ms = 3000
[[capability_profiles]]
contract = "compute.hash-files@v1"
kind = "compute"
owner = "posix-compute"
attributes = {{}}
[capability_profiles.readiness]
state_pointer = "/state"
detail_pointer = "/detail"
ready = ["READY"]
unavailable = ["UNAVAILABLE"]
[capability_profiles.readiness.step]
id = "readiness"
connection = "compute-http"
[capability_profiles.readiness.step.operation]
kind = "http"
method = "GET"
path = "/capability"
[[resources]]
id = "{node_id}-compute-slot"
kind = "compute"
capacity = 1
owner = "posix-compute"
[[state_exports]]
id = "admission"
owner = "posix-compute"
object_class = "world"
object_type = "software-work-admission"
object_id = "{node_id}"
semantic = "observed"
payload_schema = "roboguide.software-work-admission/v0.1"
valid_for_ms = 3000
interval_ms = 100
value_pointer = ""
[state_exports.step]
id = "observe-admission"
connection = "compute-http"
[state_exports.step.operation]
kind = "http"
method = "GET"
path = "/admission"
[[operations]]
operation = "compute.hash-files@v1"
owner = "posix-compute"
required_resources = ["{node_id}-compute-slot"]
local_locks = ["file-hasher"]
[operations.workflow]
poll_interval_ms = 50
[operations.workflow.local_handle]
kind = "pointer"
pointer = "/steps/dispatch/execution_id"
[operations.workflow.execution_state]
state_pointer = "/steps/status/state"
reason_pointer = "/steps/status/detail"
accepted = ["ACCEPTED"]
running = ["RUNNING"]
completed = ["COMPLETED"]
failed = ["FAILED"]
cancelled = ["CANCELLED"]
[[operations.workflow.execute]]
id = "dispatch"
connection = "compute-http"
[operations.workflow.execute.operation]
kind = "http"
method = "POST"
path = "/execute"
[[operations.workflow.execute.request.bindings]]
target = "/invocation"
[operations.workflow.execute.request.bindings.value]
kind = "pointer"
pointer = "/invocation"
[[operations.workflow.execute.request.bindings]]
target = "/attempt_id"
[operations.workflow.execute.request.bindings.value]
kind = "pointer"
pointer = "/attempt_id"
[[operations.workflow.status]]
id = "status"
connection = "compute-http"
[operations.workflow.status.operation]
kind = "http"
method = "POST"
path = "/status"
[[operations.workflow.status.request.bindings]]
target = "/execution_id"
[operations.workflow.status.request.bindings.value]
kind = "pointer"
pointer = "/local_handle"
[[operations.workflow.cancel]]
id = "cancel"
connection = "compute-http"
[operations.workflow.cancel.operation]
kind = "http"
method = "POST"
path = "/cancel"
[[operations.workflow.cancel.request.bindings]]
target = "/execution_id"
[operations.workflow.cancel.request.bindings.value]
kind = "pointer"
pointer = "/local_handle"
'''


def plan(mission: str) -> dict[str, Any]:
    """Declare dependent file computations requiring independent verification."""
    operation = {"namespace": "compute", "name": "hash-files", "version": "v1"}
    tasks = []
    for index in range(2):
        tasks.append(
            {
                "id": f"hash-{index}",
                "description": "compute input file manifest",
                "context_id": "work",
                "depends_on": [] if index == 0 else ["hash-0"],
                "timing": {
                    "earliest_start_offset_ms": 0,
                    "latest_start_offset_ms": None,
                    "completion_deadline_offset_ms": None,
                },
                "satisfaction": {
                    "expected_effect": "all file digests match",
                    "basis": "verifier-evidence",
                    "verifier": {
                        "contract": {"namespace": "observation", "name": "verify", "version": "v1"},
                        "predicate": "all file digests match",
                        "max_evidence_age_ms": 10000,
                    },
                },
                "roles": [
                    {
                        "id": "hasher",
                        "context_role": "compute",
                        "requirements": {
                            "capabilities": [{"contract": operation, "constraints": []}],
                            "resources": [{"kind": "compute", "units": 1}],
                        },
                        "execution_intent": {
                            "operation": operation,
                            "objective": "hash immutable deployment source files",
                            "parameters": {},
                        },
                        "resource_scope": "task",
                    }
                ],
            }
        )
    return {
        "schema_version": "roboguide.mission-plan/v0.8",
        "mission": {
            "id": mission,
            "objective": "verify two independent file computations",
            "actors": [{"id": "process"}],
        },
        "contexts": [
            {
                "id": "work",
                "roles": [{"id": "compute", "actor": "process"}],
                "relations": [],
                "coupling_mode": "independent",
                "executor_constraints": [],
            }
        ],
        "tasks": tasks,
    }


def install_policies(origin: str, node: str) -> None:
    """Install source, Belief and Control consumer policies through public APIs."""
    object_ref = {"class": "world", "object_type": "software-work-admission", "object_id": node}
    policy = {
        "schema": "roboguide.decision-evidence-policy/v0.1",
        "policy_id": "admission",
        "revision": 1,
        "object": object_ref,
        "channel_id": "admission",
        "allowed_semantics": ["observed"],
        "allowed_sources": [{"owner": "node", "node_id": node, "local_system_id": "posix-compute"}],
        "max_age_ms": 3000,
        "min_confidence_millionths": None,
        "allowed_decisions": ["belief_fusion"],
        "reducer": "require_equivalent",
        "payload_schema": "roboguide.software-work-admission/v0.1",
    }
    belief = {
        "command_id": str(uuid.uuid4()),
        "expected_revision": 0,
        "registry": {
            "schema_version": "roboguide.belief-policy-registry/v0.1",
            "registry_id": "software-beliefs",
            "revision": 1,
            "policies": {
                "admission": {
                    "policy_id": "admission",
                    "revision": 1,
                    "tenant_id": "local",
                    "enabled": True,
                    "minimum_sources": 1,
                    "admission": policy,
                    "algorithm": {"kind": "scalar", "value_kind": "boolean", "pointer": "/enabled"},
                }
            },
        },
    }
    assert request(origin, "/v1/belief-policies", belief)[0] == 200
    consumer = dict(
        policy,
        channel_id="belief",
        allowed_semantics=["belief"],
        allowed_sources=[{"owner": "robo_guide", "component": "belief/local/admission"}],
        allowed_decisions=["match", "commit", "bind", "rebind", "readiness", "satisfaction"],
        payload_schema="roboguide.belief/v0.1",
    )
    assert (
        request(
            origin,
            "/v1/state-policies",
            {
                "command_id": str(uuid.uuid4()),
                "expected_revision": 0,
                "registry": {
                    "schema_version": "roboguide.decision-policy-registry/v0.1",
                    "registry_id": "software-consumers",
                    "revision": 1,
                    "gates": {
                        "admission": {
                            "tenant_id": "local",
                            "enabled": True,
                            "policy": consumer,
                            "target": {
                                "operation": {
                                    "namespace": "compute",
                                    "name": "hash-files",
                                    "version": "v1",
                                }
                            },
                            "candidate_source_only": False,
                            "require_current_session": False,
                            "condition": {"kind": "boolean", "pointer": "/value", "value": True},
                        }
                    },
                },
            },
        )[0]
        == 200
    )
    assert (
        request(
            origin,
            "/v1/verifier-sources",
            {
                "command_id": str(uuid.uuid4()),
                "expected_revision": 0,
                "registry": {
                    "schema_version": "roboguide.semantic-verifier-registry/v0.1",
                    "registry_id": "file-verifier",
                    "revision": 1,
                    "sources": {
                        "file-reader": {
                            "source_id": "file-reader",
                            "revision": 1,
                            "tenant_id": "local",
                            "producer_principals": ["trusted-loopback:operator"],
                            "verifier": {
                                "namespace": "observation",
                                "name": "verify",
                                "version": "v1",
                            },
                            "predicates": ["all file digests match"],
                            "max_age_ms": 10000,
                            "stop_timeout_ms": 5000,
                            "enabled": True,
                            "continuous": False,
                            "require_artifact": True,
                        }
                    },
                },
            },
        )[0]
        == 200
    )


def stop(process: subprocess.Popen[bytes]) -> None:
    """Reap only an owned subprocess, preserving all unrelated workspace processes."""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def run(
    directory: Path,
    skip_build: bool,
    quantitative: bool = False,
    previous_controller: Path | None = None,
) -> dict[str, Any]:
    """Exercise protocol execution, independent CAS verification and restore."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    (directory / "inputs").mkdir()
    for path in sorted((ROOT / "core/domain/src").glob("*.rs"))[:12]:
        shutil.copyfile(path, directory / "inputs" / path.name)
    save(directory / "admission.json", {"enabled": False})
    if not skip_build:
        subprocess.run(
            ["cargo", "build", "-p", "integration-server", "-p", "roboguide-node"],
            cwd=ROOT,
            check=True,
        )
    grpc, http, artifact = (address() for _ in range(3))
    origin, cas = "http://" + http, "http://" + artifact
    node, mission = "posix-" + uuid.uuid4().hex, "files-" + uuid.uuid4().hex
    count = 3 if quantitative else 2
    provider = ComputeServer(
        directory, capacity=2 if quantitative else 1, hold_before_read=quantitative
    )
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    config = directory / "node.toml"
    configured = node_config(directory, node, grpc, provider.origin)
    if quantitative:
        from quantitative_resource_processes import QuantitativeCheck, configuration

        configured = configuration(configured)
    config.write_text(configured)
    processes: list[subprocess.Popen[bytes]] = []
    logs: list[BinaryIO] = []
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    environment["ROBOGUIDE_OPERATOR_LOCAL_RECOVERY_ADMIN"] = "1"
    if quantitative:
        environment["ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA"] = "roboguide.artifact-storage/v3"
        environment["ROBOGUIDE_CONTROLLER_STORAGE_SCHEMA"] = "1"

    def start(command: list[str], name: str) -> subprocess.Popen[bytes]:
        """Track the exact new process and its private diagnostic stream."""
        log = (directory / (name + ".log")).open("wb")
        logs.append(log)
        process = subprocess.Popen(
            command, cwd=ROOT, env=environment, stdout=log, stderr=log, start_new_session=True
        )
        processes.append(process)
        return process

    controller_command = [
        str(ROOT / "target/debug/integration-server"),
        grpc,
        str(directory / "controller.sqlite3"),
        http,
        artifact,
        str(directory / "cas"),
    ]
    try:
        controller = start(controller_command, "controller")
        wait_for(lambda: request(origin, "/healthz")[0] == 200, "Controller startup")
        install_policies(origin, node)
        node_command = [str(ROOT / "target/debug/roboguide-node"), str(config)]
        node_process = start(node_command, "node")
        wait_for(
            lambda: len(request(origin, "/v1/inventory")[1]["nodes"]) == 1,
            "actual software Node registration",
        )
        selected_plan = plan(mission)
        if quantitative:
            from quantitative_resource_processes import mission_plan

            selected_plan = mission_plan(selected_plan)
        assert request(origin, "/v1/missions", selected_plan)[0] == 202
        time.sleep(0.35)
        assert request(origin, "/v1/execution-attempts")[1]["attempts"] == []
        assert provider.invocations == [], "false observed admission started an actual process"
        save(directory / "admission.json", {"enabled": True})
        quantity = QuantitativeCheck(directory, origin, provider, mission) if quantitative else None
        if quantity is not None:
            quantity.freeze()
            stop(controller)
            stop(node_process)
            quantity.quiescent_recovery(previous_controller)
            controller = start(controller_command, "controller-quantities-restored")
            node_process = start(node_command, "node-quantities-restored")
            wait_for(lambda: request(origin, "/healthz")[0] == 200, "quantity Controller restore")
            quantity.restored()
        receipts = []
        for index in range(count):
            task = f"hash-{index}"
            if quantity is not None:
                quantity.release(task)
            attempts = wait_for(
                lambda task=task: [
                    entry
                    for entry in request(origin, "/v1/execution-attempts")[1]["attempts"]
                    if entry["task_id"] == task and entry["status"] == "Completed"
                ],
                "actual file process completion",
            )
            assert len(attempts) == 1
            attempt = attempts[0]
            assert request(origin, f"/v1/missions/{mission}")[1]["status"] != "Completed"
            process, output, _ = provider.jobs[attempt["execution_id"]]
            assert process.poll() == 0
            if quantity is not None:
                quantity.completed_before_verifier(index)
            subprocess.run(
                [sys.executable, str(Path(__file__)), "--verify", str(directory), str(output)],
                check=True,
                timeout=10,
            )
            raw = output.read_bytes()
            content_digest = "sha256:" + hashlib.sha256(raw).hexdigest()
            upload_id = uuid.uuid4().hex
            assert request(cas, "/v1/artifact-uploads", {"upload_id": upload_id})[0] == 201
            assert (
                request(cas, f"/v1/artifact-uploads/{upload_id}/content", raw, method="PUT")[0]
                == 202
            )
            assert (
                request(
                    cas,
                    f"/v1/artifact-uploads/{upload_id}/finalize",
                    {"content_digest": content_digest, "byte_size": len(raw)},
                )[0]
                == 201
            )
            if index == 0 and not quantitative:
                previous_epoch = next(
                    record["value"]["source_epoch"]
                    for record in request(origin, "/v1/state/records")[1]["records"]
                    if record["source"] == f"node:{node}/posix-compute"
                    and record["channel_id"] == "admission"
                )
                stop(controller)
                controller = start(controller_command, "controller-restored")
                wait_for(lambda: request(origin, "/healthz")[0] == 200, "Controller restore")
                wait_for(
                    lambda previous_epoch=previous_epoch: any(
                        record["semantic"] == "belief"
                        and record["value"]["value"].get("value") is True
                        and record["stale"] is False
                        and record["value"]["value"]["inputs"][0]["source_epoch"] != previous_epoch
                        for record in request(origin, "/v1/state/records")[1]["records"]
                    ),
                    "fresh post-restore observed admission",
                )
                assert len(provider.invocations) == 1, (
                    "restore replayed the completed file computation"
                )
            plan_digest = request(origin, f"/v1/missions/{mission}/tenant")[1]["source_plan_digest"]
            evidence = {
                "evidence_id": uuid.uuid4().hex,
                "source_id": "file-reader",
                "source_revision": 1,
                "tenant_id": "local",
                "task_ref": {"mission_id": mission, "task_id": task},
                "plan_digest": plan_digest,
                "attempts": [
                    {"role_id": attempt["role_id"], "attempt_id": attempt["execution_id"]}
                ],
                "predicate": "all file digests match",
                "verdict": "satisfied",
                "finality": "final",
                "source_observed_at_ms": time.monotonic_ns() // 1_000_000,
                "valid_for_ms": 10000,
                "sequence": 1,
                "artifact_digest": content_digest,
                "artifact_size_bytes": len(raw),
            }
            status, receipt = request(origin, "/v1/verifier-evidence", evidence)
            assert status == 200, receipt
            assert receipt["receipt"]["applied"] is True, receipt
            assert request(origin, "/v1/verifier-evidence", evidence)[1] == receipt
            receipts.append(receipt)
        wait_for(
            lambda: request(origin, f"/v1/missions/{mission}")[1]["status"] == "Completed",
            "independently verified software Mission completion",
        )
        assert len(provider.invocations) == count
        with (
            closing(
                sqlite3.connect(f"file:{directory / 'controller.sqlite3'}?mode=ro", uri=True)
            ) as connection,
            connection,
        ):
            checkpoint = json.loads(
                connection.execute("SELECT checkpoint_json FROM controller_checkpoint").fetchone()[
                    0
                ]
            )
        integration = json.loads(checkpoint["integration_json"])
        assert not integration["control"]["reservations"]
        result = {
            "passed": True,
            "schema": checkpoint["schema"],
            "devices": 0,
            "software_nodes": 1,
            "actual_compute_processes": count,
            "independent_verifier_processes": count,
            "duplicate_executions": 0,
            "configuration_digest": "sha256:" + hashlib.sha256(config.read_bytes()).hexdigest(),
            "controller_sha256": hashlib.sha256(
                (ROOT / "target/debug/integration-server").read_bytes()
            ).hexdigest(),
            "node_sha256": hashlib.sha256(
                (ROOT / "target/debug/roboguide-node").read_bytes()
            ).hexdigest(),
            "receipts": receipts,
            "quantitative_resources": quantity.finish() if quantity is not None else None,
            "hardware_evidence": "HardwareEvidencePending",
        }
        save(directory / "result.json", result)
        return result
    finally:
        for process in reversed(processes):
            stop(process)
        provider.close()
        thread.join(timeout=5)
        for log in logs:
            log.close()


def main() -> None:
    """Run conformance or one actual compute or independent verifier process."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--compute", nargs=2, type=Path)
    parser.add_argument("--read-permit", type=int)
    parser.add_argument("--quantitative-resources", action="store_true")
    parser.add_argument("--previous-controller", type=Path)
    parser.add_argument("--verify", nargs=2, type=Path)
    arguments = parser.parse_args()
    if arguments.compute:
        if arguments.read_permit is not None:
            descriptor = arguments.read_permit
            try:
                ready, _, _ = select.select([descriptor], [], [], 30)
                if not ready or os.read(descriptor, 1) != b"1":
                    raise ValueError("original process read permit expired or closed")
            finally:
                os.close(descriptor)
        compute(*arguments.compute)
    elif arguments.verify:
        verify(*arguments.verify)
    elif arguments.output:
        result = run(
            arguments.output.resolve(),
            arguments.skip_build,
            arguments.quantitative_resources,
            arguments.previous_controller,
        )
        print(
            json.dumps({key: value for key, value in result.items() if key != "receipts"}, indent=2)
        )
    else:
        parser.error("one explicit --output, --compute or --verify mode is required")


if __name__ == "__main__":
    main()
