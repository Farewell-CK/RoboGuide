"""Freeze reset-state deployment eligibility before any Mission is submitted.

The Habitat child owns this read-only observation. It never chooses an Actor,
Node, Task, or path; Control may later intersect its negative feasibility facts
with current registration and scheduling evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from typing import Any

from .backend import initial_agent_positions
from .model import SUPPORTED_OPERATIONS, IntegrationError
from .spatial_feasibility import (
    FloorTransitionProfile,
    assess_destination_floor_compatibility,
)

PREASSIGNMENT_FEASIBILITY_SCHEMA = "roboguide.deployment-intent-feasibility/v0.2"


def canonical_digest_value(value: Any) -> Any:
    """Encode IEEE-754 values by exact bits for stable Python/Rust digests."""
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("deployment feasibility contains a non-finite float")
        return {"$f64_bits": struct.pack(">d", value).hex()}
    if isinstance(value, list):
        return [canonical_digest_value(item) for item in value]
    if isinstance(value, dict):
        return {key: canonical_digest_value(item) for key, item in value.items()}
    return value


def preassignment_digest(body: dict[str, Any]) -> str:
    """Hash one v0.2 evidence body with language-neutral float representation."""
    encoded = json.dumps(
        canonical_digest_value(body),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def build_preassignment_feasibility(
    habitat_env: Any,
    semantic_document: dict[str, Any],
    profiles: tuple[FloorTransitionProfile, ...],
    profile_digest: str,
    seed: int | None,
    agent_ids: tuple[int, ...],
) -> dict[str, Any]:
    """Read one reset world and record every configured Node/intent compatibility.

    Unknown geometry stays unknown and may be admitted by a later deployment
    policy; only an explicit registered false capability plus distinct known
    floors is an incompatibility. This function never resets, steps, or calls
    Stage2, and every position comes from the already reset environment.
    """
    identity = semantic_document.get("identity")
    world = semantic_document.get("world_context")
    if not isinstance(identity, dict) or not isinstance(world, dict):
        raise IntegrationError("semantic identity is unavailable for preassignment evidence")
    episode_id = identity.get("episode_id")
    scene_id = world.get("scene_id")
    if episode_id != str(habitat_env.current_episode.episode_id) or scene_id != str(
        habitat_env.current_episode.scene_id
    ):
        raise IntegrationError("preassignment reset differs from semantic evidence identity")
    catalog = world.get("entity_catalog")
    if (
        not isinstance(catalog, list)
        or not catalog
        or any(not isinstance(entity, str) or not entity.strip() for entity in catalog)
        or len(set(catalog)) != len(catalog)
    ):
        raise IntegrationError("semantic entity catalog is unavailable or ambiguous")
    if (
        len(profiles) != len(agent_ids)
        or {profile.agent_id for profile in profiles} != set(agent_ids)
        or len({profile.node_id for profile in profiles}) != len(profiles)
    ):
        raise IntegrationError("preassignment profiles do not cover distinct configured endpoints")
    if (
        not isinstance(profile_digest, str)
        or not profile_digest.startswith("sha256:")
        or len(profile_digest) != 71
        or any(character not in "0123456789abcdef" for character in profile_digest[7:])
    ):
        raise IntegrationError("preassignment spatial profile digest is invalid")
    starts = initial_agent_positions(habitat_env, agent_ids)
    if any(
        len(position) != 3 or not all(math.isfinite(value) for value in position)
        for position in starts.values()
    ):
        raise IntegrationError("preassignment reset positions are not finite three-vectors")
    records = [
        {
            "node_id": profile.node_id,
            **assess_destination_floor_compatibility(
                habitat_env, profile.agent_id, operation, destination, profile
            ),
        }
        for operation in sorted(SUPPORTED_OPERATIONS)
        for destination in sorted(catalog)
        for profile in sorted(profiles, key=lambda item: item.node_id)
    ]
    body: dict[str, Any] = {
        "schema_version": PREASSIGNMENT_FEASIBILITY_SCHEMA,
        "authority": "deployment-observed-reset-state",
        "identity": {
            "run_id": identity["run_id"],
            "episode_id": episode_id,
            "scene_id": scene_id,
            "dataset_revision": identity["dataset_revision"],
            "dataset_sha256": identity["dataset_sha256"],
            "semantic_evidence_digest": semantic_document["digest"],
            "spatial_profile_digest": profile_digest,
            "habitat_seed": seed,
            "episode_reset_count": 1,
        },
        "initial_agent_positions": starts,
        "records": records,
    }
    return {**body, "digest": preassignment_digest(body)}
