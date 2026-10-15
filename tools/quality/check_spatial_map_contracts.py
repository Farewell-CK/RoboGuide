"""Validate actual typed-map output with an independent pinned JSON Schema implementation."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def run(python: Path, directory: Path, output: Path) -> dict[str, Any]:
    """Check the actual catalog manifest and context-derived envelope without provider imports."""
    program = r"""import copy,hashlib,importlib.metadata,json,sys
from pathlib import Path
from jsonschema import Draft202012Validator,ValidationError
from referencing import Registry,Resource
assert importlib.metadata.version("jsonschema")=="4.26.0"
root=Path(sys.argv[1]);directory=Path(sys.argv[2]);registry=Registry();schemas={}
for path in sorted(root.glob("*.schema.json")):
 value=json.loads(path.read_bytes());Draft202012Validator.check_schema(value)
 registry=registry.with_resource(value["$id"],Resource.from_contents(value))
 schemas[path.name]=value
assert len(schemas)==2
validators={name:Draft202012Validator(value,registry=registry) for name,value in schemas.items()}
manifest=json.loads((directory/"catalog-manifest.json").read_bytes())["manifest"]
receipt=json.loads((directory/"result.json").read_bytes())
assert receipt["passed"] is True
attempt=receipt["verifier_receipt"]["receipt"]["evidence"]["attempts"][0]["attempt_id"]
envelope={"schema":"roboguide.map-publication/v0.1","manifest":manifest,"execution_id":attempt}
validators["map-manifest.schema.json"].validate(manifest)
validators["map-publication.schema.json"].validate(envelope)
cases=[]
for name in ("missing-provider","missing-content-schema","missing-original-attempt",
             "missing-original-task","null-original-owner","legacy-provider-claim",
             "invented-node-selector","boolean-attempt"):
 value=copy.deepcopy(envelope)
 if name=="missing-provider":value["manifest"].pop("producer_provider_id")
 elif name=="missing-content-schema":value["manifest"].pop("payload_schema")
 elif name=="missing-original-attempt":value["manifest"].pop("source_execution_id")
 elif name=="missing-original-task":value["manifest"].pop("source_task_ref")
 elif name=="null-original-owner":value["manifest"]["producer_local_system_id"]=None
 elif name=="legacy-provider-claim":value["manifest"]["schema"]="roboguide.map-manifest/v0.1"
 elif name=="invented-node-selector":value["node_selector"]="not-authority"
 elif name=="boolean-attempt":value["execution_id"]=True
 try:validators["map-publication.schema.json"].validate(value)
 except ValidationError:cases.append(name)
 else:raise AssertionError(name)
print(json.dumps({"passed":True,"validator":"jsonschema/4.26.0","actual_catalog_manifests":1,
 "context_derived_publication_envelopes":1,"rejected_cases":cases,
 "schema_sha256":{path.name:hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in sorted(root.glob("*.schema.json"))}}))
"""
    completed = subprocess.run(
        [str(python), "-c", program, str(ROOT / "contracts/spatial/v0.2"), str(directory)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        timeout=30,
    )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict) or value.get("passed") is not True:
        raise ValueError("independent typed-map schema validation was incomplete")
    with output.open("x") as stream:
        stream.write(json.dumps(value, indent=2) + "\n")
    return value


def main() -> None:
    """Retain the independent schema receipt beside the original actual software process record."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.python, args.directory, args.output)))


if __name__ == "__main__":
    main()
