"""Loopback HTTP workflow facade consumed by the generic RoboGuide Node Service."""

from __future__ import annotations

import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol

from .model import IntegrationError

LOG = logging.getLogger("roboguide.habitat_local_eaios")
MAX_REQUEST_BYTES = 1024 * 1024


class WorkflowAdapter(Protocol):
    """The Local EAIOS surface one bridge endpoint serves."""

    def health(self) -> dict[str, object]:
        """Report process-local backend health."""

    def readiness(self) -> dict[str, object]:
        """Report exact operation readiness."""

    def accept(self, request: object) -> dict[str, object]:
        """Durably accept one exact invocation."""

    def dispatch(self, execution_id: str) -> None:
        """Idempotently schedule one accepted handle."""

    def status(self, request: object) -> dict[str, object]:
        """Return the durable local fact for one handle."""

    def cancel(self, request: object) -> dict[str, object]:
        """Accept cancellation intent for one handle."""


class HabitatBridgeServer(ThreadingHTTPServer):
    """Threaded loopback server retaining one shared Local EAIOS adapter."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], adapter: WorkflowAdapter) -> None:
        """Bind one fixed local address and attach the persistent adapter."""
        self.adapter = adapter
        super().__init__(address, HabitatBridgeHandler)


class HabitatBridgeHandler(BaseHTTPRequestHandler):
    """Serve the health, readiness, execute, status, and cancel workflow routes."""

    server: HabitatBridgeServer

    def do_GET(self) -> None:
        """Serve read-only health and exact-operation readiness routes."""
        if self.path == "/v1/health":
            self._respond(HTTPStatus.OK, self.server.adapter.health())
            return
        if self.path == "/v1/capabilities/mobility.navigate":
            self._respond(HTTPStatus.OK, self.server.adapter.readiness())
            return
        self._respond(HTTPStatus.NOT_FOUND, {"error": "route not found"})

    def do_POST(self) -> None:
        """Route one bounded JSON workflow request into the Local EAIOS adapter."""
        try:
            body = self._read_json()
            if self.path == "/v1/executions":
                response = self.server.adapter.accept(body)
                self._respond(HTTPStatus.OK, response)
                self.server.adapter.dispatch(str(response["execution_id"]))
                return
            elif self.path == "/v1/executions/status":
                response = self.server.adapter.status(body)
            elif self.path == "/v1/executions/cancel":
                response = self.server.adapter.cancel(body)
            else:
                self._respond(HTTPStatus.NOT_FOUND, {"error": "route not found"})
                return
            self._respond(HTTPStatus.OK, response)
        except IntegrationError as error:
            self._respond(HTTPStatus.CONFLICT, {"error": str(error)})
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            self._respond(HTTPStatus.BAD_REQUEST, {"error": f"invalid JSON: {error}"})

    def log_message(self, message: str, *args: Any) -> None:
        """Route bounded HTTP access messages through the adapter logger."""
        LOG.info("%s - %s", self.address_string(), message % args)

    def _read_json(self) -> object:
        """Read one bounded JSON body without accepting chunked or oversized input."""
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise IntegrationError("Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as error:
            raise IntegrationError("Content-Length is invalid") from error
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise IntegrationError("request body exceeds local limit")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _respond(self, status: HTTPStatus, body: dict[str, object]) -> None:
        """Write one deterministic JSON response and close the HTTP/1.1 request."""
        payload = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)
        self.wfile.flush()
