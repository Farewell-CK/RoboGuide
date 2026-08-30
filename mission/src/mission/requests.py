"""Durable Mission Request deliberation state machine owned by Mission Intelligence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast

from mission.controller import InventorySnapshot, MissionController
from mission.models import JSONObject, JSONValue, MissionPlan, RoleRequirement
from mission.planners import MissionPlanner

MISSION_REQUEST_SCHEMA = "roboguide.mission-request/v0.1"


class MissionRequestError(RuntimeError):
    """Report an unknown request, invalid command, or lifecycle conflict."""


class MissionRequestLifecycle(StrEnum):
    """Identify the durable pre-execution lifecycle of one user instruction."""

    RECEIVED = "Received"
    INTERPRETING = "Interpreting"
    NEEDS_CLARIFICATION = "NeedsClarification"
    DRAFTED = "Drafted"
    REVIEWING = "Reviewing"
    AWAITING_APPROVAL = "AwaitingApproval"
    SUBMITTING = "Submitting"
    ACCEPTED = "Accepted"
    BLOCKED = "Blocked"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


@dataclass(frozen=True, slots=True)
class IntentAssessment:
    """Capture a grounded objective and unresolved questions before Task decomposition."""

    objective: str
    constraints: tuple[str, ...]
    assumptions: tuple[str, ...]
    open_questions: tuple[str, ...]

    @classmethod
    def from_json(cls, value: JSONObject) -> IntentAssessment:
        """Parse strict structured interpreter output without accepting hidden fields."""
        expected = {"objective", "constraints", "assumptions", "open_questions"}
        if set(value) != expected:
            raise MissionRequestError("intent assessment fields do not match v0.1")
        objective = value["objective"]
        if not isinstance(objective, str) or not objective.strip():
            raise MissionRequestError("intent objective must be nonblank text")

        def text_tuple(field: str) -> tuple[str, ...]:
            """Read one assessment text array while rejecting blank entries."""
            items = value[field]
            if not isinstance(items, list) or not all(
                isinstance(item, str) and item.strip() for item in items
            ):
                raise MissionRequestError(f"intent {field} must contain nonblank text")
            return tuple(cast(list[str], items))

        return cls(
            objective=objective,
            constraints=text_tuple("constraints"),
            assumptions=text_tuple("assumptions"),
            open_questions=text_tuple("open_questions"),
        )

    def to_json(self) -> JSONObject:
        """Serialize grounded intent independently from the MissionPlan contract."""
        return {
            "objective": self.objective,
            "constraints": list(self.constraints),
            "assumptions": list(self.assumptions),
            "open_questions": list(self.open_questions),
        }


class MissionInterpreter(Protocol):
    """Ground an instruction using dialogue and advisory inventory without executing it."""

    def interpret(
        self,
        instruction: str,
        messages: tuple[str, ...],
        inventory: InventorySnapshot,
    ) -> IntentAssessment:
        """Return a normalized objective or explicit open questions."""
        ...


@dataclass(frozen=True, slots=True)
class MissionRequestRecord:
    """Persist one Mission Intelligence request without mirroring execution lifecycle."""

    request_id: str
    mission_id: str
    instruction: str
    messages: tuple[str, ...]
    lifecycle: MissionRequestLifecycle
    assessment: IntentAssessment | None
    plan: MissionPlan | None
    draft_revision: int
    draft_digest: str | None
    approval_required: bool
    issues: tuple[str, ...]
    created_at_ms: int
    updated_at_ms: int

    def to_json(self) -> JSONObject:
        """Serialize the versioned status projection returned by the Mission Request API."""
        return {
            "schema_version": MISSION_REQUEST_SCHEMA,
            "request_id": self.request_id,
            "mission_id": self.mission_id,
            "instruction": self.instruction,
            "messages": list(self.messages),
            "lifecycle": self.lifecycle.value,
            "assessment": self.assessment.to_json() if self.assessment is not None else None,
            "plan": self.plan.to_json() if self.plan is not None else None,
            "draft_revision": self.draft_revision,
            "draft_digest": self.draft_digest,
            "approval_required": self.approval_required,
            "issues": list(self.issues),
            "created_at_ms": self.created_at_ms,
            "updated_at_ms": self.updated_at_ms,
        }

    @classmethod
    def from_json(cls, value: JSONObject) -> MissionRequestRecord:
        """Restore one durable request and revalidate its embedded plan and assessment."""
        if value.get("schema_version") != MISSION_REQUEST_SCHEMA:
            raise MissionRequestError("unsupported Mission Request schema")
        request_id = _required_text(value, "request_id")
        mission_id = _required_text(value, "mission_id")
        instruction = _required_text(value, "instruction")
        lifecycle_text = _required_text(value, "lifecycle")
        messages = _text_array(value, "messages")
        issues = _text_array(value, "issues")
        assessment_value = value.get("assessment")
        plan_value = value.get("plan")
        assessment = (
            None
            if assessment_value is None
            else IntentAssessment.from_json(_json_object(assessment_value, "assessment"))
        )
        plan = (
            None if plan_value is None else MissionPlan.from_json(_json_object(plan_value, "plan"))
        )
        draft_revision = _required_integer(value, "draft_revision")
        created_at_ms = _required_integer(value, "created_at_ms")
        updated_at_ms = _required_integer(value, "updated_at_ms")
        draft_digest = value.get("draft_digest")
        if draft_digest is not None and not isinstance(draft_digest, str):
            raise MissionRequestError("draft_digest must be text or null")
        approval_required = value.get("approval_required")
        if not isinstance(approval_required, bool):
            raise MissionRequestError("approval_required must be a boolean")
        try:
            lifecycle = MissionRequestLifecycle(lifecycle_text)
        except ValueError as error:
            raise MissionRequestError("unknown Mission Request lifecycle") from error
        return cls(
            request_id=request_id,
            mission_id=mission_id,
            instruction=instruction,
            messages=messages,
            lifecycle=lifecycle,
            assessment=assessment,
            plan=plan,
            draft_revision=draft_revision,
            draft_digest=draft_digest,
            approval_required=approval_required,
            issues=issues,
            created_at_ms=created_at_ms,
            updated_at_ms=updated_at_ms,
        )


def _json_object(value: JSONValue | object, field: str) -> JSONObject:
    """Return a string-keyed JSON object or reject corrupt persisted evidence."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise MissionRequestError(f"{field} must be an object")
    return cast(JSONObject, value)


def _required_text(value: JSONObject, field: str) -> str:
    """Read one required nonblank text field from persisted request JSON."""
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise MissionRequestError(f"{field} must be nonblank text")
    return item


def _required_integer(value: JSONObject, field: str) -> int:
    """Read one required nonnegative integer without accepting Boolean coercion."""
    item = value.get(field)
    if isinstance(item, bool) or not isinstance(item, int) or item < 0:
        raise MissionRequestError(f"{field} must be a nonnegative integer")
    return item


def _text_array(value: JSONObject, field: str) -> tuple[str, ...]:
    """Read one persisted array containing only nonblank text."""
    items = value.get(field)
    if not isinstance(items, list) or not all(
        isinstance(item, str) and item.strip() for item in items
    ):
        raise MissionRequestError(f"{field} must contain nonblank text")
    return tuple(cast(list[str], items))


class MissionRequestStore:
    """Persist Mission Request projections in a process-local SQLite database."""

    def __init__(self, database: Path) -> None:
        """Create the database parent and versioned request table."""
        database.parent.mkdir(parents=True, exist_ok=True)
        self._database = database
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
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

    def _connect(self) -> sqlite3.Connection:
        """Open one short-lived SQLite connection with bounded lock waiting."""
        return sqlite3.connect(self._database, timeout=30.0)

    def save(self, record: MissionRequestRecord) -> None:
        """Atomically insert or replace one complete deliberation projection."""
        document = json.dumps(
            record.to_json(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        with self._lock, self._connect() as connection:
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

    def get(self, request_id: str) -> MissionRequestRecord | None:
        """Return one request projection without changing lifecycle."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT document_json FROM mission_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
        if row is None:
            return None
        decoded: object = json.loads(str(row[0]))
        return MissionRequestRecord.from_json(_json_object(decoded, "mission request"))

    def records(self) -> tuple[MissionRequestRecord, ...]:
        """Return all requests in stable identity order for conservative startup recovery."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT document_json FROM mission_requests ORDER BY request_id"
            ).fetchall()
        return tuple(
            MissionRequestRecord.from_json(_json_object(json.loads(str(row[0])), "mission request"))
            for row in rows
        )


class IdGenerator(Protocol):
    """Generate collision-resistant internal identities behind an injectable boundary."""

    def __call__(self) -> str:
        """Return one lowercase 32-character identity token."""
        ...


class Clock(Protocol):
    """Provide wall-clock evidence for Mission Request persistence."""

    def __call__(self) -> int:
        """Return current Unix time in nonnegative milliseconds."""
        ...


def uuid_token() -> str:
    """Generate one process-independent UUID4 token without exposing it to the user input."""
    return uuid.uuid4().hex


def unix_time_ms() -> int:
    """Return current Unix time in milliseconds for API evidence timestamps."""
    return time.time_ns() // 1_000_000


class _Unset:
    """Distinguish an omitted optional field update from an explicit null replacement."""


_UNSET = _Unset()


class MissionRequestEngine:
    """Drive clarification, planning, review, approval, and accepted-plan submission."""

    def __init__(
        self,
        store: MissionRequestStore,
        interpreter: MissionInterpreter,
        planner: MissionPlanner,
        controller: MissionController,
        approval_required_contracts: frozenset[str],
        id_generator: IdGenerator = uuid_token,
        clock: Clock = unix_time_ms,
    ) -> None:
        """Retain bounded dependencies and fail interrupted transitions closed on startup."""
        self._store = store
        self._interpreter = interpreter
        self._planner = planner
        self._controller = controller
        self._approval_required_contracts = approval_required_contracts
        self._id_generator = id_generator
        self._clock = clock
        self._lock = threading.RLock()
        self._recover_interrupted()

    def create(self, instruction: str) -> MissionRequestRecord:
        """Create internal identities and drive a new instruction until its first stable state."""
        instruction = instruction.strip()
        if not instruction:
            raise MissionRequestError("instruction must be nonblank text")
        with self._lock:
            now = self._clock()
            record = MissionRequestRecord(
                request_id=f"request-{self._id_generator()}",
                mission_id=f"mission-{self._id_generator()}",
                instruction=instruction,
                messages=(),
                lifecycle=MissionRequestLifecycle.RECEIVED,
                assessment=None,
                plan=None,
                draft_revision=0,
                draft_digest=None,
                approval_required=False,
                issues=(),
                created_at_ms=now,
                updated_at_ms=now,
            )
            self._store.save(record)
            return self._process(record)

    def get(self, request_id: str) -> MissionRequestRecord:
        """Return one request or raise a stable unknown-request error."""
        record = self._store.get(request_id)
        if record is None:
            raise MissionRequestError(f"unknown Mission Request {request_id}")
        return record

    def add_message(self, request_id: str, text: str) -> MissionRequestRecord:
        """Append user clarification and invalidate any older draft before reinterpretation."""
        text = text.strip()
        if not text:
            raise MissionRequestError("message text must be nonblank")
        with self._lock:
            record = self.get(request_id)
            if record.lifecycle in {
                MissionRequestLifecycle.ACCEPTED,
                MissionRequestLifecycle.CANCELLED,
                MissionRequestLifecycle.SUBMITTING,
            }:
                raise MissionRequestError(
                    f"cannot add a message while request is {record.lifecycle.value}"
                )
            updated = self._update(
                record,
                messages=(*record.messages, text),
                lifecycle=MissionRequestLifecycle.RECEIVED,
                assessment=None,
                plan=None,
                draft_digest=None,
                approval_required=False,
                issues=(),
            )
            return self._process(updated)

    def approve(
        self, request_id: str, draft_revision: int, draft_digest: str
    ) -> MissionRequestRecord:
        """Approve only the current risk-gated immutable draft and submit it once."""
        with self._lock:
            record = self.get(request_id)
            if record.lifecycle is not MissionRequestLifecycle.AWAITING_APPROVAL:
                raise MissionRequestError("request is not awaiting approval")
            if (
                record.draft_revision != draft_revision
                or record.draft_digest != draft_digest
                or record.plan is None
            ):
                raise MissionRequestError("approval references a stale MissionPlan draft")
            return self._submit(record)

    def retry(self, request_id: str) -> MissionRequestRecord:
        """Retry a failed or blocked deliberation from current dialogue and inventory."""
        with self._lock:
            record = self.get(request_id)
            if record.lifecycle not in {
                MissionRequestLifecycle.FAILED,
                MissionRequestLifecycle.BLOCKED,
            }:
                raise MissionRequestError("only Failed or Blocked requests can be retried")
            if (
                record.plan is not None
                and record.issues
                and (
                    record.issues[0].startswith("submission ")
                    or record.issues[0].startswith("Controller HTTP ")
                )
            ):
                return self._submit(record)
            return self._process(self._update(record, issues=()))

    def cancel(self, request_id: str) -> MissionRequestRecord:
        """Cancel pre-execution deliberation without fabricating Mission cancellation."""
        with self._lock:
            record = self.get(request_id)
            if record.lifecycle is MissionRequestLifecycle.ACCEPTED:
                raise MissionRequestError("accepted Missions must use the Mission cancel API")
            if record.lifecycle is MissionRequestLifecycle.CANCELLED:
                return record
            return self._update(
                record,
                lifecycle=MissionRequestLifecycle.CANCELLED,
                approval_required=False,
            )

    def _process(self, record: MissionRequestRecord) -> MissionRequestRecord:
        """Interpret and plan until clarification, blocking, approval, or submission is required."""
        try:
            record = self._update(record, lifecycle=MissionRequestLifecycle.INTERPRETING)
            inventory = self._controller.inventory()
            assessment = self._interpreter.interpret(record.instruction, record.messages, inventory)
            if assessment.open_questions:
                return self._update(
                    record,
                    lifecycle=MissionRequestLifecycle.NEEDS_CLARIFICATION,
                    assessment=assessment,
                    plan=None,
                    draft_digest=None,
                    approval_required=False,
                    issues=(),
                )
            plan = self._planner.plan(record.mission_id, assessment.objective)
            revision = record.draft_revision + 1
            digest = _plan_digest(plan)
            record = self._update(
                record,
                lifecycle=MissionRequestLifecycle.DRAFTED,
                assessment=assessment,
                plan=plan,
                draft_revision=revision,
                draft_digest=digest,
                approval_required=False,
                issues=(),
            )
            record = self._update(record, lifecycle=MissionRequestLifecycle.REVIEWING)
            contracts = _plan_contracts(plan)
            missing = sorted(
                {
                    _requirement_label(role.capability, _contract_text(role), role.resource_kind)
                    for task in plan.tasks
                    for role in task.roles
                    if not inventory.supports_requirement(
                        role.capability,
                        _contract_text(role),
                        role.resource_kind,
                    )
                }
            )
            if missing:
                return self._update(
                    record,
                    lifecycle=MissionRequestLifecycle.BLOCKED,
                    issues=tuple(
                        f"role requirement unavailable in current inventory: {requirement}"
                        for requirement in missing
                    ),
                )
            if contracts & self._approval_required_contracts:
                return self._update(
                    record,
                    lifecycle=MissionRequestLifecycle.AWAITING_APPROVAL,
                    approval_required=True,
                )
            return self._submit(record)
        except Exception as error:
            return self._update(
                record,
                lifecycle=MissionRequestLifecycle.FAILED,
                approval_required=False,
                issues=(str(error),),
            )

    def _submit(self, record: MissionRequestRecord) -> MissionRequestRecord:
        """Submit the current complete plan and reduce the receipt to a stable request state."""
        plan = record.plan
        if plan is None:
            raise MissionRequestError("cannot submit a request without a MissionPlan")
        record = self._update(
            record,
            lifecycle=MissionRequestLifecycle.SUBMITTING,
            approval_required=False,
        )
        try:
            receipt = self._controller.submit_plan(plan)
        except Exception as error:
            return self._update(
                record,
                lifecycle=MissionRequestLifecycle.FAILED,
                issues=(f"submission failed: {error}",),
            )
        if receipt.accepted:
            return self._update(
                record,
                lifecycle=MissionRequestLifecycle.ACCEPTED,
                issues=(),
            )
        return self._update(
            record,
            lifecycle=MissionRequestLifecycle.BLOCKED,
            issues=(f"Controller HTTP {receipt.status_code}: {receipt.detail}",),
        )

    def _update(
        self,
        record: MissionRequestRecord,
        *,
        messages: tuple[str, ...] | None = None,
        lifecycle: MissionRequestLifecycle | None = None,
        assessment: IntentAssessment | None | _Unset = _UNSET,
        plan: MissionPlan | None | _Unset = _UNSET,
        draft_revision: int | None = None,
        draft_digest: str | None | _Unset = _UNSET,
        approval_required: bool | None = None,
        issues: tuple[str, ...] | None = None,
    ) -> MissionRequestRecord:
        """Persist one immutable state replacement with a fresh update timestamp."""
        updated = replace(
            record,
            messages=record.messages if messages is None else messages,
            lifecycle=record.lifecycle if lifecycle is None else lifecycle,
            assessment=(record.assessment if isinstance(assessment, _Unset) else assessment),
            plan=record.plan if isinstance(plan, _Unset) else plan,
            draft_revision=(record.draft_revision if draft_revision is None else draft_revision),
            draft_digest=(
                record.draft_digest if isinstance(draft_digest, _Unset) else draft_digest
            ),
            approval_required=(
                record.approval_required if approval_required is None else approval_required
            ),
            issues=record.issues if issues is None else issues,
            updated_at_ms=self._clock(),
        )
        self._store.save(updated)
        return updated

    def _recover_interrupted(self) -> None:
        """Fence process-interrupted transient states instead of resuming model or HTTP effects."""
        transient = {
            MissionRequestLifecycle.RECEIVED,
            MissionRequestLifecycle.INTERPRETING,
            MissionRequestLifecycle.DRAFTED,
            MissionRequestLifecycle.REVIEWING,
            MissionRequestLifecycle.SUBMITTING,
        }
        for record in self._store.records():
            if record.lifecycle in transient:
                issue = (
                    "submission interrupted by Mission Service restart"
                    if record.lifecycle is MissionRequestLifecycle.SUBMITTING
                    else "deliberation interrupted by Mission Service restart"
                )
                self._update(
                    record,
                    lifecycle=MissionRequestLifecycle.FAILED,
                    approval_required=False,
                    issues=(issue,),
                )


def _plan_digest(plan: MissionPlan) -> str:
    """Compute the immutable approval identity of one canonical MissionPlan draft."""
    encoded = json.dumps(
        plan.to_json(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _plan_contracts(plan: MissionPlan) -> frozenset[str]:
    """Return every canonical contract required by a plan without selecting physical nodes."""
    return frozenset(
        f"{contract.namespace}.{contract.name}@{contract.version}"
        for task in plan.tasks
        for role in task.roles
        for contract in (role.execution.capability_contract,)
    )


def _contract_text(role: RoleRequirement) -> str:
    """Return one role contract through the validated MissionPlan object shape."""
    contract = role.execution.capability_contract
    return f"{contract.namespace}.{contract.name}@{contract.version}"


def _requirement_label(capability: str, contract: str, resource_kind: str | None) -> str:
    """Format one stable advisory requirement for an inspectable Blocked reason."""
    resource = "none" if resource_kind is None else resource_kind
    return f"capability={capability}, contract={contract}, resource={resource}"
