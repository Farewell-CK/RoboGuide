"""Deterministic conversion tests for the environment-owned semantic adapter."""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.semantic_evidence import (  # noqa: E402
    SemanticEvidenceBuildError,
    build_authoritative_semantic_evidence,
)
from mission.semantic_evidence import AuthoritativeSemanticEvidence  # noqa: E402


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


def _environment(goal: object, tmp_path: Path) -> SimpleNamespace:
    """Build a fake loaded environment with neutral world identity sources."""
    problem = SimpleNamespace(
        goal=goal,
        get_ordered_entities_list=lambda: [SimpleNamespace(name="target-a")],
    )
    dataset_path = tmp_path / "eval" / "dataset-1.json.gz"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.write_bytes(gzip.compress(b'{"episodes": []}', mtime=0))
    return SimpleNamespace(
        task=SimpleNamespace(pddl_problem=problem),
        current_episode=SimpleNamespace(episode_id="51", scene_id="scene-51"),
        _dataset=SimpleNamespace(
            config=SimpleNamespace(
                data_path=str(tmp_path / "{split}" / "dataset-1.json.gz"), split="eval"
            ),
            content_scenes_path="{data_path}/content/{scene}.json.gz",
        ),
    )


def test_adapter_preserves_joint_goal_and_digest(tmp_path: Path) -> None:
    """Environment conjunctions become one neutral terminal objective."""
    document = build_authoritative_semantic_evidence(
        _environment(
            _Logical(
                "and",
                _Predicate("at", "target-a"),
                _Predicate("at", "target-b"),
            ),
            tmp_path,
        ),
        run_id="run-51",
        episode_id="51",
        agent_ids=(0, 1),
    )
    assert document["objective_scope"] == "joint_terminal_state"
    assert document["goal"]["operator"] == "and"
    assert len(document["goal"]["operands"]) == 2
    assert document["identity"]["episode_id"] == "51"
    assert document["schema_version"] == "roboguide.authoritative-semantic-evidence/v0.2"
    assert document["identity"]["dataset_revision"] == "dataset-1"
    assert (
        document["identity"]["dataset_sha256"]
        == hashlib.sha256((tmp_path / "eval/dataset-1.json.gz").read_bytes()).hexdigest()
    )
    assert AuthoritativeSemanticEvidence.from_json(document).to_json() == document
    assert document["digest"].startswith("sha256:")
    assert "physical_entity" not in json.dumps(document)
    assert "node_id" not in json.dumps(document)


def test_adapter_fails_closed_when_concrete_arguments_are_missing(tmp_path: Path) -> None:
    """An unresolved predicate cannot be weakened into a name-only objective."""
    unresolved = SimpleNamespace(name="at", _arg_values=None)
    with pytest.raises(SemanticEvidenceBuildError, match="arguments"):
        build_authoritative_semantic_evidence(
            _environment(_Logical("and", unresolved), tmp_path),
            run_id="run-51",
            episode_id="51",
            agent_ids=(0, 1),
        )


@pytest.mark.parametrize(
    "mutation",
    ["missing-config", "missing-file", "missing-layout", "bad-path", "scene-shards"],
)
def test_adapter_requires_actual_dataset_source(tmp_path: Path, mutation: str) -> None:
    """Unavailable or unsupported dataset sources never acquire invented identity."""
    environment = _environment(_Predicate("at", "target-a"), tmp_path)
    if mutation == "missing-config":
        environment._dataset.config = None
    elif mutation == "missing-file":
        (tmp_path / "eval/dataset-1.json.gz").unlink()
    elif mutation == "missing-layout":
        environment._dataset.content_scenes_path = None
    elif mutation == "bad-path":
        environment._dataset.config.data_path = "{unknown}/dataset.json.gz"
    else:
        (tmp_path / "eval/content").mkdir()
    with pytest.raises(SemanticEvidenceBuildError, match="dataset"):
        build_authoritative_semantic_evidence(
            environment, run_id="run-51", episode_id="51", agent_ids=(0, 1)
        )


@pytest.mark.parametrize(
    "field,value",
    [("episode_id", "52"), ("episode_id", None), ("scene_id", None), ("scene_id", " ")],
)
def test_adapter_requires_loaded_episode_identity(tmp_path: Path, field: str, value: Any) -> None:
    """Configured episode text cannot relabel a different or unidentified loaded episode."""
    environment = _environment(_Predicate("at", "target-a"), tmp_path)
    setattr(environment.current_episode, field, value)
    with pytest.raises(SemanticEvidenceBuildError, match="identity"):
        build_authoritative_semantic_evidence(
            environment, run_id="run-51", episode_id="51", agent_ids=(0, 1)
        )


def test_dataset_identity_tracks_raw_bytes_and_deployment_revision(tmp_path: Path) -> None:
    """Same scene/episode cannot hide different dataset bytes or a different file revision."""
    environment = _environment(_Predicate("at", "target-a"), tmp_path)
    first = build_authoritative_semantic_evidence(
        environment, run_id="run-51", episode_id="51", agent_ids=(0, 1)
    )
    source = tmp_path / "eval/dataset-1.json.gz"
    source.write_bytes(gzip.compress(b'{"episodes": [], "revision": 2}', mtime=0))
    changed = build_authoritative_semantic_evidence(
        environment, run_id="run-51", episode_id="51", agent_ids=(0, 1)
    )
    assert changed["identity"]["dataset_sha256"] != first["identity"]["dataset_sha256"]
    renamed = source.with_name("dataset-2.json.gz")
    source.rename(renamed)
    environment._dataset.config.data_path = str(renamed)
    revised = build_authoritative_semantic_evidence(
        environment, run_id="run-51", episode_id="51", agent_ids=(0, 1)
    )
    assert revised["identity"]["dataset_revision"] == "dataset-2"
    assert revised["identity"]["dataset_sha256"] == changed["identity"]["dataset_sha256"]
