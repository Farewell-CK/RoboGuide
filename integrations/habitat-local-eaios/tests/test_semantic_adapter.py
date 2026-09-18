"""Deterministic conversion tests for the environment-owned semantic adapter."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.semantic_evidence import (  # noqa: E402
    SemanticEvidenceBuildError,
    build_authoritative_semantic_evidence,
)


class _Predicate:
    """Small PDDL-shaped test object exposing only adapter-consumed attributes."""

    def __init__(self, name: str, *arguments: str) -> None:
        """Retain one concrete predicate and its resolved arguments."""
        self.name = name
        self._arg_values = [SimpleNamespace(name=argument) for argument in arguments]


class _Logical:
    """Small logical-expression test object preserving a conjunction tree."""

    def __init__(self, operator: str, *operands: object) -> None:
        """Retain one operator and ordered child expressions."""
        self.expr_type = SimpleNamespace(value=operator)
        self.sub_exprs = list(operands)
        self.quantifier = None
        self.inputs = ()


def _environment(goal: object) -> object:
    """Build a fake loaded environment with neutral world identity sources."""
    problem = SimpleNamespace(
        goal=goal,
        get_ordered_entities_list=lambda: [SimpleNamespace(name="target-a")],
    )
    return SimpleNamespace(
        task=SimpleNamespace(pddl_problem=problem),
        current_episode=SimpleNamespace(scene_id="scene-51"),
    )


def test_adapter_preserves_joint_goal_and_digest() -> None:
    """Environment conjunctions become one neutral terminal objective."""
    document = build_authoritative_semantic_evidence(
        _environment(
            _Logical(
                "and",
                _Predicate("at", "target-a"),
                _Predicate("at", "target-b"),
            )
        ),
        run_id="run-51",
        episode_id="51",
        agent_ids=(0, 1),
    )
    assert document["objective_scope"] == "joint_terminal_state"
    assert document["goal"]["operator"] == "and"
    assert len(document["goal"]["operands"]) == 2
    assert document["identity"]["episode_id"] == "51"
    assert document["digest"].startswith("sha256:")
    assert "physical_entity" not in json.dumps(document)
    assert "node_id" not in json.dumps(document)


def test_adapter_fails_closed_when_concrete_arguments_are_missing() -> None:
    """An unresolved predicate cannot be weakened into a name-only objective."""
    unresolved = SimpleNamespace(name="at", _arg_values=None)
    with pytest.raises(SemanticEvidenceBuildError, match="arguments"):
        build_authoritative_semantic_evidence(
            _environment(_Logical("and", unresolved)),
            run_id="run-51",
            episode_id="51",
            agent_ids=(0, 1),
        )
