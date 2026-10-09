"""Offline admission fences for stopped worlds and exact continuation attempts."""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from habitat_local_eaios.backend import LocalExecutionOutcome  # noqa: E402
from habitat_local_eaios.model import CanonicalMobilityInvocation, IntegrationError  # noqa: E402
from habitat_local_eaios.retained_session import (  # noqa: E402
    MAX_CONTINUATIONS,
    RetainedWorldSession,
    claim_retained_world,
)


def _invocations() -> dict[int, CanonicalMobilityInvocation]:
    """Create two independent exact slots with unrelated semantic target names."""
    session: dict[str, Any] = {
        "schema_version": "roboguide.execution-session/v0.1",
        "mission_id": "mission",
        "group_id": "group",
        "slots": [
            {
                "task_id": f"task-{agent}",
                "role_id": "role",
                "actor_id": f"actor-{agent}",
                "dependencies": [],
                "independent": True,
            }
            for agent in range(2)
        ],
    }
    session["digest"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(session, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    return {
        agent: CanonicalMobilityInvocation.from_request(
            {
                "invocation": {
                    "mission_id": "mission",
                    "group_id": "group",
                    "task_id": f"task-{agent}",
                    "role_id": "role",
                    "operation": "mobility.move@v1",
                    "objective": "Reach the assigned destination",
                    "parameters": {"destination": f"destination-{agent}"},
                    "resource_ids": [f"space-{agent}"],
                    "attempt_id": f"original-{agent}",
                    "execution_session": session,
                }
            }
        )
        for agent in range(2)
    }


def _outcomes(
    invocations: dict[int, CanonicalMobilityInvocation], *, completed: frozenset[int] = frozenset()
) -> dict[int, LocalExecutionOutcome]:
    """Create already-observed local outcomes without assuming official goal satisfaction."""
    return {
        agent: LocalExecutionOutcome(
            state="COMPLETED" if agent in completed else "CANCELLED",
            detail="observed local terminal",
            episode_id="unrelated-episode",
            scene_id="scene",
            destination=invocation.destination,
            simulator_steps=3,
            initial_position=(0.0, 0.0, 0.0),
            final_position=(0.0, 0.0, 0.0),
            local_skill_completed=agent in completed,
            terminal_basis="oracle-nav-skill" if agent in completed else "cancellation",
        )
        for agent, invocation in invocations.items()
    }


def test_resume_preserves_completed_effects_and_original_budget() -> None:
    """Only the stopped slot gets a new attempt; a resource choice grants no semantic change."""
    invocations = _invocations()
    session = RetainedWorldSession(invocations, 10)
    outcomes = _outcomes(invocations, completed=frozenset({0}))
    session.finish(outcomes, 3, False)
    assert session.pending_agents == {1}
    replacement = replace(invocations[1], attempt_id="retry", resource_ids=("new-space",))
    session.resume({1: replacement})
    assert session.steps == 3
    assert session.outcomes == {0: outcomes[0]}
    assert session.invocations[0] == invocations[0]
    assert session.invocations[1] == replacement
    session.finish({0: outcomes[0], 1: replace(outcomes[1], state="COMPLETED")}, 10, False)
    assert session.phase == "closed"
    assert session.continuations == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"attempt_id": None},
        {"attempt_id": "original-0"},
        {"parameters": {"destination": "other"}, "attempt_id": "new"},
        {"operation": "mobility.navigate@v1", "attempt_id": "new"},
        {"objective": "Other objective", "attempt_id": "new"},
        {"task_id": "task-0", "attempt_id": "new"},
        {"mission_id": "other", "attempt_id": "new"},
        {"execution_session": None, "attempt_id": "new"},
    ],
)
def test_altered_or_stale_attempt_is_rejected_without_mutation(changes: dict[str, Any]) -> None:
    """Identity and intent changes cannot become a local recovery decision."""
    invocations = _invocations()
    session = RetainedWorldSession(invocations, 10)
    session.finish(_outcomes(invocations), 3, False)
    before = session.as_dict()
    assert not session.can_replace(1, replace(invocations[1], **changes))
    with pytest.raises(IntegrationError, match="all stopped slots"):
        session.resume({0: replace(invocations[0], attempt_id="new-0")})
    assert session.as_dict() == before


def test_joint_resume_requires_complete_cancelled_set_and_unique_attempts() -> None:
    """A half-populated or duplicate-attempt continuation cannot start the world."""
    invocations = _invocations()
    session = RetainedWorldSession(invocations, 10)
    session.finish(_outcomes(invocations), 1, False)
    with pytest.raises(IntegrationError):
        session.resume(
            {agent: replace(value, attempt_id="same") for agent, value in invocations.items()}
        )
    session.resume(
        {agent: replace(value, attempt_id=f"retry-{agent}") for agent, value in invocations.items()}
    )
    assert session.phase == "running"
    assert session.steps == 1


@pytest.mark.parametrize(
    "ended,steps,state", [(True, 3, "CANCELLED"), (False, 10, "CANCELLED"), (False, 3, "FAILED")]
)
def test_ended_failed_or_exhausted_world_cannot_continue(
    ended: bool, steps: int, state: str
) -> None:
    """Only actual cancellation in an unfinished, budgeted world permits continuation."""
    invocations = _invocations()
    session = RetainedWorldSession(invocations, 10)
    outcomes = {
        agent: replace(outcome, state=state) for agent, outcome in _outcomes(invocations).items()
    }
    session.finish(outcomes, steps, ended)
    assert session.phase == "closed"
    assert session.pending_agents == frozenset()


def test_local_retry_budget_never_renews() -> None:
    """Repeated real stops cannot grow per-session memory or reset a fixed continuation ceiling."""
    invocations = _invocations()
    session = RetainedWorldSession(invocations, 100)
    for index in range(MAX_CONTINUATIONS):
        session.finish(_outcomes(session.invocations), index, False)
        session.resume(
            {
                agent: replace(value, attempt_id=f"retry-{index}-{agent}")
                for agent, value in invocations.items()
            }
        )
    session.finish(_outcomes(session.invocations), MAX_CONTINUATIONS, False)
    assert session.phase == "closed"
    assert len(session._seen) == 2 * (MAX_CONTINUATIONS + 1)


def test_completed_outcome_cannot_be_rewritten_by_later_segment() -> None:
    """A resumed attempt cannot erase a sibling's already observed completion."""
    invocations = _invocations()
    session = RetainedWorldSession(invocations, 10)
    outcomes = _outcomes(invocations, completed=frozenset({0}))
    session.finish(outcomes, 1, False)
    session.resume({1: replace(invocations[1], attempt_id="retry")})
    with pytest.raises(IntegrationError, match="inconsistent"):
        session.finish(_outcomes(invocations), 2, False)
    assert session.phase == "closed"


def test_world_owner_fence_refuses_process_restart(tmp_path: Path) -> None:
    """Even clean shutdown never authorizes a second reset in the same retained run directory."""
    claim_retained_world(tmp_path)
    original = (tmp_path / "retained-world-owner.json").read_bytes()
    with pytest.raises(IntegrationError, match="unused durable"):
        claim_retained_world(tmp_path)
    assert (tmp_path / "retained-world-owner.json").read_bytes() == original
