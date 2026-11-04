"""Robonix task integration with durable identities and explicit deployment admission."""

from __future__ import annotations

import argparse
import json
import socket
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import grpc

from .journal import Conflict, Journal, canonical
from .protocol import Liaison, execution_intent, task


class Adapter:
    """Bridge stable local handles to one bounded native Task and one scoped abort."""

    def __init__(
        self,
        directory: str | Path,
        client: Liaison,
        *,
        allowed_operations: Iterable[str] = (),
        enabled: bool = False,
        limits: dict[str, int] | None = None,
        task_results: bool = False,
        task_profile: str | None = None,
    ) -> None:
        """Freeze local operation and result semantics for the lifetime of the journal."""
        self.client, self.enabled = client, enabled
        self.allowed = frozenset(allowed_operations)
        self.task_results = task_results
        self.task_profile = task_profile
        self.journal = Journal(
            directory,
            {
                "atlas": client.atlas,
                "consumer": client.consumer,
                "user_id": client.user_id,
                "operations": sorted(self.allowed),
                "protocol": "dog2-c9c159e",
                "task_results": task_results,
                "task_profile": task_profile,
            },
            **(limits or {}),
        )
        self.workers = ThreadPoolExecutor(max_workers=3, thread_name_prefix="pilot-lab")
        self.lock = threading.RLock()
        self.jobs: set[Future[Any]] = set()
        self.abort_jobs: set[str] = set()
        self.closed = False

    def validate(self, body: dict[str, Any]) -> None:
        """Accept only the deployment's declared operation and task profile."""
        if not self.enabled:
            raise ValueError("dispatch disabled by deployment configuration")
        if set(body) != {"attempt_id", "invocation", "budget_ms"}:
            raise ValueError("request requires only attempt_id, invocation and budget_ms")
        budget = body["budget_ms"]
        if isinstance(budget, bool) or not isinstance(budget, int) or not 100 <= budget <= 300000:
            raise ValueError("budget_ms must be an integer in 100..300000")
        intent = execution_intent(body)
        op = intent.get("operation")
        if isinstance(op, str):
            operation = op
        elif isinstance(op, dict) and set(op) == {"namespace", "name", "version"}:
            operation = f"{op['namespace']}.{op['name']}@{op['version']}"
        else:
            raise ValueError("canonical operation required")
        if operation not in self.allowed:
            raise ValueError("operation has no frozen local binding")
        if (
            not isinstance(intent.get("objective"), str)
            or not 1 <= len(intent["objective"]) <= 8192
        ):
            raise ValueError("bounded objective required")
        if not isinstance(intent.get("parameters"), dict):
            raise ValueError("parameters object required")
        if self.task_profile is not None and intent["parameters"] != {"profile": self.task_profile}:
            raise ValueError("task profile differs from this deployment")
        canonical(body)

    def schedule(self, function: Callable[..., Any], *args: Any) -> None:
        """Track bounded workers so shutdown cannot close SQLite beneath an active reader."""
        with self.lock:
            if self.closed:
                raise RuntimeError("adapter closed")
            job = self.workers.submit(function, *args)
            self.jobs.add(job)
            job.add_done_callback(self.finished)

    def finished(self, job: Future[Any]) -> None:
        """Forget a joined operation without changing its durable execution facts."""
        with self.lock:
            self.jobs.discard(job)

    def submit(self, body: dict[str, Any]) -> dict[str, Any]:
        """Return the same durable record on retries without renewing time or native identity."""
        deadline = time.monotonic() + body.get("budget_ms", 0) / 1000
        with self.lock:
            if self.closed:
                raise RuntimeError("adapter closed")
            row, created = self.journal.admit(body, self.validate)
            if created:
                self.schedule(self.run, row["handle"], deadline)
            return self.status(row["handle"])

    def run(self, handle: str, deadline: float) -> None:
        """Cross the durable send boundary once and retain every native event until detach."""
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.journal.cancel(handle)
                return
            if not self.journal.begin(handle):
                return
            record = self.journal.get(handle)
            for raw, event, provider in self.client.stream(
                task(record, self.client.user_id),
                remaining,
                on_connect=lambda identity: self.before_send(handle, identity),
                send_guard=lambda: self.guard_send(handle),
                deadline=deadline,
            ):
                self.journal.append(handle, "task", raw, event, provider)
                if self.task_results and self.journal.finish_unscheduled_cancel(handle):
                    return
                if self.journal.get(handle)["cancel_requested"]:
                    self.cancel(handle)
            if self.task_results and self.journal.finish_task(handle):
                return
            if self.task_results and self.journal.finish_unscheduled_cancel(handle):
                return
            self.journal.mark_unknown(
                handle,
                "Pilot stream ended without an attributable terminal task result",
            )
        except Exception as exc:
            if (
                isinstance(exc, grpc.RpcError)
                and exc.code() == grpc.StatusCode.INTERNAL
                and "truncated JSON response" in (exc.details() or "")
            ):
                self.journal.record_planning_error(
                    handle,
                    {
                        "source": "native-rpc",
                        "error": "truncated JSON response",
                        "rpc_details": exc.details(),
                        "session_id": self.journal.get(handle)["session_id"],
                    },
                )
            self.journal.mark_unknown(
                handle, f"task stream unavailable: {type(exc).__name__}: {exc}"
            )
        finally:
            # Cleanup is stop-only, scoped to this session, with its own short I/O bound.
            record = self.journal.get(handle)
            if record["dispatched"] and record["state"] == "UNKNOWN":
                self.cancel(handle)

    def before_send(self, handle: str, identity: dict[str, Any]) -> None:
        """Pin the provider and suppress work if cancellation arrived during discovery."""
        self.journal.bind_provider(handle, identity)
        if self.journal.get(handle)["cancel_requested"]:
            self.journal.record_withheld_cancel(handle)
            raise ValueError("cancel requested before native Task; dispatch withheld")

    @contextmanager
    def guard_send(self, handle: str) -> Iterator[None]:
        """Serialize final cancellation validation and native call initiation against Cancel."""
        with self.lock:
            if self.closed or self.journal.get(handle)["cancel_requested"]:
                self.journal.record_withheld_cancel(handle)
                raise ValueError("native Task suppressed by shutdown or pre-send cancellation")
            yield

    def cancel(self, handle: str) -> dict[str, Any]:
        """Persist cancellation independently from physical result and schedule one abort."""
        with self.lock:
            needs_abort = self.journal.cancel(handle)
            row = self.journal.get(handle)
            observable = row["progress"].get("pilot_event_observed") or row["state"] == "UNKNOWN"
            if needs_abort and observable and not self.closed and handle not in self.abort_jobs:
                self.abort_jobs.add(handle)
                self.schedule(self.abort, handle)
            return self.status(handle)

    def abort(self, handle: str) -> None:
        """Send only the original session's abort, never treating its Completed as a result."""
        if not self.journal.begin_abort(handle):
            return
        record = self.journal.get(handle)
        try:
            provider = record["progress"].get("provider")
            if provider is None:
                raise ValueError(
                    "original Liaison registration was not established; abort withheld"
                )
            for raw, event, identity in self.client.stream(
                task(record, self.client.user_id, abort=True),
                3.0,
                expected_provider=provider,
            ):
                self.journal.append(handle, "abort", raw, event, identity)
            self.journal.mark_unknown(handle, "abort replied; physical stop remains unverified")
            if self.task_results:
                self.journal.finish_idle_planning_failure(handle)
        except Exception as exc:
            self.journal.mark_unknown(handle, f"abort outcome unknown: {type(exc).__name__}: {exc}")

    def status(self, handle: str) -> dict[str, Any]:
        """Expose local admission and progress without pretending they are RG satisfaction."""
        row = self.journal.get(handle)
        return {
            key: row[key]
            for key in (
                "handle",
                "attempt",
                "digest",
                "state",
                "detail",
                "session_id",
                "task_id",
                "created_ns",
                "dispatched",
                "cancel_requested",
                "abort_sent",
                "sequence",
                "progress",
            )
        }

    def readiness(self) -> dict[str, Any]:
        """Expose actual Liaison discovery and local journal ownership independently."""
        if not self.enabled or not self.task_results:
            return {"state": "UNAVAILABLE", "detail": "task delegation disabled"}
        with self.journal.lock:
            busy = self.journal.db.execute(
                "SELECT handle FROM runs WHERE state NOT IN ('SUCCEEDED','FAILED','CANCELED')"
            ).fetchone()
        if busy:
            return {"state": "UNAVAILABLE", "detail": "original task retains the slot"}
        try:
            provider = self.client.discover()
        except Exception as exc:
            return {"state": "UNAVAILABLE", "detail": f"Liaison unavailable: {type(exc).__name__}"}
        return {
            "state": "READY",
            "detail": "native task ingress available; Robonix owns task admission",
            "provider_id": provider.id,
            "registration_id": provider.registration_id,
        }

    def close(self) -> None:
        """Fence workers and detach streams; interrupted work remains Unknown, without replay."""
        with self.lock:
            self.closed = True
        self.client.close()
        self.workers.shutdown(wait=True)
        self.journal.close()


class Server(ThreadingHTTPServer):
    """Serve fixed loopback-only candidate routes with a bounded number of HTTP workers."""

    daemon_threads = False

    def __init__(self, port: int, adapter: Adapter) -> None:
        """Bind only the local interface and retain one supplied deployment adapter."""
        self.adapter = adapter
        self.capacity = threading.BoundedSemaphore(16)
        super().__init__(("127.0.0.1", port), Handler)

    def process_request(
        self, request: socket.socket | tuple[bytes, socket.socket], address: Any
    ) -> None:
        """Reject excess connections before creating a request thread."""
        if not self.capacity.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.capacity.release()
            raise

    def process_request_thread(
        self, request: socket.socket | tuple[bytes, socket.socket], address: Any
    ) -> None:
        """Return the HTTP permit even when parsing or the client connection fails."""
        try:
            super().process_request_thread(request, address)
        finally:
            self.capacity.release()


class Handler(BaseHTTPRequestHandler):
    """Bound request size and expose no arbitrary RPC, endpoint, SQL or shell routes."""

    server: Server

    def setup(self) -> None:
        """Bound idle request sockets before reading headers or bodies."""
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, format: str, *args: Any) -> None:
        """Avoid logging request bodies, user identity and native task text."""

    def reply(self, status: int, value: Any) -> None:
        """Write one finite bounded response and close this local HTTP connection."""
        body = canonical(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_GET(self) -> None:
        """Report process health separately from deliberately unavailable production readiness."""
        if self.path == "/v1/health":
            self.reply(
                200,
                {
                    "state": "ONLINE",
                    "detail": "candidate journal and HTTP process available",
                },
            )
        elif self.path == "/v1/readiness":
            self.reply(200, self.server.adapter.readiness())
        elif self.path == "/v1/executions/safe-state":
            self.reply(200, self.server.adapter.journal.no_effects_observations())
        else:
            self.reply(404, {"error": "route not found"})

    def do_POST(self) -> None:
        """Use exact execution handles and map admission errors to explicit client responses."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if self.headers.get("Transfer-Encoding") or not 0 < length <= 65536:
                raise ValueError("bounded Content-Length required")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("incomplete body")
            body = json.loads(
                raw,
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON")),
            )
            if not isinstance(body, dict):
                raise ValueError("JSON object required")
            app = self.server.adapter
            if self.path == "/v1/executions":
                result = app.submit(body)
            elif self.path == "/v1/executions/status":
                result = app.status(body["handle"])
            elif self.path == "/v1/executions/find":
                result = app.status(app.journal.find(body["attempt_id"])["handle"])
            elif self.path == "/v1/executions/cancel":
                result = app.cancel(body["handle"])
            else:
                self.reply(404, {"error": "route not found"})
                return
            self.reply(200, result)
        except Conflict as exc:
            self.reply(409, {"error": str(exc)})
        except KeyError as exc:
            self.reply(404, {"error": str(exc)})
        except (ValueError, TypeError) as exc:
            self.reply(400, {"error": str(exc)})
        except Exception as exc:
            self.reply(503, {"error": type(exc).__name__})


def main() -> None:
    """Serve a local task profile, enabled only by an explicit deployment option."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", required=True)
    parser.add_argument("--port", type=int, default=18115)
    parser.add_argument("--atlas", default="127.0.0.1:50051")
    parser.add_argument("--task-profile", default="local-task-v1")
    parser.add_argument("--enable-task-delegation", action="store_true")
    args = parser.parse_args()
    app = Adapter(
        args.journal,
        Liaison(args.atlas, "rg/local-task", "local:rg-local-task"),
        allowed_operations=("task.delegate@v1",),
        enabled=args.enable_task_delegation,
        task_results=True,
        task_profile=args.task_profile,
    )
    server = Server(args.port, app)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.close()


if __name__ == "__main__":
    main()
