"""Project reset-bound canonical operation admission, separately from route feasibility."""

from __future__ import annotations

from typing import Any

from .model import RELOCATION_OPERATION, IntegrationError
from .preassignment_feasibility import PREASSIGNMENT_FEASIBILITY_SCHEMA, preassignment_digest
from .relocation_deployment import RELOCATION_PROFILE_SCHEMA
from .relocation_deployment import _digest as registration_digest
from .relocation_start import validate_relocation_start

OPERATION_ADMISSION_SCHEMA = "roboguide.deployment-operation-admission/v0.1"
OPERATION_FEASIBILITY_SCHEMA = "roboguide.deployment-intent-feasibility/v0.4"


def relocation_operation_admission(
    navigation: dict[str, Any],
    semantic: dict[str, Any],
    start: dict[str, Any],
    registration: dict[str, Any],
) -> dict[str, Any]:
    """Bind exact reset sources and configured endpoints without asserting a manipulation route.

    Inputs are the already observed world and startup-validated Node profile.
    No environment, model, action, predicate, RNG or live registration is queried.
    The navigation matrix keeps its existing decision rules and coverage; this
    separate operation profile never turns those observations into relocation
    feasibility. Control still checks current readiness and owns commitments.
    """
    validate_relocation_start(start, semantic)
    navigation_body = {key: value for key, value in navigation.items() if key != "digest"}
    if (
        navigation.get("schema_version") != PREASSIGNMENT_FEASIBILITY_SCHEMA
        or navigation.get("digest") != preassignment_digest(navigation_body)
        or registration.get("schema_version") != RELOCATION_PROFILE_SCHEMA
        or registration.get("digest")
        != registration_digest(
            {key: value for key, value in registration.items() if key != "digest"}
        )
        or start["registration_profile_digest"] != registration["digest"]
    ):
        raise IntegrationError("operation admission source schema or digest is invalid")
    identity = navigation.get("identity")
    if (
        not isinstance(identity, dict)
        or {key: identity.get(key) for key in start["identity"]} != start["identity"]
        or identity.get("semantic_evidence_digest") != start["semantic_evidence_digest"]
        or identity.get("episode_reset_count") != 1
        or type(identity.get("episode_reset_count")) is not int
        or not isinstance(navigation.get("initial_agent_positions"), dict)
        or any(
            navigation["initial_agent_positions"].get(str(agent["agent_id"])) != agent["position"]
            for agent in start["agents"]
        )
        or (
            start["schema_version"] != "roboguide.habitat-relocation-start/v0.2"
            and set(navigation["initial_agent_positions"])
            != {str(agent["agent_id"]) for agent in start["agents"]}
        )
    ):
        raise IntegrationError("operation admission differs from the navigation reset identity")
    endpoints = []
    for profile in registration["agents"]:
        if (
            profile["operation"] != RELOCATION_OPERATION
            or profile["resource"]["kind"] != "space"
            or profile["resource"]["capacity"] != 1
            or type(profile["resource"]["capacity"]) is not int
        ):
            raise IntegrationError("operation admission requires an exclusive relocation endpoint")
        records = [
            record for record in navigation["records"] if record["node_id"] == profile["node_id"]
        ]
        if not records or any(
            record["agent_id"] != profile["agent_id"]
            or record["profile"]["source_digest"] != profile["node_config_digest"]
            for record in records
        ):
            raise IntegrationError("operation admission endpoint differs from its Node source")
        endpoints.append(
            {
                "agent_id": profile["agent_id"],
                "node_id": profile["node_id"],
                "node_config_digest": profile["node_config_digest"],
                "resource_kind": "space",
                "resource_capacity": 1,
            }
        )
    covered_nodes = {record["node_id"] for record in navigation["records"]}
    endpoint_nodes = {endpoint["node_id"] for endpoint in endpoints}
    if (
        not endpoint_nodes.issubset(covered_nodes)
        if start["schema_version"] == "roboguide.habitat-relocation-start/v0.2"
        else covered_nodes != endpoint_nodes
    ) or [endpoint["agent_id"] for endpoint in endpoints] != [
        agent["agent_id"] for agent in start["agents"]
    ]:
        raise IntegrationError("operation admission endpoint coverage is invalid")
    return {
        "schema_version": OPERATION_ADMISSION_SCHEMA,
        "operation": RELOCATION_OPERATION,
        "parameter_names": ["destination", "object", "source"],
        "source_basis": "observed-initial-location",
        "source_snapshot_digest": start["digest"],
        "registration_profile_digest": registration["digest"],
        "object_sources": {
            record["entity_id"]: record["source_entity_id"] for record in start["objects"]
        },
        "destination_entities": [record["entity_id"] for record in start["destinations"]],
        "endpoint_profiles": endpoints,
        "route_reachability": "unknown",
    }


def attach_relocation_admission(
    navigation: dict[str, Any],
    semantic: dict[str, Any],
    start: dict[str, Any],
    registration: dict[str, Any],
) -> dict[str, Any]:
    """Version the combined source so persisted restrictions bind both independent profiles."""
    admission = relocation_operation_admission(navigation, semantic, start, registration)
    body = {key: value for key, value in navigation.items() if key != "digest"}
    body.update(schema_version=OPERATION_FEASIBILITY_SCHEMA, operation_admission=admission)
    return {**body, "digest": preassignment_digest(body)}
