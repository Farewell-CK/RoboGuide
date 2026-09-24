"""Build neutral spatial planning facts from a loaded Habitat episode before reset."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .semantic_evidence import _dataset_identity, _expression

_SCHEMA = "roboguide.authoritative-planning-world-evidence/v0.1"
_SOURCE_REVISION = "habitat-local-eaios-episode-static-scene/v0.1"


class PlanningWorldEvidenceBuildError(RuntimeError):
    """Report an identity failure while creating pre-reset world evidence."""


def build_authoritative_planning_world_evidence(
    environment: Any,
    *,
    run_id: str,
    episode_id: str,
    episode: Any | None = None,
) -> dict[str, Any]:
    """Read the selected episode and static scene without reset or simulator mutation.

    Habitat binds PDDL ``sim_info`` and instantiates episode objects during reset.
    Neither is a valid source before MI freezes its input. Only the selected
    dataset episode and already loaded scene regions are read here; unresolved
    object and agent locations remain explicit gaps.
    """
    if not run_id.strip() or not episode_id.strip():
        raise PlanningWorldEvidenceBuildError("run_id and episode_id must be nonblank")
    selected = episode if episode is not None else getattr(environment, "current_episode", None)
    scene_id = getattr(selected, "scene_id", None)
    loaded_episode_id = getattr(selected, "episode_id", None)
    if not isinstance(scene_id, str) or not scene_id.strip():
        raise PlanningWorldEvidenceBuildError("authoritative scene identity is unavailable")
    if loaded_episode_id is None or str(loaded_episode_id) != episode_id:
        raise PlanningWorldEvidenceBuildError(
            "loaded episode identity does not match requested episode"
        )
    dataset_revision, dataset_sha256 = _dataset_identity(environment)
    facts: list[dict[str, str]] = []
    gaps: list[dict[str, str]] = [
        {
            "code": "agent_start_state_pending_reset",
            "detail": "agent start state is sampled by reset and is unavailable during planning",
        }
    ]
    regions = _regions(getattr(environment, "sim", None), gaps)
    relations: list[dict[str, str]] = []
    if regions:
        _append_object_facts(selected, regions, facts, gaps)
        _append_target_facts(environment, selected, regions, facts, gaps)
    _append_floor_relation(selected, relations, gaps)
    gaps = list({(item["code"], item["detail"]): item for item in gaps}.values())
    body: dict[str, Any] = {
        "schema_version": _SCHEMA,
        "authority": "environment-authoritative",
        "identity": {
            "run_id": run_id,
            "episode_id": episode_id,
            "scene_id": scene_id,
            "dataset_revision": dataset_revision,
            "dataset_sha256": dataset_sha256,
            "source_revision": _SOURCE_REVISION,
        },
        "facts": sorted(facts, key=lambda item: item["entity_id"]),
        "relations": sorted(
            relations,
            key=lambda item: (
                item["subject_entity_id"],
                item["relation"],
                item["object_entity_id"],
            ),
        ),
        "gaps": sorted(gaps, key=lambda item: (item["code"], item["detail"])),
    }
    return {**body, "digest": _digest(body)}


def _regions(sim: Any, gaps: list[dict[str, str]]) -> list[Any]:
    """Return loaded semantic regions or record their unavailability."""
    regions = getattr(getattr(sim, "semantic_scene", None), "regions", None)
    if regions is None:
        gaps.append(
            {
                "code": "semantic_regions_unavailable",
                "detail": "the loaded scene exposes no semantic regions before reset",
            }
        )
        return []
    try:
        result = list(regions)
    except TypeError:
        result = []
    if not result:
        gaps.append(
            {
                "code": "semantic_regions_unavailable",
                "detail": "the loaded scene has no usable semantic regions",
            }
        )
    return result


def _episode_labels(episode: Any, gaps: list[dict[str, str]]) -> Mapping[str, str]:
    """Read exact instance-handle labels from the selected dataset episode."""
    info = getattr(episode, "info", None)
    labels = info.get("object_labels") if isinstance(info, Mapping) else None
    if (
        not isinstance(labels, Mapping)
        or not labels
        or not all(
            isinstance(handle, str) and isinstance(label, str) and label.strip()
            for handle, label in labels.items()
        )
        or len(set(labels.values())) != len(labels)
    ):
        gaps.append(
            {
                "code": "episode_object_labels_unavailable",
                "detail": "the selected episode has no complete object-label mapping",
            }
        )
        return {}
    return labels


def _append_object_facts(
    episode: Any,
    regions: list[Any],
    facts: list[dict[str, str]],
    gaps: list[dict[str, str]],
) -> None:
    """Use only exact episode handle-to-transform identities for object floors."""
    labels = _episode_labels(episode, gaps)
    rigid_objects = getattr(episode, "rigid_objs", None)
    if not isinstance(rigid_objects, Sequence) or isinstance(rigid_objects, (str, bytes)):
        gaps.append(
            {
                "code": "object_spatial_facts_unavailable",
                "detail": "the selected episode has no rigid-object transforms",
            }
        )
        return
    transforms: dict[str, Any] = {}
    for entry in rigid_objects:
        if isinstance(entry, Sequence) and not isinstance(entry, (str, bytes)) and len(entry) == 2:
            handle = entry[0]
            if isinstance(handle, str) and handle not in transforms:
                transforms[handle] = entry[1]
    for handle, entity_id in sorted(labels.items()):
        if handle not in transforms:
            gaps.append(
                {
                    "code": "object_spatial_fact_unavailable",
                    "detail": f"{entity_id}: no exact pre-reset instance transform",
                }
            )
            continue
        _append_location_fact(entity_id, transforms[handle], regions, facts, gaps, "object")


def _append_target_facts(
    environment: Any,
    episode: Any,
    regions: list[Any],
    facts: list[dict[str, str]],
    gaps: list[dict[str, str]],
) -> None:
    """Map exact episode target handles to names present in the official objective."""
    labels = _episode_labels(episode, gaps)
    targets = getattr(episode, "targets", None)
    goal = getattr(getattr(getattr(environment, "task", None), "pddl_problem", None), "goal", None)
    if (
        not isinstance(targets, Mapping)
        or not all(isinstance(handle, str) for handle in targets)
        or goal is None
    ):
        gaps.append(
            {
                "code": "goal_spatial_facts_unavailable",
                "detail": "episode target transforms or the official objective are unavailable",
            }
        )
        return
    objective_names = set(_goal_arguments(_expression(goal)))
    for handle, transform in sorted(targets.items()):
        label = labels.get(handle)
        if label is None or f"TARGET_{label}" not in objective_names:
            gaps.append(
                {
                    "code": "goal_entity_mapping_incomplete",
                    "detail": "episode target handle has no exact objective entity mapping",
                }
            )
            continue
        _append_location_fact(f"TARGET_{label}", transform, regions, facts, gaps, "goal")


def _append_floor_relation(
    episode: Any,
    relations: list[dict[str, str]],
    gaps: list[dict[str, str]],
) -> None:
    """Translate one exact dataset floor relation without guessing its endpoints."""
    info = getattr(episode, "info", None)
    if not isinstance(info, Mapping) or "same_floor" not in info:
        gaps.append(
            {
                "code": "floor_relation_unavailable",
                "detail": "selected episode does not expose an exact object-target floor relation",
            }
        )
        return
    same_floor = info["same_floor"]
    labels = _episode_labels(episode, gaps)
    targets = getattr(episode, "targets", None)
    if not isinstance(same_floor, bool) or len(labels) != 1 or not isinstance(targets, Mapping):
        gaps.append(
            {
                "code": "floor_relation_unresolved",
                "detail": "dataset floor relation is not a single exact object-target fact",
            }
        )
        return
    label = next(iter(labels.values()))
    target_handles = [
        handle
        for handle in targets
        if isinstance(handle, str) and handle in labels and labels[handle] == label
    ]
    if len(target_handles) != 1:
        gaps.append(
            {
                "code": "floor_relation_unresolved",
                "detail": "dataset floor relation has no unique labelled target endpoint",
            }
        )
        return
    relations.append(
        {
            "subject_entity_id": label,
            "relation": "same_floor" if same_floor else "different_floor",
            "object_entity_id": f"TARGET_{label}",
        }
    )


def _append_location_fact(
    entity_id: str,
    transform: Any,
    regions: list[Any],
    facts: list[dict[str, str]],
    gaps: list[dict[str, str]],
    kind: str,
) -> None:
    """Record one uniquely located entity or a bounded unavailable fact."""
    try:
        position = _matrix_position(transform)
        location = _region_location(position, regions)
    except (TypeError, ValueError, OverflowError, IndexError, KeyError):
        location = None
    if location is None:
        gaps.append(
            {
                "code": f"{kind}_region_unresolved",
                "detail": f"no unique pre-reset semantic region for {entity_id}",
            }
        )
        return
    region_id, floor_id = location
    facts.append({"entity_id": entity_id, "region_id": region_id, "floor_id": floor_id})


def _goal_arguments(expression: dict[str, Any]) -> Iterable[str]:
    """Walk the neutral official goal tree to identify exact target names."""
    if expression.get("kind") == "predicate":
        return tuple(item for item in expression.get("arguments", ()) if isinstance(item, str))
    arguments: list[str] = []
    for operand in expression.get("operands", ()):
        if isinstance(operand, dict):
            arguments.extend(_goal_arguments(operand))
    return tuple(arguments)


def _matrix_position(transform: Any) -> list[float]:
    """Read a finite affine 4x4 episode transform without simulator calls."""
    values = transform.tolist() if callable(getattr(transform, "tolist", None)) else transform
    if not isinstance(values, Sequence) or len(values) != 4:
        raise ValueError("episode transform is not a 4x4 matrix")
    rows: list[list[float]] = []
    for row in values:
        if not isinstance(row, Sequence) or len(row) != 4:
            raise ValueError("episode transform is not a 4x4 matrix")
        if any(isinstance(item, bool) for item in row):
            raise ValueError("episode transform must be numeric")
        converted = [float(item) for item in row]
        if not all(math.isfinite(item) for item in converted):
            raise ValueError("episode transform must be finite")
        rows.append(converted)
    if any(abs(rows[3][index]) > 1e-6 for index in range(3)) or abs(rows[3][3] - 1) > 1e-6:
        raise ValueError("episode transform must be affine")
    return [rows[index][3] for index in range(3)]


def _vector(value: Any) -> list[float]:
    """Convert an array-like three-vector to finite coordinates."""
    values = value.tolist() if callable(getattr(value, "tolist", None)) else value
    if all(hasattr(values, component) for component in ("x", "y", "z")):
        values = [getattr(values, component) for component in ("x", "y", "z")]
    if not isinstance(values, Sequence) or len(values) != 3:
        raise ValueError("position vector is unavailable")
    if any(isinstance(item, bool) for item in values):
        raise ValueError("position vector must be numeric")
    result = [float(item) for item in values]
    if not all(math.isfinite(item) for item in result):
        raise ValueError("position vector must be finite")
    return result


def _region_location(position: list[float], regions: list[Any]) -> tuple[str, str] | None:
    """Return a unique opaque region/floor pair containing a static point."""
    matches: list[tuple[str, str]] = []
    for region in regions:
        aabb = getattr(region, "aabb", None)
        center = getattr(aabb, "center", None)
        sizes = getattr(aabb, "sizes", None)
        region_id = getattr(region, "id", None)
        floor_id = getattr(getattr(region, "level", None), "id", None)
        if center is None or sizes is None or region_id is None or floor_id is None:
            continue
        try:
            center_values = _vector(center)
            size_values = _vector(sizes)
        except (TypeError, ValueError):
            continue
        lower = [center_values[index] - size_values[index] / 2.0 for index in range(3)]
        upper = [center_values[index] + size_values[index] / 2.0 for index in range(3)]
        if all(lower[index] <= position[index] <= upper[index] for index in range(3)):
            matches.append((str(region_id), str(floor_id)))
    unique = sorted(set(matches))
    return unique[0] if len(unique) == 1 else None


def _digest(value: dict[str, Any]) -> str:
    """Return the canonical digest for one finite evidence body."""
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
