"""Freeze explicit B1 deployment choices without selecting a plan or an executor."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from roboguide_eval.b1_workload import extract_b1_workload

DEPLOYMENT_SCHEMA = "roboguide.e1.b1-deployment/v0.2"
LEGACY_DEPLOYMENT_SCHEMA = "roboguide.e1.b1-deployment/v0.1"
LOCAL_PROFILE_REQUIREMENT_SCHEMA = "roboguide.e1.b1-local-execution-profile-required/v0.1"
MAX_DECLARATION_BYTES = 65536


@dataclass(frozen=True)
class B1Deployment:
    """Carry only deployment-owned original Habitat configuration and local profile selection."""

    habitat_config: str
    max_steps: int
    enable_relocation: bool
    relocation_completion_binding: bool
    schema_version: str


def load_deployment(scenario: Path) -> B1Deployment:
    """Reject unknown fields, unsafe paths and mistyped budgets before any component launches."""
    with (scenario / "b1-deployment.json").open("rb") as source:
        raw = source.read(MAX_DECLARATION_BYTES + 1)
    if len(raw) > MAX_DECLARATION_BYTES:
        raise ValueError("B1 deployment declaration exceeds the byte budget")
    document = json.loads(raw)
    fields = {"schema_version", "habitat_config", "max_steps", "enable_relocation"}
    if isinstance(document, dict) and document.get("schema_version") == DEPLOYMENT_SCHEMA:
        fields.add("relocation_completion_binding")
    if (
        not isinstance(document, dict)
        or set(document) != fields
        or document["schema_version"] not in (DEPLOYMENT_SCHEMA, LEGACY_DEPLOYMENT_SCHEMA)
    ):
        raise ValueError("B1 deployment declaration schema is invalid")
    config = document["habitat_config"]
    if (
        not isinstance(config, str)
        or not config
        or Path(config).is_absolute()
        or any(part in {"", ".", ".."} for part in config.split("/"))
        or any(ord(character) < 32 for character in config)
        or Path(config).suffix not in {".yaml", ".yml"}
    ):
        raise ValueError("Habitat configuration must be a relative YAML path within EMOS")
    steps = document["max_steps"]
    relocation = document["enable_relocation"]
    if isinstance(steps, bool) or not isinstance(steps, int) or not 1 <= steps <= 100000:
        raise ValueError("B1 deployment step budget is invalid")
    if not isinstance(relocation, bool):
        raise ValueError("B1 deployment relocation selection must be boolean")
    completion = document.get("relocation_completion_binding", False)
    if not isinstance(completion, bool) or (completion and not relocation):
        raise ValueError("B1 deployment completion binding must be boolean and requires relocation")
    return B1Deployment(config, steps, relocation, completion, document["schema_version"])


def freeze_deployment(
    run: Path,
    scenario: Path,
    emos_root: Path,
    *,
    completion_binding_override: str | None = None,
) -> dict[str, str | int]:
    """Bind workload, original config bytes and configured Node identities in a new run.

    The result is shell-quoted by the CLI, never supplied by a model. It
    neither changes benchmark budgets nor claims that Node readiness, route
    feasibility, a MissionPlan or a physical outcome has been verified.
    """
    extract_b1_workload(json.loads((run / "b1-input-used.json").read_bytes()))
    declaration = load_deployment(scenario)
    completion = declaration.relocation_completion_binding
    if completion_binding_override is not None:
        if completion_binding_override not in {"0", "1"}:
            raise ValueError("invalid_relocation_completion_binding_flag")
        selected = completion_binding_override == "1"
        if declaration.schema_version == DEPLOYMENT_SCHEMA and selected != completion:
            raise ValueError("completion binding override conflicts with deployment declaration")
        completion = selected
    if completion and not declaration.enable_relocation:
        raise ValueError("relocation_completion_binding_requires_relocation")
    config = (emos_root / declaration.habitat_config).resolve(strict=True)
    if not config.is_relative_to(emos_root.resolve()):
        raise ValueError("Habitat configuration resolves outside the EMOS checkout")
    node_ids = []
    for name in ("node-a.toml", "node-b.toml"):
        document = tomllib.loads((scenario / name).read_text(encoding="utf-8"))
        node_id = document.get("node_id")
        if not isinstance(node_id, str) or not node_id.strip():
            raise ValueError("B1 deployment needs a nonblank configured Node identity")
        node_ids.append(node_id)
    if len(set(node_ids)) != 2:
        raise ValueError("B1 deployment endpoints must have distinct Node identities")
    body: dict[str, Any] = {
        "schema_version": "roboguide.e1.b1-deployment-used/v0.2",
        "declaration": {
            "schema_version": declaration.schema_version,
            "habitat_config": declaration.habitat_config,
            "max_steps": declaration.max_steps,
            "enable_relocation": declaration.enable_relocation,
            **(
                {"relocation_completion_binding": declaration.relocation_completion_binding}
                if declaration.schema_version == DEPLOYMENT_SCHEMA
                else {}
            ),
        },
        "scenario_path": str(scenario.resolve()),
        "habitat_config_path": str(config),
        "habitat_config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
        "frozen_input_sha256": hashlib.sha256(
            (run / "b1-input-used.json").read_bytes()
        ).hexdigest(),
        "node_ids": node_ids,
        "relocation_completion_binding": completion,
    }
    requirement_path = run / "b1-local-execution-profile-required.json"
    if requirement_path.exists():
        raise ValueError("refusing to overwrite a frozen local execution profile requirement")
    with (run / "b1-deployment-used.json").open("x", encoding="utf-8") as output:
        output.write(json.dumps(body, indent=2, sort_keys=True) + "\n")
    requirement = {
        "schema_version": LOCAL_PROFILE_REQUIREMENT_SCHEMA,
        "run_id": run.name,
        "deployment_sha256": hashlib.sha256(
            (run / "b1-deployment-used.json").read_bytes()
        ).hexdigest(),
        "frozen_input_sha256": body["frozen_input_sha256"],
        "relocation_completion_binding": completion,
    }
    with requirement_path.open("x", encoding="utf-8") as output:
        output.write(json.dumps(requirement, indent=2, sort_keys=True) + "\n")
    return {
        "HABITAT_CONFIG": str(config),
        "MAX_STEPS": declaration.max_steps,
        "RELOCATION_ENABLED": int(declaration.enable_relocation),
        "RELOCATION_COMPLETION_BINDING": int(completion),
        "NODE_A_ID": node_ids[0],
        "NODE_B_ID": node_ids[1],
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Freeze one startup declaration and emit safely quoted assignments, with no services."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--emos-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        assignments = freeze_deployment(
            args.run,
            args.scenario,
            args.emos_root,
            completion_binding_override=os.environ.get("HABITAT_RELOCATION_COMPLETION_BINDING"),
        )
    except (OSError, ValueError) as error:
        parser.exit(1, f"B1 deployment invalid: {error}\n")
    for name, value in assignments.items():
        print(f"{name}={shlex.quote(str(value))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
