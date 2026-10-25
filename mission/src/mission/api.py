"""HTTP composition for text Mission ingress and durable deliberation commands."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import socket
import sqlite3
import ssl
import threading
import time
from collections.abc import Mapping
from contextlib import nullcontext
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, unquote

from mission.authorization import CertificateGrant, load_authorization
from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.config import current_environment, load_settings
from mission.controller import HttpMissionController
from mission.execution_planning_evidence import ExecutionPlanningEvidence
from mission.execution_profile import load_optional_execution_profile
from mission.grounding_reader import HttpMissionGroundingReader, UrllibGroundingJsonTransport
from mission.models import JSONObject
from mission.planning_profile import load_optional_planning_profile
from mission.request_store import HTTP_IDEMPOTENCY_COMPLETE
from mission.requests import MissionRequestEngine, MissionRequestError, MissionRequestStore
from mission.responses import (
    ResponsesMissionInterpreter,
    ResponsesMissionPlanner,
    ResponsesMissionRepairer,
    ResponsesMissionReviewer,
)
from mission.service_config import (
    MissionServiceConfigError,
    MissionServiceSettings,
    load_service_settings,
)

LOG = logging.getLogger("roboguide.mission_service")
Principal = tuple[str, str, str]


def _file_digest(path: Path) -> str:
    """Freeze exact configured source bytes without persisting credentials or machine paths."""
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def build_engine(
    mission_config: Path, service_config: Path, repository_root: Path
) -> tuple[MissionRequestEngine, MissionServiceSettings]:
    """Compose the Mission Request engine from validated non-secret configuration."""
    planner_settings = load_settings(mission_config, repository_root=repository_root)
    capability_catalog = CanonicalCapabilityCatalog.load(planner_settings.capability_catalog_path)
    service_settings = load_service_settings(service_config, repository_root=repository_root)
    execution_profile = load_optional_execution_profile(service_settings.execution_profile_path)
    planning_profile = load_optional_planning_profile(service_settings.planning_profile_path)
    if planning_profile is not None:
        planning_profile.validate_catalog(capability_catalog)
    execution_planning_evidence = None
    if service_settings.execution_planning_evidence_path is not None:
        if not service_settings.execution_planning_evidence_path.is_file():
            if service_settings.execution_planning_evidence_required:
                raise MissionServiceConfigError(
                    "required execution planning evidence is unavailable: "
                    f"{service_settings.execution_planning_evidence_path}"
                )
        else:
            execution_planning_evidence = ExecutionPlanningEvidence.from_json(
                json.loads(service_settings.execution_planning_evidence_path.read_bytes())
            )
    environment = current_environment()
    controller = HttpMissionController(
        service_settings.controller_endpoint,
        service_settings.controller_timeout_seconds,
        tls_context=_client_tls_context(
            service_settings.controller_client_cert,
            service_settings.controller_client_key,
            service_settings.controller_client_ca,
        ),
    )
    plan_validator = (
        partial(controller.validate_plan, evidence=execution_planning_evidence)
        if execution_planning_evidence is not None
        else None
    )
    reviewer = (
        ResponsesMissionReviewer(
            planner_settings,
            environment,
            execution_profile=execution_profile,
            planning_profile=planning_profile,
            execution_planning_evidence=execution_planning_evidence,
        )
        if planner_settings.review_enabled
        else None
    )
    repairer = (
        ResponsesMissionRepairer(
            planner_settings,
            environment,
            execution_profile=execution_profile,
            planning_profile=planning_profile,
            execution_planning_evidence=execution_planning_evidence,
            plan_validator=plan_validator,
        )
        if reviewer is not None
        and (
            planner_settings.max_repair_attempts > 0
            or service_settings.max_deployment_recovery_attempts > 0
        )
        else None
    )
    engine = MissionRequestEngine(
        MissionRequestStore(service_settings.state_db),
        ResponsesMissionInterpreter(planner_settings, environment),
        ResponsesMissionPlanner(
            planner_settings,
            environment,
            execution_profile=execution_profile,
            planning_profile=planning_profile,
            execution_planning_evidence=execution_planning_evidence,
            plan_validator=plan_validator,
        ),
        controller,
        capability_catalog,
        service_settings.approval_policy,
        prevalidation_recovery_attempts=planner_settings.prevalidation_recovery_attempts,
        controller_preflight_enabled=service_settings.controller_preflight_enabled,
        readiness_probe=controller.readiness,
        max_deployment_recovery_attempts=service_settings.max_deployment_recovery_attempts,
        deployment_recovery_timeout_ms=service_settings.deployment_recovery_timeout_ms,
        provider_identity={
            "mission_configuration_digest": _file_digest(mission_config),
            "service_configuration_digest": _file_digest(service_config),
            "catalog_source_digest": _file_digest(planner_settings.capability_catalog_path),
            "output_schema_digest": _file_digest(planner_settings.schema_path),
            "dependency_lock_digest": _file_digest(repository_root / "uv.lock")
            if (repository_root / "uv.lock").is_file()
            else None,
            "prompt_digests": {
                "interpreter": _file_digest(planner_settings.prompts.interpreter_path),
                "planner": _file_digest(planner_settings.prompts.planner_path),
                "reviewer": _file_digest(planner_settings.prompts.reviewer_path),
                "repairer": _file_digest(planner_settings.prompts.repairer_path),
            },
            "satisfaction_policy_digest": planner_settings.satisfaction_policy.to_json()[
                "policy_digest"
            ]
            if planner_settings.satisfaction_policy is not None
            else None,
            "provider": planner_settings.llm.model_provider,
            "resolved_endpoint_digest": "sha256:"
            + hashlib.sha256(planner_settings.provider.endpoint(environment).encode()).hexdigest(),
            "model": planner_settings.llm.model,
            "review_model": planner_settings.llm.review_model,
            "reasoning_effort": planner_settings.llm.reasoning_effort,
            **(
                {
                    "planning_profile": {
                        "profile_id": planning_profile.profile_id,
                        "revision": planning_profile.revision,
                        "digest": planning_profile.profile_digest,
                    }
                }
                if planning_profile is not None
                else {}
            ),
            **(
                {
                    "execution_planning_evidence": {
                        "schema_version": execution_planning_evidence.to_json()["schema_version"],
                        "digest": execution_planning_evidence.to_json()["digest"],
                    }
                }
                if execution_planning_evidence is not None
                else {}
            ),
        },
        reviewer=reviewer,
        repairer=repairer,
        max_repair_attempts=(planner_settings.max_repair_attempts if reviewer is not None else 0),
        grounding_reader=HttpMissionGroundingReader(
            service_settings.controller_endpoint,
            service_settings.artifact_endpoint,
            service_settings.grounding_timeout_seconds,
            service_settings.max_grounding_state_evidence,
            service_settings.max_grounding_memory_evidence,
            transport=UrllibGroundingJsonTransport(
                {
                    origin: context
                    for origin, context in (
                        (
                            service_settings.controller_endpoint,
                            _client_tls_context(
                                service_settings.controller_client_cert,
                                service_settings.controller_client_key,
                                service_settings.controller_client_ca,
                            ),
                        ),
                        (
                            service_settings.artifact_endpoint,
                            _client_tls_context(
                                service_settings.artifact_client_cert,
                                service_settings.artifact_client_key,
                                service_settings.artifact_client_ca,
                            ),
                        ),
                    )
                    if context is not None
                }
            ),
            admitted_world_payload_schemas=(service_settings.grounding_world_payload_schemas),
            max_gaps=service_settings.max_grounding_gaps,
            max_acquisition_attempts=service_settings.grounding_acquisition_attempts,
            semantic_evidence_path=service_settings.grounding_semantic_evidence_path,
            planning_world_evidence_path=service_settings.grounding_planning_world_evidence_path,
            planning_world_evidence_required=(
                service_settings.grounding_planning_world_evidence_required
            ),
        ),
    )
    return engine, service_settings


class MissionRequestHandler(BaseHTTPRequestHandler):
    """Expose bounded Mission Request commands without owning execution state."""

    server: MissionRequestHttpServer
    _principal: Principal | None = None
    _idempotency_key: str | None = None

    def log_message(self, format: str, *args: Any) -> None:
        """Route request metadata through logging without recording instruction bodies."""
        LOG.info("%s - %s", self.address_string(), format % args)

    def do_GET(self) -> None:
        """Return process health or one durable Mission Request projection."""
        path = self._path()
        if path == "/healthz":
            self._send(HTTPStatus.OK, {"status": "ok"})
            return
        if path == "/readyz":
            try:
                readiness = self.server.engine.readiness()
            except (OSError, sqlite3.Error, MissionRequestError):
                self._send(
                    HTTPStatus.SERVICE_UNAVAILABLE, {"ready": False, "storage": "unavailable"}
                )
            else:
                self._send(
                    HTTPStatus.OK
                    if readiness.get("ready") is True
                    else HTTPStatus.SERVICE_UNAVAILABLE,
                    readiness,
                )
            return
        if not self._authorize(path, "GET"):
            return
        if path == "/metrics":
            self._send(
                HTTPStatus.OK,
                {
                    **self.server.engine.metrics(),
                    "http_connections": self.server.transport_metrics(),
                },
            )
            return
        if path == "/metrics/prometheus":
            self._send_text(
                HTTPStatus.OK,
                self.server.engine.metrics_prometheus() + self.server.transport_prometheus(),
            )
            return
        lookup = "/v1/mission-requests/by-mission/"
        if path.startswith(lookup):
            principal = self._principal
            if principal is None or principal[2] == "*":
                self._send(HTTPStatus.FORBIDDEN, {"error": "Mission lookup requires one tenant"})
                return
            try:
                mission = unquote(path[len(lookup) :], errors="strict")
                request_id = self.server.engine.request_for_mission(mission, principal[2])
            except (MissionRequestError, UnicodeError):
                self._send(HTTPStatus.BAD_REQUEST, {"error": "invalid Mission lookup"})
                return
            except (OSError, sqlite3.Error):
                self._send(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Mission lookup unavailable"})
                return
            if request_id is None:
                self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
            else:
                self._send(
                    HTTPStatus.OK,
                    {
                        "schema_version": "roboguide.mission-request-lookup/v0.1",
                        "mission_id": mission,
                        "request_id": request_id,
                    },
                )
            return
        prefix = "/v1/mission-requests/"
        if path.startswith(prefix):
            parts = path[len(prefix) :].split("/")
            if len(parts) == 1 and parts[0]:
                if not self._authorize_request_owner(parts[0]):
                    return
                self._run(lambda: self.server.engine.get(parts[0]), HTTPStatus.OK)
                return
            if len(parts) == 2 and parts[0] and parts[1] == "observations":
                if not self._authorize_request_owner(parts[0]):
                    return
                self._run(lambda: self.server.engine.get(parts[0]).observations(), HTTPStatus.OK)
                return
            if len(parts) == 2 and parts[0] and parts[1] == "provenance":
                if not self._authorize_request_owner(parts[0]):
                    return
                try:
                    query = parse_qs(
                        self.path.partition("?")[2], keep_blank_values=True, max_num_fields=2
                    )
                    if set(query) - {"after_sequence", "limit"} or any(
                        len(values) != 1 for values in query.values()
                    ):
                        raise ValueError("invalid provenance query")
                    after_sequence = int(query.get("after_sequence", ["0"])[0])
                    limit = int(query.get("limit", ["32"])[0])
                except ValueError:
                    self._send(HTTPStatus.BAD_REQUEST, {"error": "invalid provenance page"})
                    return
                self._run(
                    lambda: self.server.engine.provenance(
                        parts[0], after_sequence=after_sequence, limit=limit
                    ),
                    HTTPStatus.OK,
                )
                return
            if len(parts) == 3 and all(parts) and parts[1] == "grounding-contexts":
                if not self._authorize_request_owner(parts[0]):
                    return
                self._run(
                    lambda: self.server.engine.grounding_context(parts[0], parts[2]),
                    HTTPStatus.OK,
                )
                return
        self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        """Create, clarify, approve, retry, or cancel one Mission Request."""
        path = self._path()
        if not self._authorize(path, "POST"):
            return
        try:
            body = self._read_json()
        except (MissionRequestError, UnicodeDecodeError, json.JSONDecodeError) as error:
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        if path in {"/v1/http-commands/observe", "/v1/http-commands/reconcile"}:
            if set(body) != {"method", "path", "idempotency_key"} or not all(
                isinstance(value, str) and value.strip() for value in body.values()
            ):
                self._send(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "command lookup requires method, path and idempotency_key"},
                )
                return
            principal = self._principal
            if principal is None:
                self._send(HTTPStatus.UNAUTHORIZED, {"error": "command principal is unavailable"})
                return
            identity = (
                principal[0],
                principal[2],
                str(body["method"]),
                str(body["path"]),
                str(body["idempotency_key"]),
            )
            try:
                result = self.server.engine.http_command(
                    identity, reconcile=path.endswith("/reconcile")
                )
            except MissionRequestError as error:
                self._send(
                    HTTPStatus.NOT_FOUND
                    if str(error).startswith("unknown HTTP")
                    else HTTPStatus.CONFLICT,
                    {"error": str(error)},
                )
            except (OSError, sqlite3.Error):
                self._send(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": "command recovery storage unavailable"},
                )
            else:
                self._send(HTTPStatus.OK, result)
            return
        if path == "/v1/tenant-migrations":
            if set(body) != {"request_id", "tenant_id", "source_digest"} or not all(
                isinstance(value, str) and value.strip() for value in body.values()
            ):
                self._send(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "tenant migration requires request_id, tenant_id and source_digest"},
                )
                return
            principal = self._principal
            if principal is None or principal[1] != "admin":
                self._send(
                    HTTPStatus.FORBIDDEN, {"error": "trusted administrator migration is required"}
                )
                return
            if principal[2] != "*" and body["tenant_id"] != principal[2]:
                self._send(
                    HTTPStatus.FORBIDDEN, {"error": "administrator scope does not grant migration"}
                )
                return
            self._run(
                lambda: self.server.engine.migrate_request_tenant(
                    str(body["request_id"]),
                    str(body["tenant_id"]),
                    str(body["source_digest"]),
                    principal[0],
                ),
                HTTPStatus.OK,
                body,
            )
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
                body,
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
        if not self._authorize_request_owner(request_id):
            return
        if command == "messages":
            if set(body) not in ({"text"}, {"text", "question_id"}) or not isinstance(
                body.get("text"), str
            ):
                self._send(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "message body requires text and optional question_id"},
                )
                return
            question_id = body.get("question_id")
            if question_id is not None and (
                not isinstance(question_id, str) or not question_id.strip()
            ):
                self._send(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "question_id must be nonblank text when supplied"},
                )
                return
            self._run(
                lambda: self.server.engine.add_message(
                    request_id, cast(str, body["text"]), question_id
                ),
                HTTPStatus.OK,
                body,
            )
        elif command == "approve":
            self._approve(request_id, body)
        elif command == "retry" and not body:
            self._run(lambda: self.server.engine.retry(request_id), HTTPStatus.OK, body)
        elif command == "cancel" and not body:
            self._run(lambda: self.server.engine.cancel(request_id), HTTPStatus.OK, body)
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
            body,
        )

    def _authorize(self, path: str, method: str) -> bool:
        """Authorize one HTTP route from the TLS certificate principal or local profile."""
        required = self.server.required_role(path, method)
        if required is None:
            return True
        principal = self.server.principal_for(self.connection)
        if principal is None:
            self._send(HTTPStatus.UNAUTHORIZED, {"error": "mission principal is not authenticated"})
            return False
        local_metrics = (
            required == "metrics-reader"
            and not self.server.mtls_enabled
            and not isinstance(self.connection, ssl.SSLSocket)
            and principal[0] == "loopback:operator"
        )
        if not local_metrics and not self.server.role_grants(principal, required):
            self._send(HTTPStatus.FORBIDDEN, {"error": "mission principal is not authorized"})
            return False
        selector = self.headers.get("X-RoboGuide-Tenant")
        if selector is not None:
            if (
                not selector.strip()
                or len(selector) > 128
                or (principal[2] != "*" and principal[2] != selector)
            ):
                self._send(
                    HTTPStatus.FORBIDDEN, {"error": "certificate does not delegate this tenant"}
                )
                return False
            principal = (principal[0], principal[1], selector)
        if path == "/v1/mission-requests" and principal[2] == "*":
            self._send(
                HTTPStatus.BAD_REQUEST, {"error": "global administrator must select a tenant"}
            )
            return False
        self._principal = principal
        LOG.info(
            "mission HTTP request authorized principal=%s role=%s method=%s path=%s "
            "correlation=%s idempotency=%s",
            principal[0],
            principal[1],
            method,
            path,
            self.headers.get("X-RoboGuide-Correlation-Id", ""),
            self.headers.get("Idempotency-Key", ""),
        )
        try:
            self.server.engine.record_http_audit(
                principal_id=principal[0],
                tenant_id=principal[2],
                role=principal[1],
                method=method,
                path=path,
                correlation_id=self.headers.get("X-RoboGuide-Correlation-Id", "").strip()
                or "local-unknown",
                idempotency_key=self.headers.get("Idempotency-Key", "").strip() or "none",
            )
        except (OSError, sqlite3.Error, MissionRequestError) as error:
            LOG.error("Mission HTTP audit persistence failed: %s", error)
            self._send(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "audit persistence unavailable"})
            return False
        return True

    def _authorize_request_owner(self, request_id: str) -> bool:
        """Reject cross-tenant access before reading or mutating a Mission Request."""
        principal = self._principal
        if principal is None:
            self._send(HTTPStatus.UNAUTHORIZED, {"error": "mission principal is not authenticated"})
            return False
        try:
            owner = self.server.engine.request_tenant(request_id)
            if owner is None:
                if principal[1] == "admin" and self.command == "GET":
                    return True
                if principal[0] == "loopback:operator":
                    return True
                self._send(HTTPStatus.FORBIDDEN, {"error": "Mission Request tenant is unbound"})
                return False
            if owner != principal[2] and not (principal[1] == "admin" and principal[2] == "*"):
                self._send(
                    HTTPStatus.FORBIDDEN, {"error": "Mission Request belongs to another tenant"}
                )
                return False
            return True
        except (MissionRequestError, sqlite3.Error, OSError) as error:
            LOG.error("Mission Request tenant authorization failed: %s", error)
            self._send(
                HTTPStatus.SERVICE_UNAVAILABLE, {"error": "tenant authorization unavailable"}
            )
            return False

    def _run(
        self,
        operation: Any,
        success: HTTPStatus,
        body: JSONObject | None = None,
    ) -> None:
        """Execute one engine command and map domain errors without exposing tracebacks."""
        if self.command == "POST" and not self._begin_idempotency(body or {}):
            return
        try:
            principal = self._principal
            effective_tenant = principal[2] if principal is not None else "local"
            if (
                principal is not None
                and principal[1] == "admin"
                and principal[2] == "*"
                and self._path().startswith("/v1/mission-requests/")
            ):
                target = self._path().removeprefix("/v1/mission-requests/").split("/")[0]
                effective_tenant = self.server.engine.request_tenant(target) or "*"
            context = (
                self.server.engine.authenticated_command(
                    effective_tenant,
                    principal[1] == "admin",
                    (principal[0], principal[2], self.command, self._path(), self._idempotency_key)
                    if self._idempotency_key is not None
                    else None,
                )
                if self.command == "POST" and principal is not None
                else nullcontext()
            )
            with context:
                record = operation()
                response = record if isinstance(record, dict) else record.to_json()
                try:
                    self._complete_idempotency(success, response)
                except (MissionRequestError, sqlite3.Error, OSError) as error:
                    LOG.error("Mission HTTP response durability failed: %s", error)
                    self._send(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": "response durability unavailable"},
                    )
                    return
        except MissionRequestError as error:
            status = (
                HTTPStatus.NOT_FOUND
                if str(error).startswith("unknown Mission Request")
                else HTTPStatus.CONFLICT
            )
            response = {"error": str(error)}
            try:
                self._complete_idempotency(status, response)
            except (MissionRequestError, sqlite3.Error, OSError):
                self._send(
                    HTTPStatus.SERVICE_UNAVAILABLE, {"error": "response durability unavailable"}
                )
                return
            self._send(status, response)
        except Exception:  # pragma: no cover - defensive process boundary
            LOG.exception("Mission Request command failed")
            self._send(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "command outcome requires reconciliation"},
            )
        else:
            self._send(success, response)

    def _begin_idempotency(self, body: JSONObject) -> bool:
        """Reserve a caller key or return its previously committed response."""
        self._idempotency_key = None
        principal = self._principal
        key = self.headers.get("Idempotency-Key", "").strip()
        if principal is not None and principal[0] != "loopback:operator" and not key:
            self._send(HTTPStatus.BAD_REQUEST, {"error": "Idempotency-Key is required"})
            return False
        if principal is None or not key:
            return True
        digest = hashlib.sha256(
            json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        try:
            state, status, response = self.server.engine.begin_http_idempotency(
                principal_id=principal[0],
                tenant_id=principal[2],
                method=self.command,
                path=self._path(),
                idempotency_key=key,
                request_digest=f"sha256:{digest}",
            )
        except MissionRequestError as error:
            self._send(HTTPStatus.CONFLICT, {"error": str(error)})
            return False
        except (OSError, sqlite3.Error):
            self._send(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "idempotency storage unavailable"})
            return False
        if state == HTTP_IDEMPOTENCY_COMPLETE:
            if status is None or response is None:
                self._send(
                    HTTPStatus.SERVICE_UNAVAILABLE, {"error": "idempotency response unavailable"}
                )
            else:
                self._send(HTTPStatus(status), response)
            return False
        if state != "new":
            self._send(
                HTTPStatus.CONFLICT,
                {
                    "error": "the same Idempotency-Key requires observation or reconciliation",
                    "reconciliation_endpoint": "/v1/http-commands/reconcile",
                    "original_method": self.command,
                    "original_path": self._path(),
                    "idempotency_key": key,
                },
            )
            return False
        self._idempotency_key = key
        return True

    def _complete_idempotency(self, status: HTTPStatus, response: JSONObject) -> None:
        """Publish a successful response for the current reserved caller key."""
        if self._idempotency_key is None or self._principal is None:
            return
        self.server.engine.complete_http_idempotency(
            principal_id=self._principal[0],
            tenant_id=self._principal[2],
            method=self.command,
            path=self._path(),
            idempotency_key=self._idempotency_key,
            status_code=int(status),
            response=response,
        )
        self._idempotency_key = None

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

    def _send_text(self, status: HTTPStatus, value: str) -> None:
        """Write one scrapeable text response with explicit framing."""
        payload = value.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
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
        role_map: Mapping[str, CertificateGrant | tuple[str, str]] | None = None,
        tls_context: ssl.SSLContext | None = None,
        authorization_path: Path | None = None,
        connection_limit: int = 128,
        connection_timeout_seconds: float = 35.0,
    ) -> None:
        """Bind the configured listener and expose immutable service dependencies."""
        try:
            loopback = ip_address(address[0]).is_loopback
        except ValueError:
            loopback = address[0] == "localhost"
        if not loopback and (tls_context is None or not role_map):
            raise MissionServiceConfigError(
                "remote Mission listener requires mTLS and certificate grants"
            )
        if type(connection_limit) is not int or not 1 <= connection_limit <= 1024:
            raise MissionServiceConfigError(
                "Mission connection limit must be an integer in 1..1024"
            )
        if (
            type(connection_timeout_seconds) not in (int, float)
            or not 0 < connection_timeout_seconds <= 900
        ):
            raise MissionServiceConfigError(
                "Mission transport timeout must be positive and at most 900 seconds"
            )
        self.connection_timeout_seconds = connection_timeout_seconds
        self.connection_limit = connection_limit
        self._connections = threading.BoundedSemaphore(connection_limit)
        self._transport_lock = threading.Lock()
        self._active_connections = 0
        self._rejected_connections = 0
        self._service_lock = engine.acquire_http_service_lock()
        try:
            if authorization_path is not None:
                profile = load_authorization(authorization_path)
                engine.accept_authorization(profile)
                role_map = profile.grants
            super().__init__(address, MissionRequestHandler)
        except BaseException:
            self._service_lock.close()
            raise
        self.engine = engine
        self.max_request_bytes = max_request_bytes
        self.role_map = {
            digest: grant
            if isinstance(grant, CertificateGrant)
            else CertificateGrant(digest, *grant)
            for digest, grant in (role_map or {}).items()
        }
        self.authorization_path = authorization_path
        self._authorization_lock = threading.RLock()
        self.tls_context = tls_context
        self.mtls_enabled = tls_context is not None

    def server_close(self) -> None:
        """Wait for owned HTTP workers before releasing the single-writer process lock."""
        try:
            super().server_close()
        finally:
            self._service_lock.close()

    def get_request(self) -> tuple[Any, Any]:
        """Accept one TCP request and perform mTLS before HTTP parsing when configured."""
        connection, address = super().get_request()
        if self.tls_context is None:
            return connection, address
        try:
            connection.settimeout(5.0)
            return self.tls_context.wrap_socket(
                connection, server_side=True, do_handshake_on_connect=False
            ), address
        except OSError:
            connection.close()
            raise

    def process_request(self, request: Any, client_address: Any) -> None:
        """Bound independent workers so unauthenticated clients cannot exhaust threads."""
        if not self._connections.acquire(blocking=False):
            with self._transport_lock:
                self._rejected_connections = min(self._rejected_connections + 1, 2**64 - 1)
            self.shutdown_request(request)
            return
        with self._transport_lock:
            self._active_connections += 1
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._release_transport()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        """Release the worker slot after normal handling, timeout or handshake rejection."""

        def close_original() -> None:
            """Close only the original transport; committed Mission/model work keeps its journal."""
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        timer = threading.Timer(self.connection_timeout_seconds, close_original)
        timer.daemon = True
        try:
            timer.start()
            super().process_request_thread(request, client_address)
        finally:
            timer.cancel()
            self._release_transport()

    def _release_transport(self) -> None:
        """Release the original worker permit and its observation after actual close or failure."""
        with self._transport_lock:
            self._active_connections -= 1
        self._connections.release()

    def transport_metrics(self) -> JSONObject:
        """Observe bounded process capacity without granting HTTP or Mission authority."""
        with self._transport_lock:
            return {
                "active": self._active_connections,
                "limit": self.connection_limit,
                "rejected_total": self._rejected_connections,
            }

    def transport_prometheus(self) -> str:
        """Render fixed metric names without peer, request, tenant or certificate labels."""
        metrics = self.transport_metrics()
        return "".join(
            f"# TYPE roboguide_mission_http_connections_{name} {kind}\n"
            f"roboguide_mission_http_connections_{name} {metrics[name]}\n"
            for name, kind in (
                ("active", "gauge"),
                ("limit", "gauge"),
                ("rejected_total", "counter"),
            )
        )

    def finish_request(self, request: Any, client_address: Any) -> None:
        """Perform the bounded TLS handshake in its worker before parsing HTTP."""
        if isinstance(request, ssl.SSLSocket):
            request.do_handshake()
        request.settimeout(30.0)
        super().finish_request(request, client_address)

    def required_role(self, path: str, method: str) -> str | None:
        """Return the minimum role required by one Mission Service route."""
        if method == "GET" and path in {"/metrics", "/metrics/prometheus"}:
            return "metrics-reader"
        if method == "POST" and path == "/v1/tenant-migrations":
            return "admin"
        if method == "POST" and path == "/v1/http-commands/observe":
            return "viewer"
        if method == "POST" and path == "/v1/http-commands/reconcile":
            return "submitter"
        if method == "GET" and path.startswith("/v1/mission-requests/"):
            return "viewer"
        if method == "POST" and path == "/v1/mission-requests":
            return "submitter"
        if method == "POST" and path.endswith("/messages"):
            return "submitter"
        if method == "POST" and path.endswith("/approve"):
            return "approver"
        if method == "POST" and path.endswith(("/retry", "/cancel")):
            return "operator"
        return None

    def principal_for(self, connection: object) -> Principal | None:
        """Resolve one request principal from a peer certificate or trusted local mode."""
        if isinstance(connection, ssl.SSLSocket):
            certificate = connection.getpeercert(binary_form=True)
            if not certificate:
                return None
            digest = f"sha256:{hashlib.sha256(certificate).hexdigest()}"
            now_ms = int(time.time() * 1000)
            with self._authorization_lock:
                if self.authorization_path is not None:
                    try:
                        profile = load_authorization(self.authorization_path)
                        self.engine.accept_authorization(profile)
                    except (OSError, ValueError, MissionRequestError, sqlite3.Error):
                        LOG.error("authorization revision could not be committed; request fenced")
                        return None
                    self.role_map = profile.grants
                grant = self.role_map.get(digest)
                decoded = connection.getpeercert()
                if not decoded or "notAfter" not in decoded:
                    return None
                certificate_expiry = int(ssl.cert_time_to_seconds(str(decoded["notAfter"])) * 1000)
                if grant is None or not grant.active(now_ms) or now_ms >= certificate_expiry:
                    return None
                return (grant.principal_id, grant.role, grant.tenant_id)
        if self.mtls_enabled:
            return None
        return ("loopback:operator", "operator", "local")

    @staticmethod
    def role_grants(principal: Principal, required: str) -> bool:
        """Return whether a mapped role grants one bounded Mission Service permission."""
        if required == "metrics-reader":
            return principal[1] in {"metrics-reader", "admin"} and principal[2] == "*"
        if principal[1] == "metrics-reader":
            return False
        rank = {"viewer": 0, "submitter": 1, "approver": 2, "operator": 3, "admin": 4}
        return rank.get(principal[1], -1) >= rank.get(required, 99)


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
    role_map = _load_role_map(settings.mtls_role_map)
    tls_context = _build_mtls_context(settings) if settings.mtls_server_cert is not None else None
    server = MissionRequestHttpServer(
        (settings.listen_host, settings.listen_port),
        engine,
        settings.max_request_bytes,
        role_map=role_map,
        tls_context=tls_context,
        authorization_path=settings.mtls_role_map,
        connection_limit=settings.connection_limit,
        connection_timeout_seconds=settings.connection_timeout_seconds,
    )
    LOG.info("listening on %s:%s", settings.listen_host, settings.listen_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("shutdown requested")
    finally:
        server.server_close()
    return 0


def _load_role_map(path: Path | None) -> dict[str, CertificateGrant]:
    """Load and validate certificate digest to Mission role mappings."""
    return load_authorization(path).grants


def _client_tls_context(
    cert: Path | None, key: Path | None, ca: Path | None
) -> ssl.SSLContext | None:
    """Load an origin-specific client identity, trusting only its configured authority CA."""
    if cert is None and key is None and ca is None:
        return None
    if cert is None or key is None or ca is None:
        raise MissionServiceConfigError("service client mTLS identity is incomplete")
    context = ssl.create_default_context(cafile=str(ca))
    context.load_cert_chain(cert, key)
    return context


def _build_mtls_context(settings: MissionServiceSettings) -> ssl.SSLContext:
    """Build a client-certificate-required TLS context from startup-owned files."""
    if not all(
        path is not None
        for path in (
            settings.mtls_server_cert,
            settings.mtls_server_key,
            settings.mtls_client_ca,
        )
    ):
        raise MissionServiceConfigError("Mission Service mTLS profile is incomplete")
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(cafile=str(settings.mtls_client_ca))
    context.load_cert_chain(
        certfile=str(settings.mtls_server_cert), keyfile=str(settings.mtls_server_key)
    )
    return context
