"""Convert environment-owned task semantics into neutral MI evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class SemanticEvidenceBuildError(RuntimeError):
    """Report an authoritative semantic snapshot that could not be built completely."""


def build_authoritative_semantic_evidence(
    environment: Any,
    *,
    run_id: str,
    episode_id: str,
    agent_ids: tuple[int, ...],
    episode: Any | None = None,
) -> dict[str, Any]:
    """Serialize the loaded environment goal without introducing Core-specific fields."""
    if not run_id.strip() or not episode_id.strip():
        raise SemanticEvidenceBuildError("run_id and episode_id must be nonblank")
    task = getattr(environment, "task", None)
    problem = getattr(task, "pddl_problem", None)
    goal = getattr(problem, "goal", None)
    if problem is None or goal is None:
        raise SemanticEvidenceBuildError("authoritative environment goal is unavailable")
    encoded_goal = _expression(goal)
    entities = getattr(problem, "get_ordered_entities_list", None)
    if not callable(entities):
        raise SemanticEvidenceBuildError("authoritative environment entity context is unavailable")
    entity_names = sorted(_entity_name(entity) for entity in entities())
    episode_value = (
        episode if episode is not None else getattr(environment, "current_episode", None)
    )
    scene_id = getattr(episode_value, "scene_id", None)
    if not isinstance(scene_id, str) or not scene_id.strip():
        raise SemanticEvidenceBuildError("authoritative environment scene identity is unavailable")
    loaded_episode_id = getattr(episode_value, "episode_id", None)
    if loaded_episode_id is None or str(loaded_episode_id) != episode_id:
        raise SemanticEvidenceBuildError("loaded episode identity does not match requested episode")
    dataset_revision, dataset_sha256 = _dataset_identity(environment)
    goal_digest = _digest(encoded_goal)
    body: dict[str, Any] = {
        "schema_version": "roboguide.authoritative-semantic-evidence/v0.2",
        "authority": "environment-authoritative",
        "identity": {
            "run_id": run_id,
            "episode_id": episode_id,
            "revision": f"goal-{goal_digest.removeprefix('sha256:')}",
            "dataset_revision": dataset_revision,
            "dataset_sha256": dataset_sha256,
        },
        "objective_scope": "joint_terminal_state",
        "goal": encoded_goal,
        "world_context": {
            "scene_id": str(scene_id),
            "agent_ids": sorted(agent_ids),
            "entity_catalog": entity_names,
        },
    }
    return {**body, "digest": _digest(body)}


def _dataset_identity(environment: Any) -> tuple[str, str]:
    """Bind the loaded monolithic dataset to its deployment filename and raw bytes.

    Habitat resolves data_path against its process working directory and split.
    The current deployment's revision is the filename without .json.gz; its
    digest covers gzip bytes. Missing sources and scene shards fail closed.
    """
    dataset = getattr(environment, "_dataset", None)
    config = getattr(dataset, "config", None)
    data_path = getattr(config, "data_path", None)
    split = getattr(config, "split", None)
    if not isinstance(data_path, str) or not data_path.strip() or not isinstance(split, str):
        raise SemanticEvidenceBuildError("loaded dataset configuration is unavailable")
    try:
        path = Path(data_path.format(split=split))
        if not path.name.endswith(".json.gz") or path.name == ".json.gz":
            raise SemanticEvidenceBuildError("dataset revision requires a named .json.gz source")
        content_template = getattr(dataset, "content_scenes_path", None)
        if not isinstance(content_template, str) or "{scene}" not in content_template:
            raise SemanticEvidenceBuildError("dataset content source layout is unavailable")
        content_dir = Path(content_template.split("{scene}")[0].format(data_path=path.parent))
        if content_dir.exists():
            raise SemanticEvidenceBuildError("scene-sharded dataset identity is unsupported")
        hasher = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                hasher.update(chunk)
    except (OSError, ValueError, KeyError, IndexError) as error:
        raise SemanticEvidenceBuildError("loaded dataset identity is unavailable") from error
    return path.name.removesuffix(".json.gz"), hasher.hexdigest()


def _expression(value: Any) -> dict[str, Any]:
    """Recursively preserve logical nesting and concrete predicate arguments."""
    sub_exprs = getattr(value, "sub_exprs", None)
    if sub_exprs is not None:
        operator_value = getattr(getattr(value, "expr_type", None), "value", None)
        if not isinstance(operator_value, str) or not operator_value.strip():
            raise SemanticEvidenceBuildError("logical goal operator is unavailable")
        operands = tuple(sub_exprs)
        if not operands:
            raise SemanticEvidenceBuildError("logical goal cannot be empty")
        quantifier = getattr(value, "quantifier", None)
        quantifier_value = getattr(quantifier, "value", quantifier)
        inputs = getattr(value, "inputs", ()) or ()
        return {
            "kind": "logical",
            "operator": operator_value,
            "operands": [_expression(operand) for operand in operands],
            "quantifier": (str(quantifier_value) if quantifier_value is not None else None),
            "variables": [_entity_name(item) for item in inputs],
        }
    name = getattr(value, "name", None)
    arguments = getattr(value, "_arg_values", None)
    if not isinstance(name, str) or not name.strip() or not isinstance(arguments, (list, tuple)):
        raise SemanticEvidenceBuildError("concrete goal predicate arguments are unavailable")
    return {
        "kind": "predicate",
        "name": name,
        "arguments": [_entity_name(argument) for argument in arguments],
    }


def _entity_name(value: Any) -> str:
    """Extract one stable neutral entity label from an environment object."""
    name = getattr(value, "name", None)
    if not isinstance(name, str) or not name.strip():
        raise SemanticEvidenceBuildError("environment semantic entity has no stable name")
    return name


def _digest(value: Any) -> str:
    """Hash one finite canonical JSON value for revision and artifact identity."""
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise SemanticEvidenceBuildError("semantic evidence is not finite JSON") from error
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
