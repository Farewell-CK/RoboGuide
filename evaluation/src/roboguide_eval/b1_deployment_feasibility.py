"""Preflight one reset-state deployment policy before a B1 Mission is submitted."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import tomllib
from pathlib import Path
from typing import Any

from roboguide_eval.b1_provenance import (
    _semantic_expression_valid,
    _semantic_predicates,
    load_document,
)
from roboguide_eval.b1_workload import extract_b1_workload

_SCHEMA = "roboguide.deployment-intent-feasibility/v0.3"
_OPERATION_SCHEMA = "roboguide.deployment-intent-feasibility/v0.4"
_LIVE_SCHEMA = "roboguide.deployment-intent-feasibility/v0.5"


def _check_reset_source(document: dict[str, Any], schema: str) -> None:
    """Check the native bounded JSON identity before projecting archived source fields."""
    body = {key: value for key, value in document.items() if key != "digest"}
    encoded = json.dumps(
        body, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
    if document.get("schema_version") != schema or document.get("digest") != digest:
        raise ValueError("deployment operation admission reset source schema or digest is invalid")


def _validate_operation_sources(run: Path, document: dict[str, Any]) -> None:
    """Cross-bind the neutral profile to archived reset sources, never infer route feasibility."""
    admission = document["operation_admission"]
    start = load_document(run / "evidence/relocation-episode-start.json")
    profile = load_document(run / "relocation-registration-profile.json")
    if not all(isinstance(item, dict) for item in (admission, start, profile)):
        raise ValueError("deployment operation admission or its reset sources are missing")
    assert isinstance(start, dict)
    assert isinstance(profile, dict)
    assert isinstance(admission, dict)
    _check_reset_source(
        start,
        "roboguide.habitat-relocation-start/v0.2"
        if document["schema_version"] == _LIVE_SCHEMA
        else "roboguide.habitat-relocation-start/v0.1",
    )
    _check_reset_source(profile, "roboguide.habitat-relocation-profile/v0.1")
    try:
        objects, destinations, agents = start["objects"], start["destinations"], profile["agents"]
        if any(
            not isinstance(records, list)
            or not 1 <= len(records) <= 64
            or any(not isinstance(record, dict) for record in records)
            for records in (objects, destinations, agents)
        ):
            raise ValueError("reset source records are invalid")
        object_sources = {record["entity_id"]: record["source_entity_id"] for record in objects}
        destination_entities = [record["entity_id"] for record in destinations]
        endpoints = [
            {
                "agent_id": record["agent_id"],
                "node_id": record["node_id"],
                "node_config_digest": record["node_config_digest"],
                "resource_kind": record["resource"]["kind"],
                "resource_capacity": record["resource"]["capacity"],
            }
            for record in agents
        ]
        if len(object_sources) != len(objects) or any(
            record["operation"] != "object.relocate@v1"
            or record["resource"]["kind"] != "space"
            or type(record["resource"]["capacity"]) is not int
            or record["resource"]["capacity"] != 1
            for record in agents
        ):
            raise ValueError("reset source endpoint contract is invalid")
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(
            "deployment operation admission reset source records are invalid"
        ) from error
    expected = {
        "schema_version": "roboguide.deployment-operation-admission/v0.1",
        "operation": "object.relocate@v1",
        "parameter_names": ["destination", "object", "source"],
        "source_basis": "observed-initial-location",
        "source_snapshot_digest": start.get("digest"),
        "registration_profile_digest": profile.get("digest"),
        "object_sources": object_sources,
        "destination_entities": destination_entities,
        "endpoint_profiles": endpoints,
        "route_reachability": "unknown",
    }
    if (
        _content_digest(admission) != _content_digest(expected)
        or start.get("identity")
        != {
            key: document["identity"][key]
            for key in (
                "run_id",
                "episode_id",
                "scene_id",
                "dataset_revision",
                "dataset_sha256",
                "habitat_seed",
            )
        }
        or start.get("semantic_evidence_digest") != document["identity"]["semantic_evidence_digest"]
        or start.get("registration_profile_digest") != profile.get("digest")
        or start.get("complete") is not True
        or type(start.get("reset_count")) is not int
        or start["reset_count"] != 1
        or type(start.get("simulator_steps")) is not int
        or start["simulator_steps"] != 0
    ):
        raise ValueError("deployment operation admission differs from its actual reset sources")


def _validate_live_sources(run: Path, matrix: dict[str, Any]) -> None:
    """Cross-bind opt-in topology and operations to actual run-local Node configuration.

    Rehashed registry metadata cannot invent endpoint abilities. The child also
    verifies loaded classes and sensors; those startup facts are not a guarantee
    of motion, positive detection or official goal satisfaction.
    """
    registry = load_document(run / "endpoint-registry.json")
    used = load_document(run / "evidence/endpoint-registry-used.json")
    deployment = load_document(run / "b1-deployment-used.json")
    requirement = load_document(run / "b1-local-execution-profile-required.json")
    how = load_document(run / "evidence/local-how-profile.json")
    if not all(isinstance(item, dict) for item in (registry, used, deployment, requirement, how)):
        raise ValueError("live deployment sources are missing")
    _check_reset_source(registry, "roboguide.habitat-endpoint-registry/v0.1")
    records = registry.get("endpoints")
    if (
        registry != used
        or registry.get("profile") != "independent-live/v0.1"
        or not isinstance(records, list)
        or not 1 <= len(records) <= 4
        or deployment.get("schema_version") != "roboguide.e1.b1-deployment-used/v0.3"
        or deployment.get("declaration", {}).get("schema_version")
        != "roboguide.e1.b1-deployment/v0.3"
    ):
        raise ValueError("live deployment registry and declaration differ")
    declared = deployment["declaration"]
    expected_requirement = {
        "schema_version": "roboguide.e1.b1-local-execution-profile-required/v0.1",
        "run_id": run.name,
        "deployment_sha256": hashlib.sha256(
            (run / "b1-deployment-used.json").read_bytes()
        ).hexdigest(),
        "frozen_input_sha256": hashlib.sha256(
            (run / "b1-input-used.json").read_bytes()
        ).hexdigest(),
        "relocation_completion_binding": declared["relocation_completion_binding"],
    }
    if (
        requirement != expected_requirement
        or deployment.get("frozen_input_sha256") != expected_requirement["frozen_input_sha256"]
    ):
        raise ValueError("live deployment is not bound to the frozen workload")
    identities: dict[str, set[Any]] = {key: set() for key in ("node_id", "port", "resource_id")}
    for agent, record in enumerate(records):
        path = (run / f"node-{chr(97 + agent)}.toml").resolve()
        config = tomllib.loads(path.read_text())
        owners = config["local_systems"]
        resources = config["resources"]
        if len(owners) != 1 or len(resources) != 1:
            raise ValueError("live endpoint has an ambiguous local owner/resource")
        owner = owners[0]
        resource = resources[0]
        operations = config["operations"]
        connections = [
            item for item in config["connections"] if item["local_system"] == owner["id"]
        ]
        if len(connections) != 1 or not isinstance(record, dict):
            raise ValueError("live endpoint has an ambiguous connection")
        expected = {
            "agent_id": agent,
            "node_id": config["node_id"],
            "robot_type": owner["metadata"]["roboguide.habitat-robot-type"],
            "port": record["port"],
            "state_db": str((run / f"bridge-{agent}.sqlite3").resolve()),
            "node_config_path": str(path),
            "node_config_digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
            "resource_id": resource["id"],
            "operations": sorted(item["operation"] for item in operations),
        }
        if (
            record != expected
            or config.get("schema") != "roboguide.node-config/v0.7"
            or resource.get("owner") != owner["id"]
            or resource.get("kind") != "space"
            or type(resource.get("capacity")) is not int
            or resource["capacity"] != 1
            or type(record["port"]) is not int
            or not 1 <= record["port"] <= 65535
            or connections[0].get("driver") != "http"
            or connections[0].get("endpoint") != f"http://127.0.0.1:{record['port']}"
            or owner["metadata"].get("roboguide.habitat-execution-profile") != registry["profile"]
            or any(
                item.get("owner") != owner["id"]
                or item.get("required_resources") != [resource["id"]]
                for item in operations
            )
        ):
            raise ValueError("live registry differs from the actual Node declarations")
        for key, seen in identities.items():
            if record[key] in seen:
                raise ValueError("live endpoint identities overlap")
            seen.add(record[key])
    if deployment.get("node_ids") != [record["node_id"] for record in records] or len(
        declared.get("endpoints", [])
    ) != len(records):
        raise ValueError("live declaration endpoint coverage differs")
    expected_profile = {
        "mode": registry["profile"],
        "registry_digest": registry["digest"],
        "endpoints": [
            {key: record[key] for key in ("node_id", "agent_id", "operations")}
            for record in records
        ],
    }
    if matrix.get("execution_profile") != expected_profile:
        raise ValueError("live execution profile differs from frozen registry")
    bound_relocation = declared["relocation_completion_binding"]
    expected_base = {
        "schema_version": "roboguide.habitat-local-how-profile/v0.8"
        if bound_relocation
        else "roboguide.habitat-local-how-profile/v0.2",
        "navigation_point_resolver": "original-emos-oracle",
        "official_success_authority": "habitat-pddl",
        "stage2_execution_feedback_profile": "observed-local-skill-feedback/v0.4"
        if bound_relocation
        else "observed-local-skill-feedback/v0.1",
        "reset_route_support_enabled": False,
    }
    if bound_relocation:
        expected_base["relocation_completion_profile"] = "exact-object-released-place/v0.1"
    if (
        set(how)
        != {
            "schema_version",
            "base_profile",
            "execution_profile",
            "endpoint_registry_digest",
            "observation_enabled",
            "unassigned_policy",
            "official_success_authority",
        }
        or how["schema_version"] != "roboguide.habitat-local-how-profile/v0.9"
        or how["execution_profile"] != registry["profile"]
        or how["endpoint_registry_digest"] != registry["digest"]
        or how["observation_enabled"] is not declared["enable_observation"]
        or how["unassigned_policy"] != "original-wait-model-free"
        or how["official_success_authority"] != "habitat-pddl"
        or how["base_profile"] != expected_base
    ):
        raise ValueError("loaded Local How differs from live deployment")
    if declared["enable_observation"]:
        readiness = load_document(run / "evidence/perception-readiness.json")
        if (
            readiness.get("enabled") is not True
            or readiness.get("source") != "loaded-original-sensors-and-cameras"
            or set(readiness.get("sensors", {})) != {str(agent) for agent in range(len(records))}
            or any(
                key not in ("detected_objects", f"agent_{agent}_detected_objects")
                for agent, key in readiness["sensors"].items()
            )
        ):
            raise ValueError("loaded perception readiness is unavailable")


def _canonical_digest_value(value: Any) -> Any:
    """Normalize floats to exact IEEE-754 bits before cross-language hashing."""
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("preassignment feasibility contains a non-finite float")
        return {"$f64_bits": struct.pack(">d", value).hex()}
    if isinstance(value, list):
        return [_canonical_digest_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _canonical_digest_value(item) for key, item in value.items()}
    return value


def _content_digest(body: dict[str, Any]) -> str:
    """Calculate the v0.3 content identity without float formatting drift."""
    encoded = json.dumps(
        _canonical_digest_value(body),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _goal_occupancy(semantic: dict[str, Any], destination: str) -> str:
    """Classify an exact destination from the frozen neutral goal expression."""
    goal = semantic.get("goal")
    if not _semantic_expression_valid(goal):
        raise ValueError("authoritative semantic goal is unavailable")
    matches = [
        predicate
        for predicate in _semantic_predicates(goal)
        if destination in predicate["arguments"]
    ]
    if not matches:
        return "none"
    if all(
        predicate["name"] == "any_at" and predicate["arguments"] == [destination]
        for predicate in matches
    ):
        return "any_at"
    return "other"


def preflight_deployment_feasibility(run: Path) -> dict[str, Any]:
    """Bind the child reset artifact to this run's frozen workload and sources.

    This check authenticates archive consistency, not the truth of a simulator
    observation. Control revalidates the decision shape and intersects it with
    current Node registration when accepting a Mission.
    """
    workload = extract_b1_workload(load_document(run / "b1-input-used.json"))
    document = load_document(run / "evidence/preassignment-feasibility.json")
    semantic = load_document(run / "evidence/authoritative-semantic-evidence.json")
    profile = load_document(run / "spatial-profile.json")
    if not all(isinstance(item, dict) for item in (document, semantic, profile)):
        raise ValueError("preassignment feasibility or an identity source is missing")
    assert isinstance(document, dict)
    assert isinstance(semantic, dict)
    assert isinstance(profile, dict)
    fields = {
        "schema_version",
        "authority",
        "identity",
        "initial_agent_positions",
        "records",
        "digest",
    }
    schema = document.get("schema_version")
    if schema == _OPERATION_SCHEMA or (
        schema == _LIVE_SCHEMA and "operation_admission" in document
    ):
        fields.add("operation_admission")
    if schema == _LIVE_SCHEMA:
        fields.add("execution_profile")
    if set(document) != fields or schema not in {_SCHEMA, _OPERATION_SCHEMA, _LIVE_SCHEMA}:
        raise ValueError("preassignment feasibility schema is invalid")
    claimed = document["digest"]
    body = {key: value for key, value in document.items() if key != "digest"}
    actual = _content_digest(body)
    if claimed != actual:
        raise ValueError("preassignment feasibility digest does not match content")
    identity = document["identity"]
    if not isinstance(identity, dict) or set(identity) != {
        "run_id",
        "episode_id",
        "scene_id",
        "dataset_revision",
        "dataset_sha256",
        "semantic_evidence_digest",
        "spatial_profile_digest",
        "habitat_seed",
        "episode_reset_count",
    }:
        raise ValueError("preassignment feasibility identity is invalid")
    expected = {
        "run_id": run.name,
        "episode_id": workload.episode_id,
        "scene_id": workload.scene_id,
        "dataset_revision": workload.dataset_revision,
        "dataset_sha256": workload.dataset_sha256,
        "semantic_evidence_digest": semantic.get("digest"),
        "spatial_profile_digest": profile.get("digest"),
        "habitat_seed": workload.seed,
        "episode_reset_count": 1,
    }
    if identity != expected:
        raise ValueError("preassignment feasibility differs from frozen B1 identity")
    if schema == _LIVE_SCHEMA:
        _validate_live_sources(run, document)
    if "operation_admission" in document:
        _validate_operation_sources(run, document)
    if not isinstance(document["records"], list) or not document["records"]:
        raise ValueError("preassignment feasibility has no candidate records")
    for record in document["records"]:
        if not isinstance(record, dict):
            raise ValueError("preassignment feasibility record is invalid")
        destination = record.get("destination")
        claimed = record.get("goal_occupancy")
        if (
            not isinstance(destination, str)
            or not destination.strip()
            or claimed
            not in {
                "none",
                "any_at",
                "other",
                "unavailable",
            }
        ):
            raise ValueError("preassignment feasibility goal occupancy is invalid")
        expected_goal = _goal_occupancy(semantic, destination)
        if claimed not in {expected_goal, "unavailable"}:
            raise ValueError("preassignment feasibility goal differs from semantic evidence")
        if expected_goal == "any_at" and record.get("status") == "incompatible":
            raise ValueError("preassignment feasibility excludes an official distance goal")
    return document


def main() -> None:
    """Check one run-local artifact without invoking Habitat or a Provider."""
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--require-route-support", action="store_true")
    parser.add_argument("--require-route-geometry", action="store_true")
    args = parser.parse_args()
    preflight_deployment_feasibility(args.run)
    if args.require_route_support or args.require_route_geometry:
        from roboguide_eval.b1_reset_route_support import preflight_reset_route_support

        preflight_reset_route_support(args.run, require_geometry=args.require_route_geometry)


if __name__ == "__main__":
    main()
