"""Public facade and aggregate validation for versioned MissionPlan values."""

from __future__ import annotations

from dataclasses import dataclass

from mission.context import (
    ContextRole,
    ExecutionRelation,
    ExecutionRelationEndpoint,
    GroupSharedView,
    GroupViewBinding,
    MissionContext,
    PeerChannel,
    SharedSpatialReference,
    _validate_coordination_mechanisms,
)
from mission.contract_values import (
    CAPABILITIES,
    COUPLING_MODES,
    EXECUTABLE_RELATION_KINDS,
    GROUP_VIEW_FIELDS,
    MAP_ID_PATTERN,
    MISSION_PLAN_COMPAT_VERSION,
    MISSION_PLAN_COUPLING_VERSION,
    MISSION_PLAN_SATISFACTION_VERSION,
    MISSION_PLAN_SCHEDULING_VERSION,
    MISSION_PLAN_VERSION,
    RELATION_KINDS,
    RESOURCE_KINDS,
    U32_MAX,
    U64_MAX,
    JSONObject,
    JSONScalar,
    JSONValue,
    MissionPlanError,
    _array,
    _exact_keys,
    _object,
    _text,
)
from mission.role import (
    CapabilityConstraint,
    CapabilityConstraintOperator,
    CapabilityContractRef,
    CapabilityRequirement,
    ExecutionIntent,
    OperationRef,
    ResourceRequirement,
    RoleRequirement,
)
from mission.task import (
    MissionTask,
    TaskSatisfaction,
    TaskSatisfactionBasis,
    TaskTiming,
    VerifierSatisfactionPolicy,
)

__all__ = [
    "CAPABILITIES",
    "COUPLING_MODES",
    "CapabilityConstraint",
    "CapabilityConstraintOperator",
    "CapabilityContractRef",
    "CapabilityRequirement",
    "ContextRole",
    "EXECUTABLE_RELATION_KINDS",
    "ExecutionIntent",
    "ExecutionRelation",
    "ExecutionRelationEndpoint",
    "GROUP_VIEW_FIELDS",
    "GroupSharedView",
    "GroupViewBinding",
    "JSONObject",
    "JSONScalar",
    "JSONValue",
    "MAP_ID_PATTERN",
    "MISSION_PLAN_COMPAT_VERSION",
    "MISSION_PLAN_COUPLING_VERSION",
    "MISSION_PLAN_SATISFACTION_VERSION",
    "MISSION_PLAN_SCHEDULING_VERSION",
    "MISSION_PLAN_VERSION",
    "MissionActor",
    "MissionContext",
    "MissionPlan",
    "MissionPlanError",
    "MissionSpec",
    "MissionTask",
    "OperationRef",
    "PeerChannel",
    "RELATION_KINDS",
    "RESOURCE_KINDS",
    "ResourceRequirement",
    "RoleRequirement",
    "SharedSpatialReference",
    "TaskSatisfaction",
    "TaskSatisfactionBasis",
    "TaskTiming",
    "U32_MAX",
    "U64_MAX",
    "VerifierSatisfactionPolicy",
]


@dataclass(frozen=True, slots=True)
class MissionActor:
    """Declare one Mission-scoped logical participant without physical placement."""

    actor_id: str

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> MissionActor:
        """Parse one logical Actor declaration."""
        item = _object(value, path)
        _exact_keys(item, {"id"}, path)
        return cls(_text(item["id"], f"{path}.id"))

    def to_json(self) -> JSONObject:
        """Serialize one logical Actor declaration."""
        return {"id": self.actor_id}


@dataclass(frozen=True, slots=True)
class MissionSpec:
    """Identify a mission and retain the user-visible objective."""

    mission_id: str
    objective: str
    actors: tuple[MissionActor, ...] = ()

    @classmethod
    def from_json(cls, value: JSONValue, version: str) -> MissionSpec:
        """Parse a mission specification from contract JSON."""
        item = _object(value, "mission")
        _exact_keys(
            item,
            {"id", "objective", "actors"}
            if version == MISSION_PLAN_VERSION
            else {"id", "objective"},
            "mission",
        )
        actors = (
            tuple(
                MissionActor.from_json(actor, f"mission.actors[{index}]")
                for index, actor in enumerate(_array(item["actors"], "mission.actors"))
            )
            if version == MISSION_PLAN_VERSION
            else ()
        )
        if version == MISSION_PLAN_VERSION and not actors:
            raise MissionPlanError("mission.actors must not be empty")
        if len({actor.actor_id for actor in actors}) != len(actors):
            raise MissionPlanError("mission.actors contains duplicate ids")
        return cls(
            mission_id=_text(item["id"], "mission.id"),
            objective=_text(item["objective"], "mission.objective"),
            actors=actors,
        )

    def to_json(self, version: str) -> JSONObject:
        """Serialize the mission specification without planner metadata."""
        result: JSONObject = {"id": self.mission_id, "objective": self.objective}
        if version == MISSION_PLAN_VERSION:
            result["actors"] = [actor.to_json() for actor in self.actors]
        return result


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
        if version not in {
            MISSION_PLAN_COMPAT_VERSION,
            MISSION_PLAN_COUPLING_VERSION,
            MISSION_PLAN_SCHEDULING_VERSION,
            MISSION_PLAN_SATISFACTION_VERSION,
            MISSION_PLAN_VERSION,
        }:
            raise MissionPlanError(f"unsupported schema_version: {version}")
        tasks = tuple(
            MissionTask.from_json(task, f"tasks[{index}]", version)
            for index, task in enumerate(_array(item["tasks"], "tasks"))
        )
        contexts = tuple(
            MissionContext.from_json(context, f"contexts[{index}]", version)
            for index, context in enumerate(_array(item["contexts"], "contexts"))
        )
        plan = cls(version, MissionSpec.from_json(item["mission"], version), tasks, contexts)
        plan.validate_graph()
        plan.validate_contexts()
        return plan

    def validate_contexts(self) -> None:
        """Reject invalid Context continuity and execution relation endpoints."""
        context_ids = [context.context_id for context in self.contexts]
        if len(set(context_ids)) != len(context_ids):
            raise MissionPlanError("contexts contains duplicate ids")
        contexts = {context.context_id: context for context in self.contexts}
        declared_actors = {actor.actor_id for actor in self.mission.actors}
        if declared_actors:
            for declared_context in self.contexts:
                for declared_context_role in declared_context.roles:
                    if declared_context_role.actor_id not in declared_actors:
                        raise MissionPlanError(
                            f"context role {declared_context_role.role_id} references "
                            "undeclared actor"
                        )
        relation_ids = [
            relation.relation_id for context in self.contexts for relation in context.relations
        ]
        if len(set(relation_ids)) != len(relation_ids):
            raise MissionPlanError("contexts contain duplicate execution relation ids")
        tasks = {task.task_id: task for task in self.tasks}
        for task in self.tasks:
            task_context = contexts.get(task.context_id)
            if task_context is None:
                raise MissionPlanError(f"task {task.task_id} references unknown context")
            _validate_coordination_mechanisms(
                task_context,
                task.coupling_mode or task_context.coupling_mode,
                f"task {task.task_id}",
            )
            context_roles = {role.role_id: role for role in task_context.roles}
            for role in task.roles:
                if role.context_role is None:
                    if self.schema_version == MISSION_PLAN_VERSION:
                        raise MissionPlanError(
                            f"task {task.task_id} role {role.role_id} lacks context role"
                        )
                    continue
                referenced_context_role = context_roles.get(role.context_role)
                if referenced_context_role is None:
                    raise MissionPlanError(
                        f"task {task.task_id} role {role.role_id} references unknown context role"
                    )
                if role.actor_id is not None and referenced_context_role.actor_id != role.actor_id:
                    raise MissionPlanError(
                        f"task {task.task_id} role {role.role_id} actor differs from context role"
                    )
        for context in self.contexts:
            shared_view_role_ids = {role.role_id for role in context.roles}
            if context.shared_view is not None:
                for binding in context.shared_view.bindings:
                    if binding.context_role_id not in shared_view_role_ids:
                        raise MissionPlanError(
                            f"context {context.context_id} shared view references unknown "
                            f"context role {binding.context_role_id}"
                        )
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

    def validate_implementation_support(self) -> None:
        """Reject valid relation syntax without a Controller/Runtime evidence reducer."""
        for context in self.contexts:
            for relation in context.relations:
                if relation.kind not in EXECUTABLE_RELATION_KINDS:
                    raise MissionPlanError(
                        f"relation {relation.relation_id} uses {relation.kind}, which is valid "
                        "contract syntax but is not executable by this RoboGuide build"
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
            "mission": self.mission.to_json(self.schema_version),
            "contexts": [context.to_json(self.schema_version) for context in self.contexts],
            "tasks": [task.to_json(self.schema_version) for task in self.tasks],
        }
