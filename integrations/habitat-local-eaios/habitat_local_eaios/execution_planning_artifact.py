"""Publish digest-bound verifier and endpoint-lifetime facts for Mission Intelligence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .endpoint_registry import load_endpoint_registry
from .model import IntegrationError
from .task_verifier import build_task_verifier_source

SCHEMA = "roboguide.execution-planning-evidence/v0.1"


def _digest(value: object) -> str:
    """Hash bounded strict JSON with the same canonical encoding as Mission."""
    try:
        raw = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as error:
        raise IntegrationError("execution planning artifact is not finite JSON") from error
    if len(raw.encode()) > 65536:
        raise IntegrationError("execution planning artifact exceeds its byte budget")
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def build(semantic_path: Path, verifier_path: Path, registry_path: Path) -> dict[str, Any]:
    """Combine reset identity, official verifier support, and registry capacity only."""
    semantic = json.loads(semantic_path.read_bytes())
    verifier = json.loads(verifier_path.read_bytes())
    registry = load_endpoint_registry(registry_path)
    if not isinstance(verifier, dict) or verifier != build_task_verifier_source(semantic):
        raise IntegrationError("official verifier source is unavailable")
    if not isinstance(registry, dict) or not isinstance(registry.get("endpoints"), list):
        raise IntegrationError("endpoint registry is unavailable")
    identity = verifier.get("identity")
    if not isinstance(identity, dict):
        raise IntegrationError("verifier identity is unavailable")
    if verifier.get("source_revision") != semantic.get("digest"):
        raise IntegrationError("verifier and semantic source digests differ")
    body: dict[str, Any] = {
        "schema_version": SCHEMA,
        "authority": "deployment-published",
        "identity": {
            **{
                key: identity.get(key)
                for key in (
                    "run_id",
                    "episode_id",
                    "scene_id",
                    "dataset_revision",
                    "dataset_sha256",
                )
            },
            "semantic_evidence_digest": semantic.get("digest"),
        },
        "verifier_source": verifier,
        "execution_lifetime": {
            "declaration_digest": registry.get("digest"),
            "configured_endpoint_count": len(registry["endpoints"]),
            "endpoint_capacity": 1,
            "exclusive_resource_kind": "space",
            "max_slots": 32,
            "resource_release": "after-task-satisfaction",
            "verifier_publication": "after-all-session-slots",
        },
    }
    if any(value is None for value in body["identity"].values()):
        raise IntegrationError("execution planning identity is incomplete")
    return {**body, "digest": _digest(body)}


def main() -> None:
    """Write one run-local artifact without querying inventory or changing execution."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--semantic", type=Path, required=True)
    parser.add_argument("--verifier-source", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    document = build(args.semantic, args.verifier_source, args.registry)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    main()
