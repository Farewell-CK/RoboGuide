"""Private Unix HTTP Local How for original, once-only actual spatial file computation."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import socketserver
import stat
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from types import FrameType
from typing import Any

from .__main__ import observe, parse_job
from .contracts import canonical, digest, exact, fields, integer
from .geometry import code_digest, sdk
from .storage import read_json, read_source, save_json, sync_directory


def directory(path: Path, create: bool) -> Path:
    """Traverse fixed absolute deployment roots without following any symlink component."""
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise ValueError("spatial service roots must be exact absolute paths")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
    finally:
        os.close(descriptor)
    return path


class SpatialService:
    """Own local process handles/files only; Control, Runtime and CAS publication remain outside."""

    def __init__(
        self,
        root: Path,
        source_root: Path,
        output_root: Path,
        configuration: dict[str, Any],
        readonly: bool = False,
    ) -> None:
        """Freeze backend/config identity and rebuild original admissions without launching jobs."""
        raw = fields(configuration, {"schema_version", "max_jobs", "max_active", "profiles"})
        if raw["schema_version"] != "roboguide.spatial-local-service/v0.1":
            raise ValueError("unsupported spatial Local How service schema")
        self.max_jobs = integer(raw["max_jobs"], "max_jobs", 1, 4096)
        self.max_active = integer(raw["max_active"], "max_active", 1, 16)
        if not isinstance(raw["profiles"], list) or not 1 <= len(raw["profiles"]) <= 128:
            raise ValueError("one to 128 explicit immutable file profiles are required")
        self.profiles: dict[str, dict[str, Any]] = {}
        selectors = set()
        for item in raw["profiles"]:
            profile = fields(
                item, {"profile_id", "map_id", "revision_id", "spatial_anchor_id", "job"}
            )
            for key in ("profile_id", "map_id", "revision_id", "spatial_anchor_id"):
                exact(profile[key], key)
            job = parse_job(profile["job"])
            if job.job_id != profile["revision_id"]:
                raise ValueError(
                    "native immutable revision must match the declared semantic revision"
                )
            map_id = profile["map_id"]
            if any(part in map_id for part in ("/", "\\", ".", ":")):
                raise ValueError("map family must be one safe configured path component")
            selector = (map_id, profile["revision_id"])
            if selector in selectors or profile["profile_id"] in self.profiles:
                raise ValueError("duplicate profiles cannot claim the same immutable selector")
            selectors.add(selector)
            self.profiles[profile["profile_id"]] = profile
        self.root = directory(root, True)
        self.source_root = directory(source_root, False)
        self.output_root = directory(output_root, True)
        self.records_root = directory(root / "records", True)
        self.evidence_root = directory(root / "evidence", True)
        self._writer = os.open(
            root / ".service.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        self._lock = threading.RLock()
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._watchers: list[threading.Thread] = []
        self.records: dict[str, dict[str, Any]] = {}
        self.readonly = readonly
        try:
            fcntl.flock(self._writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
            identity_path = root / "instance.json"
            expected = {
                "configuration_digest": digest(canonical(configuration)),
                "source_root": str(source_root),
                "output_root": str(output_root),
                "code_digest": code_digest(),
            }
            if identity_path.exists():
                self.identity = read_json(identity_path)
                if self.identity.get("schema_version") != "roboguide.spatial-backend-instance/v0.1":
                    raise ValueError("original backend instance schema differs")
                uuid.UUID(self.identity["instance_id"])
                if any(
                    self.identity.get(key) != expected[key]
                    for key in ("source_root", "output_root")
                ):
                    raise ValueError("original spatial backend storage roots changed")
                if (
                    any(
                        self.identity.get(key) != expected[key]
                        for key in ("configuration_digest", "code_digest")
                    )
                    and not readonly
                ):
                    raise ValueError(
                        "spatial backend profile changed; original storage is observation-only"
                    )
            else:
                self.identity = {
                    "schema_version": "roboguide.spatial-backend-instance/v0.1",
                    "instance_id": str(uuid.uuid4()),
                    **expected,
                }
                save_json(identity_path, self.identity)
            for path in self.records_root.iterdir():
                if (
                    len(self.records) >= self.max_jobs
                    or path.suffix != ".json"
                    or path.is_symlink()
                ):
                    raise ValueError("spatial admission history is invalid or exceeds its bound")
                record = read_json(path)
                if path.stem != record.get("handle") or digest(
                    canonical(record["request"])
                ) != record.get("request_digest"):
                    raise ValueError("spatial original admission identity differs")
                if (
                    record.get("schema_version") != "roboguide.spatial-local-admission/v0.1"
                    or record.get("backend_instance_id") != self.identity["instance_id"]
                    or digest(
                        exact(record["request"].get("attempt_id"), "attempt_id", 256).encode()
                    ).removeprefix("sha256:")
                    != path.stem
                    or Path(record["native_directory"]) != self._job_directory(record["profile"])
                ):
                    raise ValueError(
                        "original admission escaped its backend/attempt/output identity"
                    )
                parse_job(record["profile"]["job"])
                self.records[path.stem] = record
        except BaseException:
            os.close(self._writer)
            raise

    def close(self) -> None:
        """Interrupt only owned local CLI processes and wait for their bounded cleanup."""
        with self._lock:
            processes = list(self._processes.values())
        for process in processes:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for watcher in self._watchers:
            watcher.join(timeout=1)
        fcntl.flock(self._writer, fcntl.LOCK_UN)
        os.close(self._writer)

    def _profile(self, body: dict[str, Any]) -> dict[str, Any]:
        """Bind the semantic artifact slot/revision/anchor to fixed deployment-owned Local How."""
        fields(body, {"operation", "attempt_id", "invocation", "artifact_path"})
        exact(body["attempt_id"], "attempt_id", 256)
        invocation = body["invocation"]
        if not isinstance(invocation, dict) or not isinstance(invocation.get("parameters"), dict):
            raise ValueError("canonical invocation/parameters are required")
        parameters = invocation["parameters"]
        slot = exact(parameters.get("artifact_slot"), "artifact_slot")
        profile = self.profiles.get(slot)
        if profile is None or any(
            parameters.get(key) != profile[key]
            for key in ("map_id", "revision_id", "spatial_anchor_id")
        ):
            raise ValueError("semantic output differs from the configured immutable file profile")
        operation = exact(body["operation"], "operation")
        expected = {
            "build-map": ("build", "prepare-output"),
            "publish-map": ("publish", "publish"),
        }.get(operation)
        if expected is None or parameters.get("artifact_operation") != expected[1]:
            raise ValueError("operation does not match its canonical artifact directive")
        if invocation.get("operation") != f"spatial.map.{expected[0]}@v0":
            raise ValueError("Local How refuses a different canonical operation")
        target = Path(exact(body["artifact_path"], "artifact_path", 4096))
        target.relative_to(self.output_root)
        if not target.is_absolute() or ".." in target.parts:
            raise ValueError("artifact handoff leaves the configured Node cache")
        if operation == "build-map" and target != self._job_directory(profile) / "map.bundle":
            raise ValueError("build output must be its original native immutable bundle")
        return profile

    def _job_directory(self, profile: dict[str, Any]) -> Path:
        """Resolve the exact configured selector, without using a model-provided backend path."""
        selector = digest(canonical([profile["map_id"], profile["revision_id"]])).removeprefix(
            "sha256:"
        )
        return self.output_root / "outputs" / selector / str(profile["revision_id"])

    def _original_compute(self, record: dict[str, Any]) -> dict[str, Any]:
        """Rehash original native proof and require an actual joined-worker exit receipt."""
        native = Path(record["native_directory"])
        result = observe(native)
        intent = read_json(native / "intent.json")
        if digest(canonical(record["profile"]["job"])) != intent["request_digest"]:
            raise ValueError("native result differs from original admitted Local How")
        if result["status"] == "rejected_before_compute":
            return result
        if (
            result["status"] == "cancelled"
            and read_json(native / "cancelled.json").get("dispatched") is False
        ):
            return result
        exit_proof = read_json(native / "worker-exit.json")
        started = read_json(native / "worker-started.json")
        if (
            exit_proof.get("schema_version") != "roboguide.spatial-process-exit/v0.1"
            or exit_proof.get("request_digest") != intent["request_digest"]
            or exit_proof.get("clock_id") != intent.get("clock_id")
            or exit_proof.get("assurance") != "software-process"
            or started.get("request_digest") != intent["request_digest"]
            or started.get("pid") != exit_proof.get("worker_pid")
        ):
            raise ValueError("original CPU worker exit proof is unavailable")
        integer(exit_proof.get("worker_pid"), "worker PID", 1, 2**31 - 1)
        integer(exit_proof.get("worker_returncode"), "worker exit", -(2**31), 2**31 - 1)
        return result

    def status(self, handle: str) -> dict[str, Any]:
        """Observe original proof without compute, import, publication or budget renewal."""
        with self._lock:
            record = self.records.get(exact(handle, "execution_id"))
            if record is None:
                raise ValueError("unknown original Local How handle")
            live = self._processes.get(handle)
            if live is not None and live.poll() is None:
                return {
                    "execution_id": handle,
                    "state": "RUNNING",
                    "detail": "original CPU invocation remains active",
                }
            try:
                if record["request"]["operation"] == "publish-map":
                    proof = read_json(self.evidence_root / handle / "publish-check.json")
                    if (
                        proof.get("request_digest") != record["request_digest"]
                        or proof.get("status") != "verified"
                    ):
                        return {
                            "execution_id": handle,
                            "state": "FAILED",
                            "detail": proof.get("reason", "original verification failed"),
                        }
                    path = Path(record["request"]["artifact_path"])
                    payload = read_source(
                        self.output_root,
                        str(path.relative_to(self.output_root)),
                        proof["byte_size"],
                    )
                    if digest(payload) != proof["content_digest"]:
                        raise ValueError("original prepared publication bytes changed")
                    return {
                        "execution_id": handle,
                        "state": "COMPLETED",
                        "detail": "original bundle verified for Node-owned publication",
                    }
                result = self._original_compute(record)
                phases = {
                    "completed": "COMPLETED",
                    "cancelled": "CANCELLED",
                    "unavailable": "FAILED",
                    "rejected_before_compute": "FAILED",
                }
                return {
                    "execution_id": handle,
                    "state": phases.get(result["status"], "UNKNOWN"),
                    "detail": result.get("reason", "original native file computation"),
                    "artifact": result.get("artifact"),
                    "native_status": result["status"],
                }
            except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
                state = "RUNNING" if live is not None and live.poll() is None else "UNKNOWN"
                return {"execution_id": handle, "state": state, "detail": str(error)[:512]}

    def readiness(self) -> dict[str, Any]:
        """Report actual process-slot availability, without claiming any device or localization."""
        with self._lock:
            active = sum(process.poll() is None for process in self._processes.values())
            interrupted = any(
                self.status(handle)["state"] in {"UNKNOWN", "RUNNING"}
                for handle in self.records
                if handle not in self._processes
            )
            ready = (
                not self.readonly
                and active < self.max_active
                and not interrupted
                and len(self.records) < self.max_jobs
            )
            return {
                "state": "ONLINE",
                "instance_id": self.identity["instance_id"],
                "code_digest": self.identity["code_digest"],
                "active_processes": active,
                "capabilities": {
                    f"spatial.map.{operation}@v0": {
                        "state": "READY"
                        if (
                            ready
                            if operation == "build"
                            else not self.readonly and len(self.records) < self.max_jobs
                        )
                        else "UNAVAILABLE",
                        "detail": "CPU runtime loaded; source bytes checked at original admission",
                    }
                    for operation in ("build", "publish")
                },
            }

    def submit(self, body: dict[str, Any]) -> dict[str, Any]:
        """Persist exact original admission before one CLI launch; retries are readonly."""
        if "artifact_paths" in body:
            fields(body, {"operation", "attempt_id", "invocation", "artifact_paths"})
            paths = body["artifact_paths"]
            invocation = body["invocation"]
            if not isinstance(invocation, dict) or not isinstance(
                invocation.get("parameters"), dict
            ):
                raise ValueError("canonical invocation/parameters are required")
            slot = exact(invocation["parameters"].get("artifact_slot"), "artifact_slot")
            if not isinstance(paths, dict) or set(paths) != {slot}:
                raise ValueError("Node artifact context must contain only the selected output")
            body = {key: value for key, value in body.items() if key != "artifact_paths"}
            body["artifact_path"] = paths[slot]
        profile = self._profile(body)
        identity = digest(canonical(body))
        handle = digest(body["attempt_id"].encode()).removeprefix("sha256:")
        with self._lock:
            if handle in self.records:
                if self.records[handle]["request_digest"] != identity:
                    raise ValueError(
                        "attempt identity conflicts with its original Local How request"
                    )
                return self.status(handle)
            capability = (
                "spatial.map.build@v0"
                if body["operation"] == "build-map"
                else "spatial.map.publish@v0"
            )
            if self.readiness()["capabilities"][capability]["state"] != "READY":
                raise ValueError(
                    "original backend work or readonly/capacity fence prevents admission"
                )
            if body["operation"] == "build-map" and any(
                record["profile"]["profile_id"] == profile["profile_id"]
                and record["request"]["operation"] == "build-map"
                for record in self.records.values()
            ):
                raise ValueError(
                    "immutable map revision already has its original producing attempt"
                )
            record = {
                "schema_version": "roboguide.spatial-local-admission/v0.1",
                "handle": handle,
                "request_digest": identity,
                "request": body,
                "profile": profile,
                "native_directory": str(self._job_directory(profile)),
                "received_at_ms": time.time_ns() // 1_000_000,
                "backend_instance_id": self.identity["instance_id"],
            }
            save_json(self.records_root / f"{handle}.json", record)
            self.records[handle] = record
            evidence = directory(self.evidence_root / handle, True)
            if body["operation"] == "publish-map":
                self._verify_publication(record, evidence)
                return self.status(handle)
            job_path = evidence / "job.json"
            save_json(job_path, profile["job"])
            save_json(
                evidence / "dispatch-intent.json",
                {"request_digest": identity, "code_digest": code_digest()},
            )
            store = directory(self._job_directory(profile).parent, True)
            with (
                (evidence / "cli.stdout").open("xb") as output,
                (evidence / "cli.stderr").open("xb") as error,
            ):
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "roboguide_spatial_provider",
                        "--request",
                        str(job_path),
                        "--source-root",
                        str(self.source_root),
                        "--store",
                        str(store),
                    ],
                    stdout=output,
                    stderr=error,
                    start_new_session=True,
                )
            self._processes[handle] = process
            watcher = threading.Thread(target=self._watch, args=(handle, process), daemon=False)
            self._watchers.append(watcher)
            watcher.start()
            return {"execution_id": handle, "state": "ACCEPTED"}

    def _verify_publication(self, record: dict[str, Any], evidence: Path) -> None:
        """Verify original produced bytes for Node Service's remote CAS publication."""
        proof: dict[str, Any] = {"request_digest": record["request_digest"], "status": "rejected"}
        try:
            original = next(
                item
                for item in self.records.values()
                if item["profile"]["profile_id"] == record["profile"]["profile_id"]
                and item["request"]["operation"] == "build-map"
            )
            result = self._original_compute(original)
            if result["status"] != "completed":
                raise ValueError("original native computation has no verified complete result")
            artifact = result["artifact"]
            path = Path(record["request"]["artifact_path"])
            payload = read_source(
                self.output_root, str(path.relative_to(self.output_root)), artifact["byte_size"]
            )
            if digest(payload) != artifact["content_digest"]:
                raise ValueError("prepared output differs from its original native bytes")
            proof.update(
                status="verified",
                content_digest=artifact["content_digest"],
                byte_size=artifact["byte_size"],
            )
        except (ValueError, OSError, KeyError, StopIteration) as error:
            proof["reason"] = str(error)[:512]
        save_json(evidence / "publish-check.json", proof)

    def _watch(self, handle: str, process: subprocess.Popen[bytes]) -> None:
        """Retain actual owned CLI exit evidence without changing Controller execution lifecycle."""
        code = process.wait()
        with self._lock:
            save_json(
                self.evidence_root / handle / "cli-exit.json",
                {
                    "returncode": code,
                    "pid": process.pid,
                    "request_digest": self.records[handle]["request_digest"],
                },
            )
            self._processes.pop(handle, None)

    def cancel(self, handle: str) -> dict[str, Any]:
        """Admit interruption of an actual owned process; acceptance alone is never CANCELLED."""
        with self._lock:
            observed = self.status(handle)
            if observed["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                return {**observed, "cancel_accepted": False}
            process = self._processes.get(handle)
            if process is None or process.poll() is not None:
                return {**observed, "cancel_accepted": False}
            if not (self.evidence_root / handle / "cancel-request.json").exists():
                save_json(
                    self.evidence_root / handle / "cancel-request.json",
                    {"request_digest": self.records[handle]["request_digest"], "pid": process.pid},
                )
                process.send_signal(signal.SIGINT)
            return {"execution_id": handle, "state": "RUNNING", "cancel_accepted": True}


class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """Serve fixed HTTP routes through a private 0600 local socket, never a remote listener."""

    daemon_threads = False


def handler_type(service: SpatialService) -> type[BaseHTTPRequestHandler]:
    """Bind the immutable deployment service to bounded fixed Node workflow routes."""

    class Handler(BaseHTTPRequestHandler):
        """Use OS socket permissions for the trusted local Node/EAIOS process boundary."""

        def log_message(self, format: str, *args: object) -> None:
            """Keep canonical objectives, paths and local invocation bodies out of access logs."""

        def _body(self) -> dict[str, Any]:
            """Read one bounded JSON object with exact, unambiguous HTTP framing."""
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or self.headers.get("Transfer-Encoding") is not None:
                raise ValueError("one Content-Length and no Transfer-Encoding are required")
            length = int(lengths[0])
            if not 0 <= length <= 1024 * 1024:
                raise ValueError("Local How request exceeds its bound")
            self.connection.settimeout(10)
            content = self.rfile.read(length)
            if len(content) != length:
                raise ValueError("Local How request was truncated")
            value = json.loads(content or b"{}")
            if not isinstance(value, dict):
                raise ValueError("Local How request must be an object")
            return value

        def _respond(self, value: object, status: int = 200) -> None:
            """Return bounded JSON for one request without changing any backend authority."""
            payload = canonical(value)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
            self.close_connection = True

        def do_GET(self) -> None:  # noqa: N802 - stdlib HTTP dispatch.
            """Observe actual backend identity/readiness; no query launches a worker."""
            if self.path in {"/v1/health", "/v1/readiness"}:
                self._respond(service.readiness())
            elif self.path == "/v1/instance":
                self._respond(service.identity)
            else:
                self._respond({"error": "unknown readonly route"}, 404)

        def do_POST(self) -> None:  # noqa: N802 - stdlib HTTP dispatch.
            """Invoke one configured computation or read/interupt one original local handle."""
            try:
                body = self._body()
                if self.path == "/v1/executions":
                    self._respond(service.submit(body))
                elif self.path == "/v1/executions/status":
                    fields(body, {"execution_id"})
                    self._respond(service.status(body["execution_id"]))
                elif self.path == "/v1/executions/observe":
                    fields(body, {"attempt_id"})
                    self._respond(
                        service.status(
                            digest(
                                exact(body["attempt_id"], "attempt_id", 256).encode()
                            ).removeprefix("sha256:")
                        )
                    )
                elif self.path == "/v1/executions/cancel":
                    fields(body, {"execution_id"})
                    self._respond(service.cancel(body["execution_id"]))
                else:
                    self._respond({"error": "unknown Local How route"}, 404)
            except (ValueError, OSError, KeyError, TypeError) as error:
                self._respond({"error": str(error)[:512]}, 409)

    return Handler


def termination_requested(_number: int, _frame: FrameType | None) -> None:
    """Enter the same owned-worker cleanup for deployment SIGTERM and interactive SIGINT."""
    raise KeyboardInterrupt


def main() -> None:
    """Run the actual CPU provider on a private Unix socket without registering a Node or device."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--readonly", action="store_true")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, termination_requested)
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    sdk()
    service = SpatialService(
        args.root,
        args.source_root,
        args.output_root,
        read_json(args.config, 2 * 1024 * 1024),
        args.readonly,
    )
    socket_path = args.root / "local.sock"
    server: UnixServer | None = None
    inode: int | None = None
    try:
        if socket_path.exists() or socket_path.is_symlink():
            metadata = socket_path.lstat()
            if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.geteuid():
                raise ValueError("original socket path is not an owned stale socket")
            socket_path.unlink()
        server = UnixServer(str(socket_path), handler_type(service))
        os.chmod(socket_path, 0o600)
        inode = socket_path.lstat().st_ino
        sync_directory(args.root)
        print(
            json.dumps(
                {
                    "endpoint": "unix://" + str(socket_path),
                    "instance_id": service.identity["instance_id"],
                }
            ),
            flush=True,
        )
        server.serve_forever(poll_interval=0.1)
    finally:
        if server is not None:
            server.server_close()
        service.close()
        if inode is not None and socket_path.exists() and socket_path.lstat().st_ino == inode:
            socket_path.unlink()


if __name__ == "__main__":
    main()
