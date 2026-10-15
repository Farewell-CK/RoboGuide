"""Serve the explicit local file Memory contract over bounded loopback HTTP."""

from __future__ import annotations

import argparse
import ipaddress
import json
import signal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .jobs import FileJobs
from .store import MAX_METADATA_BYTES, MemoryStore, canonical


def handler_type(store: MemoryStore, jobs: FileJobs | None = None) -> type[BaseHTTPRequestHandler]:
    """Bind an already configured actual backend to fixed readonly and import routes."""

    class Handler(BaseHTTPRequestHandler):
        """Accept bounded local JSON; clients never select arbitrary methods or backend paths."""

        def log_message(self, format: str, *args: object) -> None:
            """Avoid exposing complete manifest data in access logs."""

        def _body(self) -> dict[str, Any]:
            """Read one bounded JSON object with unambiguous request framing."""
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or self.headers.get("Transfer-Encoding") is not None:
                raise ValueError("one exact Content-Length is required")
            length = int(lengths[0])
            if not 0 <= length <= MAX_METADATA_BYTES:
                raise ValueError("metadata body exceeds its bound")
            self.connection.settimeout(10)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("metadata body was truncated")
            value = json.loads(raw or b"{}")
            if not isinstance(value, dict):
                raise ValueError("JSON object required")
            return value

        def _respond(self, status: int, value: object) -> None:
            """Return a framed bounded JSON response and close the exact request connection."""
            payload = canonical(value)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
            self.close_connection = True

        def do_GET(self) -> None:  # noqa: N802 - stdlib HTTP dispatch contract.
            """Read actual backend identity or query an original operation without mutation."""
            try:
                if jobs is not None and self.path == "/v1/file-jobs/health":
                    self._respond(200, jobs.health())
                elif jobs is not None and self.path == "/v1/file-jobs/status":
                    self._respond(200, jobs.status(self._body()["execution_id"]))
                elif self.path == "/v1/instance":
                    self._respond(200, store.identity())
                elif self.path == "/v1/import-query":
                    self._respond(200, {"result": store.query(self._body()["memory_import"])})
                elif self.path == "/v1/availability":
                    self._respond(
                        200, {"availability": store.availability(self._body()["memory_import"])}
                    )
                elif self.path == "/v1/discover":
                    self._respond(200, {"manifests": store.discover()})
                elif self.path == "/v1/export":
                    self._respond(
                        200, {"artifact_path": store.export(self._body()["memory_manifest"])}
                    )
                else:
                    self._respond(404, {"error": "unknown readonly Memory route"})
            except (ValueError, KeyError, TypeError, OSError) as error:
                self._respond(409, {"error": str(error)[:512]})

        def do_POST(self) -> None:  # noqa: N802 - stdlib HTTP dispatch contract.
            """Import an exact actual staged file once under its complete original identity."""
            try:
                if jobs is not None and self.path == "/v1/file-jobs/compute":
                    body = self._body()
                    if set(body) == {"invocation", "attempt_id"}:
                        invocation = body["invocation"]
                        if not isinstance(invocation, dict) or "execution_id" in invocation:
                            raise ValueError(
                                "original software invocation cannot replace attempt identity"
                            )
                        body = {**invocation, "execution_id": body["attempt_id"]}
                    self._respond(200, jobs.compute(body))
                    return
                if jobs is not None and self.path == "/v1/file-jobs/cancel":
                    self._respond(200, jobs.cancel(self._body()["execution_id"]))
                    return
                if self.path != "/v1/import":
                    self._respond(404, {"error": "unknown Memory operation"})
                    return
                body = self._body()
                self._respond(
                    200,
                    store.import_artifact(
                        body["memory_import"], body["memory_manifest"], body["staged_artifact"]
                    ),
                )
            except (ValueError, KeyError, TypeError, OSError) as error:
                self._respond(409, {"error": str(error)[:512]})

    return Handler


def main() -> None:
    """Run an explicitly configured local file backend without creating any robot registration."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--provider-id", required=True)
    parser.add_argument("--input-root", required=True, type=Path, action="append")
    parser.add_argument("--max-bytes", type=int, default=256 * 1024 * 1024)
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=0)
    parser.add_argument("--export-config", type=Path)
    parser.add_argument("--enable-file-jobs", action="store_true")
    parser.add_argument("--export-directory", type=Path)
    args = parser.parse_args()
    if not ipaddress.ip_address(args.listen_host).is_loopback:
        parser.error("this Local EAIOS facade requires a literal loopback address")
    store = MemoryStore(
        args.root,
        args.provider_id,
        tuple(args.input_root),
        args.max_bytes,
        args.export_config,
        args.export_directory,
    )
    jobs = FileJobs(store) if args.enable_file_jobs else None
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def terminate(_signal: int, _frame: object) -> None:
        """Enter owned request-worker cleanup; acceptance is separate from actual job stop."""
        if jobs is not None:
            jobs.stop()
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    try:
        server = ThreadingHTTPServer(
            (args.listen_host, args.listen_port), handler_type(store, jobs)
        )
        server.daemon_threads = False
        print(
            json.dumps({"endpoint": f"http://{args.listen_host}:{server.server_port}"}), flush=True
        )
        try:
            server.serve_forever(poll_interval=0.2)
        finally:
            server.server_close()
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        if jobs is not None:
            jobs.close()
        store.close()


if __name__ == "__main__":
    main()
