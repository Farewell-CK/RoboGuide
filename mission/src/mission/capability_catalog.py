"""Deployment-independent canonical capability vocabulary and plan validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import cast

from mission.models import (
    CapabilityContractRef,
    ExecutionIntent,
    JSONObject,
    JSONValue,
    MissionPlan,
    MissionPlanError,
)

CAPABILITY_CATALOG_SCHEMA = "roboguide.capability-catalog/v0.1"


class CapabilityCatalogError(ValueError):
    """Report malformed Catalog artifacts or Mission intents outside the vocabulary."""


class CapabilityParameterType(StrEnum):
    """Describe scalar parameter types supported by MissionPlan v0.5."""

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
class CanonicalCapabilityContract:
    """Describe one known canonical execution contract and its closed parameters."""

    contract: CapabilityContractRef
    description: str
    parameters: tuple[CapabilityParameterDefinition, ...]

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> CanonicalCapabilityContract:
        """Parse one contract definition and reject duplicate parameter names."""
        item = _object(value, path)
        _exact_keys(item, {"contract", "description", "parameters"}, path)
        try:
            contract = CapabilityContractRef.from_json(item["contract"], f"{path}.contract")
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
        return cls(
            contract=contract,
            description=_text(item["description"], f"{path}.description"),
            parameters=parameters,
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
            "contract": self.contract.to_json(),
            "description": self.description,
            "parameters": [parameter.to_json() for parameter in self.parameters],
        }


@dataclass(frozen=True, slots=True)
class CanonicalCapabilityCatalog:
    """Hold the stable Mission semantic vocabulary independently of live providers."""

    schema_version: str
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
        _exact_keys(item, {"schema_version", "contracts"}, "capability_catalog")
        schema_version = _text(item["schema_version"], "capability_catalog.schema_version")
        if schema_version != CAPABILITY_CATALOG_SCHEMA:
            raise CapabilityCatalogError(f"unsupported Capability Catalog: {schema_version}")
        contracts = tuple(
            sorted(
                (
                    CanonicalCapabilityContract.from_json(contract, f"contracts[{index}]")
                    for index, contract in enumerate(_array(item["contracts"], "contracts"))
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
        return cls(schema_version=schema_version, contracts=contracts)

    def validate_intent(self, intent: ExecutionIntent, path: str) -> None:
        """Validate one intent against an exact known contract and parameter definition."""
        definition = next(
            (
                candidate
                for candidate in self.contracts
                if candidate.contract == intent.capability_contract
            ),
            None,
        )
        if definition is None:
            raise CapabilityCatalogError(
                f"{path}.capability_contract is unknown: {_contract_id(intent.capability_contract)}"
            )
        definition.validate_intent(intent, path)

    def validate_plan(self, plan: MissionPlan) -> None:
        """Validate every Role intent before semantic review or Mission submission."""
        for task_index, task in enumerate(plan.tasks):
            for role_index, role in enumerate(task.roles):
                self.validate_intent(
                    role.execution,
                    f"tasks[{task_index}].roles[{role_index}].execution",
                )

    def contract_ids(self) -> tuple[str, ...]:
        """Return stable canonical identities for diagnostics and deterministic tests."""
        return tuple(_contract_id(definition.contract) for definition in self.contracts)

    def to_json(self) -> JSONObject:
        """Serialize the complete deployment-independent Planner/Reviewer context."""
        return {
            "schema_version": self.schema_version,
            "contracts": [definition.to_json() for definition in self.contracts],
        }
