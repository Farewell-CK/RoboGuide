"""Deployment-independent canonical capability vocabulary and plan validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import cast

from mission.models import (
    CapabilityContractRef,
    CapabilityRequirement,
    ExecutionIntent,
    JSONObject,
    JSONValue,
    MissionPlan,
    MissionPlanError,
    TaskSatisfactionBasis,
)

CAPABILITY_CATALOG_SCHEMA = "roboguide.capability-catalog/v0.2"
_LEGACY_CAPABILITY_CATALOG_SCHEMA = "roboguide.capability-catalog/v0.1"


class CapabilityCatalogError(ValueError):
    """Report malformed Catalog artifacts or Mission intents outside the vocabulary."""


class CapabilityParameterType(StrEnum):
    """Describe scalar parameter types supported by the current MissionPlan boundary."""

    BOOLEAN = "boolean"
    INTEGER = "integer"
    NUMBER = "number"
    STRING = "string"

    def accepts(self, value: object) -> bool:
        """Return whether one scalar value exactly satisfies this Catalog type."""
        if self is CapabilityParameterType.BOOLEAN:
            return isinstance(value, bool)
        if self is CapabilityParameterType.INTEGER:
            return isinstance(value, int) and not isinstance(value, bool)
        if self is CapabilityParameterType.NUMBER:
            return isinstance(value, int | float) and not isinstance(value, bool)
        return isinstance(value, str)


def _object(value: JSONValue | object, path: str) -> JSONObject:
    """Return a string-keyed JSON object or reject malformed Catalog input."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise CapabilityCatalogError(f"{path} must be an object")
    return cast(JSONObject, value)


def _array(value: JSONValue | object, path: str) -> list[JSONValue]:
    """Return one JSON array or reject malformed Catalog input."""
    if not isinstance(value, list):
        raise CapabilityCatalogError(f"{path} must be an array")
    return cast(list[JSONValue], value)


def _text(value: JSONValue | object, path: str) -> str:
    """Return nonblank text while preserving the declared Catalog spelling."""
    if not isinstance(value, str) or not value.strip():
        raise CapabilityCatalogError(f"{path} must be nonblank text")
    return value


def _exact_keys(value: JSONObject, expected: set[str], path: str) -> None:
    """Reject missing or unknown Catalog fields so format drift remains explicit."""
    if set(value) != expected:
        missing = sorted(expected - set(value))
        unknown = sorted(set(value) - expected)
        raise CapabilityCatalogError(f"{path} fields differ; missing={missing}, unknown={unknown}")


def _contract_id(contract: CapabilityContractRef) -> str:
    """Format an exact canonical contract identity using ADR-0017 rules."""
    return f"{contract.namespace}.{contract.name}@{contract.version}"


def _operation_id(intent: ExecutionIntent) -> str:
    """Format the canonical OperationRef carried by one ExecutionIntent."""
    return f"{intent.operation.namespace}.{intent.operation.name}@{intent.operation.version}"


@dataclass(frozen=True, slots=True)
class CapabilityParameterDefinition:
    """Define one closed scalar parameter accepted by a canonical contract."""

    name: str
    value_type: CapabilityParameterType
    required: bool
    description: str

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> CapabilityParameterDefinition:
        """Parse one parameter definition without accepting unversioned extensions."""
        item = _object(value, path)
        _exact_keys(item, {"name", "type", "required", "description"}, path)
        type_text = _text(item["type"], f"{path}.type")
        try:
            value_type = CapabilityParameterType(type_text)
        except ValueError as error:
            raise CapabilityCatalogError(f"{path}.type is unsupported: {type_text}") from error
        required = item["required"]
        if not isinstance(required, bool):
            raise CapabilityCatalogError(f"{path}.required must be a boolean")
        return cls(
            name=_text(item["name"], f"{path}.name"),
            value_type=value_type,
            required=required,
            description=_text(item["description"], f"{path}.description"),
        )

    def to_json(self) -> JSONObject:
        """Serialize one parameter definition for Planner and Reviewer context."""
        return {
            "name": self.name,
            "type": self.value_type.value,
            "required": self.required,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class CapabilityAttributeDefinition:
    """Define one typed provider attribute usable in feasibility predicates."""

    name: str
    value_type: CapabilityParameterType
    description: str

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> CapabilityAttributeDefinition:
        """Parse one closed attribute declaration."""
        item = _object(value, path)
        _exact_keys(item, {"name", "type", "description"}, path)
        type_text = _text(item["type"], f"{path}.type")
        try:
            value_type = CapabilityParameterType(type_text)
        except ValueError as error:
            raise CapabilityCatalogError(f"{path}.type is unsupported: {type_text}") from error
        return cls(
            name=_text(item["name"], f"{path}.name"),
            value_type=value_type,
            description=_text(item["description"], f"{path}.description"),
        )

    def to_json(self) -> JSONObject:
        """Serialize one provider attribute definition."""
        return {
            "name": self.name,
            "type": self.value_type.value,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class CanonicalCapabilityDefinition:
    """Define one provider-independent capability and its feasibility attributes."""

    contract: CapabilityContractRef
    description: str
    attributes: tuple[CapabilityAttributeDefinition, ...]

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> CanonicalCapabilityDefinition:
        """Parse one capability and reject duplicate attribute identities."""
        item = _object(value, path)
        _exact_keys(item, {"contract", "description", "attributes"}, path)
        try:
            contract = CapabilityContractRef.from_json(item["contract"], f"{path}.contract")
        except MissionPlanError as error:
            raise CapabilityCatalogError(str(error)) from error
        attributes = tuple(
            sorted(
                (
                    CapabilityAttributeDefinition.from_json(
                        attribute, f"{path}.attributes[{index}]"
                    )
                    for index, attribute in enumerate(
                        _array(item["attributes"], f"{path}.attributes")
                    )
                ),
                key=lambda attribute: attribute.name,
            )
        )
        names = [attribute.name for attribute in attributes]
        if len(set(names)) != len(names):
            raise CapabilityCatalogError(f"{path}.attributes contains duplicate names")
        return cls(
            contract=contract,
            description=_text(item["description"], f"{path}.description"),
            attributes=attributes,
        )

    def validate_requirement(self, requirement: CapabilityRequirement, path: str) -> None:
        """Reject unknown, duplicated, or incorrectly typed capability predicates."""
        definitions = {attribute.name: attribute for attribute in self.attributes}
        for constraint in requirement.constraints:
            definition = definitions.get(constraint.attribute)
            if definition is None:
                raise CapabilityCatalogError(
                    f"{path}.constraints.{constraint.attribute} is not defined by the Catalog"
                )
            if not definition.value_type.accepts(constraint.value):
                raise CapabilityCatalogError(
                    f"{path}.constraints.{constraint.attribute} must be "
                    f"{definition.value_type.value}"
                )

    def to_json(self) -> JSONObject:
        """Serialize one capability definition in stable attribute order."""
        return {
            "contract": self.contract.to_json(),
            "description": self.description,
            "attributes": [attribute.to_json() for attribute in self.attributes],
        }


@dataclass(frozen=True, slots=True)
class CanonicalCapabilityContract:
    """Describe one known canonical operation and its closed parameters."""

    contract: CapabilityContractRef
    description: str
    parameters: tuple[CapabilityParameterDefinition, ...]
    required_capabilities: tuple[CapabilityContractRef, ...] = ()

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> CanonicalCapabilityContract:
        """Parse one contract definition and reject duplicate parameter names."""
        item = _object(value, path)
        _exact_keys(
            item,
            {"operation", "description", "parameters", "required_capabilities"}
            if "operation" in item
            else {"contract", "description", "parameters"},
            path,
        )
        try:
            contract_field = "operation" if "operation" in item else "contract"
            contract = CapabilityContractRef.from_json(
                item[contract_field], f"{path}.{contract_field}"
            )
        except MissionPlanError as error:
            raise CapabilityCatalogError(str(error)) from error
        parameters = tuple(
            sorted(
                (
                    CapabilityParameterDefinition.from_json(
                        parameter, f"{path}.parameters[{index}]"
                    )
                    for index, parameter in enumerate(
                        _array(item["parameters"], f"{path}.parameters")
                    )
                ),
                key=lambda parameter: parameter.name,
            )
        )
        names = [parameter.name for parameter in parameters]
        if len(set(names)) != len(names):
            raise CapabilityCatalogError(f"{path}.parameters contains duplicate names")
        required_capabilities = (
            tuple(
                CapabilityContractRef.from_json(
                    capability, f"{path}.required_capabilities[{index}]"
                )
                for index, capability in enumerate(
                    _array(item["required_capabilities"], f"{path}.required_capabilities")
                )
            )
            if "required_capabilities" in item
            else (contract,)
        )
        if not required_capabilities or len(set(required_capabilities)) != len(
            required_capabilities
        ):
            raise CapabilityCatalogError(
                f"{path}.required_capabilities must be nonempty and unique"
            )
        return cls(
            contract=contract,
            description=_text(item["description"], f"{path}.description"),
            parameters=parameters,
            required_capabilities=required_capabilities,
        )

    def validate_intent(self, intent: ExecutionIntent, path: str) -> None:
        """Reject missing, unknown, or incorrectly typed parameters atomically."""
        definitions = {parameter.name: parameter for parameter in self.parameters}
        values = dict(intent.parameters)
        missing = sorted(
            parameter.name
            for parameter in self.parameters
            if parameter.required and parameter.name not in values
        )
        unknown = sorted(set(values) - set(definitions))
        if missing or unknown:
            raise CapabilityCatalogError(
                f"{path}.parameters differ from Catalog; missing={missing}, unknown={unknown}"
            )
        for name, value in values.items():
            definition = definitions[name]
            if not definition.value_type.accepts(value):
                raise CapabilityCatalogError(
                    f"{path}.parameters.{name} must be {definition.value_type.value}"
                )

    def to_json(self) -> JSONObject:
        """Serialize one definition with stable parameter ordering."""
        return {
            "operation": self.contract.to_json(),
            "description": self.description,
            "parameters": [parameter.to_json() for parameter in self.parameters],
            "required_capabilities": [
                capability.to_json() for capability in self.required_capabilities
            ],
        }

    def to_legacy_json(self) -> JSONObject:
        """Serialize one operation in the v0.1 combined contract shape."""
        return {
            "contract": self.contract.to_json(),
            "description": self.description,
            "parameters": [parameter.to_json() for parameter in self.parameters],
        }


@dataclass(frozen=True, slots=True)
class CanonicalCapabilityCatalog:
    """Hold the stable Mission semantic vocabulary independently of live providers."""

    schema_version: str
    capabilities: tuple[CanonicalCapabilityDefinition, ...]
    contracts: tuple[CanonicalCapabilityContract, ...]

    @classmethod
    def load(cls, path: Path) -> CanonicalCapabilityCatalog:
        """Load and validate one versioned Catalog artifact from disk."""
        try:
            decoded: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CapabilityCatalogError(
                f"cannot load Capability Catalog {path}: {error}"
            ) from error
        return cls.from_json(decoded)

    @classmethod
    def from_json(cls, value: object) -> CanonicalCapabilityCatalog:
        """Parse a complete Catalog and establish deterministic contract ordering."""
        item = _object(value, "capability_catalog")
        schema_version = _text(item["schema_version"], "capability_catalog.schema_version")
        if schema_version == CAPABILITY_CATALOG_SCHEMA:
            _exact_keys(
                item,
                {"schema_version", "capabilities", "operations"},
                "capability_catalog",
            )
        elif schema_version == _LEGACY_CAPABILITY_CATALOG_SCHEMA:
            _exact_keys(item, {"schema_version", "contracts"}, "capability_catalog")
        else:
            raise CapabilityCatalogError(f"unsupported Capability Catalog: {schema_version}")
        operation_field = (
            "operations" if schema_version == CAPABILITY_CATALOG_SCHEMA else "contracts"
        )
        contracts = tuple(
            sorted(
                (
                    CanonicalCapabilityContract.from_json(contract, f"contracts[{index}]")
                    for index, contract in enumerate(_array(item[operation_field], operation_field))
                ),
                key=lambda definition: _contract_id(definition.contract),
            )
        )
        if not contracts:
            raise CapabilityCatalogError("capability_catalog.contracts must not be empty")
        identities = [_contract_id(definition.contract) for definition in contracts]
        if len(set(identities)) != len(identities):
            raise CapabilityCatalogError(
                "capability_catalog.contracts contains duplicate identities"
            )
        capabilities = (
            tuple(
                sorted(
                    (
                        CanonicalCapabilityDefinition.from_json(
                            capability, f"capabilities[{index}]"
                        )
                        for index, capability in enumerate(
                            _array(item["capabilities"], "capabilities")
                        )
                    ),
                    key=lambda definition: _contract_id(definition.contract),
                )
            )
            if schema_version == CAPABILITY_CATALOG_SCHEMA
            else tuple(
                CanonicalCapabilityDefinition(contract.contract, contract.description, ())
                for contract in contracts
            )
        )
        capability_ids = [_contract_id(definition.contract) for definition in capabilities]
        if not capabilities or len(set(capability_ids)) != len(capability_ids):
            raise CapabilityCatalogError(
                "capability_catalog.capabilities must be nonempty and unique"
            )
        known_capabilities = set(capability_ids)
        for operation in contracts:
            unknown = [
                _contract_id(required)
                for required in operation.required_capabilities
                if _contract_id(required) not in known_capabilities
            ]
            if unknown:
                raise CapabilityCatalogError(
                    f"operation {_contract_id(operation.contract)} requires unknown capabilities: "
                    f"{unknown}"
                )
        return cls(
            schema_version=schema_version,
            capabilities=capabilities,
            contracts=contracts,
        )

    def validate_intent(self, intent: ExecutionIntent, path: str) -> None:
        """Validate one intent against an exact known contract and parameter definition."""
        definition = next(
            (
                candidate
                for candidate in self.contracts
                if _contract_id(candidate.contract) == _operation_id(intent)
            ),
            None,
        )
        if definition is None:
            raise CapabilityCatalogError(f"{path}.operation is unknown: {_operation_id(intent)}")
        definition.validate_intent(intent, path)

    def validate_plan(self, plan: MissionPlan) -> None:
        """Validate every Role requirement and intent without consulting live providers."""
        for task_index, task in enumerate(plan.tasks):
            for role_index, role in enumerate(task.roles):
                role_capabilities = {_contract_id(item.contract) for item in role.capabilities}
                for requirement_index, requirement in enumerate(role.capabilities):
                    definition = next(
                        (
                            candidate
                            for candidate in self.capabilities
                            if candidate.contract == requirement.contract
                        ),
                        None,
                    )
                    if definition is None:
                        raise CapabilityCatalogError(
                            "tasks"
                            f"[{task_index}].roles[{role_index}].requirements.capabilities"
                            f"[{requirement_index}] is unknown: "
                            f"{_contract_id(requirement.contract)}"
                        )
                    definition.validate_requirement(
                        requirement,
                        "tasks"
                        f"[{task_index}].roles[{role_index}].requirements.capabilities"
                        f"[{requirement_index}]",
                    )
                self.validate_intent(
                    role.execution,
                    f"tasks[{task_index}].roles[{role_index}].execution_intent",
                )
                operation = next(
                    candidate
                    for candidate in self.contracts
                    if _contract_id(candidate.contract) == _operation_id(role.execution)
                )
                missing = [
                    _contract_id(required)
                    for required in operation.required_capabilities
                    if _contract_id(required) not in role_capabilities
                ]
                if missing:
                    raise CapabilityCatalogError(
                        f"tasks[{task_index}].roles[{role_index}] lacks operation baseline "
                        f"capabilities: {missing}"
                    )
            if task.satisfaction.basis is TaskSatisfactionBasis.VERIFIER_EVIDENCE:
                verifier = task.satisfaction.verifier
                if verifier is None:
                    raise CapabilityCatalogError(
                        f"tasks[{task_index}].satisfaction lacks verifier policy"
                    )
                if all(
                    definition.contract != verifier.contract for definition in self.capabilities
                ):
                    raise CapabilityCatalogError(
                        f"tasks[{task_index}].satisfaction verifier is unknown: "
                        f"{_contract_id(verifier.contract)}"
                    )

    def contract_ids(self) -> tuple[str, ...]:
        """Return stable canonical identities for diagnostics and deterministic tests."""
        return tuple(_contract_id(definition.contract) for definition in self.contracts)

    def to_json(self) -> JSONObject:
        """Serialize the complete deployment-independent Planner/Reviewer context."""
        if self.schema_version == _LEGACY_CAPABILITY_CATALOG_SCHEMA:
            return {
                "schema_version": self.schema_version,
                "contracts": [definition.to_legacy_json() for definition in self.contracts],
            }
        return {
            "schema_version": CAPABILITY_CATALOG_SCHEMA,
            "capabilities": [definition.to_json() for definition in self.capabilities],
            "operations": [definition.to_json() for definition in self.contracts],
        }
