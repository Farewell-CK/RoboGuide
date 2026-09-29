"""Durable loopback bridge from RoboGuide Node workflows to COHERENT."""

from __future__ import annotations

import hashlib
import json
import logging
import queue
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol

LOG = logging.getLogger("roboguide.coherent_local_eaios")
MAX_REQUEST_BYTES = 1024 * 1024
SUPPORTED_OPERATION = "coherent.execute-plan@v1"
SUPPORTED_TASK = "Merom_1_int_Task1"
TERMINAL_STATES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


class IntegrationError(RuntimeError):
    """Report a closed-boundary invocation or local execution failure."""


class WorkflowAdapter(Protocol):
    """Structural contract consumed by the shared loopback workflow server."""

    def health(self) -> dict[str, object]:
        """Return local process health without changing execution state."""
        ...

    def readiness(self) -> dict[str, object]:
        """Return readiness for the startup-approved operation set."""
        ...

    def accept(self, request: object) -> dict[str, object]:
        """Durably accept or recover one canonical execution request."""
        ...

    def dispatch(self, execution_id: str) -> None:
        """Schedule an accepted local handle without duplicate execution."""
        ...

    def status(self, request: object) -> dict[str, object]:
        """Return the current durable state for one local handle."""
        ...

    def cancel(self, request: object) -> dict[str, object]:
        """Return the adapter's explicit cancellation decision."""
        ...


@dataclass(frozen=True)
class CanonicalInvocation:
    """Validated semantic request retained intact at the Local EAIOS boundary."""

    mission_id: str
    task_id: str
    group_id: str
    role_id: str
    operation: str
    objective: str
    parameters: dict[str, object]
    resource_ids: tuple[str, ...]

    @classmethod
    def from_request(cls, request: object) -> CanonicalInvocation:
        """Validate the exact Node workflow request without accepting shell input."""
        body = _object(request, "request")
        if set(body) != {"invocation"}:
            raise IntegrationError("execute request must contain only invocation")
        invocation = _object(body["invocation"], "invocation")
        expected = {
            "mission_id",
            "task_id",
            "group_id",
            "role_id",
            "operation",
            "objective",
            "parameters",
            "resource_ids",
        }
        if set(invocation) != expected:
            raise IntegrationError("canonical invocation fields do not match the E2-S0 contract")
        operation = _string(invocation["operation"], "operation")
        if operation != SUPPORTED_OPERATION:
            raise IntegrationError(f"unsupported canonical operation {operation!r}")
        parameters = _object(invocation["parameters"], "parameters")
        if set(parameters) != {"task"}:
            raise IntegrationError("coherent.execute-plan@v1 requires exactly task")
        task = _string(parameters["task"], "parameters.task")
        if task != SUPPORTED_TASK:
            raise IntegrationError(f"unsupported COHERENT task {task!r}")
        raw_resources = invocation["resource_ids"]
        if not isinstance(raw_resources, list):
            raise IntegrationError("resource_ids must be an array")
        resources = tuple(_string(value, "resource_ids") for value in raw_resources)
        if len(resources) != len(set(resources)):
            raise IntegrationError("resource_ids contains duplicates")
        return cls(
            mission_id=_string(invocation["mission_id"], "mission_id"),
            task_id=_string(invocation["task_id"], "task_id"),
            group_id=_string(invocation["group_id"], "group_id"),
            role_id=_string(invocation["role_id"], "role_id"),
            operation=operation,
            objective=_string(invocation["objective"], "objective"),
            parameters={"task": task},
            resource_ids=resources,
        )

    def as_dict(self) -> dict[str, object]:
        """Return the canonical JSON shape used for persistence and audit."""
        return {
            "group_id": self.group_id,
            "mission_id": self.mission_id,
            "objective": self.objective,
            "operation": self.operation,
            "parameters": dict(self.parameters),
            "resource_ids": list(self.resource_ids),
            "role_id": self.role_id,
            "task_id": self.task_id,
        }

    def request_key(self) -> str:
        """Derive a stable idempotency key from the exact canonical invocation."""
        encoded = json.dumps(
            self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class ExecutionStore:
    """Persist local handles without claiming Controller execution authority."""

    def __init__(self, database: Path) -> None:
        """Create the database and fail interrupted local work on restart."""
        database.parent.mkdir(parents=True, exist_ok=True)
        self._database = database
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS executions (
                    execution_id TEXT PRIMARY KEY,
                    request_key TEXT NOT NULL UNIQUE,
                    invocation_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    local_outcome_json TEXT,
                    created_at_unix_ms INTEGER NOT NULL,
                    updated_at_unix_ms INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                UPDATE executions
                   SET state = 'FAILED',
                       detail = 'COHERENT bridge restarted before local execution terminated',
                       updated_at_unix_ms = ?
                 WHERE state IN ('ACCEPTED', 'RUNNING')
                """,
                (_now_ms(),),
            )

    def create_or_get(self, invocation: CanonicalInvocation) -> tuple[dict[str, object], bool]:
        """Create one idempotent accepted handle or return the retained row."""
        key = invocation.request_key()
        encoded = json.dumps(
            invocation.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM executions WHERE request_key = ?", (key,)
            ).fetchone()
            if row is not None:
                return self._decode(row), False
            now = _now_ms()
            execution_id = "coherent-" + key[:24]
            connection.execute(
                """
                INSERT INTO executions(
                    execution_id, request_key, invocation_json, state, detail,
                    created_at_unix_ms, updated_at_unix_ms
                ) VALUES (?, ?, ?, 'ACCEPTED', ?, ?, ?)
                """,
                (execution_id, key, encoded, "accepted by COHERENT Local EAIOS", now, now),
            )
            created = connection.execute(
                "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
            if created is None:
                raise IntegrationError("local execution disappeared after durable creation")
            return self._decode(created), True

    def get(self, execution_id: str) -> dict[str, object] | None:
        """Read one durable local execution."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
            return None if row is None else self._decode(row)

    def active(self) -> dict[str, object] | None:
        """Return the sole accepted or running execution."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM executions WHERE state IN ('ACCEPTED', 'RUNNING')"
            ).fetchall()
            if len(rows) > 1:
                raise IntegrationError("multiple active executions violate simulator ownership")
            return None if not rows else self._decode(rows[0])

    def mark_running(self, execution_id: str) -> None:
        """Persist the transition from accepted work to a started COHERENT run."""
        self._transition(execution_id, ("ACCEPTED",), "RUNNING", "COHERENT run started", None)

    def mark_terminal(
        self, execution_id: str, state: str, detail: str, outcome: dict[str, object]
    ) -> None:
        """Persist a terminal result backed by local process and goal evidence."""
        if state not in TERMINAL_STATES:
            raise IntegrationError(f"invalid terminal state {state!r}")
        self._transition(execution_id, ("ACCEPTED", "RUNNING"), state, detail, outcome)

    def _transition(
        self,
        execution_id: str,
        expected: tuple[str, ...],
        state: str,
        detail: str,
        outcome: dict[str, object] | None,
    ) -> None:
        """Atomically apply an allowed durable execution-state transition.

        Raises:
            IntegrationError: If the handle is unknown or its current state is not expected.
        """
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT state FROM executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
            if row is None:
                raise IntegrationError(f"unknown local execution {execution_id!r}")
            if str(row["state"]) not in expected:
                raise IntegrationError("invalid local execution transition")
            outcome_json = None if outcome is None else json.dumps(outcome, sort_keys=True)
            connection.execute(
                """
                UPDATE executions
                   SET state = ?, detail = ?, local_outcome_json = ?, updated_at_unix_ms = ?
                 WHERE execution_id = ?
                """,
                (state, detail[:2000], outcome_json, _now_ms(), execution_id),
            )

    def _connect(self) -> sqlite3.Connection:
        """Open one configured SQLite connection with named-row decoding enabled."""
        connection = sqlite3.connect(str(self._database), timeout=30.0)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, object]:
        """Decode one persisted execution row into the adapter's typed local representation."""
        invocation = CanonicalInvocation.from_request(
            {"invocation": json.loads(str(row["invocation_json"]))}
        )
        raw_outcome = row["local_outcome_json"]
        return {
            "execution_id": str(row["execution_id"]),
            "request_key": str(row["request_key"]),
            "invocation": invocation,
            "state": str(row["state"]),
            "detail": str(row["detail"]),
            "cancel_requested": bool(row["cancel_requested"]),
            "local_outcome": None if raw_outcome is None else json.loads(str(raw_outcome)),
            "created_at_unix_ms": int(row["created_at_unix_ms"]),
            "updated_at_unix_ms": int(row["updated_at_unix_ms"]),
        }


@dataclass(frozen=True)
class BackendConfig:
    """Startup-fixed deployment choices for one COHERENT installation."""

    container: str
    runner: str
    results_root: Path
    evidence_dir: Path
    timeout_s: int


class CoherentLocalAdapter:
    """Run one fixed COHERENT physical plan behind durable workflow handles."""

    def __init__(self, store: ExecutionStore, config: BackendConfig) -> None:
        """Start the sole worker for the startup-fixed COHERENT deployment."""
        self._store = store
        self._config = config
        self._jobs: queue.Queue[str | None] = queue.Queue()
        self._scheduled: set[str] = set()
        self._lock = threading.RLock()
        self._worker = threading.Thread(target=self._run_worker, daemon=True)
        self._worker.start()

    def close(self) -> None:
        """Request worker shutdown after current local work returns."""
        self._jobs.put(None)
        self._worker.join(timeout=5.0)

    def health(self) -> dict[str, object]:
        """Report process-local worker health without changing execution state."""
        state = "ONLINE" if self._worker.is_alive() else "OFFLINE"
        return {"state": state, "detail": "COHERENT execution worker is " + state.lower()}

    def readiness(self) -> dict[str, object]:
        """Report exact fixed-plan operation readiness."""
        ready = self._worker.is_alive()
        return {
            "state": "READY" if ready else "UNAVAILABLE",
            "detail": "fixed Merom Trio plan is available" if ready else "worker stopped",
            "operation": SUPPORTED_OPERATION,
        }

    def accept(self, request: object) -> dict[str, object]:
        """Durably accept one canonical invocation without duplicate execution."""
        invocation = CanonicalInvocation.from_request(request)
        with self._lock:
            active = self._store.active()
            if active is not None:
                if active["request_key"] == invocation.request_key():
                    return self._response(active)
                raise IntegrationError("COHERENT simulator already owns another execution")
            execution, _ = self._store.create_or_get(invocation)
            return self._response(execution)

    def dispatch(self, execution_id: str) -> None:
        """Idempotently schedule one accepted durable local handle."""
        with self._lock:
            execution = self._store.get(execution_id)
            if execution is None:
                raise IntegrationError(f"unknown local execution {execution_id!r}")
            if execution["state"] != "ACCEPTED" or execution_id in self._scheduled:
                return
            self._scheduled.add(execution_id)
            self._jobs.put(execution_id)

    def status(self, request: object) -> dict[str, object]:
        """Return the current durable local fact for one handle."""
        execution_id = _execution_id(request)
        execution = self._store.get(execution_id)
        if execution is None:
            raise IntegrationError(f"unknown local execution {execution_id!r}")
        LOG.info("status observed for %s: %s", execution_id, execution["state"])
        return self._response(execution)

    def cancel(self, request: object) -> dict[str, object]:
        """Reject cancellation explicitly without fabricating a terminal fact."""
        execution_id = _execution_id(request)
        execution = self._store.get(execution_id)
        if execution is None:
            raise IntegrationError(f"unknown local execution {execution_id!r}")
        response = self._response(execution)
        response["accepted"] = False
        response["detail"] = "cancellation is unsupported for the E2-S0 COHERENT bridge"
        return response

    def _run_worker(self) -> None:
        """Consume queued handles serially so one simulator owns the physical backend."""
        while True:
            execution_id = self._jobs.get()
            if execution_id is None:
                return
            try:
                self._execute(execution_id)
            except Exception as error:
                execution = self._store.get(execution_id)
                if execution is not None and execution["state"] not in TERMINAL_STATES:
                    self._store.mark_terminal(
                        execution_id,
                        "FAILED",
                        f"COHERENT backend failed: {error}",
                        {"error": str(error)},
                    )
            finally:
                with self._lock:
                    self._scheduled.discard(execution_id)

    def _execute(self, execution_id: str) -> None:
        """Run COHERENT once and persist a terminal fact backed by result artifacts.

        Raises:
            IntegrationError: If persisted data or generated evidence violates the adapter contract.
            subprocess.TimeoutExpired: If the physical runner exceeds its bounded timeout.
        """
        execution = self._store.get(execution_id)
        if execution is None:
            return
        invocation = execution["invocation"]
        if not isinstance(invocation, CanonicalInvocation):
            raise IntegrationError("stored invocation has an invalid type")
        task = str(invocation.parameters["task"])
        before = set(self._task_runs(task))
        self._config.evidence_dir.mkdir(parents=True, exist_ok=True)
        log_path = self._config.evidence_dir / (execution_id + ".log")
        command = f"{self._config.runner} {task} {self._config.timeout_s}"
        self._store.mark_running(execution_id)
        started_ms = _now_ms()
        with log_path.open("wb") as stream:
            completed = subprocess.run(
                ["docker", "exec", self._config.container, "bash", "-lc", command],
                stdout=stream,
                stderr=subprocess.STDOUT,
                timeout=self._config.timeout_s + 180,
                check=False,
            )
        run_dir = self._newest_run(task, before)
        summary = _read_json(run_dir / "summary.json")
        goal = _read_json(run_dir / "goal_check.json")
        succeeded = (
            completed.returncode == 0
            and summary.get("success") is True
            and goal.get("passed") is True
        )
        outcome: dict[str, object] = {
            "state": "COMPLETED" if succeeded else "FAILED",
            "task": task,
            "result_dir": str(run_dir),
            "bridge_log": str(log_path),
            "process_returncode": completed.returncode,
            "started_at_unix_ms": started_ms,
            "finished_at_unix_ms": _now_ms(),
            "summary": summary,
            "goal_check": goal,
        }
        state = str(outcome["state"])
        detail = (
            "COHERENT plan and physical goal completed"
            if succeeded
            else "COHERENT plan or physical goal failed"
        )
        self._store.mark_terminal(execution_id, state, detail, outcome)

    def _task_runs(self, task: str) -> list[Path]:
        """List stable result directories for one startup-approved COHERENT task."""
        root = self._config.results_root / task
        return sorted((path for path in root.glob("*") if path.is_dir()), key=lambda p: p.name)

    def _newest_run(self, task: str, before: set[Path]) -> Path:
        """Select the new result directory created by this execution attempt.

        Raises:
            IntegrationError: If the runner did not create a new evidence directory.
        """
        new_runs = [path for path in self._task_runs(task) if path not in before]
        if not new_runs:
            raise IntegrationError("COHERENT runner did not create a result directory")
        return max(new_runs, key=lambda path: path.stat().st_mtime_ns)

    @staticmethod
    def _response(execution: dict[str, object]) -> dict[str, object]:
        """Project durable local execution state into the workflow HTTP response schema."""
        invocation = execution["invocation"]
        if not isinstance(invocation, CanonicalInvocation):
            raise IntegrationError("stored invocation has an invalid type")
        return {
            "cancel_requested": execution["cancel_requested"],
            "detail": execution["detail"],
            "execution_id": execution["execution_id"],
            "group_id": invocation.group_id,
            "local_outcome": execution["local_outcome"],
            "mission_id": invocation.mission_id,
            "objective": invocation.objective,
            "operation": invocation.operation,
            "parameters": dict(invocation.parameters),
            "resource_ids": list(invocation.resource_ids),
            "role_id": invocation.role_id,
            "state": execution["state"],
            "task_id": invocation.task_id,
            "updated_at_unix_ms": execution["updated_at_unix_ms"],
        }


class BridgeServer(ThreadingHTTPServer):
    """Threaded loopback server retaining one Local EAIOS adapter."""

    daemon_threads = True

    adapter: WorkflowAdapter

    def __init__(self, address: tuple[str, int], adapter: WorkflowAdapter) -> None:
        """Bind one loopback address to the shared adapter instance."""
        self.adapter = adapter
        super().__init__(address, BridgeHandler)


class BridgeHandler(BaseHTTPRequestHandler):
    """Serve health, readiness, execute, status, and explicit cancel rejection."""

    server: BridgeServer

    def do_GET(self) -> None:
        """Serve read-only health and exact-operation readiness routes."""
        if self.path == "/v1/health":
            self._respond(HTTPStatus.OK, self.server.adapter.health())
        elif self.path.startswith("/v1/capabilities/"):
            self._respond(HTTPStatus.OK, self.server.adapter.readiness())
        else:
            self._respond(HTTPStatus.NOT_FOUND, {"error": "route not found"})

    def do_POST(self) -> None:
        """Route one bounded workflow request into the adapter."""
        try:
            body = self._read_json()
            if self.path == "/v1/executions":
                response = self.server.adapter.accept(body)
                self._respond(HTTPStatus.OK, response)
                self.server.adapter.dispatch(str(response["execution_id"]))
            elif self.path == "/v1/executions/status":
                self._respond(HTTPStatus.OK, self.server.adapter.status(body))
            elif self.path == "/v1/executions/cancel":
                self._respond(HTTPStatus.OK, self.server.adapter.cancel(body))
            else:
                self._respond(HTTPStatus.NOT_FOUND, {"error": "route not found"})
        except IntegrationError as error:
            self._respond(HTTPStatus.CONFLICT, {"error": str(error)})
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            self._respond(HTTPStatus.BAD_REQUEST, {"error": f"invalid JSON: {error}"})

    def log_message(self, message: str, *args: Any) -> None:
        """Route bounded access messages through the bridge logger."""
        LOG.info("%s - %s", self.address_string(), message % args)

    def _read_json(self) -> object:
        """Read one bounded JSON request body.

        Raises:
            IntegrationError: If the request length is absent or outside the local limit.
            UnicodeDecodeError: If the request is not valid UTF-8.
            json.JSONDecodeError: If the body is not valid JSON.
        """
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise IntegrationError("Content-Length is required")
        length = int(raw_length)
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise IntegrationError("request body exceeds local limit")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _respond(self, status: HTTPStatus, body: dict[str, object]) -> None:
        """Write one deterministic JSON response and close the HTTP connection."""
        payload = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)


def preflight(config: BackendConfig) -> None:
    """Fail before registration unless the fixed container and runner are available."""
    if config.timeout_s <= 0:
        raise IntegrationError("timeout must be positive")
    result = subprocess.run(
        ["docker", "exec", config.container, "test", "-x", config.runner],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise IntegrationError(
            "COHERENT container or runner unavailable: "
            + result.stdout.decode("utf-8", errors="replace")[:1000]
        )
    config.results_root.mkdir(parents=True, exist_ok=True)


def _object(value: object, field: str) -> dict[str, object]:
    """Validate and copy a JSON object whose keys are all strings."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise IntegrationError(f"{field} must be an object with string keys")
    return {str(key): item for key, item in value.items()}


def _string(value: object, field: str) -> str:
    """Validate one required non-empty string field without changing its value."""
    if not isinstance(value, str) or not value.strip():
        raise IntegrationError(f"{field} must be a non-empty string")
    return value


def _execution_id(request: object) -> str:
    """Extract the sole execution identifier allowed in status and cancel requests."""
    body = _object(request, "request")
    if set(body) != {"execution_id"}:
        raise IntegrationError("request must contain only execution_id")
    return _string(body["execution_id"], "execution_id")


def _read_json(path: Path) -> dict[str, object]:
    """Read a required JSON-object evidence file from the COHERENT result directory.

    Raises:
        IntegrationError: If the file is unreadable, invalid JSON, or not a JSON object.
    """
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise IntegrationError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise IntegrationError(f"{path} must contain a JSON object")
    return {str(key): item for key, item in value.items()}


def _now_ms() -> int:
    """Return the current Unix wall-clock time in integer milliseconds."""
    return time.time_ns() // 1_000_000
