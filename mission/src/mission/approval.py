"""Deterministic context-aware approval policy for admitted MissionPlan drafts."""

from __future__ import annotations

from dataclasses import dataclass

from mission.intent import GroundedIntent
from mission.models import MissionPlan

type ApprovalScalar = str | int | float | bool


@dataclass(frozen=True, slots=True)
class ApprovalRule:
    """Match one canonical operation plus optional semantic risk predicates."""

    rule_id: str
    operation: str
    parameter_equals: tuple[tuple[str, ApprovalScalar], ...] = ()
    objective_contains: tuple[str, ...] = ()
    grounded_constraint_contains: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Reject blank identities, duplicated parameters, and empty text predicates."""
        if not self.rule_id.strip() or not self.operation.strip():
            raise ValueError("approval rule identity and operation must be nonblank")
        names = [name for name, _ in self.parameter_equals]
        if any(not name.strip() for name in names) or len(set(names)) != len(names):
            raise ValueError("approval rule parameters must be nonblank and unique")
        if any(not value.strip() for value in self.objective_contains):
            raise ValueError("approval objective predicates must be nonblank")
        if any(not value.strip() for value in self.grounded_constraint_contains):
            raise ValueError("approval grounded-constraint predicates must be nonblank")

    def matches(self, plan: MissionPlan, grounded_intent: GroundedIntent) -> bool:
        """Return whether any Role intent satisfies every predicate in this rule."""
        grounded_constraints = "\n".join(grounded_intent.constraints).casefold()
        if any(
            token.casefold() not in grounded_constraints
            for token in self.grounded_constraint_contains
        ):
            return False
        for task in plan.tasks:
            for role in task.roles:
                intent = role.execution
                operation = (
                    f"{intent.operation.namespace}.{intent.operation.name}"
                    f"@{intent.operation.version}"
                )
                if operation != self.operation:
                    continue
                parameters = dict(intent.parameters)
                if any(parameters.get(name) != value for name, value in self.parameter_equals):
                    continue
                objective = intent.objective.casefold()
                if any(token.casefold() not in objective for token in self.objective_contains):
                    continue
                return True
        return False


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    """Explain whether one immutable draft requires explicit user approval."""

    required: bool
    matched_rule_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ApprovalPolicy:
    """Evaluate deployment risk rules without Node or resource authority."""

    rules: tuple[ApprovalRule, ...]

    def __post_init__(self) -> None:
        """Reject duplicate rule identities so persisted reasons remain unambiguous."""
        identities = [rule.rule_id for rule in self.rules]
        if len(set(identities)) != len(identities):
            raise ValueError("approval policy contains duplicate rule identities")

    @classmethod
    def from_contracts(cls, contracts: frozenset[str]) -> ApprovalPolicy:
        """Normalize the historical contract-name gate into unconditional operation rules."""
        return cls(
            tuple(
                ApprovalRule(f"legacy-contract:{contract}", contract)
                for contract in sorted(contracts)
            )
        )

    def evaluate(self, plan: MissionPlan, grounded_intent: GroundedIntent) -> ApprovalDecision:
        """Return stable matching rule identities without mutating the admitted draft."""
        matches = tuple(rule.rule_id for rule in self.rules if rule.matches(plan, grounded_intent))
        return ApprovalDecision(bool(matches), matches)
