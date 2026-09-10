"""Serve the RoboGuide console and proxy read-only RoboGuide HTTP APIs.

The console is a static single-page application. Browsers refuse cross-origin
requests to the Controller (`apps/integration-server`) and Mission Service
(`apps/mission-service`) HTTP APIs because neither sends CORS headers, so this
process also exposes bounded same-origin reverse proxies:

- ``/proxy/controller/<path>`` forwards to the Controller endpoint, and
- ``/proxy/mission/<path>`` forwards to the Mission Service endpoint.

The proxy never mutates RoboGuide semantics: it forwards bytes verbatim, adds
no RoboGuide authority, and only relays requests the browser already issued.
Machine-specific endpoints stay outside Git through ``ROBOGUIDE_CONSOLE_*``
environment variables or explicit command-line flags.
"""

from __future__ import annotations

import argparse
import logging
import os
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

LOG = logging.getLogger("roboguide.console")

STATIC_ROOT = Path(__file__).resolve().parent
MAX_PROXY_BODY_BYTES = 1 << 20
"""Upper bound for a proxied request body; console commands are small JSON."""

PROXY_PREFIXES: dict[str, str] = {
    "/proxy/controller/": "controller",
    "/proxy/mission/": "mission",
}
"""Maps console proxy prefixes to logical upstream names."""

DEFAULT_ENDPOINTS: dict[str, str] = {
    "controller": "http://127.0.0.1:8080",
    "mission": "http://127.0.0.1:8070",
}
"""Default RoboGuide endpoints, overridable per environment or flag."""

ENV_ENDPOINT_VARS: dict[str, str] = {
    "controller": "ROBOGUIDE_CONSOLE_CONTROLLER",
    "mission": "ROBOGUIDE_CONSOLE_MISSION",
}
"""Environment variables that override default upstream endpoints."""


def resolve_endpoints(overrides: dict[str, str | None]) -> dict[str, str]:
    """Resolve effective upstream endpoints from flags, environment, and defaults.

    Args:
        overrides: Explicit ``--controller``/``--mission`` flag values; ``None``
            when a flag was not provided.

    Returns:
        A mapping from logical upstream name to base URL without trailing slash.
    """
    resolved: dict[str, str] = {}
    for name, default in DEFAULT_ENDPOINTS.items():
        value = overrides.get(name) or os.environ.get(ENV_ENDPOINT_VARS[name]) or default
        resolved[name] = value.rstrip("/")
    return resolved


class ConsoleRequestHandler(BaseHTTPRequestHandler):
    """Serve static console files and relay bounded RoboGuide API requests."""

    server: ConsoleHttpServer

    def log_message(self, format: str, *args: Any) -> None:
        """Route request metadata through logging without duplicating stderr noise."""
        LOG.debug("%s - %s", self.address_string(), format % args)

    def do_GET(self) -> None:
        """Answer static file requests and proxied Controller/Mission GET calls."""
        target = self._proxy_target()
        if target is not None:
            self._proxy(target, body=None)
            return
        self._serve_static(self._path())

    def do_POST(self) -> None:
        """Relay proxied console commands; static POST requests are rejected."""
        target = self._proxy_target()
        if target is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid Content-Length"})
            return
        if length < 0 or length > MAX_PROXY_BODY_BYTES:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "request body too large"})
            return
        self._proxy(target, body=self.rfile.read(length) if length else b"")

    def _path(self) -> str:
        """Return the URL-decoded request path without its query string."""
        from urllib.parse import unquote, urlsplit

        return unquote(urlsplit(self.path).path)

    def _proxy_target(self) -> str | None:
        """Match one proxy prefix and build the full upstream URL including query."""
        from urllib.parse import unquote, urlsplit

        split = urlsplit(self.path)
        path = unquote(split.path)
        for prefix, name in PROXY_PREFIXES.items():
            if path.startswith(prefix):
                suffix = path[len(prefix) :].lstrip("/")
                target = f"{self.server.endpoints[name]}/{suffix}"
                if split.query:
                    target = f"{target}?{split.query}"
                return target
        return None

    def _proxy(self, target: str, body: bytes | None) -> None:
        """Forward one request to an upstream and copy its JSON response back."""
        request = urllib.request.Request(target, data=body, method=self.command)
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=self.server.proxy_timeout) as reply:
                payload = reply.read()
                self._send_raw(reply.status, payload)
        except urllib.error.HTTPError as error:
            self._send_raw(error.code, error.read())
        except (urllib.error.URLError, TimeoutError) as error:
            LOG.warning("proxy target %s unreachable: %s", target, error)
            self._send_json(HTTPStatus.BAD_GATEWAY, {"error": f"upstream unreachable: {target}"})

    def _serve_static(self, path: str) -> None:
        """Send one static console file, defaulting to ``index.html``."""
        relative = path.lstrip("/") or "index.html"
        candidate = (STATIC_ROOT / relative).resolve()
        if not candidate.is_relative_to(STATIC_ROOT) or not candidate.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
        }.get(candidate.suffix, "application/octet-stream")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(candidate.stat().st_size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with candidate.open("rb") as static_file:
            self.wfile.write(static_file.read())

    def _send_raw(self, status: int, payload: bytes) -> None:
        """Write one upstream payload with explicit JSON framing headers."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, status: HTTPStatus, value: dict[str, Any]) -> None:
        """Write one small locally produced JSON response."""
        import json

        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._send_raw(status.value, payload)


class ConsoleHttpServer(ThreadingHTTPServer):
    """Carry immutable console composition settings for every handler thread."""

    def __init__(
        self,
        address: tuple[str, int],
        endpoints: dict[str, str],
        proxy_timeout: float,
    ) -> None:
        """Bind the listener and expose immutable upstream configuration."""
        super().__init__(address, ConsoleRequestHandler)
        self.endpoints = endpoints
        self.proxy_timeout = proxy_timeout


def _parser() -> argparse.ArgumentParser:
    """Build the console server command-line interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="127.0.0.1", help="listen address")
    parser.add_argument("--port", type=int, default=8095, help="listen port")
    parser.add_argument("--controller", default=None, help="Controller HTTP base URL")
    parser.add_argument("--mission", default=None, help="Mission Service HTTP base URL")
    parser.add_argument("--proxy-timeout", type=float, default=5.0, help="upstream timeout")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Start the console server and run until interrupted."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    arguments = _parser().parse_args(argv)
    endpoints = resolve_endpoints(
        {"controller": arguments.controller, "mission": arguments.mission}
    )
    server = ConsoleHttpServer((arguments.bind, arguments.port), endpoints, arguments.proxy_timeout)
    LOG.info(
        "console on http://%s:%s (controller=%s mission=%s)",
        arguments.bind,
        arguments.port,
        endpoints["controller"],
        endpoints["mission"],
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("shutdown requested")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
