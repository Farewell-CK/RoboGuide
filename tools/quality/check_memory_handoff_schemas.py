"""Independently validate actual handoff artifacts against local closed JSON contracts."""

from __future__ import annotations

import argparse
import copy
import importlib
import importlib.metadata
import json
import tomllib
from pathlib import Path
from typing import Any

from check_state_verifier_processes import ROOT, save
from spatial_content_policy import digest


def run(directory: Path) -> dict[str, Any]:
    """Check original actual commands, both configurations and complete original receiver proofs."""
    jsonschema = importlib.import_module("jsonschema")
    referencing = importlib.import_module("referencing")
    draft = importlib.import_module("referencing.jsonschema").DRAFT202012
    resources = []
    for path in sorted((ROOT / "contracts").rglob("*.json")):
        value = json.loads(path.read_bytes())
        if isinstance(value, dict) and "$schema" in value and "$id" in value:
            resources.append((value["$id"], draft.create_resource(value)))
    registry = referencing.Registry().with_resources(resources)
    paths = {
        "handoff": "memory/handoff-v0.1/request.schema.json",
        "mission": "mission/v0.9/mission-plan.schema.json",
        "config": "node/v0.13/node-config.schema.json",
    }
    for kind in ("preparation", "control", "receipt", "availability"):
        paths[kind] = f"memory/preparation-v0.2/{kind}.schema.json"
    for kind in ("control", "receipt"):
        paths["use_" + kind] = f"memory/use-v0.1/{kind}.schema.json"
    validators = {}
    for kind, relative in paths.items():
        schema = json.loads((ROOT / "contracts" / relative).read_bytes())
        jsonschema.Draft202012Validator.check_schema(schema)
        validators[kind] = jsonschema.Draft202012Validator(schema, registry=registry)
    command = json.loads((directory / "handoff-command.json").read_bytes())
    validators["handoff"].validate(command)
    for name in ("source", "target"):
        validators["mission"].validate(
            json.loads((directory / f"handoff-{name}-mission.json").read_bytes())
        )
    for name in ("source", "consumer"):
        validators["config"].validate(tomllib.loads((directory / name / "node.toml").read_text()))
    committed = json.loads((directory / "handoff-committed.json").read_bytes())["record"]
    assert committed["request"] == command
    assert committed["receiver_proof_digest"] == digest(committed["receiver_proofs"])
    observations = 0
    use_receipts = 0
    for proof in committed["receiver_proofs"].values():
        preparation = proof["preparation"]["preparation"]
        validators["preparation"].validate(preparation)
        for delivery in proof["preparation"]["controls"].values():
            validators["control"].validate(delivery["command"])
            assert delivery["command"]["preparation_digest"] == digest(preparation)
            if delivery["receipt"] is not None:
                validators["receipt"].validate(delivery["receipt"])
                for observed in delivery["receipt"]["observations"]:
                    validators["availability"].validate(observed)
                    observations += 1
        for delivery in proof["use_record"]["controls"].values():
            validators["use_control"].validate(delivery["command"])
            if delivery["receipt"] is not None:
                validators["use_receipt"].validate(delivery["receipt"])
                use_receipts += 1
    assert observations > 0 and use_receipts > 0
    rejected = []
    mutations = [
        ("source_execution_ids", []),
        ("source_execution_ids", command["source_execution_ids"] * 2),
        ("schema_version", "roboguide.memory-handoff/v0.0"),
        ("handoff_id", "../another"),
        ("deadline_ms", 0),
        ("registry_revision", 0),
        ("target_membership", {**command["target_membership"], "generation": 0}),
        ("node_id", "not-a-binding-command"),
        ("stop_confirmed", True),
        ("child", {**command["child"], "scope": {"kind": "global"}}),
    ]
    for name, value in mutations:
        changed = copy.deepcopy(command)
        changed[name] = value
        assert list(validators["handoff"].iter_errors(changed)), name
        rejected.append(name)
    result = {
        "passed": True,
        "jsonschema": importlib.metadata.version("jsonschema"),
        "positive_availability_observations": observations,
        "positive_use_receipts": use_receipts,
        "structural_rejections": rejected,
        "semantic_authority_validated_by_controller": True,
    }
    save(directory / "handoff-schema-validation.json", result)
    return result


def main() -> None:
    """Inspect only an explicit retained actual software handoff directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(run(arguments.directory), sort_keys=True))


if __name__ == "__main__":
    main()
