"""Serve the existing camera, state, and Pilot workflows from one local process."""

import argparse
import json
import signal
import ssl
import threading
import time
from contextlib import ExitStack
from pathlib import Path

from artifact_client import upload_capture
from camera_adapter import Captures
from camera_adapter import handler_type as camera_handler
from robonix_task.adapter import Adapter, Server
from robonix_task.adapter import Handler as PilotHandler
from robonix_task.chassis_state import ChassisStateObserver
from robonix_task.host import inspect_host
from robonix_task.image_inspection import ImageInspector
from robonix_task.observation_workers import ObservationWorkers
from robonix_task.origin import OriginStore
from robonix_task.photo_handoff import PhotoHandoffReader
from robonix_task.photo_jobs import PhotoInspectionJobs
from robonix_task.position import annotate_mapping_age
from robonix_task.protocol import Liaison
from status_bridge import Status
from status_bridge import handler_type as observer_handler


def combined_handler(
    captures, observer, origins=None, node_id=None, chassis=None, image_jobs=None, photo_memory=None
):
    """Keep the existing route implementations and disambiguate readiness paths."""
    camera = camera_handler(captures)
    state = observer_handler(observer)

    def snapshot():
        """Attach the optional original chassis observation to the normal Node State snapshot."""
        value = observer.read()
        if chassis is not None:
            value["chassis"] = chassis.read()
        return value

    class Handler(PilotHandler, camera):
        """Share a bounded HTTP listener while each module retains its own data."""

        def do_GET(self):
            """Select the module by the fixed Node workflow route."""
            if self.path.startswith("/v1/image-inspections/"):
                if image_jobs is None:
                    self.reply(404, {"error": "image inspection profile disabled"})
                elif self.path == "/v1/image-inspections/health":
                    self.reply(200, image_jobs.health())
                elif self.path == "/v1/image-inspections/latest":
                    self.reply(200, image_jobs.latest())
                else:
                    self.reply(404, {"error": "route not found"})
            elif self.path == "/v1/photo-memories/discover":
                if photo_memory is None:
                    self.reply(404, {"error": "photo Memory profile disabled"})
                else:
                    try:
                        photo_memory.refresh(captures)
                        self.reply(200, photo_memory.discover())
                    except (ValueError, OSError):
                        self.reply(503, {"error": "photo Memory unavailable"})
            elif self.path == "/v1/host/status":
                if node_id is None:
                    self.reply(404, {"error": "host inspection profile disabled"})
                else:
                    self.reply(200, inspect_host(node_id, snapshot()))
            elif self.path.startswith("/v1/origins/"):
                if origins is None:
                    self.reply(404, {"error": "origin profile disabled"})
                elif self.path == "/v1/origins/discover":
                    try:
                        self.reply(200, origins.discover())
                    except (ValueError, OSError):
                        self.reply(503, {"error": "origin content unavailable"})
                elif self.path == "/v1/origins/readiness":
                    self.reply(200, {"state": "READY", "detail": "immutable origin store open"})
                else:
                    self.reply(404, {"error": "route not found"})
            elif self.path.startswith("/v1/pilot/"):
                self.path = "/v1/" + self.path.removeprefix("/v1/pilot/")
                PilotHandler.do_GET(self)
            elif self.path == "/v1/state":
                value = annotate_mapping_age(snapshot(), time.time_ns())
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
            if (
                self.path.startswith("/v1/image-inspections")
                or self.path == "/v1/photo-memories/export"
            ):
                try:
                    lengths = self.headers.get_all("Content-Length", [])
                    if self.headers.get("Transfer-Encoding") or len(lengths) != 1:
                        raise ValueError("one bounded Content-Length required")
                    length = int(lengths[0])
                    if not 0 < length <= 65536:
                        raise ValueError("bounded Content-Length required")
                    self.connection.settimeout(10)
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise ValueError("incomplete request")
                    body = json.loads(raw)
                    if not isinstance(body, dict):
                        raise ValueError("object required")
                    if self.path == "/v1/photo-memories/export" and photo_memory is not None:
                        value = photo_memory.export(body)
                    elif image_jobs is None:
                        self.reply(404, {"error": "image inspection profile disabled"})
                        return
                    elif self.path == "/v1/image-inspections":
                        value = image_jobs.submit(body)
                    elif self.path in (
                        "/v1/image-inspections/status",
                        "/v1/image-inspections/cancel",
                    ):
                        if set(body) != {"handle"}:
                            raise ValueError("exact handle required")
                        value = (
                            image_jobs.cancel(body["handle"])
                            if self.path.endswith("/cancel")
                            else image_jobs.status(body["handle"])
                        )
                    else:
                        self.reply(404, {"error": "route not found"})
                        return
                    self.reply(200, value)
                except (ValueError, KeyError, TypeError, OSError):
                    self.reply(409, {"error": "image workflow refused or unavailable"})
            elif self.path.startswith("/v1/origins/"):
                if origins is None:
                    self.reply(404, {"error": "origin profile disabled"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if self.headers.get("Transfer-Encoding") or not 0 < length <= 65536:
                        raise ValueError("bounded Content-Length required")
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise ValueError("incomplete request")
                    body = json.loads(raw)
                    if not isinstance(body, dict):
                        raise ValueError("object required")
                    if self.path == "/v1/origins/capture":
                        value = origins.capture(body, observer.read())
                    elif self.path in ("/v1/origins/status", "/v1/origins/cancel"):
                        if set(body) != {"handle"}:
                            raise ValueError("exact handle required")
                        value = origins.status(body["handle"])
                    elif self.path == "/v1/origins/export":
                        if set(body) != {"manifest"}:
                            raise ValueError("exact manifest required")
                        value = origins.export(body["manifest"])
                    else:
                        self.reply(404, {"error": "route not found"})
                        return
                    self.reply(200, value)
                except (ValueError, KeyError, TypeError, OSError):
                    self.reply(409, {"error": "origin request refused or content unavailable"})
            elif self.path in (
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
    parser.add_argument("--origin-root", type=Path)
    parser.add_argument("--origin-node-id")
    parser.add_argument("--node-id")
    parser.add_argument("--chassis-state", action="store_true")
    parser.add_argument("--image-inspection-config", type=Path)
    parser.add_argument("--photo-memory-config", type=Path)
    args = parser.parse_args()
    if (args.origin_root is None) != (args.origin_node_id is None):
        parser.error("origin-root and origin-node-id must be supplied together")

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
            chassis = ChassisStateObserver() if args.chassis_state else None
            if chassis is not None:
                cleanup.callback(chassis.close)
            captures = Captures(args.capture_root, publisher=publish)
            cleanup.callback(stop_captures, captures)
            origins = (
                OriginStore(args.origin_root, args.origin_node_id)
                if args.origin_root is not None
                else None
            )
            if origins is not None:
                cleanup.callback(origins.close)
            image_jobs = None
            if args.image_inspection_config is not None:
                config = json.loads(args.image_inspection_config.read_bytes())
                if config["consumer_node_id"] != args.node_id:
                    raise ValueError("image consumer must match the enrolled Connector identity")
                contexts = {}
                for name in ("controller", "artifact"):
                    profile = config[name]
                    context = ssl.create_default_context(cafile=profile["ca"])
                    context.load_cert_chain(profile["cert"], profile["key"])
                    contexts[name] = context
                input_root = Path(config["input_root"])
                reader = PhotoHandoffReader(
                    config["controller"]["origin"],
                    config["artifact"]["origin"],
                    input_root,
                    controller_context=contexts["controller"],
                    artifact_context=contexts["artifact"],
                    tenant_id=config["tenant_id"],
                    producer_node_id=config["producer_node_id"],
                    consumer_node_id=config["consumer_node_id"],
                    expected_memory_id=config["expected_memory_id"],
                )
                inspector = ImageInspector(
                    Path(config["store_root"]) / "inspection", args.node_id, input_root=input_root
                )
                cleanup.callback(inspector.close)
                image_jobs = PhotoInspectionJobs(
                    Path(config["store_root"]) / "jobs",
                    inspector,
                    lambda invocation, attempt_id: reader.resolve(
                        invocation, consumer_attempt_id=attempt_id
                    ),
                )
                cleanup.callback(image_jobs.close)
            photo_memory = None
            if args.photo_memory_config is not None:
                from robonix_task.photo_capture_memory import (
                    PhotoCaptureMemories,
                    PhotoCaptureMemory,
                )

                config = json.loads(args.photo_memory_config.read_bytes())
                profiles = config["publications"] if "publications" in config else [config]
                if not isinstance(profiles, list) or not 0 < len(profiles) <= 16:
                    raise ValueError("bounded explicit photo publications required")
                with ExitStack() as opening:
                    publications = []
                    for profile in profiles:
                        if profile["node_id"] != args.node_id:
                            raise ValueError("photo publisher must match the Connector identity")
                        publication = PhotoCaptureMemory(**profile)
                        opening.callback(publication.close)
                        publications.append(publication)
                    photo_memory = PhotoCaptureMemories(
                        publications, root=Path(profiles[0]["root"])
                    )
                    opening.pop_all()
                cleanup.callback(photo_memory.close)
            server.RequestHandlerClass = combined_handler(
                captures, observer, origins, args.node_id, chassis, image_jobs, photo_memory
            )
            server.timeout = 0.5
            workers = ObservationWorkers(
                observer.sample, chassis.sample if chassis is not None else None, stopping
            )
            cleanup.callback(workers.close)
            workers.start()
            while not stopping.is_set():
                server.handle_request()
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
