"""Task Role requirements and semantic execution values for Mission contracts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from mission.contract_values import (
    CAPABILITIES,
    MISSION_PLAN_SATISFACTION_VERSION,
    MISSION_PLAN_SCHEDULING_VERSION,
    MISSION_PLAN_VERSION,
    RESOURCE_KINDS,
    U32_MAX,
    JSONObject,
    JSONValue,
    MissionPlanError,
    _array,
    _exact_keys,
    _object,
    _text,
)


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
class OperationRef:
    """Identify one canonical semantic operation independently of provider evidence."""

    namespace: str
    name: str
    version: str

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> OperationRef:
        """Parse an operation through the same canonical identity grammar as capabilities."""
        contract = CapabilityContractRef.from_json(value, path)
        return cls(contract.namespace, contract.name, contract.version)

    @classmethod
    def from_legacy_contract(cls, contract: CapabilityContractRef) -> OperationRef:
        """Normalize a legacy executable capability contract into an OperationRef."""
        return cls(contract.namespace, contract.name, contract.version)

    def to_json(self) -> JSONObject:
        """Serialize the canonical operation without adapter-local names."""
        return {
            "namespace": self.namespace,
            "name": self.name,
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class ExecutionIntent:
    """Describe a semantic Local EAIOS objective and canonical operation."""

    operation: OperationRef
    objective: str
    parameters: tuple[tuple[str, str | int | float | bool], ...]

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> ExecutionIntent:
        """Parse current semantic intent or normalize its legacy executable contract."""
        item = _object(value, path)
        current = "operation" in item or "objective" in item
        _exact_keys(
            item,
            {"operation", "objective", "parameters"}
            if current
            else {"capability_contract", "parameters"},
            path,
        )
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
        if current:
            operation = OperationRef.from_json(item["operation"], f"{path}.operation")
            objective = _text(item["objective"], f"{path}.objective")
        else:
            contract = CapabilityContractRef.from_json(
                item["capability_contract"], f"{path}.capability_contract"
            )
            operation = OperationRef.from_legacy_contract(contract)
            objective = f"Execute canonical operation {contract.namespace}.{contract.name}"
        return cls(operation=operation, objective=objective, parameters=tuple(parameters))

    def to_json(self) -> JSONObject:
        """Serialize intent parameters in stable lexical key order."""
        return {
            "operation": self.operation.to_json(),
            "objective": self.objective,
            "parameters": dict(self.parameters),
        }

    def to_legacy_json(self) -> JSONObject:
        """Serialize the pre-v0.7 executable-contract shape for compatibility outputs."""
        return {
            "capability_contract": self.operation.to_json(),
            "parameters": dict(self.parameters),
        }

    @property
    def capability_contract(self) -> CapabilityContractRef:
        """Return a compatibility capability-shaped view of the canonical operation."""
        return CapabilityContractRef(
            self.operation.namespace,
            self.operation.name,
            self.operation.version,
        )


class CapabilityConstraintOperator(StrEnum):
    """Name a closed comparison over one Catalog-defined provider attribute."""

    EQUALS = "equals"
    AT_LEAST = "at-least"
    AT_MOST = "at-most"


@dataclass(frozen=True, slots=True)
class CapabilityConstraint:
    """Constrain one provider capability attribute without naming a concrete Node."""

    attribute: str
    operator: CapabilityConstraintOperator
    value: str | int | float | bool

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> CapabilityConstraint:
        """Parse one typed feasibility predicate from MissionPlan JSON."""
        item = _object(value, path)
        _exact_keys(item, {"attribute", "operator", "value"}, path)
        operator_text = _text(item["operator"], f"{path}.operator")
        try:
            operator = CapabilityConstraintOperator(operator_text)
        except ValueError as error:
            raise MissionPlanError(f"{path}.operator is unsupported: {operator_text}") from error
        constraint_value = item["value"]
        if constraint_value is None or isinstance(constraint_value, (list, dict)):
            raise MissionPlanError(f"{path}.value must be a scalar string, number, or boolean")
        if isinstance(constraint_value, float) and not math.isfinite(constraint_value):
            raise MissionPlanError(f"{path}.value must be finite")
        if operator is not CapabilityConstraintOperator.EQUALS and (
            isinstance(constraint_value, bool) or not isinstance(constraint_value, (int, float))
        ):
            raise MissionPlanError(f"{path}.value must be numeric for {operator.value}")
        return cls(
            attribute=_text(item["attribute"], f"{path}.attribute"),
            operator=operator,
            value=constraint_value,
        )

    def to_json(self) -> JSONObject:
        """Serialize one feasibility predicate."""
        return {
            "attribute": self.attribute,
            "operator": self.operator.value,
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class CapabilityRequirement:
    """Require one exact canonical capability and optional feasibility predicates."""

    contract: CapabilityContractRef
    constraints: tuple[CapabilityConstraint, ...]

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> CapabilityRequirement:
        """Parse one capability requirement and reject duplicate attribute predicates."""
        item = _object(value, path)
        _exact_keys(item, {"contract", "constraints"}, path)
        constraints = tuple(
            CapabilityConstraint.from_json(constraint, f"{path}.constraints[{index}]")
            for index, constraint in enumerate(_array(item["constraints"], f"{path}.constraints"))
        )
        attributes = [constraint.attribute for constraint in constraints]
        if len(set(attributes)) != len(attributes):
            raise MissionPlanError(f"{path}.constraints contains duplicate attributes")
        return cls(
            contract=CapabilityContractRef.from_json(item["contract"], f"{path}.contract"),
            constraints=constraints,
        )

    def to_json(self) -> JSONObject:
        """Serialize one exact capability requirement."""
        return {
            "contract": self.contract.to_json(),
            "constraints": [constraint.to_json() for constraint in self.constraints],
        }


@dataclass(frozen=True, slots=True)
class ResourceRequirement:
    """Require one exclusive resource whose declared capacity meets a minimum."""

    kind: str
    units: int

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> ResourceRequirement:
        """Parse a positive resource demand from current MissionPlan JSON."""
        item = _object(value, path)
        _exact_keys(item, {"kind", "units"}, path)
        kind = _text(item["kind"], f"{path}.kind")
        if kind not in RESOURCE_KINDS:
            raise MissionPlanError(f"{path}.kind is unsupported: {kind}")
        units = item["units"]
        if isinstance(units, bool) or not isinstance(units, int) or units <= 0 or units > U32_MAX:
            raise MissionPlanError(f"{path}.units must be a positive 32-bit integer")
        return cls(kind=kind, units=units)

    def to_json(self) -> JSONObject:
        """Serialize one provider-independent quantitative resource demand."""
        return {"kind": self.kind, "units": self.units}


@dataclass(frozen=True, slots=True)
class RoleRequirement:
    """Describe one TaskRole through ContextRole, requirements, and execution intent."""

    role_id: str
    capabilities: tuple[CapabilityRequirement, ...]
    resources: tuple[ResourceRequirement, ...]
    execution: ExecutionIntent
    context_role: str | None
    resource_scope: str
    legacy_actor_id: str | None = None
    legacy_capability: str | None = None

    @classmethod
    def from_json(cls, value: JSONValue, path: str, version: str) -> RoleRequirement:
        """Parse and validate one role requirement from contract JSON."""
        item = _object(value, path)
        if version == MISSION_PLAN_VERSION:
            _exact_keys(
                item,
                {"id", "requirements", "execution_intent", "context_role", "resource_scope"},
                path,
            )
            requirements = _object(item["requirements"], f"{path}.requirements")
            _exact_keys(requirements, {"capabilities", "resources"}, f"{path}.requirements")
            capabilities = tuple(
                CapabilityRequirement.from_json(
                    capability, f"{path}.requirements.capabilities[{index}]"
                )
                for index, capability in enumerate(
                    _array(requirements["capabilities"], f"{path}.requirements.capabilities")
                )
            )
            if not capabilities:
                raise MissionPlanError(f"{path}.requirements.capabilities must not be empty")
            contracts = [capability.contract for capability in capabilities]
            if len(set(contracts)) != len(contracts):
                raise MissionPlanError(f"{path}.requirements.capabilities contains duplicates")
            resources = tuple(
                ResourceRequirement.from_json(resource, f"{path}.requirements.resources[{index}]")
                for index, resource in enumerate(
                    _array(requirements["resources"], f"{path}.requirements.resources")
                )
            )
            if len({resource.kind for resource in resources}) != len(resources):
                raise MissionPlanError(f"{path}.requirements.resources contains duplicate kinds")
            context_role = _text(item["context_role"], f"{path}.context_role")
            resource_scope = _text(item["resource_scope"], f"{path}.resource_scope")
            if resource_scope not in {"task", "context"}:
                raise MissionPlanError(f"{path}.resource_scope is unsupported: {resource_scope}")
            return cls(
                role_id=_text(item["id"], f"{path}.id"),
                capabilities=capabilities,
                resources=resources,
                execution=ExecutionIntent.from_json(
                    item["execution_intent"], f"{path}.execution_intent"
                ),
                context_role=context_role,
                resource_scope=resource_scope,
            )
        common = {
            "id",
            "actor",
            "capability",
            "contract",
            "execution",
            "context_role",
            "resource_scope",
        }
        _exact_keys(
            item,
            common
            | (
                {"resources"}
                if version in {MISSION_PLAN_SCHEDULING_VERSION, MISSION_PLAN_SATISFACTION_VERSION}
                else {"resource_kind"}
            ),
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
        if version in {MISSION_PLAN_SCHEDULING_VERSION, MISSION_PLAN_SATISFACTION_VERSION}:
            resources = tuple(
                ResourceRequirement.from_json(resource, f"{path}.resources[{index}]")
                for index, resource in enumerate(_array(item["resources"], f"{path}.resources"))
            )
            if len({resource.kind for resource in resources}) != len(resources):
                raise MissionPlanError(f"{path}.resources contains duplicate kinds")
            resource_value = resources[0].kind if len(resources) == 1 else None
        else:
            legacy_resource = item["resource_kind"]
            if legacy_resource is not None and not isinstance(legacy_resource, str):
                raise MissionPlanError(f"{path}.resource_kind must be text or null")
            resource_value = legacy_resource
            if resource_value is not None and resource_value not in RESOURCE_KINDS:
                raise MissionPlanError(f"{path}.resource_kind is unsupported: {resource_value}")
            resources = (
                ()
                if resource_value is None
                else (ResourceRequirement(kind=resource_value, units=1),)
            )
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
            capabilities=(CapabilityRequirement(contract, ()),),
            resources=resources,
            execution=execution,
            context_role=context_role_value,
            resource_scope=resource_scope,
            legacy_actor_id=_text(item["actor"], f"{path}.actor"),
            legacy_capability=capability,
        )

    def to_json(self, version: str) -> JSONObject:
        """Serialize the role requirement without provider-specific values."""
        if version == MISSION_PLAN_VERSION:
            if self.context_role is None:
                raise MissionPlanError("normalized Role requires a ContextRole reference")
            return {
                "id": self.role_id,
                "context_role": self.context_role,
                "requirements": {
                    "capabilities": [capability.to_json() for capability in self.capabilities],
                    "resources": [resource.to_json() for resource in self.resources],
                },
                "execution_intent": self.execution.to_json(),
                "resource_scope": self.resource_scope,
            }
        if self.legacy_actor_id is None or self.legacy_capability is None:
            raise MissionPlanError("normalized Role cannot be serialized as a legacy MissionPlan")
        contract = self.capabilities[0].contract
        result: JSONObject = {
            "id": self.role_id,
            "actor": self.legacy_actor_id,
            "capability": self.legacy_capability,
            "contract": contract.to_json(),
            "execution": self.execution.to_legacy_json(),
            "context_role": self.context_role,
            "resource_scope": self.resource_scope,
        }
        if version in {MISSION_PLAN_SCHEDULING_VERSION, MISSION_PLAN_SATISFACTION_VERSION}:
            result["resources"] = [resource.to_json() for resource in self.resources]
        else:
            result["resource_kind"] = self.resources[0].kind if len(self.resources) == 1 else None
        return result

    @property
    def actor_id(self) -> str | None:
        """Return only legacy duplicated Actor evidence; current plans resolve via ContextRole."""
        return self.legacy_actor_id

    @property
    def capability(self) -> str | None:
        """Return only the legacy coarse capability hint."""
        return self.legacy_capability

    @property
    def resource_kind(self) -> str | None:
        """Return the legacy single-resource view when exactly one requirement exists."""
        return self.resources[0].kind if len(self.resources) == 1 else None
