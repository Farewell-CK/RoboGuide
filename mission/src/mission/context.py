"""Mission collaboration Context and execution-relation contract values."""

from __future__ import annotations

from dataclasses import dataclass

from mission.contract_values import (
    COUPLING_MODES,
    GROUP_VIEW_FIELDS,
    MAP_ID_PATTERN,
    MISSION_PLAN_COMPAT_VERSION,
    MISSION_PLAN_COUPLING_VERSION,
    MISSION_PLAN_SATISFACTION_VERSION,
    MISSION_PLAN_SCHEDULING_VERSION,
    MISSION_PLAN_VERSION,
    RELATION_KINDS,
    JSONObject,
    JSONValue,
    MissionPlanError,
    _array,
    _bounded_keys,
    _exact_keys,
    _object,
    _text,
)


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
        if version in {
            MISSION_PLAN_COUPLING_VERSION,
            MISSION_PLAN_SCHEDULING_VERSION,
            MISSION_PLAN_SATISFACTION_VERSION,
            MISSION_PLAN_VERSION,
        }:
            result["coupling_mode"] = self.coupling_mode
            if self.shared_view is not None:
                result["shared_view"] = self.shared_view.to_json()
            if self.peer_channel is not None:
                result["peer_channel"] = self.peer_channel.to_json()
        return result
