"""EAIOS-neutral HTTP workflow & PeerChannel server for `roboguide_sync`."""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from . import __version__
from .adapters.base import RobotMotionAdapter
from .choreography_engine import ChoreographyEngine
from .clock_sync import ClockProbe, ClockSyncError, SystemClockProbe, verify_clock_offset
from .journal import ConflictError, SyncJournal
from .peer_manager import DEFAULT_MESSAGE_SCHEMA, DEFAULT_PROFILE_ID, PeerChannelManager
from .schemas import (
    OP_CHOREOGRAPHY_PLAY,
    OP_SYNC_FOLLOW,
    SYNC_ENDPOINT_SCHEMA,
    NormalizedExecutionRequest,
    SchemaError,
    extract_execution_intent,
    parse_choreography_spec,
    parse_neighbors,
    parse_sync_params,
    parse_track_spec,
)
from .sync_engine import SyncFollowEngine
from .transport import UdpHub

LOG = logging.getLogger("roboguide_sync.http")
MAX_HTTP_BODY_BYTES = 1_048_576


def _parse_int_param(val: Any, name: str, *, minimum: int = 0) -> int:
    """Parse an integer or numeric string parameter >= `minimum`."""
    if isinstance(val, bool):
        raise SchemaError(f"{name} must be an integer >= {minimum}")
    if isinstance(val, str):
        s = val.strip()
        if not s:
            raise SchemaError(f"{name} must not be blank")
        try:
            out = int(s)
        except ValueError as exc:
            raise SchemaError(f"{name} must be an integer: {exc}") from exc
    elif isinstance(val, int):
        out = int(val)
    else:
        raise SchemaError(f"{name} must be an integer >= {minimum}")
    if out < minimum:
        raise SchemaError(f"{name} must be >= {minimum}")
    return out


def _parse_float_param(val: Any, name: str, *, min_val: float, max_val: float) -> float:
    """Parse a finite numeric parameter within `[min_val, max_val]`."""
    if isinstance(val, bool):
        raise SchemaError(f"{name} must be a number")
    try:
        out = float(val)
    except (TypeError, ValueError) as exc:
        raise SchemaError(f"{name} must be numeric: {exc}") from exc
    if not (min_val <= out <= max_val):
        raise SchemaError(f"{name} out of range [{min_val}, {max_val}]")
    return out


class SyncService:
    """Coordinates adapters, UDP hub, PeerChannels, journal, and motion engines."""

    def __init__(
        self,
        *,
        adapter: RobotMotionAdapter,
        journal: SyncJournal,
        hub: UdpHub,
        node_id: str,
        advertise_ip: str = "127.0.0.1",
        clock_probe: ClockProbe | None = None,
        max_clock_offset_ms: float = 20.0,
        http_host: str = "127.0.0.1",
        http_port: int = 18210,
    ) -> None:
        """Bind the HTTP workflow server and initialize runtime coordinators."""
        self.adapter = adapter
        self.journal = journal
        self.hub = hub
        self.node_id = str(node_id)
        self.advertise_ip = str(advertise_ip)
        self.clock_probe: ClockProbe = (
            clock_probe if clock_probe is not None else SystemClockProbe()
        )
        self.max_clock_offset_ms = float(max_clock_offset_ms)
        self.peer_manager = PeerChannelManager(hub, self.node_id)

        self._exec_lock = threading.Lock()
        self._active_handle: str | None = None
        self._cancel_events: dict[str, threading.Event] = {}
        self._workers: dict[str, threading.Thread] = {}
        self._started = False
        self._closed = False

        service = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
                """Route HTTP access logs through Python `logging.debug`."""
                LOG.debug("http %s - " + fmt, self.address_string(), *args)

            def _send_json(self, status: int, payload: Any) -> None:
                """Serialize `payload` and write an HTTP JSON response."""
                raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)

            def _read_json(self) -> Any:
                """Parse bounded JSON request body, raising `SchemaError` or `ValueError`."""
                cl_raw = self.headers.get("Content-Length")
                if not cl_raw:
                    return {}
                try:
                    length = int(cl_raw)
                except ValueError as exc:
                    raise SchemaError(f"invalid Content-Length: {cl_raw}") from exc
                if length < 0 or length > MAX_HTTP_BODY_BYTES:
                    raise ValueError(f"body size {length} exceeds max {MAX_HTTP_BODY_BYTES}")
                if length == 0:
                    return {}
                raw = self.rfile.read(length)
                try:
                    return json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise SchemaError(f"invalid JSON body: {exc}") from exc

            def do_GET(self) -> None:  # noqa: N802
                """Serve health, readiness, endpoint, PeerChannel, and execution status routes."""
                parsed = urllib.parse.urlsplit(self.path)
                path = parsed.path
                query = urllib.parse.parse_qs(parsed.query)
                try:
                    if path == "/v1/sync/health":
                        self._send_json(HTTPStatus.OK, service.health_payload())
                    elif path == "/v1/sync/readiness":
                        op_vals = query.get("operation")
                        op = op_vals[0] if op_vals else None
                        _ready, payload = service.readiness_payload(operation=op)
                        self._send_json(HTTPStatus.OK, payload)
                    elif path == "/v1/sync/readiness/sync-follow":
                        _ready, payload = service.readiness_payload(operation=OP_SYNC_FOLLOW)
                        self._send_json(HTTPStatus.OK, payload)
                    elif path == "/v1/sync/readiness/choreography":
                        _ready, payload = service.readiness_payload(operation=OP_CHOREOGRAPHY_PLAY)
                        self._send_json(HTTPStatus.OK, payload)
                    elif path == "/v1/sync/endpoint":
                        self._send_json(HTTPStatus.OK, service.endpoint_payload())
                    elif path == "/v1/peer-channels":
                        self._send_json(HTTPStatus.OK, service.peer_manager.snapshot_channels())
                    elif path == "/v1/sync/executions/safe-state":
                        self._send_json(HTTPStatus.OK, service.journal.safe_state_observations())
                    elif path.startswith("/v1/sync/executions/"):
                        handle = path.rsplit("/", 1)[-1]
                        self._send_json(HTTPStatus.OK, service.get_execution(handle))
                    else:
                        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found", "path": path})
                except SchemaError as exc:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": str(exc)}
                    )
                except KeyError:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "unknown_execution"})
                except Exception as exc:  # pragma: no cover
                    LOG.exception("GET %s failed", path)
                    self._send_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        {"error": "internal_error", "detail": str(exc)},
                    )

            def do_POST(self) -> None:  # noqa: N802
                """Serve execution admission, status, cancel, and PeerChannel session routes."""
                parsed = urllib.parse.urlsplit(self.path)
                path = parsed.path
                try:
                    body = self._read_json()
                    if path == "/v1/sync/executions":
                        status, payload = service.submit_execution(body)
                        self._send_json(status, payload)
                    elif path == "/v1/sync/executions/status":
                        if not isinstance(body, dict):
                            raise SchemaError("request body must be a JSON object")
                        handle = body.get("handle") or body.get("execution_id")
                        if not isinstance(handle, str) or not handle.strip():
                            raise SchemaError("handle is required")
                        self._send_json(HTTPStatus.OK, service.get_execution(handle.strip()))
                    elif path == "/v1/sync/executions/cancel":
                        if not isinstance(body, dict):
                            raise SchemaError("request body must be a JSON object")
                        handle = body.get("handle") or body.get("execution_id")
                        if not isinstance(handle, str) or not handle.strip():
                            raise SchemaError("handle is required")
                        cmd_id = body.get("command_id")
                        cmd = cmd_id.strip() if isinstance(cmd_id, str) and cmd_id.strip() else None
                        self._send_json(
                            HTTPStatus.OK, service.cancel_execution(handle.strip(), command_id=cmd)
                        )
                    elif path == "/v1/peer-channels/sessions":
                        if not isinstance(body, dict):
                            raise SchemaError("request body must be a JSON object")
                        sess = service.arm_peer_session(body)
                        self._send_json(HTTPStatus.OK, sess)
                    else:
                        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found", "path": path})
                except SchemaError as exc:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": str(exc)}
                    )
                except ConflictError as exc:
                    self._send_json(HTTPStatus.CONFLICT, {"error": "conflict", "detail": str(exc)})
                except KeyError:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "unknown_execution"})
                except ValueError as exc:
                    self._send_json(
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        {"error": "payload_too_large", "detail": str(exc)},
                    )
                except Exception as exc:  # pragma: no cover
                    LOG.exception("POST %s failed", path)
                    self._send_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        {"error": "internal_error", "detail": str(exc)},
                    )

            def do_DELETE(self) -> None:  # noqa: N802
                """Silence a PeerChannel session explicitly by group, context, and role."""
                parsed = urllib.parse.urlsplit(self.path)
                path = parsed.path
                query = urllib.parse.parse_qs(parsed.query)
                try:
                    if path == "/v1/peer-channels/sessions":
                        body = self._read_json()
                        if not isinstance(body, dict):
                            raise SchemaError("request body must be a JSON object")
                        group_id = str(
                            body.get("group_id") or (query.get("group_id") or [""])[0]
                        ).strip()
                        context_id = str(
                            body.get("context_id") or (query.get("context_id") or [""])[0]
                        ).strip()
                        role_id = str(
                            body.get("context_role_id") or (query.get("context_role_id") or [""])[0]
                        ).strip()
                        if not (group_id and context_id and role_id):
                            raise SchemaError(
                                "group_id, context_id, and context_role_id are required"
                            )
                        silenced = service.peer_manager.silence_on_terminal(
                            group_id, context_id, role_id
                        )
                        self._send_json(HTTPStatus.OK, {"silenced": silenced})
                    else:
                        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found", "path": path})
                except SchemaError as exc:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": str(exc)}
                    )
                except ValueError as exc:
                    self._send_json(
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        {"error": "payload_too_large", "detail": str(exc)},
                    )
                except Exception as exc:  # pragma: no cover
                    LOG.exception("DELETE %s failed", path)
                    self._send_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        {"error": "internal_error", "detail": str(exc)},
                    )

        class _ReuseThreadingServer(ThreadingHTTPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.httpd = _ReuseThreadingServer((http_host, int(http_port)), _Handler)
        self._http_thread = threading.Thread(
            target=self.httpd.serve_forever, name="sync-http", daemon=True
        )

    @property
    def port(self) -> int:
        """Return bound HTTP server port."""
        return int(self.httpd.server_address[1])

    @property
    def udp_port(self) -> int:
        """Return bound UDP peer hub port."""
        return self.hub.local_port

    def start(self) -> None:
        """Start chassis adapter and HTTP server background thread."""
        if self._started:
            return
        self._started = True
        self.adapter.start()
        self._http_thread.start()

    def close(self) -> None:
        """Cancel active executions, stop servers, and close journal and adapter."""
        if self._closed:
            return
        self._closed = True
        with self._exec_lock:
            for ev in self._cancel_events.values():
                ev.set()
            workers = list(self._workers.values())
        try:
            self.adapter.emergency_stop()
        except Exception:
            pass
        for w in workers:
            if w.is_alive():
                w.join(timeout=4.0)
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass
        self.peer_manager.close()
        self.hub.close()
        try:
            self.adapter.close()
        finally:
            self.journal.close()

    def endpoint_payload(self) -> dict[str, Any]:
        """Return advertised UDP endpoint state export payload."""
        return {
            "schema": SYNC_ENDPOINT_SCHEMA,
            "node_id": self.node_id,
            "adapter": self.adapter.adapter_kind,
            "udp_port": self.udp_port,
            "peer_endpoint": f"{self.advertise_ip}:{self.udp_port}",
            "operations": [OP_SYNC_FOLLOW, OP_CHOREOGRAPHY_PLAY],
        }

    def health_payload(self) -> dict[str, Any]:
        """Return service health, bound ports, live pose, sensors, and clock status."""
        pose = self.adapter.read_pose()
        sensors = self.adapter.read_sensors()
        clk = self.clock_probe.read_status()
        with self._exec_lock:
            active = self._active_handle
        return {
            "status": "ok",
            "state": "ONLINE",
            "detail": f"adapter={self.adapter.adapter_kind} udp={self.udp_port}",
            "service": "roboguide-sync-provider",
            "version": __version__,
            "node_id": self.node_id,
            "adapter": self.adapter.adapter_kind,
            "http_port": self.port,
            "udp_port": self.udp_port,
            "advertise_endpoint": f"{self.advertise_ip}:{self.udp_port}",
            "active_handle": active,
            "pose": (
                {
                    "x": round(pose.x, 4),
                    "y": round(pose.y, 4),
                    "yaw": round(pose.yaw, 4),
                    "age_ms": round(max(0.0, (time.monotonic() - pose.stamp_mono) * 1000.0), 2),
                }
                if pose is not None
                else None
            ),
            "sensors": {
                "posture": sensors.posture,
                "lidar_front_min_m": sensors.lidar_front_min_m,
                "battery_pct": sensors.battery_pct,
                "healthy": sensors.healthy,
                "detail": sensors.detail,
            },
            "clock": {
                "synchronized": clk.synchronized,
                "source": clk.source,
                "offset_ms": clk.offset_ms,
                "detail": clk.detail,
            },
        }

    def readiness_payload(self, *, operation: str | None = None) -> tuple[bool, dict[str, Any]]:
        """Evaluate capability readiness overall or for one specific operation."""
        if operation is not None and operation not in (OP_SYNC_FOLLOW, OP_CHOREOGRAPHY_PLAY):
            raise SchemaError(f"unsupported operation query: {operation}")

        sync_ready, sync_detail = self.adapter.check_ready(OP_SYNC_FOLLOW)
        ch_adapter_ready, ch_detail = self.adapter.check_ready(OP_CHOREOGRAPHY_PLAY)
        ch_ready = ch_adapter_ready
        if ch_adapter_ready:
            try:
                clk = verify_clock_offset(self.clock_probe, self.max_clock_offset_ms)
                ch_detail = f"{ch_detail}; clock={clk.source} ({clk.offset_ms}ms)"
            except ClockSyncError as exc:
                ch_ready = False
                ch_detail = f"clock_unsynced: {exc}"

        ops = {
            OP_SYNC_FOLLOW: {
                "ready": sync_ready,
                "state": "READY" if sync_ready else "UNAVAILABLE",
                "detail": sync_detail,
            },
            OP_CHOREOGRAPHY_PLAY: {
                "ready": ch_ready,
                "state": "READY" if ch_ready else "UNAVAILABLE",
                "detail": ch_detail,
            },
        }
        if operation is not None:
            sel = ops[operation]
            sel_ready = bool(sel["ready"])
            return sel_ready, {
                "ready": sel_ready,
                "state": sel["state"],
                "operation": operation,
                "detail": sel["detail"],
                "node_id": self.node_id,
                "operations": ops,
            }
        return sync_ready, {
            "ready": sync_ready,
            "state": "READY" if sync_ready else "UNAVAILABLE",
            "detail": sync_detail,
            "node_id": self.node_id,
            "operations": ops,
        }

    def arm_peer_session(self, body: dict[str, Any]) -> dict[str, Any]:
        """Validate and arm a pre-dispatch PeerChannel session."""
        for req_k in ("group_id", "context_id", "context_role_id", "channel_instance_id"):
            if not isinstance(body.get(req_k), str) or not body[req_k].strip():
                raise SchemaError(f"{req_k} must be a non-empty string")
        neighbors_raw = body.get("neighbors_json", body.get("neighbors", []))
        neighbors = parse_neighbors(neighbors_raw) if neighbors_raw else ()
        timeout_ms = _parse_float_param(
            body.get("peer_timeout_ms", 2500.0), "peer_timeout_ms", min_val=100.0, max_val=60_000.0
        )
        return self.peer_manager.arm_session(
            group_id=body["group_id"].strip(),
            context_id=body["context_id"].strip(),
            context_role_id=body["context_role_id"].strip(),
            channel_instance_id=body["channel_instance_id"].strip(),
            profile_id=str(body.get("profile_id") or DEFAULT_PROFILE_ID).strip(),
            message_schema=str(body.get("message_schema") or DEFAULT_MESSAGE_SCHEMA).strip(),
            neighbors=neighbors,
            require_peer_rx=bool(body.get("require_peer_rx", False)),
            peer_timeout_ms=timeout_ms,
        )

    @staticmethod
    def _format_execution(row: dict[str, Any]) -> dict[str, Any]:
        """Format a persisted journal row into HTTP execution status JSON."""
        return {
            "handle": row["handle"],
            "execution_id": row["handle"],
            "attempt_id": row["attempt_id"],
            "execution_generation": row["execution_generation"],
            "operation": row["operation"],
            "state": row["state"],
            "detail": row["detail"],
            "cancel_requested": row["cancel_requested"],
            "safe_state_confirmed": row["safe_state_confirmed"],
            "metrics": row["metrics"],
            "created_ms": row["created_ms"],
            "updated_ms": row["updated_ms"],
        }

    def get_execution(self, handle: str) -> dict[str, Any]:
        """Fetch formatted execution status for `handle`."""
        return self._format_execution(self.journal.get(handle))

    def _parse_operation_args(self, req: NormalizedExecutionRequest) -> dict[str, Any]:
        """Parse and validate operation-specific parameters before admission."""
        p = req.parameters
        if req.operation == OP_SYNC_FOLLOW:
            track_raw = p.get("track_spec_json") if "track_spec_json" in p else p.get("track_spec")
            if track_raw is None:
                raise SchemaError("track_spec_json (or track_spec) parameter required")
            track_spec = parse_track_spec(track_raw)
            neighbors_raw = (
                p.get("neighbors_json") if "neighbors_json" in p else p.get("neighbors", [])
            )
            neighbors = parse_neighbors(neighbors_raw) if neighbors_raw not in (None, "") else ()
            sync_params_raw = (
                p.get("sync_params_json") if "sync_params_json" in p else p.get("sync_params")
            )
            sync_params = parse_sync_params(sync_params_raw)
            context_id = str(p.get("context_id") or req.group_id).strip()
            context_role_id = str(p.get("context_role_id") or req.role_id).strip()
            channel_instance_id = str(
                p.get("channel_instance_id") or f"ch:{req.group_id}:{context_id}"
            ).strip()
            epoch = _parse_int_param(p.get("epoch", req.execution_generation), "epoch", minimum=1)
            run_id = str(p.get("run_id") or req.attempt_id).strip()
            return {
                "kind": OP_SYNC_FOLLOW,
                "track_spec": track_spec,
                "neighbors": neighbors,
                "sync_params": sync_params,
                "context_id": context_id,
                "context_role_id": context_role_id,
                "channel_instance_id": channel_instance_id,
                "epoch": epoch,
                "run_id": run_id,
            }

        choreo_raw = (
            p.get("choreography_spec_json")
            if "choreography_spec_json" in p
            else p.get("choreography_spec")
        )
        if choreo_raw is None:
            raise SchemaError("choreography_spec_json (or choreography_spec) parameter required")
        choreo_spec = parse_choreography_spec(choreo_raw)
        if "t0_utc_ms" not in p:
            raise SchemaError("t0_utc_ms parameter required for choreography.play@v1")
        t0_utc_ms = _parse_int_param(p["t0_utc_ms"], "t0_utc_ms", minimum=0)
        max_off = _parse_float_param(
            p.get("max_clock_offset_ms", self.max_clock_offset_ms),
            "max_clock_offset_ms",
            min_val=0.1,
            max_val=1000.0,
        )
        return {
            "kind": OP_CHOREOGRAPHY_PLAY,
            "spec": choreo_spec,
            "t0_utc_ms": t0_utc_ms,
            "max_clock_offset_ms": max_off,
        }

    def submit_execution(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Idempotently admit an execution and spawn its worker thread."""
        req = extract_execution_intent(body)
        parsed_args = self._parse_operation_args(req)

        with self._exec_lock:
            existing = self.journal.get_by_attempt_id(req.attempt_id)
            if existing is not None:
                row, _created = self.journal.admit(
                    attempt_id=req.attempt_id,
                    execution_generation=req.execution_generation,
                    operation=req.operation,
                    canonical_body=body,
                )
                return HTTPStatus.OK, self._format_execution(row)

            if self._active_handle is not None:
                raise ConflictError(
                    f"another motion execution ({self._active_handle}) is currently active"
                )

            row, _created = self.journal.admit(
                attempt_id=req.attempt_id,
                execution_generation=req.execution_generation,
                operation=req.operation,
                canonical_body=body,
            )
            handle = row["handle"]
            cancel_ev = threading.Event()
            self._active_handle = handle
            self._cancel_events[handle] = cancel_ev
            worker = threading.Thread(
                target=self._run_worker,
                args=(handle, req, parsed_args, cancel_ev),
                name=f"sync-exec-{handle[:10]}",
                daemon=True,
            )
            self._workers[handle] = worker
            worker.start()
            return HTTPStatus.ACCEPTED, self._format_execution(row)

    def _run_worker(
        self,
        handle: str,
        req: NormalizedExecutionRequest,
        parsed_args: dict[str, Any],
        cancel_ev: threading.Event,
    ) -> None:
        """Execute one admitted motion run and record terminal safe-state facts."""
        final_state = "FAILED"
        detail = "unhandled execution failure"
        metrics: dict[str, Any] = {}
        safe_confirmed = False
        try:
            if cancel_ev.is_set():
                self.adapter.emergency_stop()
                final_state = "CANCELED"
                detail = "canceled before start"
                safe_confirmed = True
                return
            self.journal.mark_running(handle, f"running {req.operation}")
            if parsed_args["kind"] == OP_SYNC_FOLLOW:
                sync_engine = SyncFollowEngine(
                    adapter=self.adapter,
                    hub=self.hub,
                    peer_manager=self.peer_manager,
                    node_id=self.node_id,
                    group_id=req.group_id,
                    context_id=parsed_args["context_id"],
                    role_id=parsed_args["context_role_id"],
                    channel_instance_id=parsed_args["channel_instance_id"],
                    run_id=parsed_args["run_id"],
                    epoch=parsed_args["epoch"],
                    track_spec=parsed_args["track_spec"],
                    neighbors=parsed_args["neighbors"],
                    sync_params=parsed_args["sync_params"],
                    cancel_event=cancel_ev,
                    budget_ms=req.budget_ms,
                )
                final_state, detail, metrics, safe_confirmed = sync_engine.run()
            else:
                choreo_engine = ChoreographyEngine(
                    adapter=self.adapter,
                    clock_probe=self.clock_probe,
                    spec=parsed_args["spec"],
                    t0_utc_ms=parsed_args["t0_utc_ms"],
                    max_clock_offset_ms=parsed_args["max_clock_offset_ms"],
                    cancel_event=cancel_ev,
                )
                final_state, detail, metrics, safe_confirmed = choreo_engine.run()
        except Exception as exc:  # pragma: no cover
            LOG.exception("execution %s crashed", handle)
            try:
                self.adapter.emergency_stop()
                safe_confirmed = True
            except Exception:
                safe_confirmed = False
            final_state = "FAILED"
            detail = f"worker_exception:{exc}"
        finally:
            try:
                self.journal.mark_terminal(
                    handle,
                    final_state,
                    detail,
                    metrics=metrics,
                    safe_state_confirmed=safe_confirmed,
                )
            except Exception:  # pragma: no cover
                LOG.exception("failed to persist terminal state for %s", handle)
            with self._exec_lock:
                self._cancel_events.pop(handle, None)
                self._workers.pop(handle, None)
                if self._active_handle == handle:
                    self._active_handle = None

    def cancel_execution(self, handle: str, *, command_id: str | None = None) -> dict[str, Any]:
        """Signal cancellation, trip immediate estop, and return execution state."""
        with self._exec_lock:
            row = self.journal.request_cancel(handle, command_id=command_id)
            ev = self._cancel_events.get(handle)
            if ev is not None:
                ev.set()
        if ev is not None:
            try:
                self.adapter.emergency_stop()
            except Exception:
                pass
        return self._format_execution(row)
