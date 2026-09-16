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


class IntegrationError(RuntimeError):
    """Reports a closed-boundary invocation or local execution failure."""


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
        if set(invocation) != expected:
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
        return cls(
            mission_id=_non_empty_string(invocation["mission_id"], "mission_id"),
            task_id=_non_empty_string(invocation["task_id"], "task_id"),
            group_id=_non_empty_string(invocation["group_id"], "group_id"),
            role_id=_non_empty_string(invocation["role_id"], "role_id"),
            operation=operation,
            objective=_non_empty_string(invocation["objective"], "objective"),
            parameters=parameters,
            resource_ids=tuple(resources),
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
        return {
            "group_id": self.group_id,
            "mission_id": self.mission_id,
            "objective": self.objective,
            "operation": self.operation,
            "parameters": dict(self.parameters),
            "resource_ids": list(self.resource_ids),
            "role_id": self.role_id,
            "task_id": self.task_id,
        }

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
