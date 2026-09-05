"""Versioned, SDK-independent Mission Plan contract values and validation."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Final

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]
type JSONObject = dict[str, JSONValue]

MISSION_PLAN_VERSION: Final = "roboguide.mission-plan/v0.4"
MISSION_PLAN_COMPAT_VERSION: Final = "roboguide.mission-plan/v0.3"
CAPABILITIES: Final = frozenset({"mobility", "transport", "compute", "observation"})
RESOURCE_KINDS: Final = frozenset({"space", "compute", "time"})
RELATION_KINDS: Final = frozenset(
    {
        "requires-active",
        "group-member-state",
        "shared-spatial-reference",
        "relative-pose",
        "relative-distance",
        "state-requirement",
        "freshness-requirement",
    }
)
# This is an implementation profile, not the MissionPlan schema vocabulary.
EXECUTABLE_RELATION_KINDS: Final = frozenset({"requires-active", "shared-spatial-reference"})
COUPLING_MODES: Final = frozenset(
    {
        "independent",
        "sequential-handoff",
        "concurrent-cooperation",
        "tightly-coupled-cooperation",
    }
)
GROUP_VIEW_FIELDS: Final = frozenset({"pose", "velocity", "execution"})
MAP_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


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


def _bounded_keys(value: JSONObject, required: set[str], optional: set[str], path: str) -> None:
    """Reject missing required keys and keys outside the versioned optional set."""
    actual = set(value)
    missing = sorted(required - actual)
    unknown = sorted(actual - required - optional)
    if missing or unknown:
        raise MissionPlanError(f"{path} keys mismatch: missing={missing}, unknown={unknown}")


def _validate_coordination_mechanisms(context: MissionContext, mode: str, path: str) -> None:
    """Reject modes whose required static coordination declarations are absent."""
    if mode in {"concurrent-cooperation", "tightly-coupled-cooperation"}:
        if context.shared_view is None:
            raise MissionPlanError(f"{path} mode {mode} requires a Group shared view")
        if not context.relations:
            raise MissionPlanError(f"{path} mode {mode} requires an execution relation")
    if mode == "tightly-coupled-cooperation" and context.peer_channel is None:
        raise MissionPlanError(f"{path} mode {mode} requires a direct peer channel")
    if mode == "tightly-coupled-cooperation" and len(context.roles) < 2:
        raise MissionPlanError(f"{path} mode {mode} requires at least two context roles")


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
class SharedSpatialReference:
    """Identify one typed Spatial Memory revision and common coordinate frame."""

    map_id: str
    revision_id: str
    frame_id: str

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> SharedSpatialReference:
        """Parse a reference through the shared path-safe map identity grammar."""
        item = _object(value, path)
        _exact_keys(item, {"map_id", "revision_id", "frame_id"}, path)
        map_id = _text(item["map_id"], f"{path}.map_id")
        revision_id = _text(item["revision_id"], f"{path}.revision_id")
        if MAP_ID_PATTERN.fullmatch(map_id) is None:
            raise MissionPlanError(f"{path}.map_id is not a canonical map identity")
        if MAP_ID_PATTERN.fullmatch(revision_id) is None:
            raise MissionPlanError(f"{path}.revision_id is not a canonical map identity")
        return cls(map_id, revision_id, _text(item["frame_id"], f"{path}.frame_id"))

    def to_json(self) -> JSONObject:
        """Serialize the typed spatial reference without artifact bytes."""
        return {
            "map_id": self.map_id,
            "revision_id": self.revision_id,
            "frame_id": self.frame_id,
        }


@dataclass(frozen=True, slots=True)
class GroupViewBinding:
    """Bind one logical ContextRole field to an exact node State export contract."""

    context_role_id: str
    field: str
    state_export_id: str | None
    payload_schema: str | None

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> GroupViewBinding:
        """Parse an explicit binding without guessing semantics from channel names."""
        item = _object(value, path)
        _bounded_keys(
            item,
            {"context_role_id", "field"},
            {"state_export_id", "payload_schema"},
            path,
        )
        field = _text(item["field"], f"{path}.field")
        if field not in GROUP_VIEW_FIELDS:
            raise MissionPlanError(f"{path}.field is unsupported: {field}")
        state_export_value = item.get("state_export_id")
        payload_schema_value = item.get("payload_schema")
        if field == "execution":
            if state_export_value is not None or payload_schema_value is not None:
                raise MissionPlanError(f"{path} execution field cannot select a State export")
            state_export_id = None
            payload_schema = None
        else:
            state_export_id = _text(state_export_value, f"{path}.state_export_id")
            payload_schema = _text(payload_schema_value, f"{path}.payload_schema")
        return cls(
            _text(item["context_role_id"], f"{path}.context_role_id"),
            field,
            state_export_id,
            payload_schema,
        )

    def to_json(self) -> JSONObject:
        """Serialize one exact State export binding."""
        result: JSONObject = {
            "context_role_id": self.context_role_id,
            "field": self.field,
        }
        if self.state_export_id is not None:
            result["state_export_id"] = self.state_export_id
        if self.payload_schema is not None:
            result["payload_schema"] = self.payload_schema
        return result


@dataclass(frozen=True, slots=True)
class GroupSharedView:
    """Declare the bounded State evidence visible inside one execution Context."""

    bindings: tuple[GroupViewBinding, ...]
    include_freshness: bool
    spatial_reference: SharedSpatialReference | None

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> GroupSharedView:
        """Parse bindings and reject duplicate or empty view declarations."""
        item = _object(value, path)
        _bounded_keys(item, {"bindings", "include_freshness"}, {"spatial_reference"}, path)
        bindings = tuple(
            GroupViewBinding.from_json(binding, f"{path}.bindings[{index}]")
            for index, binding in enumerate(_array(item["bindings"], f"{path}.bindings"))
        )
        if not bindings:
            raise MissionPlanError(f"{path}.bindings must not be empty")
        if len(set(bindings)) != len(bindings):
            raise MissionPlanError(f"{path}.bindings contains duplicates")
        include_freshness = item["include_freshness"]
        if not isinstance(include_freshness, bool):
            raise MissionPlanError(f"{path}.include_freshness must be boolean")
        reference_value = item.get("spatial_reference")
        reference = (
            None
            if reference_value is None
            else SharedSpatialReference.from_json(reference_value, f"{path}.spatial_reference")
        )
        return cls(bindings, include_freshness, reference)

    def to_json(self) -> JSONObject:
        """Serialize the bounded Group view declaration."""
        result: JSONObject = {
            "bindings": [binding.to_json() for binding in self.bindings],
            "include_freshness": self.include_freshness,
        }
        if self.spatial_reference is not None:
            result["spatial_reference"] = self.spatial_reference.to_json()
        return result


@dataclass(frozen=True, slots=True)
class PeerChannel:
    """Describe a deployment-resolved direct Local EAIOS peer channel."""

    profile_id: str
    message_schema: str

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> PeerChannel:
        """Parse a transport-neutral peer channel descriptor."""
        item = _object(value, path)
        _exact_keys(item, {"profile_id", "message_schema"}, path)
        return cls(
            _text(item["profile_id"], f"{path}.profile_id"),
            _text(item["message_schema"], f"{path}.message_schema"),
        )

    def to_json(self) -> JSONObject:
        """Serialize the peer descriptor without middleware configuration."""
        return {"profile_id": self.profile_id, "message_schema": self.message_schema}


@dataclass(frozen=True, slots=True)
class ExecutionRelation:
    """Declare one directional execution-time constraint inside a Mission Context."""

    relation_id: str
    kind: str
    source: ExecutionRelationEndpoint
    target: ExecutionRelationEndpoint
    state_key: str | None = None
    spatial_reference: SharedSpatialReference | None = None
    frame_id: str | None = None
    requirement: str | None = None
    policy_id: str | None = None

    @classmethod
    def from_json(cls, value: JSONValue, path: str, version: str) -> ExecutionRelation:
        """Parse one versioned closed relation contract and reject self-reference."""
        item = _object(value, path)
        base_keys = {"id", "kind", "source", "target"}
        typed_keys = {"state_key", "reference", "frame_id", "requirement", "policy_id"}
        if version == MISSION_PLAN_COMPAT_VERSION:
            _exact_keys(item, base_keys, path)
        else:
            _bounded_keys(item, base_keys, typed_keys, path)
        kind = _text(item["kind"], f"{path}.kind")
        if kind not in RELATION_KINDS:
            raise MissionPlanError(f"{path}.kind is unsupported: {kind}")
        if version == MISSION_PLAN_COMPAT_VERSION and kind != "requires-active":
            raise MissionPlanError(f"{path}.kind is unsupported before MissionPlan v0.4: {kind}")
        source = ExecutionRelationEndpoint.from_json(item["source"], f"{path}.source")
        target = ExecutionRelationEndpoint.from_json(item["target"], f"{path}.target")
        if source == target:
            raise MissionPlanError(f"{path} cannot reference the same source and target")

        state_key = None
        spatial_reference = None
        frame_id = None
        requirement = None
        policy_id = None
        if kind in {"group-member-state", "state-requirement", "freshness-requirement"}:
            state_key = _text(item.get("state_key"), f"{path}.state_key")
        if kind == "shared-spatial-reference":
            spatial_reference = SharedSpatialReference.from_json(
                item.get("reference"), f"{path}.reference"
            )
        if kind in {"relative-pose", "relative-distance"}:
            frame_id = _text(item.get("frame_id"), f"{path}.frame_id")
        if kind == "state-requirement":
            requirement = _text(item.get("requirement"), f"{path}.requirement")
            if requirement not in {"available", "unavailable"}:
                raise MissionPlanError(f"{path}.requirement is unsupported: {requirement}")
        if kind == "freshness-requirement":
            policy_id = _text(item.get("policy_id"), f"{path}.policy_id")
        return cls(
            relation_id=_text(item["id"], f"{path}.id"),
            kind=kind,
            source=source,
            target=target,
            state_key=state_key,
            spatial_reference=spatial_reference,
            frame_id=frame_id,
            requirement=requirement,
            policy_id=policy_id,
        )

    def to_json(self) -> JSONObject:
        """Serialize one execution coordination relation."""
        result: JSONObject = {
            "id": self.relation_id,
            "kind": self.kind,
            "source": self.source.to_json(),
            "target": self.target.to_json(),
        }
        if self.state_key is not None:
            result["state_key"] = self.state_key
        if self.spatial_reference is not None:
            result["reference"] = self.spatial_reference.to_json()
        if self.frame_id is not None:
            result["frame_id"] = self.frame_id
        if self.requirement is not None:
            result["requirement"] = self.requirement
        if self.policy_id is not None:
            result["policy_id"] = self.policy_id
        return result


@dataclass(frozen=True, slots=True)
class MissionContext:
    """Describe semantic continuity shared by one or more Tasks."""

    context_id: str
    roles: tuple[ContextRole, ...]
    relations: tuple[ExecutionRelation, ...]
    coupling_mode: str = "independent"
    shared_view: GroupSharedView | None = None
    peer_channel: PeerChannel | None = None

    @classmethod
    def from_json(cls, value: JSONValue, path: str, version: str) -> MissionContext:
        """Parse one versioned Context and reject duplicate ContextRole identities."""
        item = _object(value, path)
        base_keys = {"id", "roles", "relations"}
        if version == MISSION_PLAN_COMPAT_VERSION:
            _exact_keys(item, base_keys, path)
        else:
            _bounded_keys(item, base_keys, {"coupling_mode", "shared_view", "peer_channel"}, path)
        roles = tuple(
            ContextRole.from_json(role, f"{path}.roles[{index}]")
            for index, role in enumerate(_array(item["roles"], f"{path}.roles"))
        )
        if len({role.role_id for role in roles}) != len(roles):
            raise MissionPlanError(f"{path}.roles contains duplicate ids")
        relations = tuple(
            ExecutionRelation.from_json(relation, f"{path}.relations[{index}]", version)
            for index, relation in enumerate(_array(item["relations"], f"{path}.relations"))
        )
        if len({relation.relation_id for relation in relations}) != len(relations):
            raise MissionPlanError(f"{path}.relations contains duplicate ids")
        coupling_mode_value = item.get("coupling_mode")
        coupling_mode = (
            "independent"
            if coupling_mode_value is None
            else _text(coupling_mode_value, f"{path}.coupling_mode")
        )
        if coupling_mode not in COUPLING_MODES:
            raise MissionPlanError(f"{path}.coupling_mode is unsupported: {coupling_mode}")
        shared_view_value = item.get("shared_view")
        shared_view = (
            None
            if shared_view_value is None
            else GroupSharedView.from_json(shared_view_value, f"{path}.shared_view")
        )
        peer_channel_value = item.get("peer_channel")
        peer_channel = (
            None
            if peer_channel_value is None
            else PeerChannel.from_json(peer_channel_value, f"{path}.peer_channel")
        )
        context = cls(
            context_id=_text(item["id"], f"{path}.id"),
            roles=roles,
            relations=relations,
            coupling_mode=coupling_mode,
            shared_view=shared_view,
            peer_channel=peer_channel,
        )
        _validate_coordination_mechanisms(context, coupling_mode, path)
        return context

    def to_json(self, version: str) -> JSONObject:
        """Serialize one Context in its declared MissionPlan version."""
        result: JSONObject = {
            "id": self.context_id,
            "roles": [role.to_json() for role in self.roles],
            "relations": [relation.to_json() for relation in self.relations],
        }
        if version == MISSION_PLAN_VERSION:
            result["coupling_mode"] = self.coupling_mode
            if self.shared_view is not None:
                result["shared_view"] = self.shared_view.to_json()
            if self.peer_channel is not None:
                result["peer_channel"] = self.peer_channel.to_json()
        return result


@dataclass(frozen=True, slots=True)
class MissionTask:
    """Describe one task node, its dependencies, and execution requirements."""

    task_id: str
    description: str
    depends_on: tuple[str, ...]
    roles: tuple[RoleRequirement, ...]
    context_id: str
    coupling_mode: str | None = None

    @classmethod
    def from_json(cls, value: JSONValue, path: str, version: str) -> MissionTask:
        """Parse one versioned task and reject empty or duplicate role requirements."""
        item = _object(value, path)
        base_keys = {"id", "description", "depends_on", "roles", "context_id"}
        if version == MISSION_PLAN_COMPAT_VERSION:
            _exact_keys(item, base_keys, path)
        else:
            _bounded_keys(item, base_keys, {"coupling_mode"}, path)
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
        coupling_mode_value = item.get("coupling_mode")
        coupling_mode = (
            None
            if coupling_mode_value is None
            else _text(coupling_mode_value, f"{path}.coupling_mode")
        )
        if coupling_mode is not None and coupling_mode not in COUPLING_MODES:
            raise MissionPlanError(f"{path}.coupling_mode is unsupported: {coupling_mode}")
        return cls(
            task_id=_text(item["id"], f"{path}.id"),
            description=_text(item["description"], f"{path}.description"),
            depends_on=dependencies,
            roles=roles,
            context_id=_text(item["context_id"], f"{path}.context_id"),
            coupling_mode=coupling_mode,
        )

    def to_json(self, version: str) -> JSONObject:
        """Serialize one task in its declared MissionPlan version."""
        result: JSONObject = {
            "id": self.task_id,
            "description": self.description,
            "depends_on": list(self.depends_on),
            "roles": [role.to_json() for role in self.roles],
            "context_id": self.context_id,
        }
        if version == MISSION_PLAN_VERSION and self.coupling_mode is not None:
            result["coupling_mode"] = self.coupling_mode
        return result


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
        if version not in {MISSION_PLAN_COMPAT_VERSION, MISSION_PLAN_VERSION}:
            raise MissionPlanError(f"unsupported schema_version: {version}")
        tasks = tuple(
            MissionTask.from_json(task, f"tasks[{index}]", version)
            for index, task in enumerate(_array(item["tasks"], "tasks"))
        )
        contexts = tuple(
            MissionContext.from_json(context, f"contexts[{index}]", version)
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
            _validate_coordination_mechanisms(
                context,
                task.coupling_mode or context.coupling_mode,
                f"task {task.task_id}",
            )
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
            "mission": self.mission.to_json(),
            "contexts": [context.to_json(self.schema_version) for context in self.contexts],
            "tasks": [task.to_json(self.schema_version) for task in self.tasks],
        }
