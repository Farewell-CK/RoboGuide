"""Deterministic admission and lifecycle tests for unassigned EMOS endpoints."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios import idle_endpoint  # noqa: E402
from habitat_local_eaios.idle_endpoint import (  # noqa: E402
    PassiveIdleAgent,
    install_passive_idle_agents,
)
from habitat_local_eaios.model import IntegrationError  # noqa: E402


class WaitSkillPolicy:
    """Stand in for the original EMOS wait skill, not a new simulator action."""


class WrongSkillPolicy:
    """Stand in for an incompatible mapping under the same skill name."""


@pytest.fixture(autouse=True)
def installed_wait_skill(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve the vendor wait class to the deterministic test double."""
    monkeypatch.setattr(idle_endpoint, "_original_wait_skill_type", lambda: WaitSkillPolicy)


class FakeAgent:
    """Count all original agent calls that the passive binding must avoid."""

    def __init__(self, name: str) -> None:
        """Create a named original agent with no completed Provider exchange."""
        self.name = name
        self.calls = 0

    def init_agent(self, **kwargs: Any) -> None:
        """Flag any unexpected initialization of an unassigned original agent."""
        del kwargs
        self.calls += 1

    def chat(self, observation: str) -> dict[str, object]:
        """Flag any unexpected model action from an unassigned original agent."""
        del observation
        self.calls += 1
        return {"name": "nav_to_obj", "arguments": {"target_obj": "wrong"}}


def _actor() -> tuple[SimpleNamespace, list[FakeAgent]]:
    """Build two vendor-shaped policies with the existing wait skill mapped."""
    agents = [FakeAgent("agent_0"), FakeAgent("agent_1")]
    policies = [
        SimpleNamespace(
            _name_to_idx={"wait": 2},
            _skills={2: WaitSkillPolicy()},
            _high_level_policy=SimpleNamespace(
                llm_agent=agent,
                _skill_name_to_idx={"wait": 2},
            ),
        )
        for agent in agents
    ]
    return SimpleNamespace(_active_policies=policies), agents


def test_only_unassigned_endpoint_uses_original_wait_skill_without_model() -> None:
    """A Control assignment selects the sole model owner; its sibling stays passive."""
    actor, originals = _actor()
    binding = install_passive_idle_agents(
        actor, {"agent_0": object(), "agent_1": object()}, "agent_0"
    )
    try:
        assert actor._active_policies[0]._high_level_policy.llm_agent is originals[0]
        passive = actor._active_policies[1]._high_level_policy.llm_agent
        assert isinstance(passive, PassiveIdleAgent)
        passive.init_agent("FetchRobot", "joint objective", "Nothing to do", [])
        assert passive.chat("scene observation") == {"name": "wait", "arguments": ["500"]}
        assert passive.get_token_usage() == 0
        assert originals[1].calls == 0
        assert binding.idle_names == frozenset({"agent_1"})
        assert binding.evidence()["idle_agents"] == [
            {
                "agent_name": "agent_1",
                "initializations": 1,
                "local_wait_selections": 1,
                "provider_calls": 0,
                "skill": "wait",
            }
        ]
    finally:
        binding.restore()
    assert [policy._high_level_policy.llm_agent for policy in actor._active_policies] == originals


@pytest.mark.parametrize(
    "bad_field", ["skill", "high-level-index", "missing-skill", "same-name-impostor"]
)
def test_unsupported_wait_mapping_fails_before_any_policy_swap(bad_field: str) -> None:
    """An EMOS skill-map change cannot silently turn idle into another action."""
    actor, originals = _actor()
    policy = actor._active_policies[1]
    if bad_field == "skill":
        policy._skills[2] = WrongSkillPolicy()
    elif bad_field == "high-level-index":
        policy._high_level_policy._skill_name_to_idx["wait"] = 3
    elif bad_field == "same-name-impostor":
        policy._skills[2] = type("WaitSkillPolicy", (), {})()
    else:
        policy._skills.clear()
    with pytest.raises(IntegrationError, match="original wait skill"):
        install_passive_idle_agents(actor, {"agent_0": object(), "agent_1": object()}, "agent_0")
    assert [item._high_level_policy.llm_agent for item in actor._active_policies] == originals


@pytest.mark.parametrize(
    ("assignment", "assigned"),
    [
        ({"agent_0": object()}, "agent_0"),
        ({"agent_0": object(), "agent_1": object()}, "agent_2"),
    ],
)
def test_incomplete_or_unknown_assignment_never_changes_agent_policy(
    assignment: dict[str, object], assigned: str
) -> None:
    """Only an exact Control-to-EMOS mapping can suppress an endpoint model."""
    actor, originals = _actor()
    with pytest.raises(IntegrationError, match="exact assignment"):
        install_passive_idle_agents(actor, assignment, assigned)
    assert [item._high_level_policy.llm_agent for item in actor._active_policies] == originals


@pytest.mark.parametrize(
    ("subtask", "history"),
    [("navigate", []), ("Nothing to do", ["prior active request"])],
)
def test_passive_agent_rejects_any_task_or_dialogue_assignment(
    subtask: str, history: list[str]
) -> None:
    """A future adapter cannot silently discard assigned work or dialogue."""
    agent = PassiveIdleAgent("agent_1")
    with pytest.raises(IntegrationError, match="assigned Stage2 subtask"):
        agent.init_agent("FetchRobot", "objective", subtask, history)
    with pytest.raises(IntegrationError, match="not initialized"):
        agent.chat("observation")


def test_restoration_allows_sequential_reuse_of_original_agent() -> None:
    """Each serial Task receives a fresh passive scope and preserves the real agent."""
    actor, originals = _actor()
    for _ in range(2):
        binding = install_passive_idle_agents(
            actor, {"agent_0": object(), "agent_1": object()}, "agent_0"
        )
        passive = actor._active_policies[1]._high_level_policy.llm_agent
        passive.init_agent("FetchRobot", "objective", "Nothing to do", [])
        assert passive.chat("observation")["name"] == "wait"
        binding.restore()
    assert [item._high_level_policy.llm_agent for item in actor._active_policies] == originals
    assert originals[1].calls == 0
