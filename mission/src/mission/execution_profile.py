"""Deployment-owned execution requirements supplied to Mission Intelligence.

The profile carries only operation-level resource minima.  It never names a
Node, ResourceId, Physical Entity, or semantic goal, so deployment execution
constraints remain separate from the MissionPlan semantic contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from mission.contract_values import (
    JSONObject,
    JSONValue,
    MissionPlanError,
    _array,
    _exact_keys,
    _object,
)
from mission.models import MissionPlan
from mission.role import OperationRef, ResourceRequirement, RoleRequirement

EXECUTION_PROFILE_SCHEMA = "roboguide.execution-profile/v0.1"


class ExecutionProfileError(ValueError):
    """Report malformed deployment execution requirements."""


@dataclass(frozen=True, slots=True)
class OperationExecutionProfile:
    """Describe the minimum exclusive resources for one canonical operation."""

    operation: OperationRef
    resources: tuple[ResourceRequirement, ...]

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> OperationExecutionProfile:
        """Parse one operation profile without accepting Node-local identities."""
        item = _object(value, path)
        _exact_keys(item, {"operation", "resources"}, path)
        try:
            operation = OperationRef.from_json(item["operation"], f"{path}.operation")
        except MissionPlanError as error:
            raise ExecutionProfileError(str(error)) from error
        try:
            resources = tuple(
                ResourceRequirement.from_json(resource, f"{path}.resources[{index}]")
                for index, resource in enumerate(_array(item["resources"], f"{path}.resources"))
            )
        except MissionPlanError as error:
            raise ExecutionProfileError(str(error)) from error
        if len({resource.kind for resource in resources}) != len(resources):
            raise ExecutionProfileError(f"{path}.resources contains duplicate kinds")
        return cls(operation=operation, resources=resources)

    def to_json(self) -> JSONObject:
        """Serialize one operation profile for the planner and reviewer inputs."""
        return {
            "operation": self.operation.to_json(),
            "resources": [resource.to_json() for resource in self.resources],
        }


@dataclass(frozen=True, slots=True)
class DeploymentExecutionProfile:
    """Contain immutable operation/resource facts owned by one deployment."""

    profiles: tuple[OperationExecutionProfile, ...]

    @classmethod
    def from_json(cls, value: JSONValue) -> DeploymentExecutionProfile:
        """Parse a versioned profile and reject duplicate operation identities."""
        item = _object(value, "execution_profile")
        _exact_keys(item, {"schema_version", "profiles"}, "execution_profile")
        if item["schema_version"] != EXECUTION_PROFILE_SCHEMA:
            raise ExecutionProfileError(
                f"execution_profile.schema_version must equal {EXECUTION_PROFILE_SCHEMA}"
            )
        profiles = tuple(
            OperationExecutionProfile.from_json(profile, f"execution_profile.profiles[{index}]")
            for index, profile in enumerate(_array(item["profiles"], "execution_profile.profiles"))
        )
        if not profiles:
            raise ExecutionProfileError("execution_profile.profiles must not be empty")
        operations = [profile.operation for profile in profiles]
        if len(set(operations)) != len(operations):
            raise ExecutionProfileError("execution_profile.profiles contains duplicate operations")
        return cls(profiles=profiles)

    @classmethod
    def load(cls, path: Path) -> DeploymentExecutionProfile:
        """Load one deployment-owned profile from a fixed repository path."""
        try:
            decoded: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ExecutionProfileError(f"cannot load execution profile {path}: {error}") from error
        if not isinstance(decoded, dict) or not all(isinstance(key, str) for key in decoded):
            raise ExecutionProfileError("execution profile must be a JSON object")
        return cls.from_json(cast(JSONObject, decoded))

    def to_json(self) -> JSONObject:
        """Serialize the stable profile artifact exposed to Mission Intelligence."""
        return {
            "schema_version": EXECUTION_PROFILE_SCHEMA,
            "profiles": [profile.to_json() for profile in self.profiles],
        }

    def requirements_for(self, operation: OperationRef) -> tuple[ResourceRequirement, ...]:
        """Return deployment resource minima for one operation, or an empty tuple."""
        for profile in self.profiles:
            if profile.operation == operation:
                return profile.resources
        return ()

    def apply(self, plan: MissionPlan) -> MissionPlan:
        """Bind configured resource minima to matching Roles before plan admission.

        Existing Role requirements remain intact.  A profile raises a matching
        kind to the deployment minimum and never invents a Node or resource
        identity.  This keeps the binding inside Mission Intelligence rather
        than allowing an experiment script to patch an accepted MissionPlan.
        """
        return replace(
            plan,
            tasks=tuple(
                replace(task, roles=tuple(self._bind_role(role) for role in task.roles))
                for task in plan.tasks
            ),
        )

    def _bind_role(self, role: RoleRequirement) -> RoleRequirement:
        """Return a new Role with validated minima while preserving all semantic fields."""
        required = self.requirements_for(role.execution.operation)
        if not required:
            return role
        current = {resource.kind: resource for resource in role.resources}
        for resource in required:
            existing = current.get(resource.kind)
            if existing is None or existing.units < resource.units:
                current[resource.kind] = resource
        return replace(role, resources=tuple(current[kind] for kind in sorted(current)))


def load_optional_execution_profile(path: Path | None) -> DeploymentExecutionProfile | None:
    """Load a configured deployment profile while preserving the no-profile default."""
    return None if path is None else DeploymentExecutionProfile.load(path)
