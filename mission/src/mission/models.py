"""Versioned, SDK-independent Mission Plan contract values and validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]
type JSONObject = dict[str, JSONValue]

MISSION_PLAN_VERSION: Final = "roboguide.mission-plan/v0"
CAPABILITIES: Final = frozenset({"mobility", "transport", "compute", "observation"})
RESOURCE_KINDS: Final = frozenset({"space", "compute", "time"})


class MissionPlanError(ValueError):
    """Report a Mission Plan contract or graph invariant violation."""


def _object(value: JSONValue, path: str) -> JSONObject:
    """Return a JSON object or reject the value with its contract path."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise MissionPlanError(f"{path} must be an object")
    return value


def _array(value: JSONValue, path: str) -> list[JSONValue]:
    """Return a JSON array or reject the value with its contract path."""
    if not isinstance(value, list):
        raise MissionPlanError(f"{path} must be an array")
    return value


def _text(value: JSONValue, path: str) -> str:
    """Return nonblank text or reject the value with its contract path."""
    if not isinstance(value, str) or not value.strip():
        raise MissionPlanError(f"{path} must be nonblank text")
    return value


def _exact_keys(value: JSONObject, required: set[str], path: str) -> None:
    """Reject missing or unknown keys so contract drift is explicit."""
    actual = set(value)
    if actual != required:
        missing = sorted(required - actual)
        unknown = sorted(actual - required)
        raise MissionPlanError(f"{path} keys mismatch: missing={missing}, unknown={unknown}")


@dataclass(frozen=True, slots=True)
class RoleRequirement:
    """Describe one role-level capability and optional resource requirement."""

    role_id: str
    capability: str
    resource_kind: str | None

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> RoleRequirement:
        """Parse and validate one role requirement from contract JSON."""
        item = _object(value, path)
        _exact_keys(item, {"id", "capability", "resource_kind"}, path)
        role_id = _text(item["id"], f"{path}.id")
        capability = _text(item["capability"], f"{path}.capability")
        if capability not in CAPABILITIES:
            raise MissionPlanError(f"{path}.capability is unsupported: {capability}")
        resource_value = item["resource_kind"]
        if resource_value is not None and not isinstance(resource_value, str):
            raise MissionPlanError(f"{path}.resource_kind must be text or null")
        if resource_value is not None and resource_value not in RESOURCE_KINDS:
            raise MissionPlanError(f"{path}.resource_kind is unsupported: {resource_value}")
        return cls(role_id=role_id, capability=capability, resource_kind=resource_value)

    def to_json(self) -> JSONObject:
        """Serialize the role requirement without provider-specific values."""
        return {
            "id": self.role_id,
            "capability": self.capability,
            "resource_kind": self.resource_kind,
        }


@dataclass(frozen=True, slots=True)
class MissionTask:
    """Describe one task node, its dependencies, and execution requirements."""

    task_id: str
    description: str
    depends_on: tuple[str, ...]
    roles: tuple[RoleRequirement, ...]

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> MissionTask:
        """Parse one task and reject empty or duplicate role requirements."""
        item = _object(value, path)
        _exact_keys(item, {"id", "description", "depends_on", "roles"}, path)
        dependencies = tuple(
            _text(dependency, f"{path}.depends_on[{index}]")
            for index, dependency in enumerate(_array(item["depends_on"], f"{path}.depends_on"))
        )
        if len(set(dependencies)) != len(dependencies):
            raise MissionPlanError(f"{path}.depends_on contains duplicates")
        roles = tuple(
            RoleRequirement.from_json(role, f"{path}.roles[{index}]")
            for index, role in enumerate(_array(item["roles"], f"{path}.roles"))
        )
        if not roles:
            raise MissionPlanError(f"{path}.roles must not be empty")
        role_ids = [role.role_id for role in roles]
        if len(set(role_ids)) != len(role_ids):
            raise MissionPlanError(f"{path}.roles contains duplicate ids")
        return cls(
            task_id=_text(item["id"], f"{path}.id"),
            description=_text(item["description"], f"{path}.description"),
            depends_on=dependencies,
            roles=roles,
        )

    def to_json(self) -> JSONObject:
        """Serialize one task in stable declaration order."""
        return {
            "id": self.task_id,
            "description": self.description,
            "depends_on": list(self.depends_on),
            "roles": [role.to_json() for role in self.roles],
        }


@dataclass(frozen=True, slots=True)
class MissionSpec:
    """Identify a mission and retain the user-visible objective."""

    mission_id: str
    objective: str

    @classmethod
    def from_json(cls, value: JSONValue) -> MissionSpec:
        """Parse a mission specification from contract JSON."""
        item = _object(value, "mission")
        _exact_keys(item, {"id", "objective"}, "mission")
        return cls(
            mission_id=_text(item["id"], "mission.id"),
            objective=_text(item["objective"], "mission.objective"),
        )

    def to_json(self) -> JSONObject:
        """Serialize the mission specification without planner metadata."""
        return {"id": self.mission_id, "objective": self.objective}


@dataclass(frozen=True, slots=True)
class MissionPlan:
    """Contain a validated, acyclic Task Graph for one mission."""

    schema_version: str
    mission: MissionSpec
    tasks: tuple[MissionTask, ...]

    @classmethod
    def from_json(cls, value: JSONValue) -> MissionPlan:
        """Parse a plan and enforce version, identity, and graph invariants."""
        item = _object(value, "mission_plan")
        _exact_keys(item, {"schema_version", "mission", "tasks"}, "mission_plan")
        version = _text(item["schema_version"], "schema_version")
        if version != MISSION_PLAN_VERSION:
            raise MissionPlanError(f"unsupported schema_version: {version}")
        tasks = tuple(
            MissionTask.from_json(task, f"tasks[{index}]")
            for index, task in enumerate(_array(item["tasks"], "tasks"))
        )
        plan = cls(version, MissionSpec.from_json(item["mission"]), tasks)
        plan.validate_graph()
        return plan

    def validate_graph(self) -> None:
        """Reject empty graphs, duplicate tasks, unknown dependencies, and cycles."""
        if not self.tasks:
            raise MissionPlanError("tasks must not be empty")
        task_ids = [task.task_id for task in self.tasks]
        if len(set(task_ids)) != len(task_ids):
            raise MissionPlanError("tasks contains duplicate ids")
        known = set(task_ids)
        for task in self.tasks:
            unknown = sorted(set(task.depends_on) - known)
            if unknown:
                raise MissionPlanError(f"task {task.task_id} has unknown dependencies: {unknown}")
            if task.task_id in task.depends_on:
                raise MissionPlanError(f"task {task.task_id} depends on itself")
        remaining = {task.task_id: set(task.depends_on) for task in self.tasks}
        while remaining:
            ready = {task_id for task_id, dependencies in remaining.items() if not dependencies}
            if not ready:
                raise MissionPlanError("task graph contains a cycle")
            remaining = {
                task_id: dependencies - ready
                for task_id, dependencies in remaining.items()
                if task_id not in ready
            }

    def to_json(self) -> JSONObject:
        """Serialize the validated plan as the versioned cross-language contract."""
        return {
            "schema_version": self.schema_version,
            "mission": self.mission.to_json(),
            "tasks": [task.to_json() for task in self.tasks],
        }
