"""Preflight one reset-state deployment policy before a B1 Mission is submitted."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
from pathlib import Path
from typing import Any

from roboguide_eval.b1_provenance import (
    _semantic_expression_valid,
    _semantic_predicates,
    load_document,
)
from roboguide_eval.b1_workload import extract_b1_workload

_SCHEMA = "roboguide.deployment-intent-feasibility/v0.3"


def _canonical_digest_value(value: Any) -> Any:
    """Normalize floats to exact IEEE-754 bits before cross-language hashing."""
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("preassignment feasibility contains a non-finite float")
        return {"$f64_bits": struct.pack(">d", value).hex()}
    if isinstance(value, list):
        return [_canonical_digest_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _canonical_digest_value(item) for key, item in value.items()}
    return value


def _content_digest(body: dict[str, Any]) -> str:
    """Calculate the v0.3 content identity without float formatting drift."""
    encoded = json.dumps(
        _canonical_digest_value(body),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _goal_occupancy(semantic: dict[str, Any], destination: str) -> str:
    """Classify an exact destination from the frozen neutral goal expression."""
    goal = semantic.get("goal")
    if not _semantic_expression_valid(goal):
        raise ValueError("authoritative semantic goal is unavailable")
    matches = [
        predicate
        for predicate in _semantic_predicates(goal)
        if destination in predicate["arguments"]
    ]
    if not matches:
        return "none"
    if all(
        predicate["name"] == "any_at" and predicate["arguments"] == [destination]
        for predicate in matches
    ):
        return "any_at"
    return "other"


def preflight_deployment_feasibility(run: Path) -> dict[str, Any]:
    """Bind the child reset artifact to this run's frozen workload and sources.

    This check authenticates archive consistency, not the truth of a simulator
    observation. Control revalidates the decision shape and intersects it with
    current Node registration when accepting a Mission.
    """
    workload = extract_b1_workload(load_document(run / "b1-input-used.json"))
    document = load_document(run / "evidence/preassignment-feasibility.json")
    semantic = load_document(run / "evidence/authoritative-semantic-evidence.json")
    profile = load_document(run / "spatial-profile.json")
    if not all(isinstance(item, dict) for item in (document, semantic, profile)):
        raise ValueError("preassignment feasibility or an identity source is missing")
    assert isinstance(document, dict)
    assert isinstance(semantic, dict)
    assert isinstance(profile, dict)
    if (
        set(document)
        != {
            "schema_version",
            "authority",
            "identity",
            "initial_agent_positions",
            "records",
            "digest",
        }
        or document["schema_version"] != _SCHEMA
    ):
        raise ValueError("preassignment feasibility schema is invalid")
    claimed = document["digest"]
    body = {key: value for key, value in document.items() if key != "digest"}
    actual = _content_digest(body)
    if claimed != actual:
        raise ValueError("preassignment feasibility digest does not match content")
    identity = document["identity"]
    if not isinstance(identity, dict) or set(identity) != {
        "run_id",
        "episode_id",
        "scene_id",
        "dataset_revision",
        "dataset_sha256",
        "semantic_evidence_digest",
        "spatial_profile_digest",
        "habitat_seed",
        "episode_reset_count",
    }:
        raise ValueError("preassignment feasibility identity is invalid")
    expected = {
        "run_id": run.name,
        "episode_id": workload.episode_id,
        "scene_id": workload.scene_id,
        "dataset_revision": workload.dataset_revision,
        "dataset_sha256": workload.dataset_sha256,
        "semantic_evidence_digest": semantic.get("digest"),
        "spatial_profile_digest": profile.get("digest"),
        "habitat_seed": workload.seed,
        "episode_reset_count": 1,
    }
    if identity != expected:
        raise ValueError("preassignment feasibility differs from frozen B1 identity")
    if not isinstance(document["records"], list) or not document["records"]:
        raise ValueError("preassignment feasibility has no candidate records")
    for record in document["records"]:
        if not isinstance(record, dict):
            raise ValueError("preassignment feasibility record is invalid")
        destination = record.get("destination")
        claimed = record.get("goal_occupancy")
        if (
            not isinstance(destination, str)
            or not destination.strip()
            or claimed
            not in {
                "none",
                "any_at",
                "other",
                "unavailable",
            }
        ):
            raise ValueError("preassignment feasibility goal occupancy is invalid")
        expected_goal = _goal_occupancy(semantic, destination)
        if claimed not in {expected_goal, "unavailable"}:
            raise ValueError("preassignment feasibility goal differs from semantic evidence")
        if expected_goal == "any_at" and record.get("status") == "incompatible":
            raise ValueError("preassignment feasibility excludes an official distance goal")
    return document


def main() -> None:
    """Check one run-local artifact without invoking Habitat or a Provider."""
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    preflight_deployment_feasibility(args.run)


if __name__ == "__main__":
    main()
