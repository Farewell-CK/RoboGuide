"""Immutable initial-operation feedback, separate from planning and admission authority."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from mission.models import JSONObject, MissionPlan

SCHEMA = "roboguide.initial-operation-assessment/v0.1"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_UNAVAILABLE_REASONS = {
    "source_unconfigured",
    "source_expired",
    "controller_restored",
    "world_already_admitted",
    "deployment_placement_unavailable",
    "current_candidates_unavailable",
    "unsupported_plan_scope",
}


def plan_body_digest(plan: MissionPlan) -> str:
    """Bind the exact production HTTP encoding, independent of canonical draft identity."""
    body = json.dumps(plan.to_json(), ensure_ascii=False, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(body).hexdigest()


@dataclass(frozen=True, slots=True)
class AssessedRole:
    """Expose exact logical slots and bounded counts, never live Node inventory."""

    task_id: str
    role_id: str
    operation: str
    witness_count: int
    bounded_miss_count: int
    scoped_disjoint_count: int
    unknown_count: int

    def __post_init__(self) -> None:
        """Reject invented identities and unbounded or contradictory candidate counts."""
        for value in (self.task_id, self.role_id, self.operation):
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise ValueError("assessment slot identity is invalid")
        counts = (
            self.witness_count,
            self.bounded_miss_count,
            self.scoped_disjoint_count,
            self.unknown_count,
        )
        if (
            any(type(value) is not int or not 0 <= value <= 128 for value in counts)
            or not 0 < sum(counts) <= 128
        ):
            raise ValueError("assessment candidate counts are invalid")

    def to_json(self) -> JSONObject:
        """Return a fresh closed slot summary without executor or resource identities."""
        return {
            "task_id": self.task_id,
            "role_id": self.role_id,
            "operation": self.operation,
            "witness_count": self.witness_count,
            "bounded_miss_count": self.bounded_miss_count,
            "scoped_disjoint_count": self.scoped_disjoint_count,
            "unknown_count": self.unknown_count,
        }

    @classmethod
    def from_json(cls, value: Any) -> AssessedRole:
        """Reject arbitrary adapter fields before restoring a slot summary."""
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("assessment role fields are invalid")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class InitialOperationAssessment:
    """Retain one exact-plan, source-bound observation without proving physical success."""

    mission_id: str
    plan_body_sha256: str
    decision: str
    reason_code: str
    source_digest: str | None
    world_snapshot_digest: str | None
    local_how_digest: str | None
    received_at_ms: int | None
    expires_at_ms: int | None
    assessed_at_ms: int
    checked_combinations: int
    roles: tuple[AssessedRole, ...]

    def __post_init__(self) -> None:
        """Fail closed on detached, stale or malformed usable feedback, retaining honest gaps."""
        if (
            not isinstance(self.mission_id, str)
            or not self.mission_id.strip()
            or len(self.mission_id) > 256
        ):
            raise ValueError("assessment Mission identity is invalid")
        if not isinstance(self.plan_body_sha256, str) or not _DIGEST.fullmatch(
            self.plan_body_sha256
        ):
            raise ValueError("assessment plan digest is invalid")
        for value in (self.source_digest, self.world_snapshot_digest, self.local_how_digest):
            if value is not None and (not isinstance(value, str) or not _DIGEST.fullmatch(value)):
                raise ValueError("assessment source digest is invalid")
        for timestamp in (self.received_at_ms, self.expires_at_ms, self.assessed_at_ms):
            if timestamp is not None and (type(timestamp) is not int or timestamp < 0):
                raise ValueError("assessment timestamp is invalid")
        if (
            type(self.assessed_at_ms) is not int
            or type(self.checked_combinations) is not int
            or not 0 <= self.checked_combinations <= 16384
        ):
            raise ValueError("assessment time or combination count is invalid")
        if (
            not isinstance(self.roles, tuple)
            or len(self.roles) > 16
            or any(not isinstance(role, AssessedRole) for role in self.roles)
        ):
            raise ValueError("assessment role summaries are invalid")
        if len({(role.task_id, role.role_id) for role in self.roles}) != len(self.roles):
            raise ValueError("assessment repeats a logical slot")
        if self.decision == "unavailable":
            if self.reason_code not in _UNAVAILABLE_REASONS or self.checked_combinations:
                raise ValueError("unavailable assessment contradicts its reason or coverage")
            return
        expected = {
            "blocked": "scoped_static_support_shortage",
            "not_blocked": "no_scoped_shortage",
        }
        if self.decision not in expected or self.reason_code != expected[self.decision]:
            raise ValueError("assessment decision contradicts its reason")
        if (
            any(
                value is None
                for value in (self.source_digest, self.world_snapshot_digest, self.local_how_digest)
            )
            or not self.roles
            or not self.checked_combinations
        ):
            raise ValueError("usable assessment lacks source or coverage")
        if (
            self.received_at_ms is None
            or self.expires_at_ms is None
            or not self.received_at_ms <= self.assessed_at_ms < self.expires_at_ms
            or not 0 < self.expires_at_ms - self.received_at_ms <= 600_000
        ):
            raise ValueError("usable assessment is outside its receive-relative lifetime")
        if self.decision == "blocked" and not any(
            role.scoped_disjoint_count for role in self.roles
        ):
            raise ValueError("a bounded search miss or unknown cannot block execution")

    def matches_plan(self, plan: MissionPlan) -> bool:
        """Require exact bytes and slot attribution; the reply cannot add plan requirements."""
        roots = [task for task in plan.tasks if not task.depends_on]
        actors = {
            (context.context_id, role.role_id): role.actor_id
            for context in plan.contexts
            for role in context.roles
        }
        if len(roots) == 2 and all(len(task.roles) == 1 for task in roots):
            root_actors = [
                actors.get(
                    (task.context_id, task.roles[0].context_role or ""), task.roles[0].actor_id
                )
                for task in roots
            ]
            if root_actors[0] is not None and root_actors[0] == root_actors[1]:
                roots = roots[:1]
        slots = {
            (
                task.task_id,
                role.role_id,
                f"{role.execution.operation.namespace}.{role.execution.operation.name}@{role.execution.operation.version}",
            )
            for task in roots
            for role in task.roles
        }
        return (
            self.mission_id == plan.mission.mission_id
            and self.plan_body_sha256 == plan_body_digest(plan)
            and all((role.task_id, role.role_id, role.operation) in slots for role in self.roles)
            and (
                self.decision == "unavailable"
                or {(role.task_id, role.role_id, role.operation) for role in self.roles} == slots
            )
        )

    def to_json(self) -> JSONObject:
        """Serialize a fresh neutral observation without mutating frozen history."""
        return {
            "schema_version": SCHEMA,
            "mission_id": self.mission_id,
            "plan_body_sha256": self.plan_body_sha256,
            "scope": "initial_static_world",
            "decision": self.decision,
            "reason_code": self.reason_code,
            "source_digest": self.source_digest,
            "world_snapshot_digest": self.world_snapshot_digest,
            "local_how_digest": self.local_how_digest,
            "received_at_ms": self.received_at_ms,
            "expires_at_ms": self.expires_at_ms,
            "assessed_at_ms": self.assessed_at_ms,
            "checked_combinations": self.checked_combinations,
            "roles": [role.to_json() for role in self.roles],
        }

    @classmethod
    def from_json(cls, value: Any) -> InitialOperationAssessment:
        """Restore only the explicit assessment schema; no response text drives policy."""
        expected = set(cls.__dataclass_fields__) | {"schema_version", "scope"}
        if (
            not isinstance(value, dict)
            or set(value) != expected
            or value["schema_version"] != SCHEMA
            or value["scope"] != "initial_static_world"
            or not isinstance(value["roles"], list)
        ):
            raise ValueError("initial assessment fields or schema are invalid")
        fields = dict(value)
        fields.pop("schema_version")
        fields.pop("scope")
        fields["roles"] = tuple(AssessedRole.from_json(role) for role in fields["roles"])
        return cls(**fields)
