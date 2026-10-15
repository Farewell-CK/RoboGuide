"""Check actual prepared Memory artifacts against local closed cross-language schemas."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import importlib.metadata
import json
import tomllib
from pathlib import Path
from typing import Any

from check_state_verifier_processes import ROOT, save
from spatial_content_policy import digest


def run(directory: Path) -> dict[str, Any]:
    """Validate actual artifacts with local schemas and reject malformed variants."""
    jsonschema = importlib.import_module("jsonschema")
    referencing = importlib.import_module("referencing")
    draft = importlib.import_module("referencing.jsonschema").DRAFT202012
    resources = []
    for path in sorted((ROOT / "contracts").rglob("*.json")):
        value = json.loads(path.read_bytes())
        if isinstance(value, dict) and "$schema" in value and "$id" in value:
            resources.append((value["$id"], draft.create_resource(value)))
    registry = referencing.Registry().with_resources(resources)
    admitted = json.loads((directory / "result.json").read_bytes())
    assert admitted["passed"] is True
    spatial = bool(admitted.get("prepared_map_consumer"))
    schema_paths = {
        "mission": ROOT / "contracts/mission/v0.9/mission-plan.schema.json",
        "config": ROOT / "contracts/node/v0.12/node-config.schema.json",
        "source_config": ROOT
        / f"contracts/node/{'v0.11' if spatial else 'v0.12'}/node-config.schema.json",
        "map": ROOT / "contracts/spatial/v0.2/map-manifest.schema.json",
        **{
            name: ROOT / f"contracts/memory/preparation-v0.1/{name}.schema.json"
            for name in ("preparation", "control", "receipt", "availability")
        },
    }
    validators = {}
    for name, path in schema_paths.items():
        schema = json.loads(path.read_bytes())
        jsonschema.Draft202012Validator.check_schema(schema)
        validators[name] = jsonschema.Draft202012Validator(schema, registry=registry)
    plan = json.loads((directory / "mission.json").read_bytes())
    validators["mission"].validate(plan)
    source_config = directory / ("node.toml" if spatial else "source/node.toml")
    validators["source_config"].validate(tomllib.loads(source_config.read_text()))
    validators["config"].validate(tomllib.loads((directory / "consumer/node.toml").read_text()))
    if spatial:
        recorded = json.loads((directory / "consumer/preparation.json").read_bytes())
        records = {recorded["execution_id"]: recorded}
    else:
        records = json.loads((directory / "preparations.json").read_bytes())
    record = next(iter(records.values()))
    preparation = record["preparation"]
    validators["preparation"].validate(preparation)
    typed = [item["typed_map"] for item in preparation["inputs"] if item.get("typed_map")]
    for source in typed:
        validators["map"].validate(source)
    if spatial:
        assert typed == [json.loads((directory / "catalog-manifest.json").read_bytes())["manifest"]]
    observations = []
    controls = []
    for delivery in record["controls"].values():
        command, receipt = delivery["command"], delivery["receipt"]
        validators["control"].validate(command)
        assert command["preparation_digest"] == digest(preparation)
        controls.append(command)
        if receipt:
            validators["receipt"].validate(receipt)
            observations.extend(receipt["observations"])
            for evidence in receipt["observations"]:
                validators["availability"].validate(evidence)
                original = next(
                    item
                    for item in preparation["inputs"]
                    if item["import_id"] == evidence["import_id"]
                )
                assert evidence["manifest_digest"] == digest(original["manifest"])
                assert (
                    evidence["content_digest"] == original["manifest"]["artifact"]["content_digest"]
                )
    assert len(observations) == len(preparation["inputs"])
    rejected = []

    def reject(schema_name: str, case: str, value: dict[str, Any]) -> None:
        """Require a validation failure; accepted malformed artifacts stop the checker."""
        if not list(validators[schema_name].iter_errors(value)):
            raise AssertionError("closed contract accepted " + case)
        rejected.append(case)

    for key in ("node_id", "consumer_provider_id", "artifact_path", "inventory"):
        changed = copy.deepcopy(plan)
        required = next(
            role["requirements"]["memories"]
            for task in changed["tasks"]
            for role in task["roles"]
            if role["requirements"]["memories"]
        )
        required[0][key] = "not-semantic-authority"
        reject("mission", "Mission requirement rejects " + key, changed)
    for generation in (0, 17):
        changed = copy.deepcopy(next(item for item in controls if item["operation"] == "probe"))
        changed["generation"] = generation
        reject("control", f"probe generation {generation}", changed)
    changed = copy.deepcopy(next(item for item in controls if item["operation"] == "probe"))
    changed["preparation"] = preparation
    reject("control", "probe cannot introduce a context", changed)
    positive = next(
        delivery["receipt"]
        for delivery in record["controls"].values()
        if delivery["receipt"] and delivery["receipt"]["status"] == "available"
    )
    for case in ("expired", "missing-config", "incomplete-inputs", "negative-input"):
        changed = copy.deepcopy(positive)
        if case == "expired":
            changed["remaining_budget_ms"] = 0
        elif case == "missing-config":
            changed["configuration_digest"] = None
        elif case == "incomplete-inputs":
            changed["observations"] = []
        else:
            changed["observations"][0]["available"] = False
        reject("receipt", case, changed)
    config = tomllib.loads((directory / "consumer/node.toml").read_text())
    changed = copy.deepcopy(config)
    changed["memory_providers"][0]["availability"]["observation"]["operation"]["method"] = "POST"
    reject("config", "availability cannot mutate", changed)
    changed = copy.deepcopy(config)
    del changed["memory_providers"][0]["continuity"]
    reject("config", "availability requires original backend continuity", changed)
    if spatial:
        changed = copy.deepcopy(config)
        changed["schema"] = "roboguide.node-config/v0.9"
        reject("source_config", "legacy config cannot negotiate current availability", changed)
        changed = copy.deepcopy(preparation)
        del changed["inputs"][0]["typed_map"]["producer_provider_id"]
        reject("preparation", "typed map cannot strip original provider", changed)
    result = {
        "passed": True,
        "jsonschema": importlib.metadata.version("jsonschema"),
        "actual_preparations": len(records),
        "actual_observations": len(observations),
        "actual_typed_maps": len(typed),
        "rejected_cases": rejected,
        "schema_identity": {
            str(path.relative_to(ROOT)): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            for path in schema_paths.values()
        },
    }
    save(directory / "schema-validation.json", result)
    return result


def main() -> None:
    """Read a completed profile without starting services or retrieving remote schemas."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    arguments = parser.parse_args()
    print(json.dumps(run(arguments.directory), sort_keys=True))


if __name__ == "__main__":
    main()
