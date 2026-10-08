"""Deployment-owned projection of Habitat's official goal metric into verifier evidence.

The source advertises only predicates that can be rendered exactly from the
loaded authoritative goal. A positive verdict is never inferred from a local
skill completion or a diagnostic pose observation.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Sequence
from typing import Any

from .model import CanonicalInvocation, IntegrationError

SOURCE_SCHEMA = "roboguide.task-verifier-source/v0.1"
VERDICT_SCHEMA = "roboguide.task-verifier-verdict/v0.1"
_VERIFIER = {"namespace": "observation", "name": "verify", "version": "v1"}
_ATOM = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*\Z")
_ENTITY = re.compile(r"[A-Za-z0-9_.|/-]+\Z")


def canonical_goal_predicate(value: object) -> str | None:
    """Render supported exact goal syntax, returning unavailable for other PDDL forms."""
    if not isinstance(value, dict):
        return None
    if value.get("kind") == "predicate" and set(value) == {"kind", "name", "arguments"}:
        name, arguments = value["name"], value["arguments"]
        if (
            not isinstance(name, str)
            or _ATOM.fullmatch(name) is None
            or not isinstance(arguments, list)
            or not arguments
            or not all(
                isinstance(item, str) and _ENTITY.fullmatch(item) is not None for item in arguments
            )
        ):
            return None
        return f"{name}({', '.join(arguments)})"
    if value.get("kind") != "logical" or set(value) != {
        "kind",
        "operator",
        "operands",
        "quantifier",
        "variables",
    }:
        return None
    if value["quantifier"] is not None or value["variables"] != []:
        return None
    operator, operands = value["operator"], value["operands"]
    if operator not in {"and", "or"} or not isinstance(operands, list) or len(operands) < 2:
        return None
    rendered = [canonical_goal_predicate(item) for item in operands]
    if any(item is None for item in rendered):
        return None
    supported = [item for item in rendered if item is not None]
    return f" {operator.upper()} ".join(
        f"({item})" if " AND " in item or " OR " in item else item for item in supported
    )


def build_task_verifier_source(semantic: object) -> dict[str, Any]:
    """Freeze a generic verifier profile against the exact reset-world goal digest."""
    if not isinstance(semantic, dict):
        raise IntegrationError("authoritative semantic evidence must be an object")
    if semantic.get("schema_version") != "roboguide.authoritative-semantic-evidence/v0.2":
        raise IntegrationError("unsupported authoritative semantic evidence schema")
    unsigned = {key: item for key, item in semantic.items() if key != "digest"}
    if semantic.get("digest") != _digest(unsigned):
        raise IntegrationError("authoritative semantic evidence digest mismatch")
    identity, world = semantic.get("identity"), semantic.get("world_context")
    if not isinstance(identity, dict) or not isinstance(world, dict):
        raise IntegrationError("authoritative semantic identity is unavailable")
    required_identity = {
        "run_id": identity.get("run_id"),
        "episode_id": identity.get("episode_id"),
        "scene_id": world.get("scene_id"),
        "dataset_revision": identity.get("dataset_revision"),
        "dataset_sha256": identity.get("dataset_sha256"),
    }
    if not all(isinstance(item, str) and item.strip() for item in required_identity.values()):
        raise IntegrationError("authoritative semantic identity is incomplete")
    predicate = canonical_goal_predicate(semantic.get("goal"))
    body: dict[str, Any] = {
        "schema_version": SOURCE_SCHEMA,
        "source_id": "habitat-official-pddl",
        "source_revision": semantic["digest"],
        "identity": required_identity,
        "verifier": _VERIFIER,
        "supported_predicates": [predicate] if predicate is not None else [],
        "verdict_finality": "terminal",
    }
    return {**body, "digest": _digest(body)}


def build_task_verifier_verdict(
    source: dict[str, Any],
    official_metrics: object,
    invocations: Sequence[CanonicalInvocation],
    *,
    source_observed_at_ms: int | None = None,
) -> dict[str, Any] | None:
    """Bind a strict official bool to completed physical attempts in this world."""
    if not isinstance(official_metrics, dict):
        return None
    pddl_success = official_metrics.get("pddl_success")
    predicates = source.get("supported_predicates")
    if (
        not isinstance(pddl_success, bool)
        or not isinstance(predicates, list)
        or len(predicates) != 1
    ):
        return None
    if not invocations:
        return None
    mission_ids = {invocation.mission_id for invocation in invocations}
    if len(mission_ids) != 1:
        raise IntegrationError("verifier source cannot join unrelated Missions")
    tasks: dict[str, list[dict[str, str]]] = {}
    for invocation in invocations:
        attempt_id = invocation.attempt_id
        if attempt_id is None:
            return None
        tasks.setdefault(invocation.task_id, []).append(
            {"role_id": invocation.role_id, "attempt_id": attempt_id}
        )
    rows: list[dict[str, object]] = []
    for task_id in sorted(tasks):
        task_attempts = tasks[task_id]
        if len({item["role_id"] for item in task_attempts}) != len(task_attempts):
            raise IntegrationError("verifier verdict has duplicate Task Role attempts")
        rows.append(
            {
                "mission_id": invocations[0].mission_id,
                "task_id": task_id,
                "attempts": sorted(task_attempts, key=lambda item: item["role_id"]),
            }
        )
    body: dict[str, Any] = {
        "schema_version": VERDICT_SCHEMA,
        "source_digest": source["digest"],
        "source_id": source["source_id"],
        "verifier": source["verifier"],
        "predicate": predicates[0],
        "source_observed_at_ms": (
            source_observed_at_ms
            if source_observed_at_ms is not None
            else time.time_ns() // 1_000_000
        ),
        "satisfied": pddl_success,
        "tasks": rows,
    }
    return {**body, "digest": _digest(body)}


def _digest(value: object) -> str:
    """Hash bounded JSON using the cross-language canonical encoding."""
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise IntegrationError("verifier evidence is not finite JSON") from error
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
