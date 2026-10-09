"""Inspect a cross-robot takeover request; this read-only gate grants no authority."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def assess_takeover(
    registry: dict[str, Any], invocation: dict[str, Any],
    source_entity: str, replacement_entity: str,
) -> dict[str, Any]:
    """Reject absent, aliased or executor-pinned replacements before any recovery mutation."""
    reasons = []
    entities = registry.get("entities", [])
    indexed = {item["entity_id"]: item for item in entities}
    if len(indexed) != len(entities):
        reasons.append("duplicate_entity_identity")
    source = indexed.get(source_entity)
    replacement = indexed.get(replacement_entity)
    if source is None:
        reasons.append("source_entity_not_registered")
    if replacement is None:
        reasons.append("replacement_entity_not_registered")
    if source_entity == replacement_entity:
        reasons.append("same_entity_is_not_cross_robot_takeover")
    if source and replacement and source.get("node_id") == replacement.get("node_id"):
        reasons.append("distinct_entities_share_node_under_one_entity_per_node_profile")
    if registry.get("routing_profile") != "one-routable-entity-per-node":
        reasons.append("unsupported_routing_profile")
    operation = invocation.get("operation", "")
    if isinstance(operation, str) and operation.startswith("coherent.agent-"):
        reasons.append("operation_pins_original_robot")
    parameters = invocation.get("parameters", {})
    if "agent_id" in parameters:
        reasons.append("parameters_pin_original_robot")
    return {
        "schema": "roboguide.e2-takeover-preflight/v0.1",
        "source_entity_id": source_entity,
        "replacement_entity_id": replacement_entity,
        "structural_prerequisites_passed": not reasons,
        "blockers": reasons,
        "execution_authorized": False,
        "required_runtime_checks": [
            "exact_current_attempt_stop_proof", "explicit_bounded_takeover_authorization",
            "current_actor_and_context_constraints", "fresh_capability_and_operation_support",
            "resource_matching_scheduling_proposal_commit_rebind",
            "replacement_local_state_preconditions",
        ],
        "note": "Read-only structural analysis; passing does not authorize Actor migration or execution.",
    }


def main() -> None:
    """Read frozen registry and invocation files and exclusively write a new analysis."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--invocation", type=Path, required=True)
    parser.add_argument("--source-entity", required=True)
    parser.add_argument("--replacement-entity", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    invocation = json.loads(args.invocation.read_text(encoding="utf-8"))
    result = assess_takeover(registry, invocation, args.source_entity, args.replacement_entity)
    with args.output.open("x", encoding="utf-8") as destination:
        json.dump(result, destination, ensure_ascii=False, indent=2)
        destination.write("\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
