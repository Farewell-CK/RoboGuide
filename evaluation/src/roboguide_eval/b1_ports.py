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


def deployment_ports(environment: Mapping[str, str], endpoint_count: int = 2) -> dict[str, int]:
    """Resolve distinct startup-owned ports; invalid input never starts a component."""
    if type(endpoint_count) is not int or not 1 <= endpoint_count <= 4:
        raise ValueError("B1 endpoint count must be in 1..4")
    defaults = {
        **{
            name: port
            for name, port in PORT_DEFAULTS.items()
            if name != "HABITAT_PORT_B" or endpoint_count >= 2
        },
        **{
            "HABITAT_PORT_" + name: port
            for name, port in (("C", 28104), ("D", 28106))
            if endpoint_count >= ord(name) - ord("A") + 1
        },
    }
    ports = {}
    for name, default in defaults.items():
        value = environment.get("ROBOGUIDE_B1_" + name, str(default))
        if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 65535:
            raise ValueError("invalid B1 deployment port: " + name)
        ports[name] = int(value)
    if len(set(ports.values())) != len(ports):
        raise ValueError("B1 deployment ports must be distinct")
    return ports


def render_deployment_configs(
    run: Path, scenario: Path, ports: Mapping[str, int], node_configs: tuple[str, ...] = ()
) -> None:
    """Render run-local configs, substituting only known endpoints and storage paths."""
    defaults = {**PORT_DEFAULTS, "HABITAT_PORT_C": 28104, "HABITAT_PORT_D": 28106}
    substitutions = {
        f"127.0.0.1:{default}": f"127.0.0.1:{ports[name]}"
        for name, default in defaults.items()
        if name in ports
    }
    # Replace in one pass so a requested port equal to another default cannot cascade.
    sources = tuple(
        (source, f"node-{chr(97 + index)}.toml")
        for index, source in enumerate(node_configs or ("node-a.toml", "node-b.toml"))
    )
    for source, target in (*sources, ("mission-service-b1.toml", "mission-service-b1.toml")):
        text = (scenario / source).read_text(encoding="utf-8")
        for index, original in enumerate(substitutions):
            text = text.replace(original, f"B1_ENDPOINT_PLACEHOLDER_{index}")
        for index, replacement in enumerate(substitutions.values()):
            text = text.replace(f"B1_ENDPOINT_PLACEHOLDER_{index}", replacement)
        if source == "mission-service-b1.toml":
            text = text.replace("listen_port = 8070", f"listen_port = {ports['MISSION_PORT']}")
        text = text.replace("NODE_STATE_PLACEHOLDER", str(run / ("node-state-" + target[5])))
        text = text.replace("STATE_DB_PLACEHOLDER", str(run / "mission-service.sqlite3"))
        text = text.replace(
            "SEMANTIC_EVIDENCE_PLACEHOLDER",
            str(run / "evidence/authoritative-semantic-evidence.json"),
        )
        text = text.replace(
            "PLANNING_WORLD_EVIDENCE_PLACEHOLDER",
            str(run / "evidence/authoritative-planning-world-evidence.json"),
        )
        for placeholder, filename in (
            ("EXECUTION_PROFILE_PLACEHOLDER", "execution-profile.json"),
            ("PLANNING_PROFILE_PLACEHOLDER", "planning-profile.json"),
        ):
            if placeholder in text:
                (run / filename).write_bytes((scenario / filename).read_bytes())
                text = text.replace(placeholder, str(run / filename))
        (run / target).write_text(text, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """Validate startup ports, optionally render configs, and emit numeric shell assignments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path)
    parser.add_argument("--scenario", type=Path)
    parser.add_argument("--declaration", type=Path)
    args = parser.parse_args(argv)
    try:
        names: tuple[str, ...] = ()
        if args.declaration is not None:
            from roboguide_eval.b1_deployment import load_deployment

            names = load_deployment(
                args.scenario or args.declaration.parent, declaration_path=args.declaration
            ).endpoints
        ports = deployment_ports(os.environ, len(names) if names else 2)
        budget = mission_observation_budget(os.environ)
        if (args.run is None) != (args.scenario is None):
            raise ValueError("run and scenario must be supplied together")
        if args.run is not None:
            render_deployment_configs(args.run, args.scenario, ports, names)
    except (OSError, ValueError) as error:
        parser.exit(1, f"B1 deployment configuration invalid: {type(error).__name__}\n")
    for name, value in ports.items():
        print(f"{name}={value}")
    print(f"MISSION_OBSERVATION_BUDGET_SECONDS={budget}")
    return 0


def mission_observation_budget(environment: Mapping[str, str]) -> int:
    """Bound the Runner wall wait separately from unchanged physical and model budgets."""
    value = environment.get("ROBOGUIDE_B1_MISSION_OBSERVATION_BUDGET_SECONDS", "1800")
    if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 86400:
        raise ValueError("mission observation budget must be an integer in 1..86400")
    return int(value)


if __name__ == "__main__":
    raise SystemExit(main())
