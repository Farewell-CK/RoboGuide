"""Configuration-owned verifier freshness for generated Mission drafts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from mission.contract_values import U64_MAX
from mission.models import JSONObject, MissionPlan


class MissionSatisfactionPolicyError(ValueError):
    """Reject absent or inconsistent policy provenance without changing a MissionPlan."""


@dataclass(frozen=True, slots=True)
class MissionSatisfactionPolicy:
    """Freeze a system-selected receive-age bound; it never proves a verdict is true."""

    policy_ref: str
    max_evidence_age_ms: int

    def __post_init__(self) -> None:
        """Reject missing identity and invalid bounds, including Boolean integer coercion."""
        if not isinstance(self.policy_ref, str) or not self.policy_ref.strip():
            raise MissionSatisfactionPolicyError("satisfaction policy_ref must be nonblank")
        age = self.max_evidence_age_ms
        if isinstance(age, bool) or not isinstance(age, int) or not 0 < age <= U64_MAX:
            raise MissionSatisfactionPolicyError(
                "satisfaction max_evidence_age_ms must be a positive u64 integer"
            )

    @classmethod
    def from_config(cls, value: object) -> MissionSatisfactionPolicy | None:
        """Parse an explicit table; omitted configuration has no numeric default."""
        if value is None:
            return None
        if not isinstance(value, dict) or set(value) != {"policy_ref", "max_evidence_age_ms"}:
            raise MissionSatisfactionPolicyError(
                "mission.satisfaction_policy requires only policy_ref and max_evidence_age_ms"
            )
        return cls(value["policy_ref"], value["max_evidence_age_ms"])

    def to_json(self) -> JSONObject:
        """Return fresh model-input evidence with a digest of the exact configured policy."""
        evidence: JSONObject = {
            "policy_ref": self.policy_ref,
            "source": "mission-system-policy",
            "time_basis": "roboguide-receive-time",
            "max_evidence_age_ms": self.max_evidence_age_ms,
        }
        canonical = json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        evidence["policy_digest"] = (
            "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        )
        return evidence


def validate_satisfaction_policy(
    plan: MissionPlan, policy: MissionSatisfactionPolicy | None
) -> None:
    """Reject ungrounded generated verifier bounds; leave execution-report Tasks untouched.

    Canonical parsing has already checked the basis/verifier pair. This admission rule never
    fills fields, weakens predicates, or guesses user overrides from natural-language text.
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
