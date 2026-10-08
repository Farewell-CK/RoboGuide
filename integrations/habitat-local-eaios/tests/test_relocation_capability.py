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


def _policy(*, actions: list[str], skills: list[str]) -> SimpleNamespace:
    """Build a vendor-shaped policy declaration without importing EMOS."""
    agent = SimpleNamespace(
        actions=[SimpleNamespace(name=name) for name in actions],
    )
    high_level = SimpleNamespace(llm_agent=agent)
    return SimpleNamespace(
        _high_level_policy=high_level,
        _idx_to_name=list(skills),
        _skills={name: object() for name in skills},
    )


def test_relocation_readiness_requires_real_tools_and_skills() -> None:
    """Readiness is true only when every loaded policy exposes the required path."""
    evidence = inspect_relocation_capability(
        [
            _policy(
                actions=["nav_to_obj", "pick", "place", "wait"],
                skills=["nav_to_obj", "pick", "place"],
            )
        ]
    )
    assert evidence.ready
    assert evidence.missing_tools == ()
    assert evidence.missing_skills == ()


def test_relocation_readiness_fails_closed_for_missing_declarations() -> None:
    """Missing action or skill declarations never become an inferred capability."""
    evidence = inspect_relocation_capability(
        [_policy(actions=["nav_to_obj", "pick"], skills=["nav_to_obj", "pick"])]
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
