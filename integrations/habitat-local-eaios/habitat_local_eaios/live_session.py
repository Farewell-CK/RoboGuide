"""Bounded local dispatch identity for one immutable independent execution session."""

from __future__ import annotations

from typing import Any

from .endpoint_registry import MAX_SLOTS
from .model import CanonicalInvocation, CanonicalRelocationInvocation, IntegrationError


class LiveSlotLedger:
    """Bound one immutable independent session without deciding readiness or placement."""

    def __init__(self, agent_ids: tuple[int, ...]) -> None:
        """Keep bounded dispatch identities and local completion facts in one reset world."""
        self.agent_ids = agent_ids
        self.session: Any = None
        self.seen: dict[tuple[str, str], tuple[int, CanonicalInvocation]] = {}
        self.completed: set[tuple[str, str]] = set()
        self.active: dict[int, CanonicalInvocation] = {}
        self.actor_owners: dict[str, int] = {}

    def admit(self, agent: int, invocation: CanonicalInvocation) -> None:
        """Accept exact independent slots, rejecting migration, duplication and active overlap."""
        session = invocation.execution_session
        if (
            agent not in self.agent_ids
            or session is None
            or not invocation.attempt_id
            or not 1 <= len(session.slots) <= MAX_SLOTS
            or any(slot["independent"] is not True for slot in session.slots)
            or len({str(slot["task_id"]) for slot in session.slots}) != len(session.slots)
        ):
            raise IntegrationError(
                "live profile needs bounded independent single-role Tasks and attempts"
            )
        if self.session is not None and session != self.session:
            raise IntegrationError("live assignment belongs to a different immutable session")
        slot = (invocation.task_id, invocation.role_id)
        actor = next(
            (
                str(value["actor_id"])
                for value in session.slots
                if (value["task_id"], value["role_id"]) == slot
            ),
            None,
        )
        if actor is None:
            raise IntegrationError("live assignment is not an exact session slot")
        if slot in self.seen or agent in self.active:
            raise IntegrationError("live assignment repeats a slot or overlaps an active endpoint")
        if actor in self.actor_owners and self.actor_owners[actor] != agent:
            raise IntegrationError("live profile cannot migrate a Control-bound Actor")
        if isinstance(invocation, CanonicalRelocationInvocation) and any(
            isinstance(other, CanonicalRelocationInvocation)
            and other.object_ref == invocation.object_ref
            for other in self.active.values()
        ):
            raise IntegrationError("live profile cannot concurrently manipulate one object")
        self.session = session
        self.actor_owners[actor] = agent
        self.seen[slot] = (agent, invocation)
        self.active[agent] = invocation

    def finish(self, agent: int) -> CanonicalInvocation:
        """Release only the adapter's active local handle, never Control-owned resources."""
        invocation = self.active.pop(agent)
        self.completed.add((invocation.task_id, invocation.role_id))
        return invocation

    def complete(self) -> bool:
        """Detect all local session slots finished without claiming semantic satisfaction."""
        return self.session is not None and len(self.completed) == len(self.session.slots)
