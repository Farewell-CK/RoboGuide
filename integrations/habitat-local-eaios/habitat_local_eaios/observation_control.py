"""Attempt-bound observation feedback and explicitly bounded Local EAIOS pauses.

This profile reads only the cache already produced by reset/step. It neither
selects a tool nor turns a positive sample into a persistent or official effect.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .model import (
    COMPOUND_OBSERVATION_FORMAT,
    CanonicalObservationAcquisitionInvocation,
    IntegrationError,
)

OBSERVATION_CONTROL_PROFILE = "bounded-visual-feedback/v0.1"
MAX_OBSERVATION_PAUSE_STEPS = 16


def observation_control_declaration(*, compound: bool = False) -> dict[str, Any]:
    """Publish the actual local feedback and pause contract without promising visibility."""
    return {
        "profile": "bounded-visual-feedback/v0.2" if compound else OBSERVATION_CONTROL_PROFILE,
        "observation_source": "existing-gym-observation",
        "max_pause_simulator_steps": MAX_OBSERVATION_PAUSE_STEPS,
        "pause_bound": "minimum-of-profile-limit-and-original-remaining-budget",
        "condition_persistence_guaranteed": False,
        "official_success_authority": "habitat-pddl",
        **(
            {
                "condition_format": COMPOUND_OBSERVATION_FORMAT,
                "arrival_source": "loaded-original-base-transform-and-goal-position",
                "arrival_completion": "same-sample-as-detection",
                "navigation_skill_completion_is_arrival": False,
                "intermediate_departure_allowed": True,
            }
            if compound
            else {}
        ),
    }


class ObservationAcquisitionControl:
    """Retain one latest attributed sample and an original nonrenewable step budget."""

    def __init__(
        self,
        invocation: CanonicalObservationAcquisitionInvocation,
        agent_id: int,
        start_step: int,
        max_steps: int,
        semantic_digest: str,
    ) -> None:
        """Freeze authority before any model call; later samples cannot renew the budget."""
        if (
            type(agent_id) is not int
            or agent_id < 0
            or type(start_step) is not int
            or start_step < 0
            or type(max_steps) is not int
            or not 1 <= max_steps <= 4096
            or not isinstance(semantic_digest, str)
            or not semantic_digest
        ):
            raise IntegrationError("observation control needs an exact identity and bounded budget")
        self.invocation = invocation
        self.agent_id = agent_id
        self.start_step = start_step
        self.max_steps = max_steps
        self.semantic_digest = semantic_digest
        self._sample: dict[str, Any] | None = None

    def update(self, receipt: dict[str, Any]) -> None:
        """Admit only monotonic cache samples for this attempt, condition and reset world."""
        step = receipt.get("simulator_steps")
        status, holds = receipt.get("status"), receipt.get("condition_holds")
        if (
            receipt.get("invocation_digest") != "sha256:" + self.invocation.request_key()
            or receipt.get("authoritative_semantic_evidence_digest") != self.semantic_digest
            or type(receipt.get("agent_id")) is not int
            or receipt.get("agent_id") != self.agent_id
            or receipt.get("condition") != self.invocation.parameters["expected"]
            or receipt.get("source") != "existing-gym-observation"
            or receipt.get("sensor_scope") not in {"agent", "world"}
            or not isinstance(receipt.get("sensor_uuid"), str)
            or not 1 <= len(receipt["sensor_uuid"]) <= 256
            or type(step) is not int
            or step < self.start_step
            or (self._sample is not None and step < self._sample["simulator_step"])
            or status not in {"observed", "unavailable"}
            or (status == "observed" and type(holds) is not bool)
            or (status == "unavailable" and holds is not None)
        ):
            raise IntegrationError("observation feedback is malformed, stale or detached")
        sample = {
            "status": status,
            "condition_holds": holds,
            "simulator_step": step,
            "sensor_uuid": receipt.get("sensor_uuid"),
            "sensor_scope": receipt["sensor_scope"],
            "unavailable_reason": str(receipt.get("unavailable_reason", ""))[:256] or None,
        }
        if self.invocation.requires_arrival:
            detected, arrived = (
                receipt.get("detection_condition_holds"),
                receipt.get("arrival_condition_holds"),
            )
            if status == "observed" and (
                receipt.get("schema_version") != "roboguide.local-condition-observation/v0.2"
                or type(detected) is not bool
                or type(arrived) is not bool
                or holds is not (detected and arrived)
                or receipt.get("same_step_components") is not True
            ):
                raise IntegrationError("compound observation must join actual same-step components")
            sample.update(detection_condition_holds=detected, arrival_condition_holds=arrived)
        if (
            self._sample is not None
            and step == self._sample["simulator_step"]
            and sample != self._sample
        ):
            raise IntegrationError("conflicting observation feedback for the same simulator step")
        self._sample = sample

    def snapshot(self) -> dict[str, Any]:
        """Expose current evidence and remaining budget without inferring effect persistence."""
        sample = self._sample
        elapsed = 0 if sample is None else sample["simulator_step"] - self.start_step
        return {
            **observation_control_declaration(compound=self.invocation.requires_arrival),
            "invocation_digest": "sha256:" + self.invocation.request_key(),
            "mission_id": self.invocation.mission_id,
            "task_id": self.invocation.task_id,
            "role_id": self.invocation.role_id,
            "attempt_id": self.invocation.attempt_id,
            "agent_id": self.agent_id,
            "authoritative_semantic_evidence_digest": self.semantic_digest,
            "condition": self.invocation.parameters["expected"],
            "sample": deepcopy(sample),
            "acquisition_start_step": self.start_step,
            "acquisition_max_steps": self.max_steps,
            "acquisition_steps": elapsed,
            "remaining_simulator_steps": max(0, self.max_steps - elapsed),
            "local_completion_is_official_success": False,
        }

    def pause_steps(self) -> int:
        """Bound a selected pause to the published cadence and original remaining budget."""
        remaining = self.snapshot()["remaining_simulator_steps"]
        if remaining <= 0:
            raise IntegrationError("observation control budget exhausted before tool dispatch")
        return min(MAX_OBSERVATION_PAUSE_STEPS, int(remaining))
