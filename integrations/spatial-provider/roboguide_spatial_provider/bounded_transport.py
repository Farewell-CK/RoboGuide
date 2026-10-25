"""Bound actual local HTTP workers without changing backend storage or execution authority."""

from __future__ import annotations

import socket
import socketserver
import threading
from typing import Any, cast


class BoundedWorkers(socketserver.ThreadingMixIn):
    """Retain actual local request workers through bounded input, response and close."""

    def configure_capacity(
        self, connection_limit: int, request_timeout_seconds: float = 30.0
    ) -> None:
        """Validate an explicit deployment limit before any listener is bound."""
        if type(connection_limit) is not int or not 1 <= connection_limit <= 1024:
            raise ValueError("local connection limit must be an integer in 1..1024")
        if (
            type(request_timeout_seconds) not in (int, float)
            or not 0 < request_timeout_seconds <= 900
        ):
            raise ValueError("local transport timeout must be positive and at most 900 seconds")
        self.request_timeout_seconds = request_timeout_seconds
        self.connection_limit = connection_limit
        self._connections = threading.BoundedSemaphore(connection_limit)
        self._transport_lock = threading.Lock()
        self._active_connections = 0
        self._rejected_connections = 0

    def transport_snapshot(self) -> dict[str, int]:
        """Observe original process capacity without granting content, use or job authority."""
        with self._transport_lock:
            return {
                "active": self._active_connections,
                "limit": self.connection_limit,
                "rejected_total": self._rejected_connections,
            }

    def process_request(self, request: Any, client_address: Any) -> None:
        """Refuse excess sockets before another request thread or backend admission."""
        if not self._connections.acquire(blocking=False):
            with self._transport_lock:
                self._rejected_connections = min(self._rejected_connections + 1, 2**64 - 1)
            cast(socketserver.BaseServer, self).shutdown_request(request)
            return
        with self._transport_lock:
            self._active_connections += 1
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._release_transport()
            raise

    def _release_transport(self) -> None:
        """Return only this original worker's permit after actual handling ends."""
        with self._transport_lock:
            self._active_connections -= 1
        self._connections.release()

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        """Bound header and socket lifetime; committed backend work retains ownership."""

        def close_original() -> None:
            """Close original I/O while preserving backend jobs and uncertain effects."""
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        timer = threading.Timer(self.request_timeout_seconds, close_original)
        timer.daemon = True
        try:
            request.settimeout(10.0)
            timer.start()
            super().process_request_thread(request, client_address)
        finally:
            timer.cancel()
            self._release_transport()
