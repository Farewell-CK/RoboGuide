"""Configure and inspect the actual same-user Unix Memory backend without authored device facts."""

from __future__ import annotations

import http.client
import json
import socket
from typing import Any

from check_state_verifier_processes import request as http_request


class UnixConnection(http.client.HTTPConnection):
    """Speak bounded HTTP to the configured private Unix endpoint; no TCP fallback or retries."""

    def __init__(self, path: str) -> None:
        """Retain one actual socket path and a bounded per-request timeout."""
        super().__init__("localhost", timeout=6)
        self.path = path

    def connect(self) -> None:
        """Open only the original Unix socket under the calling user's actual OS credentials."""
        channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        channel.settimeout(self.timeout)
        try:
            channel.connect(self.path)
        except BaseException:
            channel.close()
            raise
        self.sock = channel


def request(
    origin: str, path: str, body: Any = None, *, method: str | None = None
) -> tuple[int, Any]:
    """Read actual backend HTTP/Unix responses without hiding failures or repeating effects."""
    if not origin.startswith("unix://"):
        return http_request(origin, path, body, method=method)
    connection = UnixConnection(origin.removeprefix("unix://"))
    try:
        encoded = None if body is None else json.dumps(body).encode()
        connection.request(
            method or ("GET" if body is None else "POST"),
            path,
            body=encoded,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        payload = response.read(1024 * 1024 + 1)
        if len(payload) > 1024 * 1024:
            raise ValueError("actual local backend response exceeds the inspection bound")
        return response.status, json.loads(payload)
    finally:
        connection.close()


def configuration(text: str) -> str:
    """Add explicit current-use negotiation and fixed original-command routes to a receiver."""
    text = text.replace("node-config/v0.10", "node-config/v0.11")
    text += '\n[memory_providers.use_lease]\nprofile = "roboguide.memory-use/shared-kernel/v0.1"\n'
    for name, method in (("control", "POST"), ("query", "GET")):
        text += f'''[memory_providers.use_lease.{name}]
id = "memory-use-{name}"
connection = "memory-http"
[memory_providers.use_lease.{name}.operation]
kind = "http"
method = "{method}"
path = "/v1/memory-use/{name}"
'''
        for field in ("schema", "control", "preparation_json", "receiver_budget", "imports"):
            text += f"""[[memory_providers.use_lease.{name}.request.bindings]]
target = "/{field}"
[memory_providers.use_lease.{name}.request.bindings.value]
kind = "pointer"
pointer = "/invocation/memory_use/{field}"
"""
    return text
