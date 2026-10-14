"""SQLite persistence for complete Mission Request deliberation projections."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path
from typing import BinaryIO

from mission.authorization import AUTHORIZATION_SCHEMA, AuthorizationProfile
from mission.deployment_recovery import DeploymentRecoverySession
from mission.grounding_context import GroundingContextSnapshot
from mission.models import JSONObject
from mission.recovery import RequestRecoveryEvidence
from mission.rejected_draft import RejectedDraftEvidence
from mission.request_record import MissionRequestError, MissionRequestRecord, _json_object
from mission.security_context import HTTP_TENANT as _HTTP_TENANT
from mission.submission_evidence import (
    COMPATIBLE_OBSERVATIONS_SCHEMAS,
    DEPLOYMENT_OBSERVATIONS_SCHEMA,
    OBSERVATIONS_SCHEMA,
    ControllerAdmissionEvidence,
    ControllerSubmissionEvidence,
    canonical_plan_digest,
)

STORAGE_SCHEMA = "roboguide.mission-request-storage/v0.2"
LEGACY_STORAGE_SCHEMA = "roboguide.mission-request-storage/v0.1"
HTTP_IDEMPOTENCY_PENDING = "pending"
HTTP_IDEMPOTENCY_COMPLETE = "complete"
HttpCommandIdentity = tuple[str, str, str, str, str]
_HTTP_COMMAND: ContextVar[HttpCommandIdentity | None] = ContextVar(
    "mission_http_command", default=None
)


class HttpWriterLease:
    """Own one explicit process lock without depending on inherited descriptor closure."""

    def __init__(self, handle: BinaryIO) -> None:
        """Retain a successfully locked descriptor until the HTTP authority has closed."""
        self._handle = handle

    def close(self) -> None:
        """Release at the application owner even when a child retains the same descriptor."""
        if not self._handle.closed:
            try:
                fcntl.flock(self._handle, fcntl.LOCK_UN)
            finally:
                self._handle.close()


def _restore_record(document: object) -> MissionRequestRecord:
    """Read legacy rows or the atomic request/observations storage envelope."""
    value = _json_object(document, "mission request storage")
    if value.get("schema_version") not in {STORAGE_SCHEMA, LEGACY_STORAGE_SCHEMA}:
        return MissionRequestRecord.from_json(value)
    request = _json_object(value["request"], "request")
    record = MissionRequestRecord.from_json(request)
    observations = _json_object(value["observations"], "observations")
    schema = observations.get("schema_version")
    if (
        not isinstance(schema, str)
        or schema not in COMPATIBLE_OBSERVATIONS_SCHEMAS
        or observations.get("request_id") != record.request_id
        or observations.get("mission_id") != record.mission_id
        or observations.get("request_record_digest") != canonical_plan_digest(request)
    ):
        raise MissionRequestError("request observations are detached from durable request")
    submission = observations.get("submission_evidence")
    failure = observations.get("failure_evidence")
    recovery = observations.get("recovery_evidence")
    admission = observations.get("admission_evidence")
    if (
        observations.get("schema_version") != "roboguide.mission-request-observations/v0.1"
        and "recovery_evidence" not in observations
    ):
        raise MissionRequestError("v0.2 observations must declare recovery evidence availability")
    if (
        schema in {OBSERVATIONS_SCHEMA, DEPLOYMENT_OBSERVATIONS_SCHEMA}
        and "admission_evidence" not in observations
    ):
        raise MissionRequestError("v0.3 observations must declare admission evidence availability")
    if (schema == DEPLOYMENT_OBSERVATIONS_SCHEMA) != ("deployment_recovery" in observations):
        raise MissionRequestError("deployment recovery observations must declare their schema")
    try:
        recovery_evidence = (
            RequestRecoveryEvidence.from_json(recovery) if recovery is not None else None
        )
        deployment_recovery = (
            DeploymentRecoverySession.from_json(observations["deployment_recovery"])
            if schema == DEPLOYMENT_OBSERVATIONS_SCHEMA
            else None
        )
    except (ValueError, TypeError, KeyError) as error:
        raise MissionRequestError("invalid durable recovery evidence") from error
    drafts_value = observations.get("rejected_drafts", [])
    if not isinstance(drafts_value, list):
        raise MissionRequestError("rejected draft evidence must be a list")
    rejected_drafts = tuple(RejectedDraftEvidence.from_json(draft) for draft in drafts_value)
    seen_indexes: set[int] = set()
    seen_ids: set[str] = set()
    for draft in rejected_drafts:
        if draft.request_id != record.request_id or draft.mission_id != record.mission_id:
            raise MissionRequestError("rejected draft evidence belongs to another request")
        if draft.attempt_index in seen_indexes or draft.attempt_id in seen_ids:
            raise MissionRequestError("rejected draft evidence contains duplicate attempts")
        seen_indexes.add(draft.attempt_index)
        seen_ids.add(draft.attempt_id)
        draft.verify_integrity()
    return replace(
        record,
        submission_evidence=(
            ControllerSubmissionEvidence.from_json(submission) if submission else None
        ),
        failure_evidence=(_json_object(failure, "failure evidence") if failure else None),
        rejected_drafts=rejected_drafts,
        recovery_evidence=recovery_evidence,
        admission_evidence=ControllerAdmissionEvidence.from_json(admission)
        if admission is not None
        else None,
        deployment_recovery=deployment_recovery,
    )


class MissionRequestStore:
    """Persist Mission Request projections in a process-local SQLite database."""

    def __init__(self, database: Path) -> None:
        """Create the database parent and versioned request table."""
        database.parent.mkdir(parents=True, exist_ok=True)
        self._database = database.resolve()
        self._lock = threading.RLock()
        self._active_http_commands: set[HttpCommandIdentity] = set()
        self._http_instance = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS mission_authorization (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), profile_id TEXT NOT NULL,
                revision INTEGER NOT NULL, digest TEXT NOT NULL, document_json TEXT NOT NULL)"""
            )
            connection.execute("""CREATE TABLE IF NOT EXISTS mission_tenant_migrations (
                request_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, source_digest TEXT NOT NULL,
                authority_subject TEXT NOT NULL, accepted_at_ms INTEGER NOT NULL)""")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS mission_requests (
                    request_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL UNIQUE,
                    document_json TEXT NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                )
                """
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS mission_grounding_contexts (
                    context_digest TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    document_json TEXT NOT NULL,
                    captured_at_ms INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS mission_grounding_contexts_request_id
                ON mission_grounding_contexts(request_id, captured_at_ms, context_digest)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS mission_request_history (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    document_digest TEXT NOT NULL UNIQUE,
                    document_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS mission_http_audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    principal_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    method TEXT NOT NULL,
                    path TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    recorded_at_ms INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS mission_request_tenants (
                    request_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    bound_at_ms INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS mission_http_idempotency (
                    principal_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    method TEXT NOT NULL,
                    path TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending', 'complete')),
                    status_code INTEGER,
                    response_json TEXT,
                    created_at_ms INTEGER NOT NULL,
                    completed_at_ms INTEGER,
                    PRIMARY KEY(principal_id, tenant_id, method, path, idempotency_key)
                )
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(mission_http_idempotency)")
            }
            for name, definition in (
                ("request_id", "TEXT"),
                ("progress_json", "TEXT"),
                ("progress_digest", "TEXT"),
                ("executor_instance", "TEXT"),
            ):
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE mission_http_idempotency ADD COLUMN {name} {definition}"
                    )

    def acquire_http_service_lock(self) -> HttpWriterLease:
        """Own the local SQLite HTTP writer process until the server closes; contention rejects."""
        path = self._database.with_name(self._database.name + ".service.lock")
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        handle = os.fdopen(descriptor, "rb")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise MissionRequestError("Mission SQLite HTTP writer is already owned") from None
        return HttpWriterLease(handle)

    def _connect(self) -> sqlite3.Connection:
        """Open one short-lived SQLite connection with bounded lock waiting."""
        return sqlite3.connect(self._database, timeout=30.0)

    def accept_authorization(self, profile: AuthorizationProfile) -> None:
        """Commit grants before publication, fencing rollback and attribution changes."""
        with self._lock, self._connect() as connection:
            prior = connection.execute(
                "SELECT profile_id,revision,digest,document_json "
                "FROM mission_authorization WHERE singleton=1"
            ).fetchone()
            if profile.profile_id is None:
                if prior is not None:
                    raise MissionRequestError(
                        "revisioned authorization cannot downgrade to a static map"
                    )
                return
            document = json.dumps(
                profile.document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            digest = hashlib.sha256(document.encode()).hexdigest()
            if prior is not None:
                if (
                    prior[0] != profile.profile_id
                    or int(prior[1]) > profile.revision
                    or int(prior[1]) == profile.revision
                    and prior[2] != digest
                ):
                    raise MissionRequestError("authorization profile rollback or revision conflict")
                previous = json.loads(str(prior[3]))
                if previous.get("schema_version") != AUTHORIZATION_SCHEMA:
                    raise MissionRequestError("stored authorization schema is invalid")
                for certificate, grant in previous["grants"].items():
                    current = profile.grants.get(certificate)
                    if current is None:
                        raise MissionRequestError(
                            "retained certificate grants must be revoked, not removed"
                        )
                    if (
                        current.principal_id != grant["principal_id"]
                        or current.tenant_id != grant["tenant_id"]
                    ):
                        raise MissionRequestError("certificate attribution is immutable")
                    if grant.get("revoked", False) and not current.revoked:
                        raise MissionRequestError("revoked certificates cannot be reactivated")
                previous_tenants = {
                    grant["principal_id"]: grant["tenant_id"]
                    for grant in previous["grants"].values()
                }
                if any(
                    previous_tenants.get(grant.principal_id, grant.tenant_id) != grant.tenant_id
                    for grant in profile.grants.values()
                ):
                    raise MissionRequestError("principal tenant attribution is immutable")
                if prior[2] == digest:
                    return
            connection.execute(
                """INSERT INTO mission_authorization VALUES (1,?,?,?,?)
                ON CONFLICT(singleton) DO UPDATE SET revision=excluded.revision,
                digest=excluded.digest,document_json=excluded.document_json""",
                (profile.profile_id, profile.revision, digest, document),
            )

    @contextmanager
    def authenticated_command(
        self,
        tenant_id: str,
        administrator: bool,
        http_command: HttpCommandIdentity | None = None,
    ) -> Iterator[None]:
        """Bind every command save to its caller without holding a transaction across network IO."""
        token = _HTTP_TENANT.set((tenant_id, administrator))
        command_token = _HTTP_COMMAND.set(http_command)
        if http_command is not None:
            with self._lock:
                if http_command in self._active_http_commands:
                    _HTTP_TENANT.reset(token)
                    _HTTP_COMMAND.reset(command_token)
                    raise MissionRequestError("HTTP command is already executing")
                self._active_http_commands.add(http_command)
        try:
            yield
        finally:
            _HTTP_TENANT.reset(token)
            _HTTP_COMMAND.reset(command_token)
            if http_command is not None:
                with self._lock:
                    self._active_http_commands.remove(http_command)

    def readiness(self) -> JSONObject:
        """Probe the actual writer and schema without committing a Mission mutation."""
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE mission_requests SET updated_at_ms=updated_at_ms WHERE 0")
            connection.execute("SELECT request_id FROM mission_request_tenants LIMIT 1").fetchall()
            connection.rollback()
        return {"ready": True, "storage": "writable"}

    def save(self, record: MissionRequestRecord) -> None:
        """Atomically persist the request projection and its immutable current context."""
        document = json.dumps(
            {
                "schema_version": STORAGE_SCHEMA,
                "request": record.to_json(),
                "observations": record.observations().to_json(),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                "SELECT document_json FROM mission_requests WHERE request_id = ?",
                (record.request_id,),
            ).fetchone()
            caller = _HTTP_TENANT.get()
            if caller is not None:
                tenant, administrator = caller
                owner = connection.execute(
                    "SELECT tenant_id FROM mission_request_tenants WHERE request_id=?",
                    (record.request_id,),
                ).fetchone()
                if owner is not None and str(owner[0]) != tenant and not administrator:
                    raise MissionRequestError("Mission Request belongs to another tenant")
                if owner is None:
                    if prior is not None and tenant != "local":
                        raise MissionRequestError("Mission Request tenant is unbound")
                    connection.execute(
                        "INSERT INTO mission_request_tenants VALUES (?, ?, ?)",
                        (record.request_id, tenant, record.updated_at_ms),
                    )
            if prior is not None and str(prior[0]) != document:
                previous = json.loads(str(prior[0]))
                if previous.get("request", previous)["lifecycle"] in {
                    "Failed",
                    "Blocked",
                    "NeedsClarification",
                    "AwaitingApproval",
                }:
                    # Preserve the complete prior review/failure/POST boundary
                    # in the same transaction before an explicit command replaces it.
                    connection.execute(
                        """INSERT OR IGNORE INTO mission_request_history
                        (request_id, document_digest, document_json) VALUES (?, ?, ?)""",
                        (record.request_id, canonical_plan_digest(previous), str(prior[0])),
                    )
            if record.grounding_context is not None:
                self._save_grounding_context(connection, record.grounding_context)
            connection.execute(
                """
                INSERT INTO mission_requests(request_id, mission_id, document_json, updated_at_ms)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(request_id) DO UPDATE SET
                    mission_id = excluded.mission_id,
                    document_json = excluded.document_json,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (record.request_id, record.mission_id, document, record.updated_at_ms),
            )
            command = _HTTP_COMMAND.get()
            if command is not None:
                updated = connection.execute(
                    """UPDATE mission_http_idempotency
                    SET request_id=?, progress_json=?, progress_digest=?
                    WHERE principal_id=? AND tenant_id=? AND method=?
                      AND path=? AND idempotency_key=?
                      AND status='pending' AND (request_id IS NULL OR request_id=?)""",
                    (
                        record.request_id,
                        document,
                        canonical_plan_digest(json.loads(document)),
                        *command,
                        record.request_id,
                    ),
                )
                if updated.rowcount != 1:
                    raise MissionRequestError("HTTP command progress identity conflicts")

    def _save_grounding_context(
        self,
        connection: sqlite3.Connection,
        context: GroundingContextSnapshot,
    ) -> None:
        """Insert one digest-addressed snapshot once and reject any conflicting replacement."""
        document = json.dumps(
            context.to_json(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        row = connection.execute(
            """
            SELECT request_id, document_json
            FROM mission_grounding_contexts
            WHERE context_digest = ?
            """,
            (context.context_digest,),
        ).fetchone()
        if row is not None:
            if str(row[0]) != context.request_id or str(row[1]) != document:
                raise MissionRequestError(
                    "grounding context digest conflicts with durable evidence"
                )
            return
        connection.execute(
            """
            INSERT INTO mission_grounding_contexts(
                context_digest, request_id, document_json, captured_at_ms
            ) VALUES (?, ?, ?, ?)
            """,
            (
                context.context_digest,
                context.request_id,
                document,
                context.captured_at_ms,
            ),
        )

    def get(self, request_id: str) -> MissionRequestRecord | None:
        """Return one request projection without changing lifecycle."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT document_json FROM mission_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
        if row is None:
            return None
        decoded: object = json.loads(str(row[0]))
        return _restore_record(decoded)

    def grounding_context(
        self, request_id: str, context_digest: str
    ) -> GroundingContextSnapshot | None:
        """Return one immutable historical snapshot by owning request and exact digest."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT document_json
                FROM mission_grounding_contexts
                WHERE request_id = ? AND context_digest = ?
                """,
                (request_id, context_digest),
            ).fetchone()
        if row is None:
            return None
        return GroundingContextSnapshot.from_json(json.loads(str(row[0])))

    def records(self) -> tuple[MissionRequestRecord, ...]:
        """Return all requests in stable identity order for conservative startup recovery."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT document_json FROM mission_requests ORDER BY request_id"
            ).fetchall()
        return tuple(_restore_record(json.loads(str(row[0]))) for row in rows)

    def history(self, request_id: str) -> tuple[MissionRequestRecord, ...]:
        """Read immutable replaced command-boundary records in durable insertion order."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT document_json FROM mission_request_history
                WHERE request_id = ? ORDER BY sequence""",
                (request_id,),
            ).fetchall()
        return tuple(_restore_record(json.loads(str(row[0]))) for row in rows)

    def record_http_audit(
        self,
        *,
        principal_id: str,
        tenant_id: str,
        role: str,
        method: str,
        path: str,
        correlation_id: str,
        idempotency_key: str,
        recorded_at_ms: int,
    ) -> None:
        """Persist one authenticated Mission HTTP command receipt independently of request state."""
        values = (
            principal_id,
            tenant_id,
            role,
            method,
            path,
            correlation_id,
            idempotency_key,
            recorded_at_ms,
        )
        if any(not isinstance(value, str) or not value.strip() for value in values[:-1]):
            raise MissionRequestError("Mission HTTP audit fields must be nonblank")
        if recorded_at_ms < 0:
            raise MissionRequestError("Mission HTTP audit time must be nonnegative")
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO mission_http_audit(
                    principal_id, tenant_id, role, method, path, correlation_id,
                    idempotency_key, recorded_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )

    def http_audit(self) -> tuple[dict[str, object], ...]:
        """Return authenticated Mission HTTP receipts in durable insertion order."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT principal_id, tenant_id, role, method, path, correlation_id,
                       idempotency_key, recorded_at_ms
                FROM mission_http_audit ORDER BY sequence
                """
            ).fetchall()
        return tuple(
            {
                "principal_id": str(row[0]),
                "tenant_id": str(row[1]),
                "role": str(row[2]),
                "method": str(row[3]),
                "path": str(row[4]),
                "correlation_id": str(row[5]),
                "idempotency_key": str(row[6]),
                "recorded_at_ms": int(row[7]),
            }
            for row in rows
        )

    def metrics(self) -> JSONObject:
        """Return bounded aggregate Mission Service metrics without tenant identifiers."""
        with self._lock, self._connect() as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM mission_requests").fetchone()[0])
            lifecycle_rows = connection.execute(
                """
                SELECT json_extract(document_json, '$.request.lifecycle'), COUNT(*)
                FROM mission_requests GROUP BY json_extract(document_json, '$.request.lifecycle')
                ORDER BY 1
                """
            ).fetchall()
            audit_total = int(
                connection.execute("SELECT COUNT(*) FROM mission_http_audit").fetchone()[0]
            )
            idempotency_rows = connection.execute(
                "SELECT status, COUNT(*) FROM mission_http_idempotency "
                "GROUP BY status ORDER BY status"
            ).fetchall()
        lifecycle_metrics: JSONObject = {
            str(row[0]): int(row[1]) for row in lifecycle_rows if row[0] is not None
        }
        idempotency_metrics: JSONObject = {str(row[0]): int(row[1]) for row in idempotency_rows}
        return {
            "schema_version": "roboguide.mission-metrics/v0.1",
            "mission_requests_total": total,
            "mission_requests_by_lifecycle": lifecycle_metrics,
            "authenticated_http_audit_total": audit_total,
            "http_idempotency_by_status": idempotency_metrics,
        }

    def bind_request_tenant(self, request_id: str, tenant_id: str, bound_at_ms: int) -> None:
        """Bind one Mission Request to its authenticated tenant exactly once."""
        if not request_id.strip() or not tenant_id.strip() or bound_at_ms < 0:
            raise MissionRequestError("Mission Request tenant binding is invalid")
        with self._lock, self._connect() as connection:
            prior = connection.execute(
                "SELECT tenant_id FROM mission_request_tenants WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if prior is not None:
                if str(prior[0]) != tenant_id:
                    raise MissionRequestError("Mission Request tenant binding conflicts")
                return
            connection.execute(
                """
                INSERT INTO mission_request_tenants(request_id, tenant_id, bound_at_ms)
                VALUES (?, ?, ?)
                """,
                (request_id, tenant_id, bound_at_ms),
            )

    def request_tenant(self, request_id: str) -> str | None:
        """Return the durable tenant owner of one Mission Request, if bound."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT tenant_id FROM mission_request_tenants WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        return None if row is None else str(row[0])

    def migrate_request_tenant(
        self, request_id: str, tenant_id: str, source_digest: str, subject: str, now_ms: int
    ) -> JSONObject:
        """Migrate one legacy Request from its original document and administrator identity."""
        if any(
            not value.strip() or len(value) > 256
            for value in (request_id, tenant_id, source_digest, subject)
        ):
            raise MissionRequestError("tenant migration fields are invalid")
        with self._lock, self._connect() as connection:
            previous = connection.execute(
                "SELECT tenant_id,source_digest,authority_subject,accepted_at_ms "
                "FROM mission_tenant_migrations WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if previous is not None:
                if previous[:2] != (tenant_id, source_digest):
                    raise MissionRequestError("tenant migration conflicts")
                subject, now_ms = str(previous[2]), int(previous[3])
            else:
                row = connection.execute(
                    "SELECT document_json FROM mission_requests WHERE request_id=?", (request_id,)
                ).fetchone()
                if row is None:
                    raise MissionRequestError(f"unknown Mission Request {request_id}")
                record = _restore_record(json.loads(str(row[0])))
                if canonical_plan_digest(record.to_json()) != source_digest:
                    raise MissionRequestError("tenant migration source digest conflicts")
                if (
                    connection.execute(
                        "SELECT tenant_id FROM mission_request_tenants WHERE request_id=?",
                        (request_id,),
                    ).fetchone()
                    is not None
                ):
                    raise MissionRequestError("Request already has immutable tenant ownership")
                connection.execute(
                    "INSERT INTO mission_request_tenants VALUES (?,?,?)",
                    (request_id, tenant_id, now_ms),
                )
                connection.execute(
                    "INSERT INTO mission_tenant_migrations VALUES (?,?,?,?,?)",
                    (request_id, tenant_id, source_digest, subject, now_ms),
                )
            return {
                "schema_version": "roboguide.request-tenant-migration/v0.1",
                "request_id": request_id,
                "tenant_id": tenant_id,
                "source_digest": source_digest,
                "authority_subject": subject,
                "accepted_at_ms": now_ms,
                "execution_repeated": False,
            }

    def begin_http_idempotency(
        self,
        *,
        principal_id: str,
        tenant_id: str,
        method: str,
        path: str,
        idempotency_key: str,
        request_digest: str,
        created_at_ms: int,
    ) -> tuple[str, int | None, JSONObject | None]:
        """Reserve or replay one durable authenticated HTTP mutation key."""
        values = (principal_id, tenant_id, method, path, idempotency_key, request_digest)
        if any(not value.strip() for value in values) or created_at_ms < 0:
            raise MissionRequestError("Mission HTTP idempotency fields must be nonblank")
        if len(idempotency_key) > 256:
            raise MissionRequestError("Idempotency-Key exceeds 256 characters")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT request_digest, status, status_code, response_json
                FROM mission_http_idempotency
                WHERE principal_id = ? AND tenant_id = ? AND method = ?
                  AND path = ? AND idempotency_key = ?
                """,
                (principal_id, tenant_id, method, path, idempotency_key),
            ).fetchone()
            if row is not None:
                if str(row[0]) != request_digest:
                    raise MissionRequestError(
                        "Idempotency-Key was reused with different request evidence"
                    )
                if str(row[1]) == HTTP_IDEMPOTENCY_PENDING:
                    return HTTP_IDEMPOTENCY_PENDING, None, None
                response_json = row[3]
                if row[2] is None or response_json is None:
                    raise MissionRequestError("durable idempotency response is incomplete")
                decoded = json.loads(str(response_json))
                if not isinstance(decoded, dict) or not all(
                    isinstance(key, str) for key in decoded
                ):
                    raise MissionRequestError("durable idempotency response is not an object")
                return HTTP_IDEMPOTENCY_COMPLETE, int(row[2]), decoded
            connection.execute(
                """
                INSERT INTO mission_http_idempotency(
                    principal_id, tenant_id, method, path, idempotency_key,
                    request_digest, status, created_at_ms, executor_instance
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    principal_id,
                    tenant_id,
                    method,
                    path,
                    idempotency_key,
                    request_digest,
                    HTTP_IDEMPOTENCY_PENDING,
                    created_at_ms,
                    self._http_instance,
                ),
            )
        return "new", None, None

    def complete_http_idempotency(
        self,
        *,
        principal_id: str,
        tenant_id: str,
        method: str,
        path: str,
        idempotency_key: str,
        status_code: int,
        response: JSONObject,
        completed_at_ms: int,
    ) -> None:
        """Publish one successful HTTP response after its Mission mutation commits."""
        if not 200 <= status_code < 500 or completed_at_ms < 0:
            raise MissionRequestError("Mission HTTP idempotency completion is invalid")
        document = json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._lock, self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE mission_http_idempotency
                SET status = ?, status_code = ?, response_json = ?, completed_at_ms = ?
                WHERE principal_id = ? AND tenant_id = ? AND method = ?
                  AND path = ? AND idempotency_key = ? AND status = ?
                """,
                (
                    HTTP_IDEMPOTENCY_COMPLETE,
                    status_code,
                    document,
                    completed_at_ms,
                    principal_id,
                    tenant_id,
                    method,
                    path,
                    idempotency_key,
                    HTTP_IDEMPOTENCY_PENDING,
                ),
            )
            if updated.rowcount != 1:
                raise MissionRequestError("Mission HTTP idempotency reservation is unavailable")

    def http_command(self, identity: HttpCommandIdentity) -> JSONObject:
        """Read a caller-owned command and its atomically associated progress without replay."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """SELECT request_digest,status,status_code,response_json,request_id,
                          progress_json,progress_digest,executor_instance
                   FROM mission_http_idempotency
                   WHERE principal_id=? AND tenant_id=? AND method=?
                     AND path=? AND idempotency_key=?""",
                identity,
            ).fetchone()
            if row is None:
                raise MissionRequestError("unknown HTTP command identity")
            result: JSONObject = {
                "schema_version": "roboguide.http-command-observation/v0.1",
                "method": identity[2],
                "path": identity[3],
                "idempotency_key": identity[4],
                "request_digest": str(row[0]),
                "status": str(row[1]),
                "status_code": int(row[2]) if row[2] is not None else None,
                "response": json.loads(str(row[3])) if row[3] is not None else None,
                "request_id": str(row[4]) if row[4] is not None else None,
                "executing": identity in self._active_http_commands,
                "legacy_unbound": row[7] is None,
                "progress": None,
            }
            if row[5] is not None:
                progress = json.loads(str(row[5]))
                if canonical_plan_digest(progress) != row[6]:
                    raise MissionRequestError("HTTP command progress digest differs")
                record = _restore_record(progress)
                if record.request_id != row[4]:
                    raise MissionRequestError("HTTP command Request identity differs")
                result["progress"] = record.to_json()
            return result

    def reconcile_http_command(self, identity: HttpCommandIdentity, now_ms: int) -> JSONObject:
        """Cache an interrupted outcome without inferring completion or repeating work."""
        with self._lock:
            observed = self.http_command(identity)
            if observed["status"] == HTTP_IDEMPOTENCY_COMPLETE or observed["executing"] is True:
                return observed
            if observed["legacy_unbound"] is True:
                observed["reconciliation"] = "legacy_evidence_unavailable"
                return observed
            response: JSONObject = {
                "status": "reconciliation_required",
                "request_id": observed["request_id"],
                "request": observed["progress"],
                "original_method": identity[2],
                "original_path": identity[3],
                "request_digest": observed["request_digest"],
                "execution_repeated": False,
            }
            self.complete_http_idempotency(
                principal_id=identity[0],
                tenant_id=identity[1],
                method=identity[2],
                path=identity[3],
                idempotency_key=identity[4],
                status_code=202,
                response=response,
                completed_at_ms=now_ms,
            )
            return self.http_command(identity)
