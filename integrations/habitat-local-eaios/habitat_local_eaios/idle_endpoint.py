"""Scoped passive policy for unassigned or locally completed shared-world endpoints.

The joint EMOS actor still advances every configured low-level policy. An
unassigned endpoint uses EMOS' existing WaitSkillPolicy, without asking a model
to invent an action for a Task it does not own. Assigned endpoints retain the
original CrabAgent, model, high-level policy, and action guard until observed
operation completion. Relocation may then use the original wait skill while
its sibling continues; no selected model action is changed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from .model import IntegrationError


def _original_wait_skill_type() -> type[Any]:
    """Resolve the installed EMOS wait implementation in the Habitat process.

    Raises:
        IntegrationError: If the original skill cannot be imported. An
            unassigned endpoint must never fall back to an arbitrary action.
    """
    try:
        from habitat_baselines.rl.hrl.skills.wait import (  # type: ignore[import-not-found]
            WaitSkillPolicy,
        )
    except ImportError as error:
        raise IntegrationError("original EMOS wait skill is unavailable") from error
    return cast(type[Any], WaitSkillPolicy)


class PassiveIdleAgent:
    """Supply only the existing wait skill to an unassigned EMOS endpoint."""

    def __init__(self, name: str) -> None:
        """Create a model-free replacement for one execution segment."""
        self.name = name
        self.initialized = False
        self.start_act = False
        self.llm_model: None = None
        self.initializations = 0
        self.wait_selections = 0

    def init_agent(
        self,
        robot_type: str,
        task_description: str,
        subtask_description: str,
        chat_history: list[Any] | None = None,
        enable_logging: bool = False,
        logging_file: str = "",
    ) -> None:
        """Accept only the adapter's explicit no-assignment arguments, without I/O."""
        del robot_type, task_description, enable_logging, logging_file
        if subtask_description != "Nothing to do" or chat_history not in (None, []):
            raise IntegrationError("passive endpoint received an assigned Stage2 subtask")
        self.initialized = True
        self.start_act = False
        self.initializations += 1

    def chat(self, observation: str) -> dict[str, Any]:
        """Choose the existing local wait skill without a Provider call."""
        del observation
        if not self.initialized:
            raise IntegrationError("passive endpoint was not initialized")
        self.wait_selections += 1
        return {"name": "wait", "arguments": ["500"]}

    def get_token_usage(self) -> int:
        """Report the absence of a Provider for this endpoint."""
        return 0

    def as_dict(self) -> dict[str, object]:
        """Describe actual local policy selections without claiming model actions."""
        return {
            "agent_name": self.name,
            "initializations": self.initializations,
            "local_wait_selections": self.wait_selections,
            "provider_calls": 0,
            "skill": "wait",
        }


@dataclass
class PassiveIdleBinding:
    """Own one temporary swap of unassigned high-level agent instances."""

    assigned_agent_name: str
    replacements: tuple[tuple[Any, Any, PassiveIdleAgent], ...]
    mode: str = "passive-unassigned-endpoints"

    def activate(self) -> None:
        """Apply the prevalidated local idle policy without another model decision."""
        for high_level, _, passive in self.replacements:
            high_level.llm_agent = passive

    @property
    def idle_names(self) -> frozenset[str]:
        """Expose the exact agents that no longer have a model decision path."""
        return frozenset(passive.name for _, _, passive in self.replacements)

    def evidence(self) -> dict[str, object]:
        """Record the deployment decision and bounded per-segment counters."""
        return {
            "schema_version": (
                "roboguide.shared-world-idle-policy/v0.2"
                if self.mode == "passive-completed-endpoint"
                else "roboguide.shared-world-idle-policy/v0.1"
            ),
            "assigned_agent_name": self.assigned_agent_name,
            "mode": self.mode,
            "idle_agents": [passive.as_dict() for _, _, passive in self.replacements],
        }

    def restore(self) -> None:
        """Return every high-level policy to its original CrabAgent instance."""
        for high_level, original, _ in reversed(self.replacements):
            high_level.llm_agent = original


def install_passive_idle_agents(
    actor: Any,
    assignment: Mapping[str, Any],
    assigned_agent_name: str,
) -> PassiveIdleBinding:
    """Fail closed on policy drift, then make only unassigned endpoints passive.

    The original EMOS joint actor and WaitSkillPolicy continue to drive Habitat.
    All validation precedes mutation, so unsupported skill maps or incomplete
    assignments cannot leave a partially changed actor.
    """
    policies = actor._active_policies
    names = [getattr(policy._high_level_policy.llm_agent, "name", None) for policy in policies]
    if (
        not policies
        or names != [f"agent_{index}" for index in range(len(policies))]
        or set(names) != set(assignment)
        or assigned_agent_name not in assignment
        or any(
            isinstance(policy._high_level_policy.llm_agent, PassiveIdleAgent) for policy in policies
        )
    ):
        raise IntegrationError("shared-world agent policies must match the exact assignment")

    pending: list[tuple[Any, Any, PassiveIdleAgent]] = []
    wait_skill_type = _original_wait_skill_type()
    for policy in policies:
        high_level = policy._high_level_policy
        original = high_level.llm_agent
        if original.name == assigned_agent_name:
            continue
        wait_index = getattr(policy, "_name_to_idx", {}).get("wait")
        high_level_wait_index = getattr(high_level, "_skill_name_to_idx", {}).get("wait")
        wait_skill = getattr(policy, "_skills", {}).get(wait_index)
        if (
            not isinstance(wait_index, int)
            or high_level_wait_index != wait_index
            or type(wait_skill) is not wait_skill_type
        ):
            raise IntegrationError("unassigned EMOS endpoint lacks its original wait skill")
        pending.append((high_level, original, PassiveIdleAgent(original.name)))

    binding = PassiveIdleBinding(assigned_agent_name, tuple(pending))
    binding.activate()
    return binding


def prepare_completed_idle_agent(actor: Any, agent_name: str) -> PassiveIdleBinding:
    """Validate a future post-place idle transition before executing relocation.

    Only an observed original place completion may activate the returned binding.
    The passive policy then selects the original wait skill, preventing further
    model-directed actions while peers continue. It does not prove pose or goal
    stability, and no model-selected action is edited.
    """
    policies = [
        policy
        for policy in actor._active_policies
        if getattr(policy._high_level_policy.llm_agent, "name", None) == agent_name
    ]
    if len(policies) != 1:
        raise IntegrationError("completed endpoint does not have one exact Stage2 policy")
    policy = policies[0]
    high_level = policy._high_level_policy
    original = high_level.llm_agent
    wait_index = getattr(policy, "_name_to_idx", {}).get("wait")
    if (
        isinstance(original, PassiveIdleAgent)
        or not isinstance(wait_index, int)
        or getattr(high_level, "_skill_name_to_idx", {}).get("wait") != wait_index
        or type(getattr(policy, "_skills", {}).get(wait_index)) is not _original_wait_skill_type()
    ):
        raise IntegrationError("completed EMOS endpoint lacks its original wait skill")
    passive = PassiveIdleAgent(agent_name)
    passive.init_agent("", "", "Nothing to do")
    return PassiveIdleBinding(
        agent_name,
        ((high_level, original, passive),),
        mode="passive-completed-endpoint",
    )
