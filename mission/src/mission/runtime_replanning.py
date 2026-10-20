"""Consume original Controller generation tickets through SQL, MI advice and actual Review.

No model chooses a Node or submits an Execute. Interrupted effects remain uncertain;
the Controller retains original stop/revoke, goal, State and commitment authority.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Protocol, cast
from urllib.parse import quote

from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.models import JSONObject, MissionPlan
from mission.request_record import MissionRequestLifecycle, MissionRequestRecord
from mission.responses import ResponsesMissionRepairer, ResponsesMissionReviewer
from mission.runtime_replanning_http import JournaledRuntimeTransport
from mission.runtime_work_store import RuntimeWorkError, RuntimeWorkStore, kernel_time
from mission.submission_evidence import (
    ControllerAdmissionEvidence,
    ControllerSubmissionEvidence,
    canonical_plan_digest,
)

SCHEMA = "roboguide.runtime-plan-revision/v0.2"


class RuntimePeer(Protocol):
    """Use fixed authenticated peers; tests explicitly label any transport fixtures."""

    endpoint: str
    tenant: str
    principal: str

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 5
    ) -> tuple[int, dict[str, Any]]:
        """Return one actual bounded response without automatically repeating an effect."""
        ...


def _read(peer: RuntimePeer, path: str) -> dict[str, Any]:
    """Require a successful original read without converting absence into permission."""
    status, document = peer.request("GET", path)
    if status != 200:
        raise RuntimeWorkError(f"runtime MI source read returned HTTP {status}")
    return document


def _digest(value: object) -> bool:
    """Require an exact canonical evidence digest before consuming an authority receipt."""
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and len(value) == 71
        and all(char in "0123456789abcdef" for char in value[7:])
    )


def controller_plan(document: dict[str, Any]) -> MissionPlan:
    """Read the known nullable export omission while preserving original authority."""
    published = deepcopy(document)
    for task in published.get("tasks", []):
        if not isinstance(task, dict):
            continue
        satisfaction = task.get("satisfaction")
        if isinstance(satisfaction, dict) and satisfaction.get("basis") == "execution-report":
            satisfaction.setdefault("verifier", None)
    return MissionPlan.from_json(published)


class RuntimeReplanningWorker:
    """Drive one original accepted Request at a time with no independent execution lifecycle."""

    def __init__(
        self,
        store: RuntimeWorkStore,
        service: RuntimePeer,
        proposer: RuntimePeer,
        reviewer: RuntimePeer | None,
        transport: JournaledRuntimeTransport,
        generator: ResponsesMissionRepairer,
        semantic_reviewer: ResponsesMissionReviewer,
        catalog: CanonicalCapabilityCatalog,
        identity: dict[str, Any],
    ) -> None:
        """Freeze actual peers and an explicitly configured separate reviewer."""
        if (
            service.tenant != proposer.tenant
            or reviewer is not None
            and (
                reviewer.endpoint != proposer.endpoint
                or reviewer.tenant != proposer.tenant
                or reviewer.principal == proposer.principal
            )
        ):
            raise RuntimeWorkError("runtime MI requires one exact tenant and a separate reviewer")
        self.store, self.service, self.proposer, self.reviewer = store, service, proposer, reviewer
        self.transport, self.generator = transport, generator
        self.semantic_reviewer, self.catalog, self.identity = semantic_reviewer, catalog, identity
        self.transport.before_call = self._admit_provider_call

    def _admit_provider_call(
        self, work_id: str, phase: str, nonce: str, request_digest: str
    ) -> None:
        """Spend the exact original phase/nonce at Controller before contacting the provider."""
        original = self.store.original(work_id, self.identity)
        if original is None:
            raise RuntimeWorkError("original provider work source absent")
        receipt = self._command(
            work_id,
            original["request"]["mission_id"],
            "permit-" + phase,
            {
                "kind": "admit-provider-call",
                "ticket_id": original["ticket_id"],
                "phase": phase,
                "nonce": nonce,
                "request_digest": request_digest,
            },
            self.proposer,
            original,
        )
        if receipt.get("status") != "provider_call_admitted" or not _digest(
            receipt.get("receipt_digest")
        ):
            raise RuntimeWorkError("actual original provider-call permission receipt unavailable")

    def _view(self, mission: str) -> dict[str, Any]:
        """Read the current original revision without inventory, submission or renewal."""
        return _read(self.proposer, f"/v1/missions/{quote(mission, safe='')}/plan-revisions")

    def _current(self, mission: str, original: dict[str, Any]) -> dict[str, Any]:
        """Recheck current delegation and source before additional work."""
        view = self._view(mission)
        authority = view.get("authority")
        if (
            not isinstance(authority, dict)
            or authority.get("generation_active") is not True
            or authority.get("policy_digest") != original["policy_digest"]
            or view.get("current_plan_revision") != original["base_revision"]
            or view.get("current_plan_digest") != original["base_digest"]
            or authority.get("policy", {}).get("proposer") != self.proposer.principal
            or not any(
                switch.get("command_id") == original["switch_command_id"]
                and switch.get("phase") == "generating"
                and switch.get("generation") is True
                for switch in authority.get("switches", [])
            )
        ):
            raise RuntimeWorkError("original generation authority, source or stop hold changed")
        return view

    def _command(
        self,
        work_id: str,
        mission: str,
        phase: str,
        action: dict[str, Any],
        peer: RuntimePeer,
        original: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist each original command once and retain uncertain outcomes."""
        view = self._current(mission, original)
        previous = next((row for row in self.store.effects(work_id) if row["phase"] == phase), None)
        if previous is not None:
            body = previous["request"]["body"]
            if body["action"] != action:
                raise RuntimeWorkError("original MI command action changed")
            if previous["status"] != "completed":
                self.observe(work_id)
        else:
            body = {
                "schema_version": SCHEMA,
                "command_id": f"mi-{phase}-{work_id[7:47]}",
                "expected_revision": view["authority"]["revision"],
                "action": action,
            }
        path = f"/v1/missions/{quote(mission, safe='')}/plan-revisions"

        def send(timeout: float) -> dict[str, Any]:
            """Send the journalled original command through its actual mTLS peer."""
            status, document = peer.request("POST", path, body, min(timeout, 5))
            return {"status_code": status, "document": document}

        result = self.store.perform(
            work_id,
            phase,
            {
                "endpoint": peer.endpoint,
                "principal": peer.principal,
                "tenant": peer.tenant,
                "path": path,
                "body": body,
            },
            send,
        )
        if result.get("outcome") == "effect_observed":
            return dict(result["document"])
        if result["status_code"] != 200:
            raise RuntimeWorkError(f"original MI command returned HTTP {result['status_code']}")
        if (
            result["document"].get("schema_version") != SCHEMA
            or result["document"].get("command_id") != body["command_id"]
            or result["document"].get("revision") != body["expected_revision"] + 1
        ):
            raise RuntimeWorkError("original MI command receipt is detached from its exact input")
        return dict(result["document"])

    def observe(self, work_id: str) -> dict[str, Any]:
        """Observe original effects without resending commands or model calls."""
        original = self.store.original(work_id, self.identity)
        if original is None:
            raise RuntimeWorkError("original runtime MI work absent")
        mission = original["request"]["mission_id"]
        path = (
            f"/v1/missions/{quote(mission, safe='')}/plan-revisions"
            f"?candidate={quote(original['ticket_id'], safe='')}"
        )
        effects = self.store.effects(work_id)
        observed = []
        for effect in effects:
            phase = effect["phase"]
            if (
                phase
                not in {
                    "claim",
                    "permit-provider-generate",
                    "permit-provider-review",
                    "candidate",
                    "review",
                }
                or effect["status"] == "completed"
            ):
                continue
            if self.store.observed(work_id, phase):
                observed.append(phase)
                continue
            peer = self.reviewer if phase == "review" else self.proposer
            if peer is None:
                continue
            generations = {row["phase"] for row in self.store.effects(work_id)}
            query = next(
                (
                    f"observe-{phase}-{number}"
                    for number in range(2)
                    if f"observe-{phase}-{number}" not in generations
                ),
                None,
            )
            if query is None:
                continue

            def read(timeout: float, original_peer: RuntimePeer = peer) -> dict[str, Any]:
                """Perform one already journalled original read without any control side effect."""
                status, document = original_peer.request("GET", path, timeout=min(timeout, 5))
                return {"status_code": status, "document": document}

            result = self.store.perform(
                work_id, query, {"endpoint": peer.endpoint, "path": path}, read
            )
            if result["status_code"] != 200:
                continue
            candidate = result["document"]
            action = effect["request"]["body"]["action"]
            if (
                candidate.get("ticket_id") != original["ticket_id"]
                or candidate.get("base_revision") != original["base_revision"]
                or candidate.get("base_plan_digest") != original["base_digest"]
                or candidate.get("principal") != self.proposer.principal
            ):
                continue
            if phase == "claim":
                claim = candidate.get("claim")
                confirmed = isinstance(claim, dict) and claim.get("input") == action["claim"]
                document = {
                    "status": "runtime_work_claimed",
                    "claim_digest": claim.get("digest") if isinstance(claim, dict) else None,
                }
            elif phase.startswith("permit-"):
                call = candidate.get("provider_calls", {}).get(action["phase"])
                confirmed = isinstance(call, dict) and all(
                    call.get(field) == expected
                    for field, expected in (
                        ("nonce", action["nonce"]),
                        ("request_digest", action["request_digest"]),
                        ("principal", peer.principal),
                    )
                )
                document = {
                    "status": "provider_call_admitted",
                    "receipt_digest": call.get("digest") if isinstance(call, dict) else None,
                }
            elif phase == "candidate":
                confirmed = (
                    candidate.get("plan") == action["plan"]
                    and candidate.get("provenance") == action["provenance"]
                )
                document = {
                    "status": "candidate_rejected"
                    if candidate.get("rejection")
                    else "awaiting_review",
                    "ticket_id": original["ticket_id"],
                    "plan_digest": candidate.get("plan_digest"),
                    "rejection": candidate.get("rejection"),
                }
            else:
                review = candidate.get("review")
                confirmed = isinstance(review, dict) and all(
                    review.get(field) == expected
                    for field, expected in (
                        ("principal", peer.principal),
                        ("plan_digest", action["plan_digest"]),
                        ("decision", action["decision"]),
                        ("reason", action["reason"]),
                    )
                )
                document = {"status": "review_effect_observed", "review": review}
            if confirmed:
                self.store.retain_observation(
                    work_id,
                    phase,
                    effect["request"],
                    {"query_effect": query, "response": result},
                    {
                        "outcome": "effect_observed",
                        "original_http_outcome": "uncertain",
                        "document": document,
                    },
                )
                observed.append(phase)
        return {
            "status": "readonly_observation",
            "work_id": work_id,
            "confirmed_effects": observed,
            "control_commands_sent": 0,
            "provider_calls": 0,
        }

    def run_once(self, request_id: str) -> dict[str, Any]:
        """Consume only a current committed ticket for an actual accepted Request."""
        if not request_id or len(request_id) > 128:
            raise RuntimeWorkError("invalid runtime MI Request identity")
        request_path = f"/v1/mission-requests/{quote(request_id, safe='')}"
        record = MissionRequestRecord.from_json(cast(JSONObject, _read(self.service, request_path)))
        if (
            record.request_id != request_id
            or record.lifecycle is not MissionRequestLifecycle.ACCEPTED
        ):
            return {"status": "request_not_accepted", "request_id": request_id}
        if record.plan is None or record.assessment is None or record.grounding_context is None:
            raise RuntimeWorkError("original accepted Request input unavailable")
        mission = record.mission_id
        prefix = f"/v1/missions/{quote(mission, safe='')}"
        observations = _read(self.service, request_path + "/observations")
        if (
            observations.get("request_id") != request_id
            or observations.get("mission_id") != mission
            or observations.get("request_record_digest") != canonical_plan_digest(record.to_json())
        ):
            raise RuntimeWorkError("original Mission Request observation is detached")
        submitted = ControllerSubmissionEvidence.from_json(observations.get("submission_evidence"))
        admitted = ControllerAdmissionEvidence.from_json(
            _read(self.proposer, prefix + "/admission")
        )
        if (
            admitted.mission_id != mission
            or submitted.submitted_mission_id != mission
            or submitted.submitted_plan_digest != canonical_plan_digest(record.plan.to_json())
            or admitted.accepted_request_body_sha256 != submitted.raw_request_body_sha256
        ):
            raise RuntimeWorkError("original MI Request does not prove this Controller admission")
        issued = kernel_time()
        view = self._view(mission)
        authority = view.get("authority")
        if (
            view.get("generation_schema") != SCHEMA
            or view.get("mission_id") != mission
            or view.get("original_group_id") != admitted.group_id
            or not isinstance(authority, dict)
            or authority.get("generation_active") is not True
            or authority.get("policy", {}).get("proposer") != self.proposer.principal
        ):
            return {"status": "no_current_generation", "mission_id": mission}
        switch = next(
            (
                row
                for row in authority.get("switches", [])
                if row.get("generation") is True
                and row.get("phase") == "generating"
                and row.get("source_group") == view.get("current_group_id")
            ),
            None,
        )
        if switch is None:
            return {"status": "waiting_for_original_stop", "mission_id": mission}
        candidate = next(
            (
                row
                for row in authority.get("candidates", [])
                if row.get("ticket_id") == switch["ticket_id"]
            ),
            None,
        )
        if candidate is None or candidate.get("principal") != self.proposer.principal:
            raise RuntimeWorkError("original generation ticket principal is unavailable")
        work_id = canonical_plan_digest(
            {
                "service": self.service.endpoint,
                "controller": self.proposer.endpoint,
                "tenant": self.proposer.tenant,
                "mission": mission,
                "ticket": switch["ticket_id"],
            }
        )
        original = self.store.original(work_id, self.identity)
        if original is None:
            remaining = min(
                authority["remaining_ms"],
                candidate["remaining_work_ms"],
                candidate["remaining_switch_ms"],
            )
            current = controller_plan(view["current_plan"])
            if current.mission.to_json(current.schema_version) != record.plan.mission.to_json(
                record.plan.schema_version
            ):
                raise RuntimeWorkError("current Mission changed the original accepted goal")
            original = {
                "request": record.to_json(),
                "admission": admitted.to_json(),
                "source": view,
                "base_revision": view["current_plan_revision"],
                "base_digest": view["current_plan_digest"],
                "policy_digest": authority["policy_digest"],
                "switch_command_id": switch["command_id"],
                "ticket_id": switch["ticket_id"],
                "context": {
                    "schema_version": "roboguide.runtime-replanning-context/v0.1",
                    "mission_id": mission,
                    "ticket_id": switch["ticket_id"],
                    "source_revision": view["current_plan_revision"],
                    "source_plan_digest": view["current_plan_digest"],
                    "published_source_plan_digest": canonical_plan_digest(current.to_json()),
                    "fault": switch["trigger"],
                    "completed_tasks": view["completed_tasks"],
                    "used_task_ids": view["used_task_ids"],
                    "new_task_id_prefix": switch["ticket_id"] + "-",
                    "original_goal_context": view["goal_context"],
                    "original_goal_contract_digest": view["goal_contract"]["digest"],
                    "grounding_context_authority": (
                        "historical-original-input; current Control evidence remains required"
                    ),
                },
            }
            self.store.admit(work_id, original, self.identity, remaining, issued)
        self._current(mission, original)
        claim_receipt = self._command(
            work_id,
            mission,
            "claim",
            {
                "kind": "claim-runtime-work",
                "ticket_id": original["ticket_id"],
                "claim": {
                    "store_instance_id": self.store.instance_id,
                    "work_id": work_id,
                    "identity_digest": canonical_plan_digest(self.identity),
                    "request_id": request_id,
                },
            },
            self.proposer,
            original,
        )
        if claim_receipt.get("status") != "runtime_work_claimed" or not _digest(
            claim_receipt.get("claim_digest")
        ):
            raise RuntimeWorkError("original actual MI worker claim receipt unavailable")
        original_record = MissionRequestRecord.from_json(original["request"])
        context = original_record.grounding_context
        if context is None or original_record.assessment is None:
            raise RuntimeWorkError("original grounded MI input absent")
        with self.transport.bind(work_id, "provider-generate"):
            plan = self.generator.replan(
                mission,
                original_record.assessment.grounded_intent(),
                controller_plan(original["source"]["current_plan"]),
                self.catalog,
                context,
                cast(JSONObject, original["context"]),
            )
        generated = next(
            row for row in self.store.effects(work_id) if row["phase"] == "provider-generate"
        )
        provenance = {
            "kind": "mission-intelligence",
            "principal": self.proposer.principal,
            "request_id": request_id,
            "provider_call_id": work_id + ":provider-generate",
            **{
                name: self.identity[name]
                for name in (
                    "code_digest",
                    "config_digest",
                    "catalog_digest",
                    "prompt_digest",
                    "dependency_digest",
                )
            },
            "original_output_digest": canonical_plan_digest(generated["response"]),
        }
        submitted_candidate = self._command(
            work_id,
            mission,
            "candidate",
            {
                "kind": "submit-candidate",
                "ticket_id": original["ticket_id"],
                "plan": plan.to_json(),
                "provenance": provenance,
            },
            self.proposer,
            original,
        )
        if submitted_candidate.get("status") != "awaiting_review":
            return {
                "status": "candidate_rejected",
                "work_id": work_id,
                "receipt": submitted_candidate,
            }
        self._current(mission, original)
        with self.transport.bind(work_id, "provider-review"):
            review = self.semantic_reviewer.review_runtime(
                original_record.assessment.grounded_intent(),
                plan,
                self.catalog,
                context,
                cast(JSONObject, original["context"]),
            )
        if self.reviewer is None:
            return {
                "status": "awaiting_administrative_review",
                "work_id": work_id,
                "plan_digest": submitted_candidate["plan_digest"],
                "advice": review.to_json(),
            }
        receipt = self._command(
            work_id,
            mission,
            "review",
            {
                "kind": "review-candidate",
                "ticket_id": original["ticket_id"],
                "plan_digest": submitted_candidate["plan_digest"],
                "decision": "approve" if review.approved else "reject",
                "reason": "Configured MI reviewer advice "
                + canonical_plan_digest(review.to_json()),
            },
            self.reviewer,
            original,
        )
        return {"status": "review_submitted", "work_id": work_id, "receipt": receipt}
