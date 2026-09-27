"""Preflight one reset-state deployment policy before a B1 Mission is submitted."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
from pathlib import Path
from typing import Any

from roboguide_eval.b1_provenance import load_document
from roboguide_eval.b1_workload import extract_b1_workload

_SCHEMA = "roboguide.deployment-intent-feasibility/v0.2"


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
    """Calculate the v0.2 content identity without float formatting drift."""
    encoded = json.dumps(
        _canonical_digest_value(body),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


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
    return document


def main() -> None:
    """Check one run-local artifact without invoking Habitat or a Provider."""
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    preflight_deployment_feasibility(args.run)


if __name__ == "__main__":
    main()
