"""Mission Request orchestration across interpretation, review, approval, and submission."""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from dataclasses import replace
from typing import Protocol

from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.controller import MissionPlanSubmitter
from mission.intent import GroundedIntent
from mission.models import MissionPlan
from mission.planners import MissionPlanner
from mission.request_record import (
    IntentAssessment,
    MissionInterpreter,
    MissionRequestError,
    MissionRequestLifecycle,
    MissionRequestRecord,
)
from mission.request_store import MissionRequestStore
from mission.review import (
    MissionPlanRepairer,
    MissionPlanReviewAttempt,
    MissionPlanReviewer,
    MissionReviewRoute,
    route_mission_review,
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
        controller: MissionPlanSubmitter,
        capability_catalog: CanonicalCapabilityCatalog,
        approval_required_contracts: frozenset[str],
        id_generator: IdGenerator = uuid_token,
        clock: Clock = unix_time_ms,
        reviewer: MissionPlanReviewer | None = None,
        repairer: MissionPlanRepairer | None = None,
        max_repair_attempts: int = 0,
    ) -> None:
        """Retain bounded dependencies and fail interrupted transitions closed on startup."""
        if max_repair_attempts < 0:
            raise MissionRequestError("max_repair_attempts must be nonnegative")
        if max_repair_attempts > 0 and (reviewer is None or repairer is None):
            raise MissionRequestError("automatic repair requires Reviewer and Repairer ports")
        self._store = store
        self._interpreter = interpreter
        self._planner = planner
        self._controller = controller
        self._capability_catalog = capability_catalog
        self._approval_required_contracts = approval_required_contracts
        self._id_generator = id_generator
        self._clock = clock
        self._reviewer = reviewer
        self._repairer = repairer
        self._max_repair_attempts = max_repair_attempts
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
                repair_attempts=0,
                review_history=(),
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
                repair_attempts=0,
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
        """Retry deliberation from dialogue or resubmit an unchanged rejected draft."""
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
            return self._process(self._update(record, issues=(), repair_attempts=0))

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
        """Interpret and plan until clarification, approval, or submission is required."""
        try:
            record = self._update(record, lifecycle=MissionRequestLifecycle.INTERPRETING)
            assessment = self._interpreter.interpret(record.instruction, record.messages)
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
            grounded_intent = assessment.grounded_intent()
            plan = self._planner.plan(
                mission_id=record.mission_id,
                grounded_intent=grounded_intent,
                capability_catalog=self._capability_catalog,
            )
            record = self._record_draft(record, assessment, grounded_intent, plan)
        except Exception as error:
            return self._update(
                record,
                lifecycle=MissionRequestLifecycle.FAILED,
                approval_required=False,
                issues=(str(error),),
            )
        return self._review_and_advance(record, grounded_intent)

    def _record_draft(
        self,
        record: MissionRequestRecord,
        assessment: IntentAssessment,
        grounded_intent: GroundedIntent,
        plan: MissionPlan,
        *,
        repair_attempts: int | None = None,
    ) -> MissionRequestRecord:
        """Validate and persist one immutable draft revision before semantic Review."""
        plan.validate_implementation_support()
        if plan.mission.mission_id != record.mission_id:
            raise MissionRequestError("Planner changed the requested mission id")
        if plan.mission.objective != grounded_intent.objective:
            raise MissionRequestError("Planner changed the grounded mission objective")
        self._capability_catalog.validate_plan(plan)
        revision = record.draft_revision + 1
        return self._update(
            record,
            lifecycle=MissionRequestLifecycle.DRAFTED,
            assessment=assessment,
            plan=plan,
            draft_revision=revision,
            draft_digest=_plan_digest(plan),
            approval_required=False,
            issues=(),
            repair_attempts=(
                record.repair_attempts if repair_attempts is None else repair_attempts
            ),
        )

    def _review_and_advance(
        self, record: MissionRequestRecord, grounded_intent: GroundedIntent
    ) -> MissionRequestRecord:
        """Review and optionally repair drafts until a bounded stable outcome is reached."""
        if self._reviewer is None:
            return self._advance_admitted_draft(record)
        while True:
            record = self._update(record, lifecycle=MissionRequestLifecycle.REVIEWING)
            plan = record.plan
            digest = record.draft_digest
            if plan is None or digest is None:
                return self._update(
                    record,
                    lifecycle=MissionRequestLifecycle.FAILED,
                    issues=("review requires an immutable MissionPlan draft",),
                )
            try:
                review = self._reviewer.review(
                    grounded_intent,
                    plan,
                    self._capability_catalog,
                )
            except Exception as error:
                return self._update(
                    record,
                    lifecycle=MissionRequestLifecycle.FAILED,
                    issues=(f"mission review failed: {error}",),
                )
            attempt = MissionPlanReviewAttempt(
                draft_revision=record.draft_revision,
                draft_digest=digest,
                review=review,
                reviewed_at_ms=self._clock(),
            )
            record = self._update(
                record,
                review_history=(*record.review_history, attempt),
            )
            route = route_mission_review(review)
            if route is MissionReviewRoute.APPROVED:
                return self._advance_admitted_draft(record)
            if route is MissionReviewRoute.CLARIFICATION:
                return self._update(
                    record,
                    lifecycle=MissionRequestLifecycle.NEEDS_CLARIFICATION,
                    approval_required=False,
                    issues=(),
                )
            if route is MissionReviewRoute.REJECTED:
                return self._update(
                    record,
                    lifecycle=MissionRequestLifecycle.FAILED,
                    approval_required=False,
                    issues=("mission review rejected automatic repair",),
                )
            if record.repair_attempts >= self._max_repair_attempts:
                return self._update(
                    record,
                    lifecycle=MissionRequestLifecycle.FAILED,
                    approval_required=False,
                    issues=("mission review repair attempts exhausted",),
                )
            if self._repairer is None:
                return self._update(
                    record,
                    lifecycle=MissionRequestLifecycle.FAILED,
                    approval_required=False,
                    issues=("mission review requires repair but no Repairer is configured",),
                )
            assessment = record.assessment
            if assessment is None:
                return self._update(
                    record,
                    lifecycle=MissionRequestLifecycle.FAILED,
                    approval_required=False,
                    issues=("mission repair requires a grounded intent assessment",),
                )
            record = self._update(record, lifecycle=MissionRequestLifecycle.REPAIRING)
            try:
                repaired_plan = self._repairer.repair(
                    record.mission_id,
                    grounded_intent,
                    plan,
                    review,
                    self._capability_catalog,
                )
                record = self._record_draft(
                    record,
                    assessment,
                    grounded_intent,
                    repaired_plan,
                    repair_attempts=record.repair_attempts + 1,
                )
            except Exception as error:
                return self._update(
                    record,
                    lifecycle=MissionRequestLifecycle.FAILED,
                    approval_required=False,
                    issues=(f"mission repair failed: {error}",),
                )

    def _advance_admitted_draft(self, record: MissionRequestRecord) -> MissionRequestRecord:
        """Apply approval policy after configured semantic and deterministic admission gates."""
        plan = record.plan
        if plan is None:
            raise MissionRequestError("approved request has no MissionPlan")
        contracts = _plan_contracts(plan)
        if contracts & self._approval_required_contracts:
            return self._update(
                record,
                lifecycle=MissionRequestLifecycle.AWAITING_APPROVAL,
                approval_required=True,
            )
        return self._submit(record)

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
        repair_attempts: int | None = None,
        review_history: tuple[MissionPlanReviewAttempt, ...] | None = None,
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
            repair_attempts=(
                record.repair_attempts if repair_attempts is None else repair_attempts
            ),
            review_history=(record.review_history if review_history is None else review_history),
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
            MissionRequestLifecycle.REPAIRING,
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
