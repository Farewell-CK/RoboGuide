"""Serve the existing camera, state, and Pilot workflows from one local process."""

import argparse
import json
import signal
import threading
import time
from contextlib import ExitStack
from pathlib import Path

from artifact_client import upload_capture
from camera_adapter import Captures
from camera_adapter import handler_type as camera_handler
from robonix_task.adapter import Adapter, Server
from robonix_task.adapter import Handler as PilotHandler
from robonix_task.position import annotate_mapping_age
from robonix_task.protocol import Liaison
from status_bridge import Status
from status_bridge import handler_type as observer_handler


def combined_handler(captures, observer):
    """Keep the existing route implementations and disambiguate readiness paths."""
    camera = camera_handler(captures)
    state = observer_handler(observer)

    class Handler(PilotHandler, camera):
        """Share a bounded HTTP listener while each module retains its own data."""

        def do_GET(self):
            """Select the module by the fixed Node workflow route."""
            if self.path.startswith("/v1/pilot/"):
                self.path = "/v1/" + self.path.removeprefix("/v1/pilot/")
                PilotHandler.do_GET(self)
            elif self.path == "/v1/state":
                value = annotate_mapping_age(observer.read(), time.time_ns())
                value["atlas"]["providers"] = [
                    {"id": p["id"], "state": p["state"]}
                    for p in value["atlas"].get("providers", [])
                ]
                raw = json.dumps(value, allow_nan=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            elif self.path in ("/v1/catalog", "/v1/observer/health"):
                if self.path == "/v1/observer/health":
                    self.path = "/v1/health"
                state.do_GET(self)
            else:
                camera.do_GET(self)

        def do_POST(self):
            """Send capture and execution requests to their original owners."""
            if self.path in (
                "/v1/captures",
                "/v1/captures/status",
                "/v1/captures/cancel",
            ):
                camera.do_POST(self)
            else:
                PilotHandler.do_POST(self)

    return Handler


def stop_captures(captures):
    """Join admitted capture work before releasing its existing journal."""
    captures.closed.set()
    for job in captures.jobs:
        job.join()
    captures.db.close()


def main():
    """Run one loopback service with an explicit optional task-delegation profile."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--pilot-journal", type=Path, required=True)
    parser.add_argument("--codegen", type=Path, required=True)
    parser.add_argument("--artifact-origin", required=True)
    parser.add_argument("--pki", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18112)
    parser.add_argument("--delegate-profile")
    args = parser.parse_args()

    def publish(path, execution_id):
        """Reuse the existing authenticated upload and finalization path."""
        return upload_capture(
            path,
            origin=args.artifact_origin,
            ca_cert=args.pki / "ca.crt",
            client_cert=args.pki / "node.crt",
            client_key=args.pki / "node.key",
            capture_id=execution_id,
            timeout=15.0,
        )

    stopping = threading.Event()

    def request_stop(_signum, _frame):
        """Let the serving thread exit its request loop without signal deadlock."""
        stopping.set()

    def observe():
        """Refresh robot observations independently of request handlers."""
        while not stopping.is_set():
            observer.sample()
            stopping.wait(3)

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    with ExitStack() as cleanup:
        pilot = Adapter(
            args.pilot_journal,
            Liaison("127.0.0.1:50051", "rg/local-task", "local:rg-local-task"),
            allowed_operations=("task.delegate@v1",),
            enabled=args.delegate_profile is not None,
            task_results=True,
            task_profile=args.delegate_profile,
        )
        cleanup.callback(pilot.close)
        # Reserve the previous camera listener before opening its writable store.
        server = Server(args.port, pilot)
        try:
            observer = Status(args.codegen)
            captures = Captures(args.capture_root, publisher=publish)
            cleanup.callback(stop_captures, captures)
            server.RequestHandlerClass = combined_handler(captures, observer)
            server.timeout = 0.5
            observer_thread = threading.Thread(target=observe, name="robonix-observer")
            observer_thread.start()
            cleanup.callback(observer_thread.join)
            cleanup.callback(stopping.set)
            while not stopping.is_set():
                server.handle_request()
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
