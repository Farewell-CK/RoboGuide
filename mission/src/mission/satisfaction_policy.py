"""Configuration-owned verifier freshness for generated Mission drafts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from mission.contract_values import U64_MAX, MissionPlanError
from mission.models import (
    CapabilityContractRef,
    JSONObject,
    MissionPlan,
    TaskSatisfactionBasis,
)
from mission.semantic_evidence import (
    AuthoritativeSemanticEvidence,
    exact_goal_verifier_predicate,
)


class MissionSatisfactionPolicyError(ValueError):
    """Reject absent or inconsistent policy provenance without changing a MissionPlan."""


@dataclass(frozen=True, slots=True)
class MissionSatisfactionPolicy:
    """Freeze receive age and any explicit authoritative-goal confirmation rule."""

    policy_ref: str
    max_evidence_age_ms: int
    authoritative_goal_verifier: CapabilityContractRef | None = None

    def __post_init__(self) -> None:
        """Reject missing identity and invalid bounds, including Boolean integer coercion."""
        if not isinstance(self.policy_ref, str) or not self.policy_ref.strip():
            raise MissionSatisfactionPolicyError("satisfaction policy_ref must be nonblank")
        age = self.max_evidence_age_ms
        if isinstance(age, bool) or not isinstance(age, int) or not 0 < age <= U64_MAX:
            raise MissionSatisfactionPolicyError(
                "satisfaction max_evidence_age_ms must be a positive u64 integer"
            )
        verifier = self.authoritative_goal_verifier
        if verifier is not None:
            if not isinstance(verifier, CapabilityContractRef):
                raise MissionSatisfactionPolicyError(
                    "authoritative_goal_verifier must be a canonical contract"
                )
            try:
                CapabilityContractRef.from_json(
                    verifier.to_json(), "satisfaction.authoritative_goal_verifier"
                )
            except MissionPlanError as error:
                raise MissionSatisfactionPolicyError(str(error)) from error

    @classmethod
    def from_config(cls, value: object) -> MissionSatisfactionPolicy | None:
        """Parse an explicit table; omitted configuration has no numeric default."""
        if value is None:
            return None
        if (
            not isinstance(value, dict)
            or not {"policy_ref", "max_evidence_age_ms"} <= set(value)
            or set(value)
            - {
                "policy_ref",
                "max_evidence_age_ms",
                "authoritative_goal_verifier",
            }
        ):
            raise MissionSatisfactionPolicyError(
                "mission.satisfaction_policy requires policy_ref, max_evidence_age_ms, "
                "and optional authoritative_goal_verifier"
            )
        verifier = value.get("authoritative_goal_verifier")
        if verifier is None and "authoritative_goal_verifier" in value:
            raise MissionSatisfactionPolicyError(
                "authoritative_goal_verifier must be a canonical contract, not null"
            )
        try:
            parsed = (
                None
                if verifier is None
                else CapabilityContractRef.from_json(
                    verifier, "mission.satisfaction_policy.authoritative_goal_verifier"
                )
            )
        except MissionPlanError as error:
            raise MissionSatisfactionPolicyError(str(error)) from error
        return cls(value["policy_ref"], value["max_evidence_age_ms"], parsed)

    def to_json(self) -> JSONObject:
        """Return fresh model-input evidence with a digest of the exact configured policy."""
        evidence: JSONObject = {
            "policy_ref": self.policy_ref,
            "source": "mission-system-policy",
            "time_basis": "roboguide-receive-time",
            "max_evidence_age_ms": self.max_evidence_age_ms,
        }
        if self.authoritative_goal_verifier is not None:
            evidence["authoritative_goal_confirmation"] = {
                "basis": "verifier-evidence",
                "verifier_contract": self.authoritative_goal_verifier.to_json(),
                "task_scope": "all-dag-terminal-tasks",
                "predicate_source": "authoritative_semantic_goal.goal",
            }
        canonical = json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        evidence["policy_digest"] = (
            "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        )
        return evidence


def authoritative_goal_predicate(
    policy: MissionSatisfactionPolicy | None,
    semantic_evidence: AuthoritativeSemanticEvidence | None,
) -> str | None:
    """Return a representable frozen goal when the deployment requires verification.

    A policy/source incompatibility is a system gap and is detected before a
    Provider call; it is never repaired by weakening the goal expression.
    """
    if policy is None or policy.authoritative_goal_verifier is None or semantic_evidence is None:
        return None
    predicate = exact_goal_verifier_predicate(semantic_evidence.goal)
    if predicate is None:
        raise MissionSatisfactionPolicyError(
            "configured authoritative-goal verifier cannot represent the frozen goal"
        )
    return predicate


def validate_satisfaction_policy(
    plan: MissionPlan,
    policy: MissionSatisfactionPolicy | None,
    semantic_evidence: AuthoritativeSemanticEvidence | None = None,
) -> None:
    """Reject unsourced bounds and policy-incompatible terminal Task satisfaction.

    Ordinary Tasks retain their declared basis. A deployment rule for an
    authoritative joint terminal goal is applied only when that exact frozen
    evidence is present; no description text or benchmark name is parsed.
    """
    for index, task in enumerate(plan.tasks):
        verifier = task.satisfaction.verifier
        if verifier is None:
            continue
        path = f"tasks[{index}].satisfaction.verifier.max_evidence_age_ms"
        if policy is None:
            raise MissionSatisfactionPolicyError(f"{path}: no configured satisfaction policy")
        if verifier.max_evidence_age_ms != policy.max_evidence_age_ms:
            raise MissionSatisfactionPolicyError(
                f"{path}: must match system policy {policy.policy_ref} "
                f"({policy.max_evidence_age_ms} ms); model-selected bounds are not admitted"
            )
    predicate = authoritative_goal_predicate(policy, semantic_evidence)
    if predicate is None or policy is None or policy.authoritative_goal_verifier is None:
        return
    prerequisites = {dependency for task in plan.tasks for dependency in task.depends_on}
    for index, task in enumerate(plan.tasks):
        if task.task_id in prerequisites:
            continue
        verifier = task.satisfaction.verifier
        if (
            task.satisfaction.basis is not TaskSatisfactionBasis.VERIFIER_EVIDENCE
            or verifier is None
            or verifier.contract != policy.authoritative_goal_verifier
            or verifier.predicate != predicate
        ):
            raise MissionPlanError(
                f"tasks[{index}].satisfaction: DAG-terminal Task must use the "
                "configured verifier-evidence contract and exact authoritative joint goal"
            )
