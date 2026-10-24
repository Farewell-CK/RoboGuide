"""Validate actual retained participant requests and policy revisions against closed contracts."""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def run(evidence: Path) -> dict[str, Any]:
    """Inspect retained process artifacts offline without granting service authority."""
    jsonschema = importlib.import_module("jsonschema")
    validators = {}
    for kind in ("prepare", "commit", "abort", "authorization"):
        path = ROOT / "contracts/federation/service-v0.2" / f"{kind}.schema.json"
        schema = json.loads(path.read_bytes())
        jsonschema.Draft202012Validator.check_schema(schema)
        validators[kind] = jsonschema.Draft202012Validator(schema)
    accepted = policies = originals = 0
    for name in ("a", "b"):
        snapshot = json.loads((evidence / f"participant-{name}-sql.json").read_bytes())
        for policy in snapshot["state"]["policies"].values():
            validators["authorization"].validate(policy)
            policies += 1
        for record in snapshot["state"]["records"].values():
            validators["prepare"].validate(record["request"])
            originals += 1
        for line in (evidence / f"domain-{name}/http-trace.jsonl").read_text().splitlines():
            trace = json.loads(line)
            if trace["status"] != 202 or trace["request"] is None:
                continue
            kind = trace["path"].rsplit("/", 1)[-1]
            validators[kind].validate(trace["request"])
            if trace["response"]["global_dispatch_ready"] is not False:
                raise ValueError("participant response incorrectly grants global dispatch")
            accepted += 1
    if not accepted or not policies or not originals:
        raise ValueError("actual Federation evidence lacks original accepted requests or policies")
    return {
        "accepted_requests": accepted,
        "policies": policies,
        "original_records": originals,
        "network_calls": 0,
        "nodes_created": 0,
    }


def main() -> None:
    """Report actual retained cross-language contract coverage without replaying any command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.evidence.resolve())
    (args.evidence / "schema-validation.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
