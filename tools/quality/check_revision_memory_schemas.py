"""Validate actual scoped-revision commands and plans using closed local JSON contracts."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path
from typing import Any

from check_state_verifier_processes import ROOT, save


def run(directory: Path) -> dict[str, Any]:
    """Check retained actual commands offline, without fetching schemas or granting authority."""
    jsonschema = importlib.import_module("jsonschema")
    referencing = importlib.import_module("referencing")
    draft = importlib.import_module("referencing.jsonschema").DRAFT202012
    resources = []
    for path in sorted((ROOT / "contracts").rglob("*.json")):
        value = json.loads(path.read_bytes())
        if isinstance(value, dict) and "$schema" in value and "$id" in value:
            resources.append((value["$id"], draft.create_resource(value)))
    registry = referencing.Registry().with_resources(resources)
    validators = {}
    identities = {}
    for name, relative in {
        "v0.1": "mission/runtime-plan-revision-v0.1/command.schema.json",
        "v0.2": "mission/runtime-plan-revision-v0.2/command.schema.json",
        "v0.3": "mission/runtime-plan-revision-v0.3/command.schema.json",
        "plan": "mission/v0.9/mission-plan.schema.json",
    }.items():
        path = ROOT / "contracts" / relative
        schema = json.loads(path.read_bytes())
        jsonschema.Draft202012Validator.check_schema(schema)
        validators[name] = jsonschema.Draft202012Validator(schema, registry=registry)
        identities[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    commands = []
    declared = None
    for path in sorted(directory.glob("*.command.json")):
        value = json.loads(path.read_bytes())
        command = value["input"]
        version = command["schema_version"].rsplit("/", 1)[1]
        validators[version].validate(command)
        commands.append(path.name)
        if command["action"]["kind"] == "authorize-memory" and value["status"] == 200:
            declared = command
    assert declared is not None
    for name in ("handoff-source-mission.json", "revision-candidate.json"):
        validators["plan"].validate(json.loads((directory / name).read_bytes()))
    rejected = []
    for name, value in (
        ("schema_version", "roboguide.runtime-plan-revision/v0.2"),
        ("stop_confirmed", True),
        ("node_id", "not-an-assignment"),
    ):
        changed = copy.deepcopy(declared)
        changed[name] = value
        version = changed["schema_version"].rsplit("/", 1)[1]
        assert list(validators[version].iter_errors(changed))
        rejected.append(name)
    for field, value in (
        ("registry_revision", 0),
        ("child", {**declared["action"]["authorization"]["child"], "scope": {"kind": "global"}}),
        ("child", {**declared["action"]["authorization"]["child"], "kind": "spatial"}),
        ("child", {**declared["action"]["authorization"]["child"], "visibility": "discoverable"}),
        ("stop_proof", True),
    ):
        changed = copy.deepcopy(declared)
        changed["action"]["authorization"][field] = value
        assert list(validators["v0.3"].iter_errors(changed))
        rejected.append(field)
    result = {
        "passed": True,
        "commands": commands,
        "structural_rejections": rejected,
        "schema_sha256": identities,
        "jsonschema_version": importlib.metadata.version("jsonschema"),
        "checker_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "authority_from_schema": False,
    }
    save(directory / "revision-schema-validation.json", result)
    return result


def main() -> None:
    """Inspect only an explicitly supplied retained actual process directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="source", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(run(arguments.source), sort_keys=True))


if __name__ == "__main__":
    main()
