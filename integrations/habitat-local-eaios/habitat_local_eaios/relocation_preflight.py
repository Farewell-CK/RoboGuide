"""Cross-check fresh relocation reset evidence before the B1 runner starts MI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .model import IntegrationError
from .relocation_capability import RELOCATION_READINESS_PROFILE
from .relocation_deployment import load_relocation_profile, verify_relocation_profile_sources
from .relocation_start import MAX_ARTIFACT_BYTES, planning_object_sources, validate_relocation_start
from .semantic_evidence import _digest


def _read_object(path: Path) -> dict[str, Any]:
    """Read a bounded evidence object; missing, malformed and over-budget files fail closed."""
    try:
        with path.open("rb") as source:
            raw = source.read(MAX_ARTIFACT_BYTES + 1)
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise ValueError("artifact exceeds the byte budget")
        document = json.loads(raw)
        if not isinstance(document, dict):
            raise ValueError("artifact is not an object")
    except (OSError, ValueError) as error:
        raise IntegrationError(f"relocation preflight cannot read {path.name}") from error
    return document


def preflight_relocation(run: Path) -> dict[str, Any]:
    """Bind reset sources and actual readiness to the caller-validated frozen B1 workload.

    The runner first uses the authoritative B1 input parser. This local
    check consumes its identity fields without changing that input contract
    or Formal admission. It never loads Habitat, calls a model, mutates the
    original evidence or infers that manipulation will succeed.
    """
    frozen = _read_object(run / "b1-input-used.json")
    profile_path = run / "relocation-registration-profile.json"
    verify_relocation_profile_sources(profile_path)
    profile = load_relocation_profile(profile_path)
    evidence = run / "evidence"
    semantic = _read_object(evidence / "authoritative-semantic-evidence.json")
    start = _read_object(evidence / "relocation-episode-start.json")
    planning = _read_object(evidence / "authoritative-planning-world-evidence.json")
    readiness = _read_object(evidence / "relocation-readiness.json")
    used_profile = _read_object(evidence / "relocation-registration-profile-used.json")
    for document in (semantic, planning):
        if document.get("digest") != _digest(
            {key: value for key, value in document.items() if key != "digest"}
        ):
            raise IntegrationError("relocation semantic or planning evidence digest is invalid")
    try:
        expected = {
            "run_id": run.name,
            "episode_id": frozen["episode_id"],
            "scene_id": frozen["scene_id"],
            "dataset_revision": frozen["dataset_revision"],
            "dataset_sha256": frozen["dataset_sha256"],
            "habitat_seed": frozen["seed"],
        }
        validate_relocation_start(start, semantic)
        planning_identity = {
            key: planning["identity"][key] for key in expected if key != "habitat_seed"
        }
    except (KeyError, TypeError, ValueError) as error:
        raise IntegrationError("relocation reset identity or structure is invalid") from error
    if (
        start["identity"] != expected
        or type(start["reset_count"]) is not int
        or type(start["simulator_steps"]) is not int
        or planning_identity
        != {key: value for key, value in expected.items() if key != "habitat_seed"}
        or start["registration_profile_digest"] != profile["digest"]
        or used_profile != profile
        or [agent["agent_id"] for agent in start["agents"]]
        != [agent["agent_id"] for agent in profile["agents"]]
    ):
        raise IntegrationError("relocation reset differs from frozen workload or registration")
    if (
        planning.get("schema_version") != "roboguide.authoritative-planning-world-evidence/v0.3"
        or planning.get("authority") != "environment-authoritative"
        or planning.get("object_sources") != planning_object_sources(start, semantic)
    ):
        raise IntegrationError("MI planning sources differ from the actual relocation reset")
    if (
        readiness.get("schema_version") != RELOCATION_READINESS_PROFILE
        or readiness.get("ready") is not True
        or readiness.get("policy_count") != len(profile["agents"])
        or type(readiness.get("policy_count")) is not int
        or readiness.get("missing_tools") != []
        or readiness.get("missing_skills") != []
    ):
        raise IntegrationError("actual relocation skill readiness is unavailable")
    return {
        "schema_version": "roboguide.habitat-relocation-preflight/v0.1",
        "valid": True,
        "identity": expected,
        "registration_profile_digest": profile["digest"],
        "start_digest": start["digest"],
        "semantic_evidence_digest": semantic["digest"],
        "planning_evidence_digest": planning["digest"],
        "reset_count": start["reset_count"],
        "simulator_steps": start["simulator_steps"],
    }


def main() -> None:
    """Preserve pass/failure evidence and return a failing status before any Mission submission."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = preflight_relocation(args.run)
    except (IntegrationError, KeyError, TypeError, ValueError) as error:
        result = {
            "schema_version": "roboguide.habitat-relocation-preflight/v0.1",
            "valid": False,
            "reason": str(error),
        }
        (args.run / "relocation-preflight.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        parser.exit(1, "relocation reset preflight failed; see relocation-preflight.json\n")
    (args.run / "relocation-preflight.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
