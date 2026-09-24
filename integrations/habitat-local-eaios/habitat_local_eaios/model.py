"""Canonical invocation validation at the Habitat Local EAIOS boundary."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Union

# Runtime compatibility is Python 3.9 because this module executes in the EMOS environment.
ScalarValue = Union[bool, int, float, str]  # noqa: UP007
SUPPORTED_OPERATION = "mobility.navigate@v1"
# The shared-world Local EAIOS executes both canonical navigation operations with the
# same original EMOS Stage2 stack; the MI organization may emit either one.
SUPPORTED_OPERATIONS = ("mobility.navigate@v1", "mobility.move@v1")
EXECUTION_SESSION_SCHEMA = "roboguide.execution-session/v0.1"


class IntegrationError(RuntimeError):
    """Reports a closed-boundary invocation or local execution failure."""


@dataclass(frozen=True)
class ExecutionSessionMetadata:
    """Validated accepted-plan topology, separate from the navigation intent."""

    mission_id: str
    group_id: str
    slots: tuple[dict[str, object], ...]
    digest: str

    @classmethod
    def from_json(
        cls, value: object, mission_id: str, group_id: str, task_id: str, role_id: str
    ) -> ExecutionSessionMetadata:
        """Reject stale, rehashed, malformed, or cross-slot topology evidence."""
        body = _string_object(value, "execution_session")
        if set(body) != {"schema_version", "mission_id", "group_id", "slots", "digest"}:
            raise IntegrationError("execution_session fields do not match v0.1")
        if (
            body["schema_version"] != EXECUTION_SESSION_SCHEMA
            or body["mission_id"] != mission_id
            or body["group_id"] != group_id
        ):
            raise IntegrationError("execution_session identity is not the invocation identity")
        raw_slots = body["slots"]
        if not isinstance(raw_slots, list) or not raw_slots:
            raise IntegrationError("execution_session needs nonempty slots")
        slots: list[dict[str, object]] = []
        identities: set[tuple[str, str]] = set()
        prerequisites: set[str] = set()
        for raw_slot in raw_slots:
            slot = _string_object(raw_slot, "execution_session slot")
            if set(slot) != {
                "task_id",
                "role_id",
                "actor_id",
                "dependencies",
                "independent",
            }:
                raise IntegrationError("execution_session slot fields do not match v0.1")
            current_task = _non_empty_string(slot["task_id"], "slot.task_id")
            current_role = _non_empty_string(slot["role_id"], "slot.role_id")
            _non_empty_string(slot["actor_id"], "slot.actor_id")
            dependencies = _string_list(slot["dependencies"], "slot.dependencies")
            if (
                len(dependencies) != len(set(dependencies))
                or dependencies != sorted(dependencies)
                or current_task in dependencies
                or slot["independent"] not in (True, False)
                or not isinstance(slot["independent"], bool)
                or (current_task, current_role) in identities
            ):
                raise IntegrationError("execution_session slot is inconsistent")
            identities.add((current_task, current_role))
            prerequisites.update(dependencies)
            slots.append(slot)
        if (task_id, role_id) not in identities:
            raise IntegrationError("execution_session does not contain the invoked Task/Role")
        if [tuple((slot["task_id"], slot["role_id"])) for slot in slots] != sorted(identities):
            raise IntegrationError("execution_session slots are not canonical")
        if not prerequisites.issubset({str(slot["task_id"]) for slot in slots}):
            raise IntegrationError("execution_session has an unknown prerequisite")
        claimed = body["digest"]
        if not isinstance(claimed, str):
            raise IntegrationError("execution_session digest is missing")
        unsigned = {key: item for key, item in body.items() if key != "digest"}
        encoded = json.dumps(
            unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        if claimed != "sha256:" + hashlib.sha256(encoded).hexdigest():
            raise IntegrationError("execution_session digest does not match")
        return cls(mission_id, group_id, tuple(slots), claimed)

    def as_dict(self) -> dict[str, object]:
        """Preserve the complete immutable descriptor in local durable identity."""
        return {
            "schema_version": EXECUTION_SESSION_SCHEMA,
            "mission_id": self.mission_id,
            "group_id": self.group_id,
            "slots": [dict(slot) for slot in self.slots],
            "digest": self.digest,
        }

    def topology(self) -> str:
        """Select only the deployment's supported topologies from accepted-plan facts."""
        if not all(slot["independent"] is True for slot in self.slots):
            return "unsupported"
        actors = {str(slot["actor_id"]) for slot in self.slots}
        tasks = {str(slot["task_id"]) for slot in self.slots}
        if len(actors) == 1 and len(tasks) == len(self.slots):
            return "single_actor_sequential"
        if (
            len(self.slots) == 2
            and len(actors) == 2
            and all(not slot["dependencies"] for slot in self.slots)
        ):
            return "two_actor_concurrent"
        return "unsupported"


@dataclass(frozen=True)
class CanonicalMobilityInvocation:
    """Validated semantic navigation request retained intact by the bridge."""

    mission_id: str
    task_id: str
    group_id: str
    role_id: str
    operation: str
    objective: str
    parameters: dict[str, ScalarValue]
    resource_ids: tuple[str, ...]
    execution_session: ExecutionSessionMetadata | None = None

    @classmethod
    def from_request(cls, request: object) -> CanonicalMobilityInvocation:
        """Validate one Node workflow request without accepting Local How fields."""
        body = _string_object(request, "request")
        if set(body) != {"invocation"}:
            raise IntegrationError("execute request must contain only invocation")
        invocation = _string_object(body["invocation"], "invocation")
        expected = {
            "mission_id",
            "task_id",
            "group_id",
            "role_id",
            "operation",
            "objective",
            "parameters",
            "resource_ids",
        }
        if set(invocation) not in (expected, expected | {"execution_session"}):
            raise IntegrationError("canonical invocation fields do not match the C1-S0 contract")
        operation = _non_empty_string(invocation["operation"], "operation")
        if operation not in SUPPORTED_OPERATIONS:
            raise IntegrationError(f"unsupported canonical operation {operation!r}")
        parameters = _parameters(invocation["parameters"])
        if set(parameters) != {"destination"}:
            raise IntegrationError("mobility.navigate@v1 requires exactly destination")
        _non_empty_string(parameters["destination"], "parameters.destination")
        resources = _string_list(invocation["resource_ids"], "resource_ids")
        if len(set(resources)) != len(resources):
            raise IntegrationError("resource_ids contains duplicates")
        mission_id = _non_empty_string(invocation["mission_id"], "mission_id")
        task_id = _non_empty_string(invocation["task_id"], "task_id")
        group_id = _non_empty_string(invocation["group_id"], "group_id")
        role_id = _non_empty_string(invocation["role_id"], "role_id")
        session = (
            ExecutionSessionMetadata.from_json(
                invocation["execution_session"], mission_id, group_id, task_id, role_id
            )
            if "execution_session" in invocation
            else None
        )
        return cls(
            mission_id=mission_id,
            task_id=task_id,
            group_id=group_id,
            role_id=role_id,
            operation=operation,
            objective=_non_empty_string(invocation["objective"], "objective"),
            parameters=parameters,
            resource_ids=tuple(resources),
            execution_session=session,
        )

    @property
    def destination(self) -> str:
        """Return the semantic destination interpreted only by the Local EAIOS."""
        value = self.parameters["destination"]
        if not isinstance(value, str):
            raise IntegrationError("parameters.destination must be a string")
        return value

    def as_dict(self) -> dict[str, object]:
        """Return a defensive canonical JSON-shaped representation for persistence."""
        result: dict[str, object] = {
            "group_id": self.group_id,
            "mission_id": self.mission_id,
            "objective": self.objective,
            "operation": self.operation,
            "parameters": dict(self.parameters),
            "resource_ids": list(self.resource_ids),
            "role_id": self.role_id,
            "task_id": self.task_id,
        }
        if self.execution_session is not None:
            result["execution_session"] = self.execution_session.as_dict()
        return result

    def request_key(self) -> str:
        """Derive the idempotency key for one exact canonical invocation."""
        encoded = json.dumps(
            self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def _string_object(value: object, field: str) -> dict[str, object]:
    """Return a string-keyed object or fail the local boundary closed."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise IntegrationError(f"{field} must be an object with string keys")
    return {str(key): item for key, item in value.items()}


def _non_empty_string(value: object, field: str) -> str:
    """Require a non-empty string identity or semantic text field."""
    if not isinstance(value, str) or not value.strip():
        raise IntegrationError(f"{field} must be a non-empty string")
    return value


def _parameters(value: object) -> dict[str, ScalarValue]:
    """Validate transport-neutral scalar parameters and reject nested Local How."""
    raw = _string_object(value, "parameters")
    parameters: dict[str, ScalarValue] = {}
    for key, item in raw.items():
        if not key:
            raise IntegrationError("parameter names must be non-empty")
        if isinstance(item, (bool, int, str)):
            parameters[key] = item
        elif isinstance(item, float) and math.isfinite(item):
            parameters[key] = item
        else:
            raise IntegrationError(f"parameter {key!r} must be a finite scalar")
    return parameters


def _string_list(value: object, field: str) -> list[str]:
    """Validate a JSON array of non-empty string identities."""
    if not isinstance(value, list):
        raise IntegrationError(f"{field} must be an array")
    return [_non_empty_string(item, field) for item in value]
