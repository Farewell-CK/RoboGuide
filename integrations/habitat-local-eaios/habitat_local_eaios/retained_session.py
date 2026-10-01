"""Bounded local continuation admission; never Control or Runtime recovery authority."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from .backend import LocalExecutionOutcome
from .model import CanonicalMobilityInvocation, IntegrationError

MAX_CONTINUATIONS = 16
SESSION_STATE_SCHEMA = "roboguide.shared-world-continuation/v0.1"


def _intent(invocation: CanonicalMobilityInvocation) -> dict[str, object]:
    """Compare immutable semantic/session identity while leaving resources to Control."""
    value = invocation.as_dict()
    value.pop("attempt_id", None)
    value.pop("resource_ids", None)
    return value


class RetainedWorldSession:
    """Fence same-endpoint attempts around actual Group stop and a fixed world budget."""

    def __init__(self, invocations: dict[int, CanonicalMobilityInvocation], max_steps: int) -> None:
        """Freeze complete supported topology before any continuation may be admitted."""
        sessions = [invocation.execution_session for invocation in invocations.values()]
        if (
            not invocations
            or max_steps < 1
            or any(session is None or session.topology() == "unsupported" for session in sessions)
            or len({session.digest for session in sessions if session is not None}) != 1
            or any(not invocation.attempt_id for invocation in invocations.values())
            or len({invocation.attempt_id for invocation in invocations.values()})
            != len(invocations)
        ):
            raise IntegrationError("retained session requires exact topology and unique attempts")
        session = sessions[0]
        assert session is not None
        if session.topology() == "two_actor_concurrent" and {
            (item.task_id, item.role_id) for item in invocations.values()
        } != {(str(slot["task_id"]), str(slot["role_id"])) for slot in session.slots}:
            raise IntegrationError("retained pair requires every accepted-plan slot")
        if session.topology() == "single_actor_sequential" and len(invocations) != 1:
            raise IntegrationError("retained serial segment requires one assigned endpoint")
        self.invocations = {
            agent: CanonicalMobilityInvocation.from_request({"invocation": value.as_dict()})
            for agent, value in invocations.items()
        }
        self.max_steps = max_steps
        self.steps = 0
        self.continuations = 0
        self.phase = "running"
        self.outcomes: dict[int, LocalExecutionOutcome] = {}
        self._seen = {value.attempt_id for value in self.invocations.values()}

    @property
    def pending_agents(self) -> frozenset[int]:
        """Return only cancelled slots of a currently resumable stopped world."""
        if self.phase != "stopped":
            return frozenset()
        return frozenset(
            agent for agent, outcome in self.outcomes.items() if outcome.state == "CANCELLED"
        )

    def can_replace(self, agent_id: int, invocation: CanonicalMobilityInvocation) -> bool:
        """Reject changed intent, completed slots, reused attempts and exhausted budgets."""
        original = self.invocations.get(agent_id)
        return bool(
            agent_id in self.pending_agents
            and self.steps < self.max_steps
            and self.continuations < MAX_CONTINUATIONS
            and original is not None
            and invocation.attempt_id
            and invocation.attempt_id not in self._seen
            and _intent(invocation) == _intent(original)
        )

    def resume(self, replacements: dict[int, CanonicalMobilityInvocation]) -> None:
        """Admit the complete cancelled set atomically, retaining completed effects and budget."""
        if (
            not replacements
            or set(replacements) != self.pending_agents
            or not all(self.can_replace(agent, value) for agent, value in replacements.items())
            or len({value.attempt_id for value in replacements.values()}) != len(replacements)
        ):
            raise IntegrationError(
                "continuation requires all stopped slots with fresh exact attempts"
            )
        copies = {
            agent: CanonicalMobilityInvocation.from_request({"invocation": value.as_dict()})
            for agent, value in replacements.items()
        }
        self.invocations.update(copies)
        self._seen.update(value.attempt_id for value in copies.values())
        self.outcomes = {
            agent: outcome
            for agent, outcome in self.outcomes.items()
            if outcome.state == "COMPLETED"
        }
        self.continuations += 1
        self.phase = "running"

    def finish(
        self, outcomes: dict[int, LocalExecutionOutcome], steps: int, episode_terminated: bool
    ) -> None:
        """Retain only measured complete segment outcomes; failures never grant another attempt."""
        if (
            self.phase != "running"
            or set(outcomes) != set(self.invocations)
            or steps < self.steps
            or any(
                outcome.state not in {"COMPLETED", "FAILED", "CANCELLED"}
                for outcome in outcomes.values()
            )
            or any(outcomes[agent] != outcome for agent, outcome in self.outcomes.items())
        ):
            self.close()
            raise IntegrationError("retained world returned inconsistent segment evidence")
        self.steps = steps
        self.outcomes = dict(outcomes)
        resumable = (
            not episode_terminated
            and steps < self.max_steps
            and self.continuations < MAX_CONTINUATIONS
            and any(outcome.state == "CANCELLED" for outcome in outcomes.values())
            and all(outcome.state in {"COMPLETED", "CANCELLED"} for outcome in outcomes.values())
        )
        self.phase = "stopped" if resumable else "closed"

    def close(self) -> None:
        """Fence an ended, unavailable, timed-out or failed world without inventing outcomes."""
        self.phase = "closed"

    def as_dict(self) -> dict[str, object]:
        """Expose bounded local admission evidence, separate from benchmark and stop authority."""
        return {
            "schema_version": SESSION_STATE_SCHEMA,
            "phase": self.phase,
            "simulator_steps": self.steps,
            "max_steps": self.max_steps,
            "continuations": self.continuations,
            "max_continuations": MAX_CONTINUATIONS,
            "attempts": {str(agent): value.as_dict() for agent, value in self.invocations.items()},
            "outcomes": {str(agent): value.as_dict() for agent, value in self.outcomes.items()},
        }


def claim_retained_world(evidence_dir: Path) -> None:
    """Durably fence one process-owned world before reset; restart cannot recreate continuation.

    The marker is deliberately never removed on shutdown. A new physical run
    needs a new evidence directory, even if no assignment reached this world.
    Persistence failure is an admission failure, not best-effort diagnostics.
    """
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_dir / "retained-world-owner.json"
    marker = {
        "schema_version": SESSION_STATE_SCHEMA,
        "owner": str(uuid.uuid4()),
        "pid": os.getpid(),
    }
    try:
        with path.open("x", encoding="utf-8") as output:
            output.write(json.dumps(marker, sort_keys=True) + "\n")
            output.flush()
            os.fsync(output.fileno())
        descriptor = os.open(evidence_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise IntegrationError(
            "retained world requires an unused durable evidence directory"
        ) from error
