"""Read-only spatial feasibility checks at the Habitat execution boundary.

The check deliberately proves only a negative fact: a deployment capability
that cannot cross floors must not be assigned a destination on another floor.
It never computes a route, changes a task, chooses another agent, or claims
that a positive result proves navigability.  Unknown observations remain
explicit in the run evidence and retain the existing Local EAIOS behaviour.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .evidence_io import write_text_atomic
from .model import SUPPORTED_OPERATIONS, CanonicalMobilityInvocation, IntegrationError
from .planning_world_evidence import _region_location

SPATIAL_FEASIBILITY_SCHEMA = "roboguide.habitat-spatial-feasibility/v0.1"
SPATIAL_PROFILE_SCHEMA = "roboguide.habitat-node-spatial-profile/v0.1"


@dataclass(frozen=True)
class FloorTransitionProfile:
    """Carry one deployment-owned capability fact into the local adapter.

    Each canonical operation retains its own registration fact. A missing
    boolean is unknown, and the Node-config digest binds the supplied facts
    to the actual deployment source.
    """

    agent_id: int
    operation_support: tuple[tuple[str, bool | None], ...]
    source_digest: str

    def __post_init__(self) -> None:
        """Reject malformed deployment capability identity before execution."""
        if self.agent_id < 0:
            raise ValueError("agent_id must be non-negative")
        if (
            not self.source_digest.startswith("sha256:")
            or len(self.source_digest) != len("sha256:") + 64
            or any(character not in "0123456789abcdef" for character in self.source_digest[7:])
        ):
            raise ValueError("source_digest must be a lowercase sha256 digest")
        if tuple(name for name, _ in self.operation_support) != tuple(sorted(SUPPORTED_OPERATIONS)):
            raise ValueError("operation_support must cover each supported mobility operation")
        if any(
            support is not None and not isinstance(support, bool)
            for _, support in self.operation_support
        ):
            raise ValueError("floor-transition support must be boolean or unknown")

    def support_for(self, operation: str) -> bool | None:
        """Return the exact declared floor-transition fact for one operation."""
        return next(value for name, value in self.operation_support if name == operation)

    def as_dict(self) -> dict[str, object]:
        """Return the exact profile fact for durable evidence."""
        return {
            "agent_id": self.agent_id,
            "source_digest": self.source_digest,
            "operation_support": dict(self.operation_support),
        }


def build_spatial_profile_snapshot(
    node_configs: tuple[tuple[int, Path], ...],
) -> dict[str, object]:
    """Freeze typed floor facts from the exact Node configs used by a run.

    This builder runs under the RoboGuide Python toolchain, where ``tomllib``
    is available. The Habitat Python 3.9 child consumes only the resulting
    strict JSON snapshot and rechecks the source-file digests at startup.
    """
    import tomllib

    if not node_configs or len({agent_id for agent_id, _ in node_configs}) != len(node_configs):
        raise IntegrationError("spatial profile needs distinct configured agent ids")
    agents: list[dict[str, object]] = []
    for agent_id, path in sorted(node_configs):
        raw = path.read_bytes()
        config = tomllib.loads(raw.decode("utf-8"))
        if config.get("schema") != "roboguide.node-config/v0.7":
            raise IntegrationError("spatial profile requires Node config v0.7")
        node_id = config.get("node_id")
        if not isinstance(node_id, str) or not node_id.strip():
            raise IntegrationError("spatial profile needs a nonblank Node identity")
        profiles = config.get("capability_profiles", [])
        if not isinstance(profiles, list):
            raise IntegrationError("Node capability_profiles must be an array")
        if any(not isinstance(item, dict) for item in profiles):
            raise IntegrationError("Node capability profile entries must be objects")
        support: dict[str, bool | None] = {}
        for operation in sorted(SUPPORTED_OPERATIONS):
            matches = [item for item in profiles if item.get("contract") == operation]
            if len(matches) > 1:
                raise IntegrationError(f"duplicate Node capability profile {operation!r}")
            if len(matches) != 1:
                raise IntegrationError(f"Node does not register capability {operation!r}")
            attributes = matches[0].get("attributes", {}) if matches else {}
            if not isinstance(attributes, dict):
                raise IntegrationError("Node capability attributes must be an object")
            value = attributes.get("supports-floor-transition")
            if value is not None and not isinstance(value, bool):
                raise IntegrationError("Node floor-transition support must be boolean")
            support[operation] = value
        agents.append(
            {
                "agent_id": agent_id,
                "node_id": node_id,
                "node_config_path": str(path.resolve()),
                "node_config_digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                "operation_support": support,
            }
        )
    body = cast(dict[str, object], {"schema_version": SPATIAL_PROFILE_SCHEMA, "agents": agents})
    return {**body, "digest": _digest(body)}


def load_spatial_profile_snapshot(path: Path) -> tuple[FloorTransitionProfile, ...]:
    """Validate one startup-frozen JSON profile against original Node files."""
    try:
        raw_snapshot = path.read_bytes()
        document = json.loads(raw_snapshot)
        if not isinstance(document, dict) or set(document) != {
            "schema_version",
            "agents",
            "digest",
        }:
            raise ValueError("spatial profile envelope is invalid")
        if document["schema_version"] != SPATIAL_PROFILE_SCHEMA:
            raise ValueError("spatial profile schema is unsupported")
        claimed_digest = document["digest"]
        body = cast(
            dict[str, object],
            {key: value for key, value in document.items() if key != "digest"},
        )
        if not isinstance(claimed_digest, str) or claimed_digest != _digest(body):
            raise ValueError("spatial profile digest does not match content")
        agents = document["agents"]
        if not isinstance(agents, list) or not agents:
            raise ValueError("spatial profile agent list is empty")
        profiles: list[FloorTransitionProfile] = []
        for item in agents:
            if not isinstance(item, dict) or set(item) != {
                "agent_id",
                "node_id",
                "node_config_path",
                "node_config_digest",
                "operation_support",
            }:
                raise ValueError("spatial profile agent fields are invalid")
            agent_id = item["agent_id"]
            source_path = item["node_config_path"]
            digest = item["node_config_digest"]
            support = item["operation_support"]
            if (
                not isinstance(agent_id, int)
                or isinstance(agent_id, bool)
                or not isinstance(source_path, str)
                or not isinstance(digest, str)
                or not isinstance(item["node_id"], str)
                or not item["node_id"]
                or not isinstance(support, dict)
                or set(support) != set(SUPPORTED_OPERATIONS)
            ):
                raise ValueError("spatial profile identity or operation support is invalid")
            source_bytes = Path(source_path).read_bytes()
            if "sha256:" + hashlib.sha256(source_bytes).hexdigest() != digest:
                raise ValueError("Node config changed after spatial profile was frozen")
            profiles.append(
                FloorTransitionProfile(
                    agent_id,
                    tuple(sorted(support.items())),
                    digest,
                )
            )
        if len({profile.agent_id for profile in profiles}) != len(profiles):
            raise ValueError("spatial profile repeats an agent id")
        return tuple(sorted(profiles, key=lambda item: item.agent_id))
    except (OSError, TypeError, ValueError, KeyError) as error:
        raise IntegrationError(f"cannot admit deployment spatial profile: {error}") from error


def assess_spatial_feasibility(
    habitat_env: Any,
    agent_id: int,
    invocation: CanonicalMobilityInvocation,
    profile: FloorTransitionProfile | None,
) -> dict[str, object]:
    """Assess floor compatibility without stepping or mutating Habitat.

    The destination position is read through the official PDDL ``sim_info``
    entity lookup, while floor identity is read from loaded semantic-region
    AABBs.  If either position has no unique region, the result is ``unknown``.
    A different floor is a deterministic incompatibility only when the
    deployment registration explicitly says that this agent cannot transition
    floors.  A capable agent receives ``compatible`` as an admission result,
    while the evidence still states that route reachability was not proven.
    """
    record: dict[str, object] = {
        "schema_version": SPATIAL_FEASIBILITY_SCHEMA,
        "agent_id": agent_id,
        "destination": invocation.destination,
        "operation": invocation.operation,
        "profile": profile.as_dict() if profile is not None else None,
        "status": "unknown",
        "reason": "spatial evidence unavailable",
        "start": {"position": None, "region_id": None, "floor_id": None},
        "destination_entity": {
            "position": None,
            "region_id": None,
            "floor_id": None,
        },
        "route_reachability_proven": False,
    }
    if profile is None:
        record["reason"] = "deployment floor-transition profile unavailable"
        return record
    try:
        problem = habitat_env.task.pddl_problem
        sim_info = problem.sim_info
        entity = problem.get_entity(invocation.destination)
        if entity is None:
            raise IntegrationError(f"destination entity {invocation.destination!r} is unresolved")
        destination_position = _vector(sim_info.get_entity_pos(entity))
        start_position = _vector(
            habitat_env.sim.get_agent_data(profile.agent_id).articulated_agent.base_pos
        )
        regions = list(habitat_env.sim.semantic_scene.regions)
        start_location = _region_location(start_position, regions)
        destination_location = _region_location(destination_position, regions)
        record["start"] = _location_record(start_position, start_location)
        record["destination_entity"] = _location_record(destination_position, destination_location)
        if start_location is None or destination_location is None:
            record["reason"] = "start or destination has no unique semantic floor"
            return record
        start_floor = start_location[1]
        destination_floor = destination_location[1]
        if start_floor == destination_floor:
            record["status"] = "compatible"
            record["reason"] = "start and destination are on the same semantic floor"
            return record
        supports_transition = profile.support_for(invocation.operation)
        if supports_transition is False:
            record["status"] = "incompatible"
            record["reason"] = "registered agent cannot transition between semantic floors"
            return record
        if supports_transition is True:
            record["status"] = "compatible"
            record["reason"] = "registered agent supports floor transition"
            return record
        record["reason"] = "registration did not provide a floor-transition capability boolean"
        return record
    except Exception as error:  # noqa: BLE001 - unknown evidence must be explicit
        record["reason"] = f"spatial evidence read failed: {type(error).__name__}: {error}"
        return record


def _location_record(position: list[float], location: tuple[str, str] | None) -> dict[str, object]:
    """Serialize one observed position and its optional semantic location."""
    return {
        "position": position,
        "region_id": location[0] if location is not None else None,
        "floor_id": location[1] if location is not None else None,
    }


def _vector(value: Any) -> list[float]:
    """Convert one finite three-vector returned by Habitat or PDDL."""
    values = value.tolist() if callable(getattr(value, "tolist", None)) else value
    if all(hasattr(values, component) for component in ("x", "y", "z")):
        values = [getattr(values, component) for component in ("x", "y", "z")]
    if not isinstance(values, (list, tuple)) or len(values) != 3:
        raise ValueError("spatial position must contain three coordinates")
    if any(isinstance(component, bool) for component in values):
        raise ValueError("spatial position must be numeric")
    result = [float(component) for component in values]
    if not all(math.isfinite(component) for component in result):
        raise ValueError("spatial position must be finite")
    return result


def _digest(value: dict[str, object]) -> str:
    """Return the canonical digest for one finite profile document."""
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def main() -> None:
    """Build an exact run-local Node registration snapshot with Python 3.11+."""
    parser = argparse.ArgumentParser(description="Freeze Node spatial capability facts")
    parser.add_argument("--node-a", type=Path, required=True)
    parser.add_argument("--node-b", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    document = build_spatial_profile_snapshot(((0, arguments.node_a), (1, arguments.node_b)))
    write_text_atomic(arguments.output, json.dumps(document, sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    main()
