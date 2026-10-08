"""Deterministic checks for Stage2 relocation readiness evidence."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.relocation_capability import (  # noqa: E402
    inspect_relocation_capability,
)


class DeclaredSkill:
    """Expose the original skill observation surface without any physical execution."""

    def should_terminate(self) -> None:
        """Fail if a readiness probe tries to invoke a termination method."""
        raise AssertionError("readiness must not call the skill")

    def _is_skill_done(self) -> None:
        """Fail if a readiness probe tries to compute physical completion."""
        raise AssertionError("readiness must not read completion")


def _policy(*, actions: list[str], skills: list[str]) -> SimpleNamespace:
    """Build a vendor-shaped policy declaration without importing EMOS."""
    agent = SimpleNamespace(
        actions=[SimpleNamespace(name=name) for name in actions],
    )
    high_level = SimpleNamespace(llm_agent=agent)
    return SimpleNamespace(
        _high_level_policy=high_level,
        _idx_to_name=dict(enumerate(skills)),
        _skills={index: DeclaredSkill() for index in range(len(skills))},
    )


def test_relocation_readiness_requires_real_tools_and_skills() -> None:
    """Readiness is true only when every loaded policy exposes the required path."""
    evidence = inspect_relocation_capability(
        [
            _policy(
                actions=["nav_to_obj", "pick", "place", "wait"],
                skills=["nav_to_obj", "pick", "place", "wait"],
            )
        ]
    )
    assert evidence.ready
    assert evidence.missing_tools == ()
    assert evidence.missing_skills == ()


def test_relocation_readiness_fails_closed_for_missing_declarations() -> None:
    """Missing action or skill declarations never become an inferred capability."""
    evidence = inspect_relocation_capability(
        [_policy(actions=["nav_to_obj", "pick"], skills=["nav_to_obj", "pick", "wait"])]
    )
    assert not evidence.ready
    assert evidence.missing_tools == ("place",)
    assert evidence.missing_skills == ("place",)
    assert "missing tools=place" in evidence.detail
    assert "missing skills=place" in evidence.detail


@pytest.mark.parametrize("policies", [[], [SimpleNamespace(_high_level_policy=object())]])
def test_relocation_readiness_never_guesses_from_incomplete_policy_objects(
    policies: list[object],
) -> None:
    """Incomplete or empty policy input remains explicitly unavailable."""
    evidence = inspect_relocation_capability(policies)
    assert not evidence.ready
    assert evidence.policy_count == len(policies)


@pytest.mark.parametrize("container", ["mapping", "sequence", "name-keyed"])
def test_relocation_readiness_handles_vendor_skill_containers(container: str) -> None:
    """Resolve integer-indexed vendor tables and retain name-keyed test compatibility."""
    policy = _policy(
        actions=["nav_to_obj", "pick", "place"], skills=["nav_to_obj", "pick", "place", "wait"]
    )
    if container == "sequence":
        policy._idx_to_name = list(policy._idx_to_name.values())
    elif container == "name-keyed":
        policy._skills = {name: DeclaredSkill() for name in policy._idx_to_name.values()}
    assert inspect_relocation_capability([policy]).ready


@pytest.mark.parametrize(
    "missing", [None, object(), SimpleNamespace(should_terminate=lambda: None)]
)
def test_skill_name_without_observable_skill_instance_is_unavailable(missing: object) -> None:
    """A configured place label alone cannot advertise an executable relocation path."""
    policy = _policy(
        actions=["nav_to_obj", "pick", "place"], skills=["nav_to_obj", "pick", "place", "wait"]
    )
    policy._skills[2] = missing
    evidence = inspect_relocation_capability([policy])
    assert evidence.ready is False
    assert evidence.missing_tools == ()
    assert evidence.missing_skills == ("place",)


def test_relocation_without_wait_cannot_advertise_shared_completion() -> None:
    """A complete manipulation skill set still needs the existing passive wait lifecycle."""
    evidence = inspect_relocation_capability(
        [_policy(actions=["nav_to_obj", "pick", "place"], skills=["nav_to_obj", "pick", "place"])]
    )
    assert not evidence.ready
    assert evidence.missing_skills == ("wait",)
