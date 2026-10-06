"""Render one B1 deployment's transport endpoints without changing task semantics."""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

PORT_DEFAULTS = {
    "CONTROLLER_GRPC_PORT": 25060,
    "CONTROLLER_PORT": 28060,
    "ARTIFACT_PORT": 28090,
    "HABITAT_PORT": 28100,
    "HABITAT_PORT_B": 28102,
    "MISSION_PORT": 8070,
}


def deployment_ports(environment: Mapping[str, str]) -> dict[str, int]:
    """Resolve distinct startup-owned ports; invalid input never starts a component."""
    ports = {}
    for name, default in PORT_DEFAULTS.items():
        value = environment.get("ROBOGUIDE_B1_" + name, str(default))
        if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 65535:
            raise ValueError("invalid B1 deployment port: " + name)
        ports[name] = int(value)
    if len(set(ports.values())) != len(ports):
        raise ValueError("B1 deployment ports must be distinct")
    return ports


def render_deployment_configs(run: Path, scenario: Path, ports: Mapping[str, int]) -> None:
    """Render run-local configs, substituting only known endpoints and storage paths."""
    substitutions = {
        f"127.0.0.1:{default}": f"127.0.0.1:{ports[name]}"
        for name, default in PORT_DEFAULTS.items()
    }
    # Replace in one pass so a requested port equal to another default cannot cascade.
    for source, target in (
        ("node-a.toml", "node-a.toml"),
        ("node-b.toml", "node-b.toml"),
        ("mission-service-b1.toml", "mission-service-b1.toml"),
    ):
        text = (scenario / source).read_text(encoding="utf-8")
        for index, original in enumerate(substitutions):
            text = text.replace(original, f"B1_ENDPOINT_PLACEHOLDER_{index}")
        for index, replacement in enumerate(substitutions.values()):
            text = text.replace(f"B1_ENDPOINT_PLACEHOLDER_{index}", replacement)
        if source == "mission-service-b1.toml":
            text = text.replace("listen_port = 8070", f"listen_port = {ports['MISSION_PORT']}")
        text = text.replace("NODE_STATE_PLACEHOLDER", str(run / ("node-state-" + source[5])))
        text = text.replace("STATE_DB_PLACEHOLDER", str(run / "mission-service.sqlite3"))
        text = text.replace(
            "SEMANTIC_EVIDENCE_PLACEHOLDER",
            str(run / "evidence/authoritative-semantic-evidence.json"),
        )
        text = text.replace(
            "PLANNING_WORLD_EVIDENCE_PLACEHOLDER",
            str(run / "evidence/authoritative-planning-world-evidence.json"),
        )
        (run / target).write_text(text, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """Validate startup ports, optionally render configs, and emit numeric shell assignments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path)
    parser.add_argument("--scenario", type=Path)
    args = parser.parse_args(argv)
    try:
        ports = deployment_ports(os.environ)
        if (args.run is None) != (args.scenario is None):
            raise ValueError("run and scenario must be supplied together")
        if args.run is not None:
            render_deployment_configs(args.run, args.scenario, ports)
    except (OSError, ValueError) as error:
        parser.exit(1, f"B1 deployment configuration invalid: {type(error).__name__}\n")
    for name, value in ports.items():
        print(f"{name}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
