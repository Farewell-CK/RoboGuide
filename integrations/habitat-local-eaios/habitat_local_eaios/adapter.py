"""Long-lived asynchronous adapter between Node workflows and Habitat Local EAIOS."""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

from .backend import MobilityBackend
from .model import SUPPORTED_OPERATION, CanonicalMobilityInvocation, IntegrationError
from .store import TERMINAL_STATES, ExecutionStore, StoredExecution

LOG = logging.getLogger("roboguide.habitat_local_eaios")


class HabitatLocalAdapter:
    """Coordinates one persistent Habitat backend and durable local execution handles."""

    def __init__(
        self,
        store: ExecutionStore,
        backend_factory: Callable[[], MobilityBackend],
        initialization_timeout_s: float,
    ) -> None:
        """Start the simulator-owning worker and wait for truthful readiness evidence."""
        if initialization_timeout_s <= 0:
            raise IntegrationError("initialization_timeout_s must be positive")
        self._store = store
        self._backend_factory = backend_factory
        self._jobs: queue.Queue[str | None] = queue.Queue()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._submit_lock = threading.RLock()
        self._scheduled_execution_ids: set[str] = set()
        self._initialization_error: str | None = None
        self._readiness_detail = "Habitat backend initialization pending"
        self._worker = threading.Thread(
            target=self._run_worker,
            name="habitat-local-eaios",
            daemon=True,
        )
        self._worker.start()
        if not self._ready.wait(initialization_timeout_s):
            self.close()
            raise IntegrationError("Habitat backend initialization timed out")
        if self._initialization_error is not None:
            self.close()
            raise IntegrationError(self._initialization_error)

    def close(self) -> None:
        """Stop the worker cooperatively and release its simulator on the owning thread."""
        if not self._stop.is_set():
            self._stop.set()
            self._jobs.put(None)
        if self._worker is not threading.current_thread():
            self._worker.join(timeout=30.0)

    def health(self) -> dict[str, object]:
        """Report process-local backend health without mutating execution state."""
        if self._initialization_error is not None:
            return {"detail": self._initialization_error, "state": "OFFLINE"}
        if not self._worker.is_alive():
            return {"detail": "Habitat execution worker is not alive", "state": "OFFLINE"}
        return {"detail": self._readiness_detail, "state": "ONLINE"}

    def readiness(self) -> dict[str, object]:
        """Report exact operation readiness from the initialized persistent backend."""
        healthy = self._initialization_error is None and self._worker.is_alive()
        return {
            "detail": self._readiness_detail,
            "operation": SUPPORTED_OPERATION,
            "state": "READY" if healthy else "UNAVAILABLE",
        }

    def accept(self, request: object) -> dict[str, object]:
        """Durably accept one exact invocation without starting simulator work."""
        invocation = CanonicalMobilityInvocation.from_request(request)
        with self._submit_lock:
            active = self._store.active_execution()
            if active is not None:
                if active["request_key"] == invocation.request_key():
                    return self._execution_response(active)
                raise IntegrationError("Habitat simulator already owns another active execution")
            execution, created = self._store.create_or_get(invocation)
            if created:
                LOG.info("accepted local execution %s", execution["execution_id"])
            return self._execution_response(execution)

    def dispatch(self, execution_id: str) -> None:
        """Idempotently schedule one durably accepted handle on the persistent worker."""
        with self._submit_lock:
            execution = self._store.get(execution_id)
            if execution is None:
                raise IntegrationError(f"unknown local execution {execution_id!r}")
            if execution["state"] != "ACCEPTED":
                return
            if execution_id in self._scheduled_execution_ids:
                return
            self._scheduled_execution_ids.add(execution_id)
            self._jobs.put(execution_id)

    def submit(self, request: object) -> dict[str, object]:
        """Accept and schedule one invocation for non-HTTP callers and tests."""
        response = self.accept(request)
        self.dispatch(str(response["execution_id"]))
        return response

    def status(self, request: object) -> dict[str, object]:
        """Return the current durable local fact for one stable execution handle."""
        execution_id = _execution_id(request)
        execution = self._store.get(execution_id)
        if execution is None:
            raise IntegrationError(f"unknown local execution {execution_id!r}")
        LOG.info("status observed for %s: %s", execution_id, execution["state"])
        return self._execution_response(execution)

    def cancel(self, request: object) -> dict[str, object]:
        """Accept cancellation intent without synthesizing a terminal CANCELLED fact."""
        execution_id = _execution_id(request)
        before = self._store.get(execution_id)
        if before is None:
            raise IntegrationError(f"unknown local execution {execution_id!r}")
        execution = self._store.request_cancel(execution_id)
        if execution is None:
            raise IntegrationError(f"unknown local execution {execution_id!r}")
        LOG.info(
            "cancellation request accepted for %s while local state is %s",
            execution_id,
            execution["state"],
        )
        response = self._execution_response(execution)
        response["accepted"] = before["state"] not in TERMINAL_STATES
        return response

    def _run_worker(self) -> None:
        """Own all Habitat initialization, stepping, cancellation, and shutdown on one thread."""
        backend: MobilityBackend | None = None
        try:
            backend = self._backend_factory()
            backend.initialize()
            self._readiness_detail = backend.readiness_detail()
        except Exception as error:
            if backend is not None:
                backend.close()
            self._initialization_error = f"Habitat backend unavailable: {error}"
            self._ready.set()
            return
        self._ready.set()
        try:
            while not self._stop.is_set():
                execution_id = self._jobs.get()
                if execution_id is None:
                    return
                try:
                    self._execute_job(backend, execution_id)
                finally:
                    with self._submit_lock:
                        self._scheduled_execution_ids.discard(execution_id)
        finally:
            backend.close()

    def _execute_job(self, backend: MobilityBackend, execution_id: str) -> None:
        """Run one queued invocation and convert backend completion into a durable local fact."""
        execution = self._store.get(execution_id)
        if execution is None:
            return

        def cancellation_requested() -> bool:
            """Check explicit cancellation or service shutdown between simulator steps."""
            return self._stop.is_set() or self._store.cancellation_requested(execution_id)

        def running(detail: str) -> None:
            """Publish RUNNING only after Habitat has initialized this execution episode."""
            current = self._store.get(execution_id)
            if current is not None and current["state"] == "ACCEPTED":
                self._store.mark_running(execution_id, detail)

        try:
            outcome = backend.execute(execution["invocation"], cancellation_requested, running)
            self._store.mark_terminal(execution_id, outcome)
            LOG.info("local execution %s terminated: %s", execution_id, outcome.state)
        except Exception as error:
            current = self._store.get(execution_id)
            if current is not None and current["state"] not in TERMINAL_STATES:
                self._store.mark_failed(execution_id, f"local Habitat execution failed: {error}")

    @staticmethod
    def _execution_response(execution: StoredExecution) -> dict[str, object]:
        """Expose local facts plus the exact semantic invocation retained for audit."""
        invocation = execution["invocation"]
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


def _execution_id(request: object) -> str:
    """Validate the fixed status/cancel request body and return its local handle."""
    if not isinstance(request, dict) or set(request) != {"execution_id"}:
        raise IntegrationError("request must contain only execution_id")
    value = request["execution_id"]
    if not isinstance(value, str) or not value:
        raise IntegrationError("execution_id must be a non-empty string")
    return value
