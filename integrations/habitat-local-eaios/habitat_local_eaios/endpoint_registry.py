"""Deployment-owned bounded endpoint registry; no Task assignment or resource commitment."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .model import IntegrationError

REGISTRY_SCHEMA = "roboguide.habitat-endpoint-registry/v0.1"
LIVE_PROFILE = "independent-live/v0.1"
MAX_ENDPOINTS = 4
MAX_SLOTS = 32
MAX_REGISTRY_BYTES = 65536


def registry_digest(body: object) -> str:
    """Digest finite, bounded public deployment evidence without credentials."""
    raw = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    if len(raw.encode()) > MAX_REGISTRY_BYTES:
        raise IntegrationError("endpoint registry exceeds byte budget")
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def build_endpoint_registry(
    node_configs: tuple[tuple[int, Path], ...], run: Path
) -> dict[str, Any]:
    """Freeze configured identities, ports, actual resource slots and operation support.

    This runs in RoboGuide Python before services start. Habitat Python validates
    the exact Node source digests and loaded agent classes independently.
    """
    import tomllib

    if not 1 <= len(node_configs) <= MAX_ENDPOINTS or tuple(
        agent for agent, _ in node_configs
    ) != tuple(range(len(node_configs))):
        raise IntegrationError("live endpoints need one to four contiguous agent ids")
    records = []
    for agent, path in node_configs:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
        systems = document.get("local_systems", [])
        if document.get("schema") != "roboguide.node-config/v0.7" or len(systems) != 1:
            raise IntegrationError("endpoint requires one configured Local EAIOS on Node v0.7")
        system = systems[0]
        owner = system["id"]
        robot_type = system.get("metadata", {}).get("roboguide.habitat-robot-type")
        if system.get("metadata", {}).get("roboguide.habitat-execution-profile") != LIVE_PROFILE:
            raise IntegrationError("endpoint must explicitly declare its live execution profile")
        connections = [
            item for item in document.get("connections", []) if item.get("local_system") == owner
        ]
        if len(connections) != 1 or connections[0].get("driver") != "http":
            raise IntegrationError("endpoint requires one fixed local HTTP connection")
        endpoint = connections[0].get("endpoint", "")
        if not endpoint.startswith("http://127.0.0.1:") or not endpoint[17:].isdecimal():
            raise IntegrationError("endpoint must use a fixed loopback port")
        port = int(endpoint[17:])
        resources = document.get("resources", [])
        if (
            len(resources) != 1
            or resources[0].get("kind") != "space"
            or type(resources[0].get("capacity")) is not int
            or resources[0]["capacity"] != 1
            or resources[0].get("owner") != owner
        ):
            raise IntegrationError("endpoint requires one exclusive capacity-one space resource")
        operations = document.get("operations", [])
        if not operations or any(
            item.get("owner") != owner
            or item.get("required_resources") != [resources[0]["id"]]
            or len(item.get("local_locks", [])) != 1
            for item in operations
        ):
            raise IntegrationError(
                "endpoint operations must share the actual exclusive slot and lock"
            )
        if len({item["local_locks"][0] for item in operations}) != 1:
            raise IntegrationError("endpoint operations must use one identical exclusive lock")
        contracts = {item.get("contract") for item in document.get("capability_profiles", [])}
        operation_names = [item.get("operation") for item in operations]
        if any(
            not isinstance(name, str)
            or name
            not in {
                "mobility.move@v1",
                "mobility.navigate@v1",
                "object.relocate@v1",
                "observation.verify@v1",
            }
            or name not in contracts
            for name in operation_names
        ) or len(set(operation_names)) != len(operation_names):
            raise IntegrationError(
                "endpoint operation support needs exact declared capability profiles"
            )
        records.append(
            {
                "agent_id": agent,
                "node_id": document.get("node_id"),
                "robot_type": robot_type,
                "port": port,
                "state_db": str((run / f"bridge-{agent}.sqlite3").resolve()),
                "node_config_path": str(path.resolve()),
                "node_config_digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                "resource_id": resources[0]["id"],
                "operations": sorted(operation_names),
            }
        )
    body = {"schema_version": REGISTRY_SCHEMA, "profile": LIVE_PROFILE, "endpoints": records}
    document = {**body, "digest": registry_digest(body)}
    validate_endpoint_registry(document)
    return document


def validate_endpoint_registry(document: Any) -> None:
    """Reject malformed, overlapping, forged or stale registry sources before child spawn."""
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "profile", "endpoints", "digest"}
        or document.get("schema_version") != REGISTRY_SCHEMA
        or document.get("profile") != LIVE_PROFILE
        or document.get("digest")
        != registry_digest({key: value for key, value in document.items() if key != "digest"})
    ):
        raise IntegrationError("endpoint registry schema/profile/digest is invalid")
    records = document["endpoints"]
    if not isinstance(records, list) or not 1 <= len(records) <= MAX_ENDPOINTS:
        raise IntegrationError("endpoint registry coverage exceeds supported bounds")
    identities: dict[str, set[Any]] = {
        name: set() for name in ("node_id", "port", "state_db", "resource_id", "node_config_path")
    }
    for agent, record in enumerate(records):
        if (
            not isinstance(record, dict)
            or set(record)
            != {
                "agent_id",
                "node_id",
                "robot_type",
                "port",
                "state_db",
                "node_config_path",
                "node_config_digest",
                "resource_id",
                "operations",
            }
            or type(record["agent_id"]) is not int
            or record["agent_id"] != agent
            or type(record["port"]) is not int
            or not 1 <= record["port"] <= 65535
        ):
            raise IntegrationError("endpoint registry record is invalid")
        for key in (
            "node_id",
            "robot_type",
            "state_db",
            "resource_id",
            "node_config_path",
            "node_config_digest",
        ):
            if not isinstance(record[key], str) or not record[key].strip():
                raise IntegrationError("endpoint registry identity is missing")
        for key, values in identities.items():
            if record[key] in values:
                raise IntegrationError("endpoint registry contains duplicate identities")
            values.add(record[key])
        operations = record["operations"]
        if (
            not isinstance(operations, list)
            or not operations
            or any(not isinstance(name, str) for name in operations)
            or operations != sorted(set(operations))
        ):
            raise IntegrationError("endpoint registry operation coverage is invalid")
        path = Path(record["node_config_path"])
        if (
            not path.is_absolute()
            or "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            != record["node_config_digest"]
        ):
            raise IntegrationError("endpoint Node source changed after registry freeze")
        if not Path(record["state_db"]).is_absolute():
            raise IntegrationError("endpoint state database must be run-local absolute storage")


def load_endpoint_registry(path: Path) -> dict[str, Any]:
    """Load a bounded explicit profile; never infer endpoint identities from goals."""
    with path.open("rb") as source:
        raw = source.read(MAX_REGISTRY_BYTES + 1)
    if len(raw) > MAX_REGISTRY_BYTES:
        raise IntegrationError("endpoint registry exceeds byte budget")
    document = json.loads(raw)
    validate_endpoint_registry(document)
    assert isinstance(document, dict)
    return document


def verify_endpoint_sources(path: Path) -> dict[str, Any]:
    """Rebuild under RoboGuide Python so rehashing cannot invent ports, abilities or Node IDs."""
    document = load_endpoint_registry(path)
    records = document["endpoints"]
    roots = {Path(record["state_db"]).parent for record in records}
    if len(roots) != 1:
        raise IntegrationError("endpoint stores do not belong to one run directory")
    sources = tuple((record["agent_id"], Path(record["node_config_path"])) for record in records)
    if build_endpoint_registry(sources, next(iter(roots))) != document:
        raise IntegrationError("endpoint registry differs from actual Node declarations")
    return document


def prepare_live_deployment(run: Path, count: int) -> None:
    """Freeze actual run-local Node sources without a service, simulator or Provider."""
    from .relocation_deployment import build_relocation_profile
    from .spatial_feasibility import build_spatial_profile_snapshot

    sources = tuple((agent, run / f"node-{chr(97 + agent)}.toml") for agent in range(count))
    registry = build_endpoint_registry(sources, run)
    artifacts: dict[str, Any] = {
        "endpoint-registry.json": registry,
        "spatial-profile.json": build_spatial_profile_snapshot(sources),
    }
    relocators = tuple(
        (record["agent_id"], Path(record["node_config_path"]))
        for record in registry["endpoints"]
        if "object.relocate@v1" in record["operations"]
    )
    if relocators:
        artifacts["relocation-registration-profile.json"] = build_relocation_profile(relocators)
    for name, body in artifacts.items():
        with (run / name).open("x", encoding="utf-8") as output:
            output.write(json.dumps(body, indent=2, sort_keys=True) + "\n")


def main() -> None:
    """Prepare or recheck a frozen live endpoint deployment with no external calls."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--count", type=int, default=2)
    parser.add_argument("--verify-sources", action="store_true")
    args = parser.parse_args()
    if args.verify_sources:
        verify_endpoint_sources(args.run / "endpoint-registry.json")
    else:
        prepare_live_deployment(args.run, args.count)


if __name__ == "__main__":
    main()
