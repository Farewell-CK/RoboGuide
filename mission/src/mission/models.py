"""Versioned, SDK-independent Mission Plan contract values and validation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]
type JSONObject = dict[str, JSONValue]

MISSION_PLAN_VERSION: Final = "roboguide.mission-plan/v0.3"
CAPABILITIES: Final = frozenset({"mobility", "transport", "compute", "observation"})
RESOURCE_KINDS: Final = frozenset({"space", "compute", "time"})
RELATION_KINDS: Final = frozenset({"requires-active"})


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
class CapabilityContractRef:
    """Identify one canonical capability contract independently of a local EAIOS skill."""

    namespace: str
    name: str
    version: str

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> CapabilityContractRef:
        """Parse a canonical capability contract reference from contract JSON."""
        item = _object(value, path)
        _exact_keys(item, {"namespace", "name", "version"}, path)
        namespace = _text(item["namespace"], f"{path}.namespace")
        name = _text(item["name"], f"{path}.name")
        version = _text(item["version"], f"{path}.version")
        if any(
            not segment or any(character.isspace() or character == "@" for character in segment)
            for segment in namespace.split(".")
        ):
            raise MissionPlanError(f"{path}.namespace is not canonical")
        if "." in name or "@" in name or any(character.isspace() for character in name):
            raise MissionPlanError(f"{path}.name must be one canonical segment")
        if "@" in version or any(character.isspace() for character in version):
            raise MissionPlanError(f"{path}.version is not canonical")
        return cls(
            namespace=namespace,
            name=name,
            version=version,
        )

    def to_json(self) -> JSONObject:
        """Serialize the canonical capability contract without adapter-local names."""
        return {
            "namespace": self.namespace,
            "name": self.name,
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class ExecutionIntent:
    """Describe one canonical role operation and scalar parameters."""

    capability_contract: CapabilityContractRef
    parameters: tuple[tuple[str, str | int | float | bool], ...]

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> ExecutionIntent:
        """Parse an intent while rejecting structured or null parameter values."""
        item = _object(value, path)
        _exact_keys(item, {"capability_contract", "parameters"}, path)
        parameter_object = _object(item["parameters"], f"{path}.parameters")
        parameters: list[tuple[str, str | int | float | bool]] = []
        for key, parameter in sorted(parameter_object.items()):
            if not key.strip():
                raise MissionPlanError(f"{path}.parameters contains a blank key")
            if parameter is None or isinstance(parameter, (list, dict)):
                raise MissionPlanError(
                    f"{path}.parameters.{key} must be a scalar string, number, or boolean"
                )
            if isinstance(parameter, float) and not math.isfinite(parameter):
                raise MissionPlanError(f"{path}.parameters.{key} must be a finite number")
            parameters.append((key, parameter))
        return cls(
            capability_contract=CapabilityContractRef.from_json(
                item["capability_contract"], f"{path}.capability_contract"
            ),
            parameters=tuple(parameters),
        )

    def to_json(self) -> JSONObject:
        """Serialize intent parameters in stable lexical key order."""
        return {
            "capability_contract": self.capability_contract.to_json(),
            "parameters": dict(self.parameters),
        }


@dataclass(frozen=True, slots=True)
class RoleRequirement:
    """Describe one role-level capability and optional resource requirement."""

    role_id: str
    actor_id: str
    capability: str
    resource_kind: str | None
    execution: ExecutionIntent
    context_role: str | None
    resource_scope: str

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> RoleRequirement:
        """Parse and validate one role requirement from contract JSON."""
        item = _object(value, path)
        _exact_keys(
            item,
            {
                "id",
                "actor",
                "capability",
                "contract",
                "resource_kind",
                "execution",
                "context_role",
                "resource_scope",
            },
            path,
        )
        role_id = _text(item["id"], f"{path}.id")
        capability = _text(item["capability"], f"{path}.capability")
        if capability not in CAPABILITIES:
            raise MissionPlanError(f"{path}.capability is unsupported: {capability}")
        contract = CapabilityContractRef.from_json(item["contract"], f"{path}.contract")
        execution = ExecutionIntent.from_json(item["execution"], f"{path}.execution")
        if execution.capability_contract != contract:
            raise MissionPlanError(f"{path}.contract differs from execution.capability_contract")
        resource_value = item["resource_kind"]
        if resource_value is not None and not isinstance(resource_value, str):
            raise MissionPlanError(f"{path}.resource_kind must be text or null")
        if resource_value is not None and resource_value not in RESOURCE_KINDS:
            raise MissionPlanError(f"{path}.resource_kind is unsupported: {resource_value}")
        context_role_value = item["context_role"]
        if context_role_value is not None:
            context_role_value = _text(context_role_value, f"{path}.context_role")
        resource_scope = _text(item["resource_scope"], f"{path}.resource_scope")
        if resource_scope not in {"task", "context"}:
            raise MissionPlanError(f"{path}.resource_scope is unsupported: {resource_scope}")
        if resource_scope == "context" and context_role_value is None:
            raise MissionPlanError(f"{path}.context_role is required for context scope")
        return cls(
            role_id=role_id,
            actor_id=_text(item["actor"], f"{path}.actor"),
            capability=capability,
            resource_kind=resource_value,
            execution=execution,
            context_role=context_role_value,
            resource_scope=resource_scope,
        )

    def to_json(self) -> JSONObject:
        """Serialize the role requirement without provider-specific values."""
        return {
            "id": self.role_id,
            "actor": self.actor_id,
            "capability": self.capability,
            "contract": self.execution.capability_contract.to_json(),
            "resource_kind": self.resource_kind,
            "execution": self.execution.to_json(),
            "context_role": self.context_role,
            "resource_scope": self.resource_scope,
        }


@dataclass(frozen=True, slots=True)
class ContextRole:
    """Associate one continuous semantic role with a Mission actor."""

    role_id: str
    actor_id: str

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> ContextRole:
        """Parse one ContextRole without introducing runtime node placement."""
        item = _object(value, path)
        _exact_keys(item, {"id", "actor"}, path)
        return cls(
            role_id=_text(item["id"], f"{path}.id"),
            actor_id=_text(item["actor"], f"{path}.actor"),
        )

    def to_json(self) -> JSONObject:
        """Serialize one semantic ContextRole."""
        return {"id": self.role_id, "actor": self.actor_id}


@dataclass(frozen=True, slots=True)
class ExecutionRelationEndpoint:
    """Identify one logical Task/Role slot without selecting a Node or physical attempt."""

    task_id: str
    role_id: str

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> ExecutionRelationEndpoint:
        """Parse one exact logical relation endpoint."""
        item = _object(value, path)
        _exact_keys(item, {"task_id", "role_id"}, path)
        return cls(
            task_id=_text(item["task_id"], f"{path}.task_id"),
            role_id=_text(item["role_id"], f"{path}.role_id"),
        )

    def to_json(self) -> JSONObject:
        """Serialize one logical execution endpoint."""
        return {"task_id": self.task_id, "role_id": self.role_id}


@dataclass(frozen=True, slots=True)
class ExecutionRelation:
    """Declare one directional execution-time constraint inside a Mission Context."""

    relation_id: str
    kind: str
    source: ExecutionRelationEndpoint
    target: ExecutionRelationEndpoint

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> ExecutionRelation:
        """Parse one closed relation contract and reject self-reference."""
        item = _object(value, path)
        _exact_keys(item, {"id", "kind", "source", "target"}, path)
        kind = _text(item["kind"], f"{path}.kind")
        if kind not in RELATION_KINDS:
            raise MissionPlanError(f"{path}.kind is unsupported: {kind}")
        source = ExecutionRelationEndpoint.from_json(item["source"], f"{path}.source")
        target = ExecutionRelationEndpoint.from_json(item["target"], f"{path}.target")
        if source == target:
            raise MissionPlanError(f"{path} cannot reference the same source and target")
        return cls(
            relation_id=_text(item["id"], f"{path}.id"),
            kind=kind,
            source=source,
            target=target,
        )

    def to_json(self) -> JSONObject:
        """Serialize one execution coordination relation."""
        return {
            "id": self.relation_id,
            "kind": self.kind,
            "source": self.source.to_json(),
            "target": self.target.to_json(),
        }


@dataclass(frozen=True, slots=True)
class MissionContext:
    """Describe semantic continuity shared by one or more Tasks."""

    context_id: str
    roles: tuple[ContextRole, ...]
    relations: tuple[ExecutionRelation, ...]

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> MissionContext:
        """Parse one Context and reject duplicate ContextRole identities."""
        item = _object(value, path)
        _exact_keys(item, {"id", "roles", "relations"}, path)
        roles = tuple(
            ContextRole.from_json(role, f"{path}.roles[{index}]")
            for index, role in enumerate(_array(item["roles"], f"{path}.roles"))
        )
        if len({role.role_id for role in roles}) != len(roles):
            raise MissionPlanError(f"{path}.roles contains duplicate ids")
        relations = tuple(
            ExecutionRelation.from_json(relation, f"{path}.relations[{index}]")
            for index, relation in enumerate(_array(item["relations"], f"{path}.relations"))
        )
        if len({relation.relation_id for relation in relations}) != len(relations):
            raise MissionPlanError(f"{path}.relations contains duplicate ids")
        return cls(
            context_id=_text(item["id"], f"{path}.id"),
            roles=roles,
            relations=relations,
        )

    def to_json(self) -> JSONObject:
        """Serialize one Context in declaration order."""
        return {
            "id": self.context_id,
            "roles": [role.to_json() for role in self.roles],
            "relations": [relation.to_json() for relation in self.relations],
        }


@dataclass(frozen=True, slots=True)
class MissionTask:
    """Describe one task node, its dependencies, and execution requirements."""

    task_id: str
    description: str
    depends_on: tuple[str, ...]
    roles: tuple[RoleRequirement, ...]
    context_id: str

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> MissionTask:
        """Parse one task and reject empty or duplicate role requirements."""
        item = _object(value, path)
        _exact_keys(item, {"id", "description", "depends_on", "roles", "context_id"}, path)
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
            context_id=_text(item["context_id"], f"{path}.context_id"),
        )

    def to_json(self) -> JSONObject:
        """Serialize one task in stable declaration order."""
        return {
            "id": self.task_id,
            "description": self.description,
            "depends_on": list(self.depends_on),
            "roles": [role.to_json() for role in self.roles],
            "context_id": self.context_id,
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
    contexts: tuple[MissionContext, ...]

    @classmethod
    def from_json(cls, value: JSONValue) -> MissionPlan:
        """Parse a plan and enforce version, identity, and graph invariants."""
        item = _object(value, "mission_plan")
        _exact_keys(item, {"schema_version", "mission", "contexts", "tasks"}, "mission_plan")
        version = _text(item["schema_version"], "schema_version")
        if version != MISSION_PLAN_VERSION:
            raise MissionPlanError(f"unsupported schema_version: {version}")
        tasks = tuple(
            MissionTask.from_json(task, f"tasks[{index}]")
            for index, task in enumerate(_array(item["tasks"], "tasks"))
        )
        contexts = tuple(
            MissionContext.from_json(context, f"contexts[{index}]")
            for index, context in enumerate(_array(item["contexts"], "contexts"))
        )
        plan = cls(version, MissionSpec.from_json(item["mission"]), tasks, contexts)
        plan.validate_graph()
        plan.validate_contexts()
        return plan

    def validate_contexts(self) -> None:
        """Reject invalid Context continuity and execution relation endpoints."""
        context_ids = [context.context_id for context in self.contexts]
        if len(set(context_ids)) != len(context_ids):
            raise MissionPlanError("contexts contains duplicate ids")
        contexts = {context.context_id: context for context in self.contexts}
        relation_ids = [
            relation.relation_id for context in self.contexts for relation in context.relations
        ]
        if len(set(relation_ids)) != len(relation_ids):
            raise MissionPlanError("contexts contain duplicate execution relation ids")
        tasks = {task.task_id: task for task in self.tasks}
        for task in self.tasks:
            context = contexts.get(task.context_id)
            if context is None:
                raise MissionPlanError(f"task {task.task_id} references unknown context")
            context_roles = {role.role_id: role for role in context.roles}
            for role in task.roles:
                if role.context_role is None:
                    continue
                context_role = context_roles.get(role.context_role)
                if context_role is None:
                    raise MissionPlanError(
                        f"task {task.task_id} role {role.role_id} references unknown context role"
                    )
                if context_role.actor_id != role.actor_id:
                    raise MissionPlanError(
                        f"task {task.task_id} role {role.role_id} actor differs from context role"
                    )
        for context in self.contexts:
            for relation in context.relations:
                for endpoint in (relation.source, relation.target):
                    endpoint_task = tasks.get(endpoint.task_id)
                    if endpoint_task is None:
                        raise MissionPlanError(
                            f"relation {relation.relation_id} references unknown task "
                            f"{endpoint.task_id}"
                        )
                    if endpoint_task.context_id != context.context_id:
                        raise MissionPlanError(
                            f"relation {relation.relation_id} endpoint belongs to another context"
                        )
                    if endpoint.role_id not in {role.role_id for role in endpoint_task.roles}:
                        raise MissionPlanError(
                            f"relation {relation.relation_id} references unknown role "
                            f"{endpoint.role_id} in task {endpoint.task_id}"
                        )
                if relation.source.task_id != relation.target.task_id and (
                    self._task_depends_on(relation.source.task_id, relation.target.task_id)
                    or self._task_depends_on(relation.target.task_id, relation.source.task_id)
                ):
                    raise MissionPlanError(
                        f"relation {relation.relation_id} connects tasks ordered by the DAG"
                    )

    def _task_depends_on(self, task_id: str, candidate_dependency: str) -> bool:
        """Return whether one Task transitively depends on another Task."""
        tasks = {task.task_id: task for task in self.tasks}
        task = tasks[task_id]
        return any(
            dependency == candidate_dependency
            or self._task_depends_on(dependency, candidate_dependency)
            for dependency in task.depends_on
        )

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
            "contexts": [context.to_json() for context in self.contexts],
            "tasks": [task.to_json() for task in self.tasks],
        }
