"""Persistent process boundary around the Habitat/EMOS simulator backend."""

from __future__ import annotations

import multiprocessing
import time
from collections.abc import Callable
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from typing import Any

from .backend import HabitatBackendConfig, LocalExecutionOutcome
from .model import CanonicalMobilityInvocation, IntegrationError

_POLL_INTERVAL_S = 0.02
_SHUTDOWN_TIMEOUT_S = 30.0


class HabitatProcessBackend:
    """Keep Habitat in one reusable child process while the HTTP bridge stays responsive."""

    def __init__(
        self,
        config: HabitatBackendConfig,
        startup_timeout_s: float,
        backend_class: type[Any],
    ) -> None:
        """Retain immutable deployment config without importing Habitat in the HTTP process."""
        if startup_timeout_s <= 0:
            raise IntegrationError("startup_timeout_s must be positive")
        self._config = config
        self._startup_timeout_s = startup_timeout_s
        self._backend_class = backend_class
        self._connection: Connection | None = None
        self._process: BaseProcess | None = None
        self._readiness_detail = "Habitat simulator process is not initialized"

    def initialize(self) -> None:
        """Start one persistent simulator process and wait for explicit readiness evidence."""
        if self._process is not None:
            return
        context = multiprocessing.get_context("spawn")
        parent_connection, child_connection = context.Pipe()
        process = context.Process(
            target=_run_habitat_process,
            args=(child_connection, self._config, self._backend_class),
            name="habitat-local-eaios-simulator",
        )
        process.start()
        child_connection.close()
        self._connection = parent_connection
        self._process = process
        deadline = time.monotonic() + self._startup_timeout_s
        while time.monotonic() < deadline:
            if parent_connection.poll(_POLL_INTERVAL_S):
                kind, payload = _receive_message(parent_connection)
                if kind == "READY":
                    self._readiness_detail = _require_text(payload, "readiness detail")
                    return
                if kind == "INITIALIZATION_FAILED":
                    raise IntegrationError(_require_text(payload, "initialization failure"))
                raise IntegrationError(f"unexpected Habitat initialization message {kind!r}")
            if not process.is_alive():
                raise IntegrationError("Habitat simulator process exited during initialization")
        raise IntegrationError("Habitat simulator process initialization timed out")

    def execute(
        self,
        invocation: CanonicalMobilityInvocation,
        cancellation_requested: Callable[[], bool],
        running: Callable[[str], None],
    ) -> LocalExecutionOutcome:
        """Invoke one operation and relay running, cancellation, and terminal evidence over IPC."""
        connection, process = self._require_initialized()
        connection.send(("EXECUTE", invocation))
        cancel_sent = False
        while True:
            if cancellation_requested() and not cancel_sent:
                connection.send(("CANCEL", None))
                cancel_sent = True
            if connection.poll(_POLL_INTERVAL_S):
                kind, payload = _receive_message(connection)
                if kind == "RUNNING":
                    running(_require_text(payload, "running detail"))
                    continue
                if kind == "TERMINAL":
                    if not isinstance(payload, LocalExecutionOutcome):
                        raise IntegrationError("Habitat terminal message has invalid outcome")
                    return payload
                if kind == "EXECUTION_FAILED":
                    raise IntegrationError(_require_text(payload, "execution failure"))
                raise IntegrationError(f"unexpected Habitat execution message {kind!r}")
            if not process.is_alive():
                raise IntegrationError("Habitat simulator process exited during execution")

    def readiness_detail(self) -> str:
        """Return readiness reported by the initialized persistent simulator process."""
        return self._readiness_detail

    def close(self) -> None:
        """Request clean simulator shutdown and bound cleanup if the child is unresponsive."""
        connection = self._connection
        process = self._process
        self._connection = None
        self._process = None
        if connection is not None:
            try:
                connection.send(("CLOSE", None))
            except (BrokenPipeError, EOFError, OSError):
                pass
            connection.close()
        if process is not None:
            process.join(timeout=_SHUTDOWN_TIMEOUT_S)
            if process.is_alive():
                process.terminate()
                process.join(timeout=_SHUTDOWN_TIMEOUT_S)

    def _require_initialized(self) -> tuple[Connection, BaseProcess]:
        """Return live IPC state or reject execution before simulator readiness."""
        if self._connection is None or self._process is None or not self._process.is_alive():
            raise IntegrationError("Habitat simulator process is not initialized")
        return self._connection, self._process


def _run_habitat_process(
    connection: Connection,
    config: HabitatBackendConfig,
    backend_class: type[Any],
) -> None:
    """Own the configured backend environment and execute commands in one child process."""
    backend = backend_class(config)
    close_requested = False
    try:
        try:
            backend.initialize()
            connection.send(("READY", backend.readiness_detail()))
        except Exception as error:
            connection.send(("INITIALIZATION_FAILED", str(error)))
            return
        while not close_requested:
            try:
                kind, payload = _receive_message(connection)
            except EOFError:
                return
            if kind == "CLOSE":
                return
            if kind == "CANCEL":
                continue
            if kind != "EXECUTE" or not isinstance(payload, CanonicalMobilityInvocation):
                connection.send(("EXECUTION_FAILED", "invalid simulator process command"))
                continue
            cancelled = False

            def cancellation_requested() -> bool:
                """Consume cancellation or shutdown commands between Habitat simulator steps."""
                nonlocal cancelled, close_requested
                while connection.poll():
                    nested_kind, _ = _receive_message(connection)
                    if nested_kind == "CANCEL":
                        cancelled = True
                    elif nested_kind == "CLOSE":
                        cancelled = True
                        close_requested = True
                    else:
                        raise IntegrationError(
                            f"unexpected concurrent simulator command {nested_kind!r}"
                        )
                return cancelled

            def running(detail: str) -> None:
                """Relay true local execution start without deriving a terminal outcome."""
                connection.send(("RUNNING", detail))

            try:
                outcome = backend.execute(payload, cancellation_requested, running)
                connection.send(("TERMINAL", outcome))
            except Exception as error:
                connection.send(("EXECUTION_FAILED", str(error)))
    finally:
        backend.close()
        connection.close()


def _receive_message(connection: Connection) -> tuple[str, object]:
    """Receive and validate one fixed two-field local IPC message."""
    value = connection.recv()
    if not isinstance(value, tuple) or len(value) != 2 or not isinstance(value[0], str):
        raise IntegrationError("Habitat process emitted an invalid IPC message")
    return value[0], value[1]


def _require_text(value: object, field: str) -> str:
    """Require a non-empty local IPC text payload."""
    if not isinstance(value, str) or not value:
        raise IntegrationError(f"Habitat process {field} is invalid")
    return value
