"""C0-C fake Local EAIOS HTTP responder.

Serves the fixed routes a `roboguide-node` v0.7 config declares
(health, capability readiness, execution dispatch/status/cancel) with
deterministic terminal-completing responses, and records every received
request to a JSONL log so Node-side Local-How invocations become
inspectable evidence.

This is test-only simulation infrastructure for the C0-C evaluation
smoke; it owns no RoboGuide authority.
"""

from __future__ import annotations

import argparse
import datetime
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeEaiosHandler(BaseHTTPRequestHandler):
    """One deterministic Local EAIOS endpoint for one capability contract."""

    server_version = "c0c-fake-eaios/0.1"

    def log_message(self, fmt: str, *args: object) -> None:
        """Silence stderr access lines; requests go to the JSONL log."""

    def _record(self, body: str | None) -> None:
        """Appends one received request to the JSONL evidence log."""
        entry = {
            "received_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "client": self.client_address[0],
            "method": self.command,
            "path": self.path,
            "body": json.loads(body) if body else None,
        }
        with open(self.server.log_file, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _respond(self, status: int, payload: dict[str, object]) -> None:
        """Writes one JSON HTTP response to the connected node."""
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        """Serves health and per-capability readiness observations."""
        self._record(None)
        if self.path == "/v1/health":
            self._respond(200, {"state": "ONLINE", "detail": "c0c-fake-eaios"})
            return
        if self.path.startswith("/v1/capabilities/"):
            self._respond(200, {"state": "READY", "detail": "c0c-fake-eaios"})
            return
        self._respond(404, {"error": f"no fake route {self.path}"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        """Serves execution dispatch, status polling, and cancel requests."""
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else None
        self._record(body)
        if self.path == "/v1/executions":
            self._respond(
                200,
                {"execution_id": "c0c-fake-execution-1", "state": "ACCEPTED"},
            )
            return
        if self.path == "/v1/executions/status":
            self._respond(200, {"state": "COMPLETED", "detail": "c0c-fake-done"})
            return
        if self.path == "/v1/executions/cancel":
            self._respond(200, {"state": "CANCELLED", "detail": "c0c-fake-cancel"})
            return
        self._respond(404, {"error": f"no fake route {self.path}"})


def main() -> None:
    """Parses arguments and serves one fake Local EAIOS listener."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--log-file", required=True)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), FakeEaiosHandler)
    server.log_file = args.log_file
    print(f"c0c fake EAIOS listening on 127.0.0.1:{args.port}, log {args.log_file}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
