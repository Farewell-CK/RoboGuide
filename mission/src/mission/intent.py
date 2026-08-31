"""Resolved Mission Intelligence input passed from interpretation into planning."""

from __future__ import annotations

from dataclasses import dataclass

from mission.models import JSONObject


@dataclass(frozen=True, slots=True)
class GroundedIntent:
    """Carry one resolved objective, confirmed constraints, and explicit assumptions."""

    objective: str
    constraints: tuple[str, ...]
    assumptions: tuple[str, ...]

    def __post_init__(self) -> None:
        """Reject blank handoff fields before they can enter a Planner prompt."""
        if not self.objective.strip():
            raise ValueError("grounded objective must be nonblank text")
        for field, values in (
            ("constraints", self.constraints),
            ("assumptions", self.assumptions),
        ):
            if any(not value.strip() for value in values):
                raise ValueError(f"grounded {field} must contain nonblank text")

    def to_json(self) -> JSONObject:
        """Serialize the complete resolved handoff in stable field order."""
        return {
            "objective": self.objective,
            "constraints": list(self.constraints),
            "assumptions": list(self.assumptions),
        }
