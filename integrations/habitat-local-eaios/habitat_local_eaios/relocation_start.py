"""Bounded initial object-location evidence for deployment-owned relocation admission."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from .model import CanonicalRelocationInvocation, IntegrationError
from .planning_world_evidence import _vector

RELOCATION_START_SCHEMA = "roboguide.habitat-relocation-start/v0.1"
MAX_ENTITIES = 64
MAX_ARTIFACT_BYTES = 65536
_SHA256_LENGTH = len("sha256:") + 64


def _digest(value: object) -> str:
    """Identify one finite, bounded JSON body without including its digest field."""
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
    except (TypeError, ValueError) as error:
        raise IntegrationError("relocation start must contain finite JSON values") from error
    if len(encoded.encode("utf-8")) > MAX_ARTIFACT_BYTES:
        raise IntegrationError("relocation start evidence exceeds the byte budget")
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def source_location_id(
    identity: Mapping[str, Any], object_id: str, position: Sequence[float]
) -> str:
    """Name an actual observed location, not an object alias or an invented receptacle."""
    return "initial-location:" + _digest(
        {
            "identity": dict(identity),
            "object_entity_id": object_id,
            "position": _vector(position),
        }
    ).removeprefix("sha256:")


def _identity(semantic: Mapping[str, Any], seed: int | None) -> dict[str, Any]:
    """Bind the source snapshot to environment-owned episode and dataset identity."""
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise IntegrationError("relocation start seed must be an integer or unknown")
    identity = semantic["identity"]
    return {
        "run_id": identity["run_id"],
        "episode_id": identity["episode_id"],
        "scene_id": semantic["world_context"]["scene_id"],
        "dataset_revision": identity["dataset_revision"],
        "dataset_sha256": identity["dataset_sha256"],
        "habitat_seed": seed,
    }


def _agent(environment: Any, agent_id: int) -> dict[str, Any]:
    """Observe reset pose and empty grasp state using existing read-only properties."""
    record: dict[str, Any] = {
        "agent_id": agent_id,
        "status": "unavailable",
        "position": None,
        "rotation_yaw_rad": None,
        "grasp_empty": None,
        "reason": None,
    }
    try:
        data = environment.sim.get_agent_data(agent_id)
        record["position"] = _vector(data.articulated_agent.base_pos)
        try:
            rotation = getattr(data.articulated_agent, "base_rot", None)
            if (
                not isinstance(rotation, bool)
                and rotation is not None
                and math.isfinite(float(rotation))
            ):
                record["rotation_yaw_rad"] = float(rotation)
        except Exception:  # noqa: BLE001 - optional yaw unavailable does not invent a rotation
            pass
        managers = data.grasp_mgrs
        if not isinstance(managers, Sequence) or not managers:
            raise ValueError("grasp observations unavailable")
        states = [manager.is_grasped for manager in managers]
        if not all(isinstance(value, bool) for value in states):
            raise ValueError("grasp observations are not boolean")
        record["grasp_empty"] = not any(states)
        if not record["grasp_empty"]:
            raise ValueError("initial relocation requires an empty gripper")
        record["status"] = "observed"
    except Exception as error:  # noqa: BLE001 - missing observations remain explicit, never inferred
        record["reason"] = type(error).__name__
    return record


def build_relocation_start(
    environment: Any,
    semantic: Mapping[str, Any],
    *,
    seed: int | None,
    registration_digest: str,
    agent_ids: tuple[int, ...],
) -> dict[str, Any]:
    """Read the existing reset world once; never reset, step, compute predicates or sample RNG.

    An initial-location reference denotes the exact observed position at step
    zero. It does not assert support-surface membership, a route or future truth.
    Missing object/goal positions and nonempty/unknown grasp state fail readiness.
    """
    if (
        not isinstance(registration_digest, str)
        or not registration_digest.startswith("sha256:")
        or len(registration_digest) != _SHA256_LENGTH
        or any(char not in "0123456789abcdef" for char in registration_digest[7:])
    ):
        raise IntegrationError("relocation registration digest is invalid")
    identity = _identity(semantic, seed)
    episode = environment.current_episode
    if (
        str(episode.episode_id) != identity["episode_id"]
        or episode.scene_id != identity["scene_id"]
    ):
        raise IntegrationError("relocation start world differs from semantic evidence")
    catalog = semantic["world_context"]["entity_catalog"]
    if (
        not isinstance(catalog, list)
        or not catalog
        or len(catalog) > MAX_ENTITIES
        or any(not isinstance(name, str) or not name.strip() for name in catalog)
        or len(set(catalog)) != len(catalog)
    ):
        raise IntegrationError("relocation start entity catalog exceeds the supported budget")
    problem = environment.task.pddl_problem
    objects: list[dict[str, Any]] = []
    destinations: list[dict[str, Any]] = []
    gaps: list[dict[str, str]] = []
    for name in sorted(catalog):
        try:
            entity = problem.get_entity(name)
            if entity is None:
                raise ValueError("exact entity is unavailable")
            movable = problem.sim_info.check_type_matches(entity, "movable_entity_type")
            destination = problem.sim_info.check_type_matches(
                entity, "goal_entity_type"
            ) or problem.sim_info.check_type_matches(entity, "static_receptacle_entity_type")
            if not movable and not destination:
                continue
            record: dict[str, Any] = {
                "entity_id": name,
                "status": "unavailable",
                "position": None,
                "reason": None,
            }
            if movable:
                record["source_entity_id"] = None
            target = objects if movable else destinations
            target.append(record)
            position = _vector(problem.sim_info.get_entity_pos(entity))
            record.update(status="observed", position=position)
            if movable:
                record["source_entity_id"] = source_location_id(identity, name, position)
        except Exception as error:  # noqa: BLE001 - one read fault cannot manufacture a source
            gaps.append({"entity_id": name, "reason": type(error).__name__})
    if (
        not agent_ids
        or len(agent_ids) > 2
        or any(
            not isinstance(agent_id, int) or isinstance(agent_id, bool) or agent_id < 0
            for agent_id in agent_ids
        )
        or len(set(agent_ids)) != len(agent_ids)
        or sorted(agent_ids) != semantic["world_context"]["agent_ids"]
    ):
        raise IntegrationError("relocation start agent coverage is invalid")
    agents = [_agent(environment, agent_id) for agent_id in sorted(agent_ids)]
    body = {
        "schema_version": RELOCATION_START_SCHEMA,
        "authority": "environment-authoritative",
        "identity": identity,
        "reset_count": 1,
        "simulator_steps": 0,
        "semantic_evidence_digest": semantic["digest"],
        "registration_profile_digest": registration_digest,
        "source_basis": "observed-initial-location",
        "agents": agents,
        "objects": objects,
        "destinations": destinations,
        "gaps": gaps,
        "complete": bool(objects and destinations)
        and not gaps
        and all(record["status"] == "observed" for record in [*agents, *objects, *destinations]),
    }
    return {**body, "digest": _digest(body)}


def validate_relocation_start(document: Mapping[str, Any], semantic: Mapping[str, Any]) -> None:
    """Reject tampering, wrong-world sources and rehashed aliases before MI or execution."""
    if (
        set(document)
        != {
            "schema_version",
            "authority",
            "identity",
            "reset_count",
            "simulator_steps",
            "semantic_evidence_digest",
            "registration_profile_digest",
            "source_basis",
            "agents",
            "objects",
            "destinations",
            "gaps",
            "complete",
            "digest",
        }
        or document["schema_version"] != RELOCATION_START_SCHEMA
    ):
        raise IntegrationError("relocation start schema is invalid")
    if document["digest"] != _digest(
        {key: value for key, value in document.items() if key != "digest"}
    ):
        raise IntegrationError("relocation start digest does not match content")
    if (
        document["authority"] != "environment-authoritative"
        or document["source_basis"] != "observed-initial-location"
        or document["reset_count"] != 1
        or isinstance(document["reset_count"], bool)
        or document["simulator_steps"] != 0
        or isinstance(document["simulator_steps"], bool)
        or not isinstance(document["identity"], dict)
        or document["identity"] != _identity(semantic, document["identity"].get("habitat_seed"))
        or document["semantic_evidence_digest"] != semantic["digest"]
        or document["complete"] is not True
        or document["gaps"] != []
    ):
        raise IntegrationError("relocation start is incomplete or has a different world identity")
    registration_digest = document["registration_profile_digest"]
    if (
        not isinstance(registration_digest, str)
        or not registration_digest.startswith("sha256:")
        or len(registration_digest) != _SHA256_LENGTH
        or any(char not in "0123456789abcdef" for char in registration_digest[7:])
    ):
        raise IntegrationError("relocation registration digest is invalid")
    ids: set[str] = set()
    source_ids: set[str] = set()
    for name in ("objects", "destinations"):
        records = document[name]
        if not isinstance(records, list) or not records or len(records) > MAX_ENTITIES:
            raise IntegrationError("relocation start records are missing or over budget")
        previous = ""
        for record in records:
            expected = {"entity_id", "status", "position", "reason"}
            if name == "objects":
                expected.add("source_entity_id")
            if not isinstance(record, dict) or set(record) != expected:
                raise IntegrationError("relocation start record schema is invalid")
            entity_id = record["entity_id"]
            if (
                not isinstance(entity_id, str)
                or entity_id <= previous
                or entity_id in ids
                or entity_id not in semantic["world_context"]["entity_catalog"]
                or record["status"] != "observed"
                or record["reason"] is not None
            ):
                raise IntegrationError("relocation start entity identity or observation is invalid")
            ids.add(entity_id)
            previous = entity_id
            position = _vector(record["position"])
            if name == "objects":
                source = record["source_entity_id"]
                if (
                    source != source_location_id(document["identity"], entity_id, position)
                    or source in source_ids
                ):
                    raise IntegrationError(
                        "relocation source does not identify its observed initial location"
                    )
                source_ids.add(source)
    agents = document["agents"]
    expected_agents = semantic["world_context"]["agent_ids"]
    if (
        not isinstance(agents, list)
        or len(agents) != len(expected_agents)
        or any(not isinstance(record, dict) for record in agents)
        or [record.get("agent_id") for record in agents] != expected_agents
    ):
        raise IntegrationError("relocation start agent coverage is invalid")
    for record in agents:
        if (
            set(record)
            != {"agent_id", "status", "position", "rotation_yaw_rad", "grasp_empty", "reason"}
            or record["status"] != "observed"
            or record["grasp_empty"] is not True
            or record["reason"] is not None
            or not isinstance(record["agent_id"], int)
            or isinstance(record["agent_id"], bool)
        ):
            raise IntegrationError("relocation start requires observed empty grasp state")
        _vector(record["position"])
        rotation = record["rotation_yaw_rad"]
        if rotation is not None and (
            isinstance(rotation, bool)
            or not isinstance(rotation, (int, float))
            or not math.isfinite(rotation)
        ):
            raise IntegrationError("relocation start rotation is invalid")


def planning_object_sources(
    document: Mapping[str, Any], semantic: Mapping[str, Any]
) -> list[dict[str, str]]:
    """Project only neutral, digest-bound initial source references into MI planning input."""
    validate_relocation_start(document, semantic)
    return [
        {
            "object_entity_id": record["entity_id"],
            "source_entity_id": record["source_entity_id"],
            "evidence_digest": document["digest"],
            "basis": "observed-initial-location",
        }
        for record in document["objects"]
    ]


def admit_relocation_source(
    environment: Any,
    invocation: CanonicalRelocationInvocation,
    agent_id: int,
    document: Mapping[str, Any],
    semantic: Mapping[str, Any],
) -> None:
    """Check the unedited source and current object before Stage2 can act on an assignment."""
    validate_relocation_start(document, semantic)
    matches = [
        record for record in document["objects"] if record["entity_id"] == invocation.object_ref
    ]
    if len(matches) != 1 or invocation.source != matches[0]["source_entity_id"]:
        raise IntegrationError(
            "relocation source is not the observed initial location of this object"
        )
    destinations = [
        record
        for record in document["destinations"]
        if record["entity_id"] == invocation.destination
    ]
    if len(destinations) != 1:
        raise IntegrationError("relocation destination is not an observed environment location")
    episode = environment.current_episode
    if (
        str(episode.episode_id) != document["identity"]["episode_id"]
        or episode.scene_id != document["identity"]["scene_id"]
    ):
        raise IntegrationError("relocation world changed after source capture")
    entity = environment.task.pddl_problem.get_entity(invocation.object_ref)
    if entity is None:
        raise IntegrationError("relocation object is unavailable in the current world")
    position = _vector(environment.task.pddl_problem.sim_info.get_entity_pos(entity))
    if position != matches[0]["position"]:
        raise IntegrationError("relocation object moved away from its frozen initial source")
    destination = environment.task.pddl_problem.get_entity(invocation.destination)
    if destination is None:
        raise IntegrationError("relocation destination is unavailable in the current world")
    destination_position = _vector(
        environment.task.pddl_problem.sim_info.get_entity_pos(destination)
    )
    if destination_position != destinations[0]["position"]:
        raise IntegrationError("relocation destination changed after source capture")
    if agent_id not in {record["agent_id"] for record in document["agents"]}:
        raise IntegrationError("relocation assignment uses an unobserved agent")
    if _agent(environment, agent_id)["status"] != "observed":
        raise IntegrationError("relocation starts with an unavailable or nonempty gripper")
