"""Deployment-owned, source-bound relocation registration snapshots."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .model import RELOCATION_OPERATION, IntegrationError

RELOCATION_PROFILE_SCHEMA = "roboguide.habitat-relocation-profile/v0.1"
MAX_PROFILE_BYTES = 65536


def _digest(value: object) -> str:
    """Hash a bounded canonical registration body without credentials or runtime inventory."""
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise IntegrationError("relocation profile must be finite JSON") from error
    if len(encoded.encode("utf-8")) > MAX_PROFILE_BYTES:
        raise IntegrationError("relocation profile exceeds the byte budget")
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _source_digest(path: Path) -> str:
    """Read one exact Node source and expose filesystem failures as integration errors."""
    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise IntegrationError("relocation Node source is unavailable") from error


def _one(values: Any, field: str, expected: object) -> dict[str, Any]:
    """Require one exact declared entry rather than accepting duplicate or inferred support."""
    if not isinstance(expected, str) or not expected.strip():
        raise IntegrationError("relocation registration identity must be nonblank")
    if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
        raise IntegrationError("relocation registration entries must be objects")
    matches = [value for value in values if value.get(field) == expected]
    if len(matches) != 1:
        raise IntegrationError(f"relocation registration needs exactly one {field}={expected}")
    return dict(matches[0])


def build_relocation_profile(node_configs: tuple[tuple[int, Path], ...]) -> dict[str, Any]:
    """Freeze validated actual Node registrations under RoboGuide Python, before child startup.

    Habitat Python 3.9 consumes this trusted deployment artifact and rechecks source
    bytes and loaded robot types. The snapshot grants no resource commitment.
    """
    import tomllib

    if not node_configs or len(node_configs) > 2:
        raise IntegrationError("relocation profile requires one or two configured endpoints")
    if len({agent_id for agent_id, _ in node_configs}) != len(node_configs):
        raise IntegrationError("relocation profile agent ids must be distinct")
    records: list[dict[str, Any]] = []
    for agent_id, path in sorted(node_configs):
        if isinstance(agent_id, bool) or agent_id < 0:
            raise IntegrationError("relocation profile agent id must be nonnegative")
        try:
            raw = path.read_bytes()
            config = tomllib.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, ValueError) as error:
            raise IntegrationError("relocation Node source is not readable TOML") from error
        if config.get("schema") != "roboguide.node-config/v0.7":
            raise IntegrationError("relocation profile requires Node config v0.7")
        capability = _one(config.get("capability_profiles"), "contract", RELOCATION_OPERATION)
        operation = _one(config.get("operations"), "operation", RELOCATION_OPERATION)
        owner = capability.get("owner")
        local_system = _one(config.get("local_systems"), "id", owner)
        robot_type = local_system.get("metadata", {}).get("roboguide.habitat-robot-type")
        if not isinstance(robot_type, str) or not robot_type.strip():
            raise IntegrationError("relocation registration needs an explicit robot type")
        if capability.get("kind") != "transport" or operation.get("owner") != owner:
            raise IntegrationError("relocation capability and operation ownership differ")
        attributes = capability.get("attributes", {})
        if not isinstance(attributes, dict) or any(
            not isinstance(value, (str, int, float, bool)) for value in attributes.values()
        ):
            raise IntegrationError("relocation capability attributes must be typed scalar facts")
        readiness = capability.get("readiness", {})
        step = readiness.get("step", {})
        connection = _one(config.get("connections"), "id", step.get("connection"))
        if (
            readiness.get("state_pointer") != "/state"
            or readiness.get("ready") != ["READY"]
            or readiness.get("unavailable") != ["UNAVAILABLE"]
            or step.get("operation")
            != {"kind": "http", "method": "GET", "path": "/v1/capabilities/object.relocate@v1"}
            or step.get("request") != {"base": {}}
            or connection.get("local_system") != owner
        ):
            raise IntegrationError("relocation readiness must observe the exact local operation")
        resources = operation.get("required_resources")
        locks = operation.get("local_locks")
        if not isinstance(resources, list) or len(resources) != 1:
            raise IntegrationError("relocation needs one exclusive endpoint resource")
        resource = _one(config.get("resources"), "id", resources[0])
        if (
            resource.get("kind") != "space"
            or resource.get("capacity") != 1
            or isinstance(resource.get("capacity"), bool)
            or resource.get("owner") != owner
            or not isinstance(locks, list)
            or len(locks) != 1
            or not isinstance(locks[0], str)
            or not locks[0].strip()
        ):
            raise IntegrationError("relocation needs a capacity-one space slot and local lock")
        node_id = config.get("node_id")
        if not isinstance(node_id, str) or not node_id.strip():
            raise IntegrationError("relocation profile requires a Node identity")
        records.append(
            {
                "agent_id": agent_id,
                "node_id": node_id,
                "node_config_path": str(path.resolve()),
                "node_config_digest": _source_digest(path),
                "robot_type": robot_type,
                "operation": RELOCATION_OPERATION,
                "capability_attributes": attributes,
                "resource": {"kind": "space", "capacity": 1, "id": resources[0]},
                "local_lock": locks[0],
            }
        )
    if len({record["node_id"] for record in records}) != len(records):
        raise IntegrationError("relocation profile needs distinct Node identities")
    body = {"schema_version": RELOCATION_PROFILE_SCHEMA, "agents": records}
    return {**body, "digest": _digest(body)}


def load_relocation_profile(path: Path) -> dict[str, Any]:
    """Read the trusted frozen profile, rechecking schema, bounds and exact Node source bytes."""
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_PROFILE_BYTES:
            raise IntegrationError("relocation profile exceeds the byte budget")
        document = json.loads(raw)
    except IntegrationError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IntegrationError("relocation profile is not readable JSON") from error
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "agents", "digest"}
        or document["schema_version"] != RELOCATION_PROFILE_SCHEMA
        or document["digest"]
        != _digest({"schema_version": document["schema_version"], "agents": document["agents"]})
    ):
        raise IntegrationError("relocation profile schema or digest is invalid")
    records = document["agents"]
    if not isinstance(records, list) or not records or len(records) > 2:
        raise IntegrationError("relocation profile endpoint coverage is invalid")
    previous = -1
    nodes: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != {
            "agent_id",
            "node_id",
            "node_config_path",
            "node_config_digest",
            "robot_type",
            "operation",
            "capability_attributes",
            "resource",
            "local_lock",
        }:
            raise IntegrationError("relocation profile entry schema is invalid")
        agent_id = record["agent_id"]
        if (
            not isinstance(agent_id, int)
            or isinstance(agent_id, bool)
            or agent_id <= previous
            or not isinstance(record["node_id"], str)
            or not record["node_id"].strip()
            or record["node_id"] in nodes
            or not isinstance(record["robot_type"], str)
            or not record["robot_type"].strip()
            or record["operation"] != RELOCATION_OPERATION
            or not isinstance(record["node_config_path"], str)
            or not Path(record["node_config_path"]).is_absolute()
            or not isinstance(record["local_lock"], str)
            or not record["local_lock"].strip()
            or not isinstance(record["capability_attributes"], dict)
            or not isinstance(record["resource"], dict)
            or set(record["resource"]) != {"id", "kind", "capacity"}
            or record["resource"]["kind"] != "space"
            or record["resource"]["capacity"] != 1
            or isinstance(record["resource"]["capacity"], bool)
        ):
            raise IntegrationError("relocation profile identity or resource declaration is invalid")
        if record["node_config_digest"] != _source_digest(Path(record["node_config_path"])):
            raise IntegrationError("relocation Node source changed after profile freeze")
        previous = agent_id
        nodes.add(record["node_id"])
    return dict(document)


def verify_relocation_profile_sources(path: Path) -> None:
    """Rebuild under RoboGuide Python to prevent a rehashed profile from inventing Node facts."""
    document = load_relocation_profile(path)
    sources = tuple(
        (record["agent_id"], Path(record["node_config_path"])) for record in document["agents"]
    )
    if build_relocation_profile(sources) != document:
        raise IntegrationError("relocation profile differs from its actual Node registrations")


def verify_loaded_robots(
    profile: Mapping[str, Any], environment: Any, agent_ids: tuple[int, ...]
) -> None:
    """Require actual constructed robot types and exact endpoint coverage before readiness."""
    records = profile["agents"]
    if tuple(record["agent_id"] for record in records) != tuple(sorted(agent_ids)):
        raise IntegrationError("relocation profile does not cover the configured agents")
    for record in records:
        robot = environment.sim.get_agent_data(record["agent_id"]).articulated_agent
        if type(robot).__name__ != record["robot_type"]:
            raise IntegrationError("loaded Habitat robot differs from Node registration")


def main() -> None:
    """Build or verify one frozen relocation profile from exact Node config sources."""
    import argparse

    parser = argparse.ArgumentParser(description="Freeze Habitat relocation Node registration")
    parser.add_argument(
        "--node",
        action="append",
        required=True,
        metavar="AGENT_ID=NODE_CONFIG",
        help="one configured endpoint; repeat once for a dual-endpoint deployment",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-sources", action="store_true")
    arguments = parser.parse_args()
    pairs: list[tuple[int, Path]] = []
    for raw in arguments.node:
        if "=" not in raw:
            raise SystemExit("--node must use AGENT_ID=NODE_CONFIG")
        raw_agent, raw_path = raw.split("=", 1)
        try:
            agent_id = int(raw_agent)
        except ValueError as error:
            raise SystemExit("--node agent id must be an integer") from error
        pairs.append((agent_id, Path(raw_path)))
    if arguments.verify_sources:
        document = load_relocation_profile(arguments.output)
        expected = tuple(
            sorted(
                (record["agent_id"], Path(record["node_config_path"]).resolve())
                for record in document["agents"]
            )
        )
        supplied = tuple(sorted((agent_id, path.resolve()) for agent_id, path in pairs))
        if supplied != expected:
            raise SystemExit("--node sources do not match the frozen relocation profile")
        verify_relocation_profile_sources(arguments.output)
        return
    document = build_relocation_profile(tuple(pairs))
    arguments.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
