"""Serve the explicit local file Memory contract over bounded loopback HTTP."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import signal
import socket
import socketserver
import sqlite3
import stat
import struct
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .active_map import ActiveMapLedger
from .bounded_transport import BoundedWorkers
from .content_references import ContentReferences
from .cost_metrics import scheduling_metrics
from .jobs import FileJobs
from .process_jobs import DEFAULT_ADDRESS_SPACE_BYTES, ProcessFileJobs
from .reclamation import ContentReclamation
from .store import MAX_METADATA_BYTES, MemoryStore, canonical
from .use_leases import UseLeases


class UnixServer(BoundedWorkers, socketserver.UnixStreamServer):
    """Serve only same-user local peers under the explicit shared-kernel budget profile."""

    daemon_threads = False
    block_on_close = True

    def __init__(
        self,
        address: str,
        handler: type[BaseHTTPRequestHandler],
        connection_limit: int = 128,
        request_timeout_seconds: float = 30.0,
    ) -> None:
        """Bound original same-user workers before binding the private local socket."""
        self.configure_capacity(connection_limit, request_timeout_seconds)
        super().__init__(address, handler)

    def verify_request(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: Any
    ) -> bool:
        """Check actual kernel peer credentials before reading a lease or content request."""
        if not isinstance(request, socket.socket):
            return False
        try:
            _, uid, _ = struct.unpack(
                "3i", request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )
            return bool(uid == os.getuid())
        except (OSError, AttributeError):
            return False


class BoundedHTTPServer(BoundedWorkers, ThreadingHTTPServer):
    """Bound only the explicit loopback reference backend, without Node or device inventory."""

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        connection_limit: int = 128,
        request_timeout_seconds: float = 30.0,
    ) -> None:
        """Retain the original worker limit before binding the actual local listener."""
        self.configure_capacity(connection_limit, request_timeout_seconds)
        super().__init__(address, handler)


def handler_type(
    store: MemoryStore,
    jobs: FileJobs | None = None,
    leases: UseLeases | None = None,
    active_maps: ActiveMapLedger | None = None,
    reclamation: ContentReclamation | None = None,
) -> type[BaseHTTPRequestHandler]:
    """Bind an already configured actual backend to fixed readonly and import routes."""
    references = ContentReferences(store) if reclamation is not None else None

    class Handler(BaseHTTPRequestHandler):
        """Accept bounded local JSON; clients never select arbitrary methods or backend paths."""

        def log_message(self, format: str, *args: object) -> None:
            """Avoid exposing complete manifest data in access logs."""

        def _body(self) -> dict[str, Any]:
            """Read one bounded JSON object with unambiguous request framing."""
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or self.headers.get("Transfer-Encoding") is not None:
                raise ValueError("one exact Content-Length is required")
            length = int(lengths[0])
            if not 0 <= length <= MAX_METADATA_BYTES:
                raise ValueError("metadata body exceeds its bound")
            self.connection.settimeout(10)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("metadata body was truncated")
            value = json.loads(raw or b"{}")
            if not isinstance(value, dict):
                raise ValueError("JSON object required")
            return value

        def _respond(self, status: int, value: object) -> None:
            """Return a framed bounded JSON response and close the exact request connection."""
            payload = canonical(value)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
            self.close_connection = True

        def do_GET(self) -> None:  # noqa: N802 - stdlib HTTP dispatch contract.
            """Read actual backend identity or query an original operation without mutation."""
            try:
                if self.path == "/v1/transport":
                    server = self.server
                    self._respond(
                        200,
                        server.transport_snapshot()
                        if isinstance(server, BoundedWorkers)
                        else {"available": False},
                    )
                elif leases is not None and self.path == "/v1/memory-use/query":
                    self._respond(200, leases.query(self._body()))
                elif reclamation is not None and self.path == "/v1/content/reclamation":
                    if not isinstance(self.server, UnixServer):
                        raise ValueError(
                            "content reclamation requires the actual private Unix transport"
                        )
                    self._respond(200, reclamation.observe(self._body()["command_id"]))
                elif references is not None and self.path == "/v1/content/references":
                    if not isinstance(self.server, UnixServer):
                        raise ValueError(
                            "content references require the actual private Unix transport"
                        )
                    self._respond(200, references.observe(self._body()))
                elif jobs is not None and self.path == "/v1/file-jobs/health":
                    self._respond(200, jobs.health())
                elif jobs is not None and self.path == "/v1/file-jobs/status":
                    self._respond(200, jobs.status(self._body()["execution_id"]))
                elif isinstance(jobs, ProcessFileJobs) and self.path == "/v1/file-jobs/quota":
                    self._respond(200, jobs.quota_evidence(self._body()["execution_id"]))
                elif jobs is not None and self.path == "/v1/file-jobs/safe-state":
                    self._respond(200, jobs.safe_state())
                elif self.path == "/v1/scheduling-cost":
                    self._respond(200, scheduling_metrics())
                elif self.path == "/v1/instance":
                    self._respond(200, store.identity())
                elif self.path == "/v1/import-query":
                    self._respond(200, {"result": store.query(self._body()["memory_import"])})
                elif self.path == "/v1/availability":
                    self._respond(
                        200, {"availability": store.availability(self._body()["memory_import"])}
                    )
                elif self.path == "/v1/discover":
                    self._respond(200, {"manifests": store.discover()})
                elif self.path == "/v1/export":
                    self._respond(
                        200, {"artifact_path": store.export(self._body()["memory_manifest"])}
                    )
                elif active_maps is not None and self.path == "/v1/active-map/state":
                    self._respond(200, active_maps.state())
                else:
                    self._respond(404, {"error": "unknown readonly Memory route"})
            except (ValueError, KeyError, TypeError, OSError) as error:
                self._respond(409, {"error": str(error)[:512]})
            except sqlite3.Error:
                self._respond(503, {"error": "original Memory storage outcome is unavailable"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib HTTP dispatch contract.
            """Import an exact actual staged file once under its complete original identity."""
            try:
                if references is not None and self.path == "/v1/content/references":
                    if not isinstance(self.server, UnixServer):
                        raise ValueError(
                            "content references require the actual private Unix transport"
                        )
                    self._respond(200, references.apply(self._body()))
                    return
                if reclamation is not None and self.path in {
                    "/v1/content/tombstone",
                    "/v1/content/collect",
                }:
                    if not isinstance(self.server, UnixServer):
                        raise ValueError(
                            "content reclamation requires the actual private Unix transport"
                        )
                    body = self._body()
                    result = (
                        reclamation.tombstone(body)
                        if self.path.endswith("/tombstone")
                        else reclamation.collect(body)
                    )
                    self._respond(200, result)
                    return
                if leases is not None and self.path == "/v1/memory-use/control":
                    self._respond(200, leases.apply(self._body()))
                    return
                if jobs is not None and self.path == "/v1/file-jobs/compute":
                    body = self._body()
                    if set(body) == {"invocation", "attempt_id"}:
                        invocation = body["invocation"]
                        if not isinstance(invocation, dict) or "execution_id" in invocation:
                            raise ValueError(
                                "original software invocation cannot replace attempt identity"
                            )
                        body = {**invocation, "execution_id": body["attempt_id"]}
                    self._respond(200, jobs.submit(body))
                    return
                if jobs is not None and self.path == "/v1/file-jobs/cancel":
                    self._respond(200, jobs.cancel(self._body()["execution_id"]))
                    return
                if active_maps is not None and self.path == "/v1/active-map/select":
                    self._respond(200, active_maps.select(self._body()))
                    return
                if active_maps is not None and self.path == "/v1/active-map/query":
                    self._respond(200, active_maps.query(self._body()))
                    return
                if active_maps is not None and self.path == "/v1/active-map/release":
                    self._respond(200, active_maps.release(self._body()))
                    return
                if active_maps is not None and self.path == "/v1/active-map/gc":
                    self._respond(200, active_maps.gc(self._body()))
                    return
                if active_maps is not None and self.path == "/v1/active-map/handoff/prepare":
                    self._respond(200, active_maps.prepare_handoff(self._body()))
                    return
                if active_maps is not None and self.path == "/v1/active-map/handoff/commit":
                    self._respond(200, active_maps.commit_handoff(self._body()))
                    return
                if self.path != "/v1/import":
                    self._respond(404, {"error": "unknown Memory operation"})
                    return
                body = self._body()
                self._respond(
                    200,
                    store.import_artifact(
                        body["memory_import"], body["memory_manifest"], body["staged_artifact"]
                    ),
                )
            except (ValueError, KeyError, TypeError, OSError) as error:
                self._respond(409, {"error": str(error)[:512]})
            except sqlite3.Error:
                self._respond(503, {"error": "original Memory storage outcome is unavailable"})

    return Handler


def main() -> None:
    """Run an explicitly configured local file backend without creating any robot registration."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--provider-id", required=True)
    parser.add_argument("--input-root", required=True, type=Path, action="append")
    parser.add_argument("--max-bytes", type=int, default=256 * 1024 * 1024)
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=0)
    parser.add_argument("--connection-limit", type=int, default=128)
    parser.add_argument("--connection-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--export-config", type=Path)
    parser.add_argument("--enable-file-jobs", action="store_true")
    parser.add_argument("--worker-processes", action="store_true")
    parser.add_argument("--max-job-seconds", type=int, default=5)
    parser.add_argument("--max-job-rounds", type=int, default=256)
    parser.add_argument("--worker-cpu-seconds", type=int)
    parser.add_argument("--worker-address-space-bytes", type=int)
    parser.add_argument("--enable-use-leases", action="store_true")
    parser.add_argument("--listen-unix", action="store_true")
    parser.add_argument("--export-directory", type=Path)
    parser.add_argument("--enable-active-map", action="store_true")
    parser.add_argument("--enable-content-reclamation", action="store_true")
    parser.add_argument(
        "--enable-snapshot-references",
        action="store_true",
        help="explicitly upgrade the original Memory store to snapshot-reference SQL v4",
    )
    parser.add_argument(
        "--enable-scoped-typed-maps",
        action="store_true",
        help=(
            "retain original typed scoped preparations under Memory SQL v5, "
            "including original snapshot references"
        ),
    )
    args = parser.parse_args()
    if not 0 < args.connection_timeout_seconds <= 900:
        parser.error("--connection-timeout-seconds must be positive and at most 900")
    if not 1 <= args.connection_limit <= 1024:
        parser.error("--connection-limit must be an integer in 1..1024")
    if not ipaddress.ip_address(args.listen_host).is_loopback:
        parser.error("this Local EAIOS facade requires a literal loopback address")
    if args.enable_use_leases and not args.listen_unix:
        parser.error("Memory-use leases require the explicit cohosted private Unix profile")
    if args.worker_processes and not args.enable_file_jobs:
        parser.error("owned process workers require explicit file jobs")
    if args.worker_cpu_seconds is not None and not args.worker_processes:
        parser.error("worker CPU quota requires the explicit owned-process profile")
    if args.worker_address_space_bytes is not None and not args.worker_processes:
        parser.error("worker address-space quota requires the explicit owned-process profile")
    if args.enable_active_map and (not args.enable_use_leases or not args.listen_unix):
        parser.error(
            "active-map requires current-use leases on the explicit cohosted private Unix profile"
        )
    if args.listen_unix and args.listen_port:
        parser.error("the Unix profile cannot bind a TCP port")
    if args.enable_content_reclamation and not (
        args.listen_unix and args.enable_use_leases and args.enable_file_jobs
    ):
        parser.error("content reclamation requires actual Unix, use-lease and file-job owners")
    store = MemoryStore(
        args.root,
        args.provider_id,
        tuple(args.input_root),
        args.max_bytes,
        args.export_config,
        args.export_directory,
        args.enable_snapshot_references,
        args.enable_scoped_typed_maps,
    )
    leases: UseLeases | None = None
    jobs: FileJobs | None = None
    active_maps: ActiveMapLedger | None = None
    socket_path = store.root / "local.sock"
    socket_inode = None
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def terminate(_signal: int, _frame: object) -> None:
        """Enter owned request-worker cleanup; acceptance is separate from actual job stop."""
        if jobs is not None:
            jobs.stop()
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    try:
        leases = UseLeases(store) if args.enable_use_leases else None
        if args.enable_file_jobs:
            jobs = (
                ProcessFileJobs(
                    store,
                    leases,
                    args.max_job_seconds,
                    args.max_job_rounds,
                    DEFAULT_ADDRESS_SPACE_BYTES
                    if args.worker_address_space_bytes is None
                    else args.worker_address_space_bytes,
                    args.worker_cpu_seconds,
                )
                if args.worker_processes
                else FileJobs(store, leases, args.max_job_seconds, args.max_job_rounds)
            )
        active_maps = (
            ActiveMapLedger(store.root / "active-map", store, leases)
            if args.enable_active_map
            else None
        )
        reclamation = (
            ContentReclamation(store, leases, jobs, active_maps)
            if args.enable_content_reclamation and leases is not None and jobs is not None
            else None
        )
        server: ThreadingHTTPServer | UnixServer
        if args.listen_unix:
            metadata = store.root.stat()
            if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
                raise ValueError("Unix Memory backend root must be private and owned by this user")
            if socket_path.exists() or socket_path.is_symlink():
                metadata = socket_path.lstat()
                if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.getuid():
                    raise ValueError("existing Local Memory socket is not an owned socket")
                socket_path.unlink()
            server = UnixServer(
                str(socket_path),
                handler_type(store, jobs, leases, active_maps, reclamation),
                args.connection_limit,
                args.connection_timeout_seconds,
            )
            socket_path.chmod(0o600)
            socket_inode = socket_path.stat().st_ino
            endpoint = "unix://" + str(socket_path)
        else:
            server = BoundedHTTPServer(
                (args.listen_host, args.listen_port),
                handler_type(store, jobs, leases, active_maps),
                args.connection_limit,
                args.connection_timeout_seconds,
            )
            endpoint = f"http://{args.listen_host}:{server.server_port}"
        server.daemon_threads = False
        print(json.dumps({"endpoint": endpoint}), flush=True)
        try:
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        if jobs is not None:
            jobs.close()
        if active_maps is not None:
            active_maps.close()
        if leases is not None:
            leases.close()
        if (
            socket_inode is not None
            and socket_path.exists()
            and socket_path.stat().st_ino == socket_inode
        ):
            socket_path.unlink()
        store.close()


if __name__ == "__main__":
    main()
