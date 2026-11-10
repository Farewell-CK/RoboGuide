"""Observe declared posture goals in the existing MI service without action authority."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from collections.abc import Callable
from typing import Any, Protocol

from mission.models import MissionPlan, TaskSatisfactionBasis
from mission.posture_verifier_store import PostureVerificationError, PostureVerificationStore
from mission.runtime_work_store import kernel_time
from mission.security_context import HTTP_TENANT
from mission.submission_evidence import canonical_plan_digest

LOG = logging.getLogger(__name__)
WINDOW_MS = 10_000
PUBLICATION_BUDGET_MS = 500


class PostureVerifierTransport(Protocol):
    """Expose only bounded reads and generic evidence publication to this worker."""

    def posture_goal_view(self, mission_id: str) -> dict[str, Any]:
        """Read original admission, current plan, Task status and complete attempt history."""
        ...

    def posture_snapshot(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Read Controller inventory and reported robot State without contacting a robot."""
        ...

    def publish_posture_evidence(self, evidence: dict[str, Any]) -> dict[str, Any]:
        """Publish one frozen generic verifier input without any action or retry route."""
        ...


class PostureGoalVerifier:
    """Own one observation thread and persistent goals under the existing MI writer lease."""

    def __init__(
        self,
        store: PostureVerificationStore,
        transport: PostureVerifierTransport,
        identity: dict[str, Any],
        *,
        clock: Callable[[], dict[str, Any]] = kernel_time,
    ) -> None:
        """Freeze independently configured publisher identity and the original ten-second bound."""
        from robonix_task.posture import inspect_posture
        from robonix_task.posture_goal import VERIFIER, assess_goal, predicate_for

        self.store = store
        self.transport = transport
        self.identity = identity
        self.clock = clock
        if (
            not isinstance(identity.get("source_id"), str)
            or not identity["source_id"].strip()
            or type(identity.get("source_revision")) is not int
            or identity["source_revision"] <= 0
            or not isinstance(identity.get("tenant_id"), str)
            or not identity["tenant_id"].strip()
            or identity["tenant_id"] == "*"
        ):
            raise PostureVerificationError(
                "posture publisher needs a fixed source and scoped tenant"
            )
        self.inspect = inspect_posture
        self.assess = assess_goal
        self.predicate_for = predicate_for
        self.contract = VERIFIER
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def prepare(self, plan: MissionPlan, tenant: str | None) -> None:
        """Require explicit verifier semantics and freeze unchanged bytes before admission.

        Only new Tasks declaring expected_posture or this verifier are selected. Historical
        execution-report Tasks are never discovered or rewritten by this worker.
        """
        goals = []
        wire = json.dumps(plan.to_json(), ensure_ascii=False, separators=(",", ":")).encode()
        for task in plan.tasks:
            spec = task.satisfaction.verifier
            declared = [
                dict(role.execution.parameters).get("expected_posture") for role in task.roles
            ]
            selected = any(value is not None for value in declared) or (
                spec is not None and spec.contract.to_json() == self.contract
            )
            if not selected:
                continue
            if (
                len(task.roles) != 1
                or not isinstance(declared[0], str)
                or declared[0] not in {"standing", "down"}
                or task.satisfaction.basis is not TaskSatisfactionBasis.VERIFIER_EVIDENCE
                or spec is None
                or spec.contract.to_json() != self.contract
                or spec.predicate != self.predicate_for(str(declared[0]))
                or task.satisfaction.expected_effect != spec.predicate
                or spec.max_evidence_age_ms > 5000
                or tenant != self.identity["tenant_id"]
            ):
                raise PostureVerificationError(
                    "posture Task needs one explicit expected_posture role "
                    "and exact verifier policy/tenant"
                )
            goals.append(
                {
                    "schema_version": "roboguide.mi-posture-goal/v0.1",
                    "mission_id": plan.mission.mission_id,
                    "task_id": task.task_id,
                    "role_ids": [role.role_id for role in task.roles],
                    "tenant_id": tenant,
                    "expected_posture": declared[0],
                    "predicate": spec.predicate,
                    "max_evidence_age_ms": spec.max_evidence_age_ms,
                    "mi_plan_digest": canonical_plan_digest(plan.to_json()),
                    "wire_plan_digest": "sha256:" + hashlib.sha256(wire).hexdigest(),
                    "publisher": self.identity,
                    "window_ms": WINDOW_MS,
                }
            )
        self.store.admit(goals)

    def start(self) -> None:
        """Start at most one observer after the HTTP server owns the MI process lease."""
        if self._thread is not None:
            raise PostureVerificationError("posture observer already started")
        self._thread = threading.Thread(target=self._run, name="mi-posture-verifier", daemon=True)
        self._thread.start()

    def admission_result(self, plan: MissionPlan, *, accepted: bool) -> None:
        """Close definitively rejected pre-admission jobs without retrying the Mission."""
        if accepted:
            return
        for job in self.store.jobs(mission_id=plan.mission.mission_id):
            if job["status"] == "pending" and job["input"][
                "mi_plan_digest"
            ] == canonical_plan_digest(plan.to_json()):
                self._manual(job, "controller_admission_rejected")

    def close(self) -> None:
        """Stop before releasing the MI writer lease; transport calls have a bounded timeout."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=15)
            if self._thread.is_alive():
                raise PostureVerificationError(
                    "posture observer did not stop within its transport bound"
                )

    def _run(self) -> None:
        """Poll existing Controller projections without task submission or physical retries."""
        while not self._stop.is_set():
            try:
                self.step()
            except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
                LOG.error("posture observation unavailable: %s", type(error).__name__)
            self._stop.wait(0.25)

    def ready(self) -> bool:
        """Expose a live observer and verified active storage without creating evidence."""
        self.store.jobs(active=True)
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def _manual(self, job: dict[str, Any], reason: str) -> None:
        """Keep uncertainty durable for operator handling without publishing invented evidence."""
        job["status"] = "manual"
        job["state"].update(verdict="unknown", reason=reason, automatic_action_retry=False)
        self.store.save(job)

    def _elapsed(self, anchor: dict[str, Any]) -> int | None:
        """Fence restart across boots/time namespaces and refuse regressed monotonic clocks."""
        now = self.clock()
        if now["boot"] != anchor["boot"] or now["namespace"] != anchor["namespace"]:
            return None
        elapsed = now["elapsed_ns"] - anchor["elapsed_ns"]
        return elapsed // 1_000_000 if elapsed >= 0 else None

    def step(self) -> None:
        """Advance all bounded active jobs with one shared State/inventory acquisition per tick."""
        token = HTTP_TENANT.set((self.identity["tenant_id"], False))
        try:
            self._step()
        finally:
            HTTP_TENANT.reset(token)

    def _step(self) -> None:
        """Keep a tick tenant scoped, including the independently authenticated HTTP publisher."""
        jobs = self.store.jobs(active=True)
        snapshot: tuple[dict[str, Any], dict[str, Any]] | None = None
        snapshot_kernel: dict[str, Any] | None = None
        for job in jobs:
            if self._stop.is_set():
                break
            if job["input"]["publisher"] != self.identity:
                self._manual(job, "publisher_configuration_changed")
                continue
            if job["status"] == "publishing":
                self._manual(job, "publication_uncertain_readonly_reconciliation_required")
                continue
            try:
                view = self.transport.posture_goal_view(job["input"]["mission_id"])
                binding = self._binding(job, view)
                if binding is None:
                    continue
                if snapshot is None:
                    snapshot_kernel = self.clock()
                    snapshot = self.transport.posture_snapshot()
                assert snapshot_kernel is not None
                observation_delay = self._elapsed(snapshot_kernel)
                if observation_delay is None:
                    self._manual(job, "observation_clock_domain_changed")
                    continue
                self._observe(job, binding, *snapshot, observation_delay_ms=observation_delay)
            except (OSError, ValueError, RuntimeError, KeyError, TypeError, sqlite3.Error) as error:
                if job["status"] == "observing":
                    elapsed = self._elapsed(job["state"]["started_kernel"])
                    if elapsed is None or elapsed >= WINDOW_MS:
                        self._manual(job, "observation_window_unavailable")
                LOG.warning("posture job observation failed: %s", error)

    def _binding(self, job: dict[str, Any], view: dict[str, Any]) -> dict[str, Any] | None:
        """Require exact original bytes, current plan and a complete unambiguous attempt set."""
        original = job["input"]
        mission, tenant, admission = view["mission"], view["tenant"], view["admission"]
        if (
            mission["mission_id"] != original["mission_id"]
            or tenant["mission_id"] != original["mission_id"]
            or tenant["tenant_id"] != original["tenant_id"]
            or admission["mission_id"] != original["mission_id"]
            or admission["accepted_request_body_sha256"] != original["wire_plan_digest"]
            or mission.get("plan_revision") != 0
            or mission.get("group_id") != admission.get("group_id")
            or not isinstance(mission.get("group_id"), str)
        ):
            self._manual(job, "original_admission_binding_changed")
            return None
        tasks = [task for task in mission["tasks"] if task["task_id"] == original["task_id"]]
        if len(tasks) != 1 or tasks[0]["status"] != "AwaitingSatisfaction":
            if mission["status"] in {"Completed", "Cancelled", "Failed"}:
                self._manual(job, "task_no_longer_awaits_this_goal")
            return None
        attempts = [a for a in view["attempts"] if a["task_id"] == original["task_id"]]
        if (
            len(attempts) != len(original["role_ids"])
            or {a["role_id"] for a in attempts} != set(original["role_ids"])
            or any(
                a["status"] != "Completed" or a["mission_id"] != original["mission_id"]
                for a in attempts
            )
            or len({a["node_id"] for a in attempts}) != 1
            or not isinstance(tenant["source_plan_digest"], str)
        ):
            self._manual(job, "current_attempt_set_not_unambiguous_and_completed")
            return None
        return {
            "plan_digest": tenant["source_plan_digest"],
            "attempts": sorted(
                [{"role_id": a["role_id"], "attempt_id": a["execution_id"]} for a in attempts],
                key=lambda a: a["role_id"],
            ),
            "node_id": attempts[0]["node_id"],
        }

    def _observe(
        self,
        job: dict[str, Any],
        binding: dict[str, Any],
        state: dict[str, Any],
        inventory: dict[str, Any],
        *,
        observation_delay_ms: int = 0,
    ) -> None:
        """Require two distinct post-completion samples without renewing cached evidence."""
        progress = job["state"]
        current = self.inspect(state, inventory, node_id=binding["node_id"])
        if current["status"] == "reported":
            current["evidence"]["remaining_valid_for_ms"] -= observation_delay_ms
            if current["evidence"]["remaining_valid_for_ms"] <= 0:
                current["status"] = "unavailable"
        if job["status"] == "pending":
            progress.update(
                binding=binding, anchor=current, started_kernel=self.clock(), reports=[]
            )
            job["status"] = "observing"
            self.store.save(job)
            return
        elapsed = self._elapsed(progress["started_kernel"])
        if binding != progress["binding"] or elapsed is None:
            self._manual(job, "current_attempt_plan_or_clock_domain_changed")
            return
        assessment = self.assess(
            progress["anchor"],
            state,
            inventory,
            node_id=binding["node_id"],
            expected_posture=job["input"]["expected_posture"],
        )
        if current["status"] != "reported":
            assessment.update(verdict="unknown", finality="provisional", valid_for_ms=0)
        if assessment["reason"] == "observation_source_changed_or_missing":
            self._manual(job, "observation_source_changed")
            return
        reports = progress["reports"]
        if elapsed < WINDOW_MS and assessment["finality"] == "final":
            evidence = current["evidence"]
            previous = (
                reports[-1]["observation"]["evidence"]
                if reports
                else progress["anchor"].get("evidence", {})
            )
            if evidence["sequence"] > previous.get("sequence", -1) and evidence[
                "chassis_received_at_ns"
            ] > previous.get("chassis_received_at_ns", -1):
                reports.append(
                    {
                        "observation": current,
                        "verdict": assessment["verdict"],
                        "kernel": self.clock(),
                    }
                )
                progress["reports"] = reports[-2:]
                reports = progress["reports"]
        pair = len(reports) == 2 and all(
            self._report_budget(report) > PUBLICATION_BUDGET_MS for report in reports
        )
        positive = pair and all(report["verdict"] == "satisfied" for report in reports)
        negative = pair and all(report["verdict"] == "unsatisfied" for report in reports)
        if elapsed >= WINDOW_MS:
            # Late reports cannot repair the window, and current contrary/unknown evidence
            # cannot support a negative verdict based only on the earlier cached pair.
            if negative and assessment["verdict"] == "unsatisfied":
                self._publish(job, "unsatisfied", state["observed_at_ms"])
            else:
                self._manual(job, "two_fresh_consistent_reports_unavailable")
        elif positive:
            self._publish(job, "satisfied", state["observed_at_ms"])
        else:
            self.store.save(job)

    def _report_budget(self, report: dict[str, Any]) -> int:
        """Spend the original report lifetime including local polling and publication delay."""
        elapsed = self._elapsed(report["kernel"])
        return (
            0
            if elapsed is None
            else max(0, report["observation"]["evidence"]["remaining_valid_for_ms"] - elapsed)
        )

    def _publish(self, job: dict[str, Any], verdict: str, observed_at_ms: int) -> None:
        """Freeze and send one bounded verdict; uncertain delivery never causes an action retry."""
        original, progress = job["input"], job["state"]
        budget = (
            min(
                original["max_evidence_age_ms"],
                *(self._report_budget(report) for report in progress["reports"]),
            )
            - PUBLICATION_BUDGET_MS
        )
        if budget <= 0 or self._stop.is_set():
            self._manual(job, "publication_freshness_budget_exhausted")
            return
        evidence = {
            "evidence_id": job["job_id"],
            "source_id": self.identity["source_id"],
            "source_revision": self.identity["source_revision"],
            "tenant_id": original["tenant_id"],
            "task_ref": {"mission_id": original["mission_id"], "task_id": original["task_id"]},
            "plan_digest": progress["binding"]["plan_digest"],
            "attempts": progress["binding"]["attempts"],
            "predicate": original["predicate"],
            "verdict": verdict,
            "finality": "final",
            "source_observed_at_ms": observed_at_ms,
            "valid_for_ms": budget,
            "artifact_digest": None,
            "artifact_size_bytes": None,
        }
        self.store.prepare_publication(job, evidence)
        if self._stop.is_set() or (
            min(self._report_budget(report) for report in progress["reports"])
            - evidence["valid_for_ms"]
            < 200
        ):
            self._manual(job, "before_io_publication_budget_exhausted")
            return
        receipt = self.transport.publish_posture_evidence(evidence)
        progress["receipt"] = receipt
        progress["verdict"] = verdict
        job["status"] = "complete"
        self.store.save(job)
