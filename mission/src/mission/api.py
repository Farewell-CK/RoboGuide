"""HTTP composition for text Mission ingress and durable deliberation commands."""

from __future__ import annotations

import argparse
import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

from mission.config import current_environment, load_settings
from mission.controller import HttpMissionController
from mission.models import JSONObject
from mission.requests import MissionRequestEngine, MissionRequestError, MissionRequestStore
from mission.responses import ResponsesMissionInterpreter, ResponsesMissionPlanner
from mission.service_config import MissionServiceSettings, load_service_settings

LOG = logging.getLogger("roboguide.mission_service")


def build_engine(
    mission_config: Path, service_config: Path, repository_root: Path
) -> tuple[MissionRequestEngine, MissionServiceSettings]:
    """Compose the Mission Request engine from validated non-secret configuration."""
    planner_settings = load_settings(mission_config, repository_root=repository_root)
    service_settings = load_service_settings(service_config, repository_root=repository_root)
    environment = current_environment()
    controller = HttpMissionController(
        service_settings.controller_endpoint,
        service_settings.controller_timeout_seconds,
    )
    engine = MissionRequestEngine(
        MissionRequestStore(service_settings.state_db),
        ResponsesMissionInterpreter(planner_settings, environment),
        ResponsesMissionPlanner(planner_settings, environment),
        controller,
        service_settings.approval_required_contracts,
    )
    return engine, service_settings


class MissionRequestHandler(BaseHTTPRequestHandler):
    """Expose bounded Mission Request commands without owning execution state."""

    server: MissionRequestHttpServer

    def log_message(self, format: str, *args: Any) -> None:
        """Route request metadata through logging without recording instruction bodies."""
        LOG.info("%s - %s", self.address_string(), format % args)

    def do_GET(self) -> None:
        """Return process health or one durable Mission Request projection."""
        path = self._path()
        if path == "/healthz":
            self._send(HTTPStatus.OK, {"status": "ok"})
            return
        prefix = "/v1/mission-requests/"
        if path.startswith(prefix) and "/" not in path[len(prefix) :]:
            self._run(lambda: self.server.engine.get(path[len(prefix) :]), HTTPStatus.OK)
            return
        self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        """Create, clarify, approve, retry, or cancel one Mission Request."""
        path = self._path()
        try:
            body = self._read_json()
        except (MissionRequestError, UnicodeDecodeError, json.JSONDecodeError) as error:
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        if path == "/v1/mission-requests":
            if set(body) != {"instruction"} or not isinstance(body["instruction"], str):
                self._send(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "request body must contain only text instruction"},
                )
                return
            self._run(
                lambda: self.server.engine.create(body["instruction"]),
                HTTPStatus.CREATED,
            )
            return
        prefix = "/v1/mission-requests/"
        if not path.startswith(prefix):
            self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        remainder = path[len(prefix) :]
        request_id, separator, command = remainder.partition("/")
        if not separator or not request_id or "/" in command:
            self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if command == "messages":
            if set(body) != {"text"} or not isinstance(body["text"], str):
                self._send(HTTPStatus.BAD_REQUEST, {"error": "message body requires text"})
                return
            self._run(
                lambda: self.server.engine.add_message(request_id, body["text"]),
                HTTPStatus.OK,
            )
        elif command == "approve":
            self._approve(request_id, body)
        elif command == "retry" and not body:
            self._run(lambda: self.server.engine.retry(request_id), HTTPStatus.OK)
        elif command == "cancel" and not body:
            self._run(lambda: self.server.engine.cancel(request_id), HTTPStatus.OK)
        else:
            self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _approve(self, request_id: str, body: JSONObject) -> None:
        """Validate a revision-bound approval command before invoking the engine."""
        if set(body) != {"draft_revision", "draft_digest"}:
            self._send(
                HTTPStatus.BAD_REQUEST,
                {"error": "approval requires draft_revision and draft_digest"},
            )
            return
        revision = body["draft_revision"]
        digest = body["draft_digest"]
        if (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or not isinstance(digest, str)
        ):
            self._send(HTTPStatus.BAD_REQUEST, {"error": "approval fields have invalid types"})
            return
        self._run(
            lambda: self.server.engine.approve(request_id, revision, digest),
            HTTPStatus.OK,
        )

    def _run(self, operation: Any, success: HTTPStatus) -> None:
        """Execute one engine command and map domain errors without exposing tracebacks."""
        try:
            record = operation()
        except MissionRequestError as error:
            status = (
                HTTPStatus.NOT_FOUND
                if str(error).startswith("unknown Mission Request")
                else HTTPStatus.CONFLICT
            )
            self._send(status, {"error": str(error)})
        except Exception as error:  # pragma: no cover - defensive process boundary
            LOG.exception("Mission Request command failed")
            self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(error)})
        else:
            self._send(success, record.to_json())

    def _path(self) -> str:
        """Return an origin-form path while rejecting query-driven command semantics."""
        return self.path.split("?", 1)[0]

    def _read_json(self) -> JSONObject:
        """Read one bounded JSON object, accepting an empty body as an empty command."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise MissionRequestError("Content-Length is invalid") from error
        if length < 0 or length > self.server.max_request_bytes:
            raise MissionRequestError("request body exceeds configured limit")
        if length == 0:
            return {}
        decoded: object = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(decoded, dict) or not all(isinstance(key, str) for key in decoded):
            raise MissionRequestError("request body must be a JSON object")
        return cast(JSONObject, decoded)

    def _send(self, status: HTTPStatus, value: JSONObject) -> None:
        """Write one compact UTF-8 JSON response with explicit framing."""
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)


class MissionRequestHttpServer(ThreadingHTTPServer):
    """Carry one Mission Request authority and fixed request-size policy."""

    def __init__(
        self,
        address: tuple[str, int],
        engine: MissionRequestEngine,
        max_request_bytes: int,
    ) -> None:
        """Bind the configured listener and expose immutable service dependencies."""
        super().__init__(address, MissionRequestHandler)
        self.engine = engine
        self.max_request_bytes = max_request_bytes


def _parser() -> argparse.ArgumentParser:
    """Build command-line options without reading configuration or starting network access."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission-config", type=Path, default=Path("config/mission.toml"))
    parser.add_argument("--service-config", type=Path, default=Path("config/mission-service.toml"))
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    return parser


def main(argv: list[str] | None = None) -> int:
    """Start the Mission Request service and run until interrupted."""
    arguments = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    engine, settings = build_engine(
        arguments.mission_config,
        arguments.service_config,
        arguments.repository_root.resolve(),
    )
    server = MissionRequestHttpServer(
        (settings.listen_host, settings.listen_port),
        engine,
        settings.max_request_bytes,
    )
    LOG.info("listening on %s:%s", settings.listen_host, settings.listen_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("shutdown requested")
    finally:
        server.server_close()
    return 0
