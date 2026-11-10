"""Exercise optional photo routes on the real combined loopback HTTP handler."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import types
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch


def load_connector() -> Any:
    """Substitute only deployment camera/sensor imports, keeping the actual route composition."""
    camera = types.ModuleType("camera_adapter")
    camera.Captures = object  # type: ignore[attr-defined]
    camera.handler_type = lambda _captures: BaseHTTPRequestHandler  # type: ignore[attr-defined]
    observer = types.ModuleType("status_bridge")
    observer.Status = object  # type: ignore[attr-defined]
    observer.handler_type = lambda _observer: BaseHTTPRequestHandler  # type: ignore[attr-defined]
    artifacts = types.ModuleType("artifact_client")
    artifacts.upload_capture = lambda *_args, **_kwargs: None  # type: ignore[attr-defined]
    path = Path(__file__).resolve().parents[1] / "deploy/connector.py"
    spec = importlib.util.spec_from_file_location("connector_photo_route_fixture", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {"camera_adapter": camera, "status_bridge": observer, "artifact_client": artifacts},
    ):
        spec.loader.exec_module(module)
    return module


class Jobs:
    """Record calls to the supplied local job owner without accessing hardware or networks."""

    def __init__(self) -> None:
        """Keep the exact submitted body for route forwarding assertions."""
        self.submitted: dict[str, Any] | None = None

    def submit(self, body: dict[str, Any]) -> dict[str, Any]:
        """Return a fixed original handle as a real job store would."""
        self.submitted = body
        return {"handle": "original", "state": "PENDING"}

    def status(self, handle: str) -> dict[str, Any]:
        """Expose the queried handle without submitting another job."""
        return {"handle": handle, "state": "SUCCEEDED"}

    def health(self) -> dict[str, Any]:
        """Expose only local process readiness, with no claim about a robot."""
        return {"state": "ONLINE", "readiness_state": "READY"}

    def latest(self) -> dict[str, Any]:
        """Return a compact final projection for the Node State route."""
        return {"inspection": self.status("original")}


class PhotoRoutesTests(unittest.TestCase):
    """Check actual HTTP framing, opt-in boundaries and unchanged original request forwarding."""

    def test_routes_forward_exact_body_and_refuse_unknown_or_oversized_requests(self) -> None:
        """The fixed HTTP route cannot choose another operation or bypass request bounds."""
        connector = load_connector()
        jobs = Jobs()
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), connector.combined_handler(None, None, image_jobs=jobs)
        )
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        origin = "http://127.0.0.1:" + str(server.server_port)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def call(path: str, body: object | None = None) -> tuple[int, dict[str, Any]]:
            """Read one finite actual loopback response, retaining rejection status."""
            raw = None if body is None else json.dumps(body).encode()
            request = urllib.request.Request(
                origin + path, data=raw, headers={"Content-Type": "application/json"}
            )
            try:
                response = opener.open(request, timeout=3)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                return response.status, json.load(response)

        try:
            body = {"attempt_id": "fixture", "invocation": {"operation": "image.inspect@v1"}}
            self.assertEqual(
                call("/v1/image-inspections", body),
                (200, {"handle": "original", "state": "PENDING"}),
            )
            self.assertEqual(jobs.submitted, body)
            self.assertEqual(
                call("/v1/image-inspections/status", {"handle": "original"})[1]["state"],
                "SUCCEEDED",
            )
            self.assertEqual(call("/v1/image-inspections/health")[1]["readiness_state"], "READY")
            self.assertEqual(
                call("/v1/image-inspections/latest")[1]["inspection"]["handle"], "original"
            )
            self.assertEqual(
                call("/v1/image-inspections/status", {"handle": "original", "url": "forbidden"})[0],
                409,
            )
            self.assertEqual(call("/v1/image-inspections/unknown", {})[0], 404)
            self.assertEqual(call("/v1/image-inspections", {"padding": "x" * 65536})[0], 409)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
