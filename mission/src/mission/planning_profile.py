"""Versioned deployment-owned capability facts for Mission Intelligence planning."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.models import (
    CapabilityContractRef,
    JSONObject,
    JSONValue,
    MissionPlanError,
)

PLANNING_PROFILE_SCHEMA = "roboguide.deployment-planning-profile/v0.2"
LEGACY_PLANNING_PROFILE_SCHEMA = "roboguide.deployment-planning-profile/v0.1"
_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
_EFFECT_DOMAIN = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")


class PlanningProfileError(ValueError):
    """Report a malformed or untrusted deployment planning profile."""


def _object(value: object, path: str) -> JSONObject:
    """Return a string-keyed JSON object or fail with its contract path."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise PlanningProfileError(f"{path} must be an object")
    return cast(JSONObject, value)


def _array(value: object, path: str) -> list[JSONValue]:
    """Return a JSON array or fail with its contract path."""
    if not isinstance(value, list):
        raise PlanningProfileError(f"{path} must be an array")
    return cast(list[JSONValue], value)


def _text(value: object, path: str) -> str:
    """Return nonblank text without accepting provider-generated placeholders."""
    if not isinstance(value, str) or not value.strip():
        raise PlanningProfileError(f"{path} must be nonblank text")
    return value


def _exact_keys(value: JSONObject, expected: set[str], path: str) -> None:
    """Reject missing and unknown keys so profile evolution stays versioned."""
    actual = set(value)
    if actual != expected:
        raise PlanningProfileError(
            f"{path} keys mismatch: missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


def _digest(value: JSONObject) -> str:
    """Return the canonical digest used to bind profile evidence to its content."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


@dataclass(frozen=True, slots=True)
class PlanningCapabilityFact:
    """Describe one abstract capability class fact without naming a provider instance."""

    contract: CapabilityContractRef
    attributes: tuple[tuple[str, str | int | float | bool], ...]

    @classmethod
    def from_json(cls, value: object, path: str) -> PlanningCapabilityFact:
        """Parse one capability fact with stable scalar attributes."""
        item = _object(value, path)
        _exact_keys(item, {"contract", "attributes"}, path)
        raw_attributes = _object(item["attributes"], f"{path}.attributes")
        attributes: list[tuple[str, str | int | float | bool]] = []
        for raw_name in sorted(raw_attributes):
            name = _text(raw_name, f"{path}.attributes.name")
            attribute = raw_attributes[name]
            if (
                not isinstance(attribute, (str, int, float, bool))
                or isinstance(attribute, float)
                and not math.isfinite(attribute)
            ):
                raise PlanningProfileError(
                    f"{path}.attributes.{name} must be a finite scalar string, number, or boolean"
                )
            attributes.append((name, attribute))
        try:
            contract = CapabilityContractRef.from_json(item["contract"], f"{path}.contract")
        except MissionPlanError as error:
            raise PlanningProfileError(str(error)) from error
        return cls(contract=contract, attributes=tuple(attributes))

    def to_json(self) -> JSONObject:
        """Serialize one capability fact without deployment instance identity."""
        return {
            "contract": self.contract.to_json(),
            "attributes": {name: value for name, value in self.attributes},
        }


@dataclass(frozen=True, slots=True)
class PlanningCapabilityClass:
    """Group equivalent deployment capability facts under an abstract class identity."""

    class_id: str
    description: str
    capabilities: tuple[PlanningCapabilityFact, ...]

    @classmethod
    def from_json(cls, value: object, path: str) -> PlanningCapabilityClass:
        """Parse one abstract class and reject duplicate capability contracts."""
        item = _object(value, path)
        _exact_keys(item, {"id", "description", "capabilities"}, path)
        capabilities = tuple(
            PlanningCapabilityFact.from_json(capability, f"{path}.capabilities[{index}]")
            for index, capability in enumerate(_array(item["capabilities"], f"{path}.capabilities"))
        )
        if not capabilities:
            raise PlanningProfileError(f"{path}.capabilities must not be empty")
        contracts = [capability.contract for capability in capabilities]
        if len(set(contracts)) != len(contracts):
            raise PlanningProfileError(f"{path}.capabilities contains duplicate contracts")
        return cls(
            class_id=_text(item["id"], f"{path}.id"),
            description=_text(item["description"], f"{path}.description"),
            capabilities=capabilities,
        )

    def to_json(self) -> JSONObject:
        """Serialize one abstract class with deterministic capability ordering."""
        return {
            "id": self.class_id,
            "description": self.description,
            "capabilities": [capability.to_json() for capability in self.capabilities],
        }


@dataclass(frozen=True, slots=True)
class PlanningStateEffect:
    """Declare a neutral state domain read or possibly changed by an operation."""

    domain: str
    access: str
    subject_scope: str
    subject_parameter: str | None
    result_parameter: str | None

    @classmethod
    def from_json(cls, value: object, path: str) -> PlanningStateEffect:
        """Parse a bounded state access whose subject and result bind to operation parameters."""
        item = _object(value, path)
        _exact_keys(
            item,
            {"domain", "access", "subject_scope", "subject_parameter", "result_parameter"},
            path,
        )
        domain = _text(item["domain"], f"{path}.domain")
        if _EFFECT_DOMAIN.fullmatch(domain) is None:
            raise PlanningProfileError(f"{path}.domain is not a canonical state domain")
        access = _text(item["access"], f"{path}.access")
        if access not in {"read", "may_change"}:
            raise PlanningProfileError(f"{path}.access must be read or may_change")
        subject_scope = _text(item["subject_scope"], f"{path}.subject_scope")
        if subject_scope not in {"actor", "world"}:
            raise PlanningProfileError(f"{path}.subject_scope must be actor or world")
        subject_parameter = item["subject_parameter"]
        result_parameter = item["result_parameter"]
        if subject_parameter is not None:
            subject_parameter = _text(subject_parameter, f"{path}.subject_parameter")
        if result_parameter is not None:
            result_parameter = _text(result_parameter, f"{path}.result_parameter")
        if subject_scope == "world" and subject_parameter is None:
            raise PlanningProfileError(f"{path} world scope requires a subject_parameter")
        if subject_scope == "actor" and subject_parameter is not None:
            raise PlanningProfileError(f"{path} actor scope cannot name a world subject")
        return cls(domain, access, subject_scope, subject_parameter, result_parameter)

    def to_json(self) -> JSONObject:
        """Serialize one effect without naming a Node or physical entity."""
        return {
            "domain": self.domain,
            "access": self.access,
            "subject_scope": self.subject_scope,
            "subject_parameter": self.subject_parameter,
            "result_parameter": self.result_parameter,
        }


@dataclass(frozen=True, slots=True)
class PlanningOperationEffects:
    """Publish known possible effects and the completeness of that declaration."""

    operation: CapabilityContractRef
    coverage: str
    effects: tuple[PlanningStateEffect, ...]

    @classmethod
    def from_json(cls, value: object, path: str) -> PlanningOperationEffects:
        """Parse one operation declaration; omitted operations remain unknown."""
        item = _object(value, path)
        _exact_keys(item, {"operation", "coverage", "effects"}, path)
        try:
            operation = CapabilityContractRef.from_json(item["operation"], f"{path}.operation")
        except MissionPlanError as error:
            raise PlanningProfileError(str(error)) from error
        coverage = _text(item["coverage"], f"{path}.coverage")
        if coverage not in {"complete", "partial"}:
            raise PlanningProfileError(f"{path}.coverage must be complete or partial")
        effects = tuple(
            PlanningStateEffect.from_json(effect, f"{path}.effects[{index}]")
            for index, effect in enumerate(_array(item["effects"], f"{path}.effects"))
        )
        effect_keys = [
            (
                effect.domain,
                effect.access,
                effect.subject_scope,
                effect.subject_parameter,
                effect.result_parameter,
            )
            for effect in effects
        ]
        if len(set(effect_keys)) != len(effect_keys):
            raise PlanningProfileError(f"{path}.effects contains duplicate declarations")
        return cls(operation, coverage, effects)

    def to_json(self) -> JSONObject:
        """Serialize one operation effect profile in stable order."""
        return {
            "operation": self.operation.to_json(),
            "coverage": self.coverage,
            "effects": [effect.to_json() for effect in self.effects],
        }


@dataclass(frozen=True, slots=True)
class PlanningStateDomain:
    """Name and explain one deployment-neutral state domain used by effect declarations."""

    domain_id: str
    description: str

    @classmethod
    def from_json(cls, value: object, path: str) -> PlanningStateDomain:
        """Parse one documented state-domain identity."""
        item = _object(value, path)
        _exact_keys(item, {"id", "description"}, path)
        domain_id = _text(item["id"], f"{path}.id")
        if _EFFECT_DOMAIN.fullmatch(domain_id) is None:
            raise PlanningProfileError(f"{path}.id is not a canonical state domain")
        return cls(domain_id, _text(item["description"], f"{path}.description"))

    def to_json(self) -> JSONObject:
        """Serialize one neutral state-domain description."""
        return {"id": self.domain_id, "description": self.description}


@dataclass(frozen=True, slots=True)
class DeploymentPlanningProfile:
    """Freeze deployment capability classes for one MI deliberation process."""

    profile_id: str
    revision: str
    source: str
    capability_classes: tuple[PlanningCapabilityClass, ...]
    profile_digest: str
    schema_version: str = LEGACY_PLANNING_PROFILE_SCHEMA
    state_domains: tuple[PlanningStateDomain, ...] = ()
    operation_effects: tuple[PlanningOperationEffects, ...] = ()

    @classmethod
    def from_json(cls, value: object) -> DeploymentPlanningProfile:
        """Parse and digest-check a deployment-owned profile artifact."""
        item = _object(value, "deployment_planning_profile")
        schema = item.get("schema_version")
        if schema == PLANNING_PROFILE_SCHEMA:
            expected = {
                "schema_version",
                "authority",
                "identity",
                "capability_classes",
                "state_domains",
                "operation_effects",
                "digest",
            }
        elif schema == LEGACY_PLANNING_PROFILE_SCHEMA:
            expected = {"schema_version", "authority", "identity", "capability_classes", "digest"}
        else:
            raise PlanningProfileError("unsupported deployment planning profile schema")
        _exact_keys(item, expected, "deployment_planning_profile")
        if item["authority"] != "deployment-owned":
            raise PlanningProfileError("deployment planning profile authority is unsupported")
        identity = _object(item["identity"], "deployment_planning_profile.identity")
        _exact_keys(
            identity, {"profile_id", "revision", "source"}, "deployment_planning_profile.identity"
        )
        classes = tuple(
            PlanningCapabilityClass.from_json(
                capability_class, f"deployment_planning_profile.capability_classes[{index}]"
            )
            for index, capability_class in enumerate(
                _array(item["capability_classes"], "deployment_planning_profile.capability_classes")
            )
        )
        if not classes:
            raise PlanningProfileError(
                "deployment_planning_profile.capability_classes must not be empty"
            )
        class_ids = [capability_class.class_id for capability_class in classes]
        if len(set(class_ids)) != len(class_ids):
            raise PlanningProfileError(
                "deployment_planning_profile.capability_classes contains duplicate ids"
            )
        profile = cls(
            profile_id=_text(identity["profile_id"], "identity.profile_id"),
            revision=_text(identity["revision"], "identity.revision"),
            source=_text(identity["source"], "identity.source"),
            capability_classes=classes,
            profile_digest=_text(item["digest"], "deployment_planning_profile.digest"),
            schema_version=schema,
            operation_effects=(
                tuple(
                    PlanningOperationEffects.from_json(
                        effect, f"deployment_planning_profile.operation_effects[{index}]"
                    )
                    for index, effect in enumerate(
                        _array(item["operation_effects"], "operation_effects")
                    )
                )
                if schema == PLANNING_PROFILE_SCHEMA
                else ()
            ),
            state_domains=(
                tuple(
                    PlanningStateDomain.from_json(domain, f"state_domains[{index}]")
                    for index, domain in enumerate(_array(item["state_domains"], "state_domains"))
                )
                if schema == PLANNING_PROFILE_SCHEMA
                else ()
            ),
        )
        domain_ids = [domain.domain_id for domain in profile.state_domains]
        if len(set(domain_ids)) != len(domain_ids):
            raise PlanningProfileError("state_domains contains duplicate ids")
        operation_ids = [effect.operation for effect in profile.operation_effects]
        if len(set(operation_ids)) != len(operation_ids):
            raise PlanningProfileError("operation_effects contains duplicate operations")
        if _DIGEST.fullmatch(profile.profile_digest) is None:
            raise PlanningProfileError("deployment planning profile digest is invalid")
        if profile.profile_digest != _digest(profile._body()):
            raise PlanningProfileError("deployment planning profile digest does not match content")
        return profile

    @classmethod
    def load(cls, path: Path) -> DeploymentPlanningProfile:
        """Load one fixed deployment profile without accepting request-controlled paths."""
        try:
            decoded: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PlanningProfileError(
                f"cannot load deployment planning profile {path}: {error}"
            ) from error
        return cls.from_json(decoded)

    def validate_catalog(self, catalog: CanonicalCapabilityCatalog) -> None:
        """Ensure every deployment fact uses an exact Catalog capability and typed value."""
        definitions = {definition.contract: definition for definition in catalog.capabilities}
        operations = {definition.contract: definition for definition in catalog.contracts}
        for class_index, capability_class in enumerate(self.capability_classes):
            for capability_index, fact in enumerate(capability_class.capabilities):
                definition = definitions.get(fact.contract)
                if definition is None:
                    raise PlanningProfileError(
                        f"capability_classes[{class_index}].capabilities[{capability_index}] "
                        f"uses unknown capability {fact.contract}"
                    )
                attribute_definitions = {
                    attribute.name: attribute for attribute in definition.attributes
                }
                for attribute_name, attribute_value in fact.attributes:
                    attribute_definition = attribute_definitions.get(attribute_name)
                    if attribute_definition is None:
                        raise PlanningProfileError(
                            f"capability_classes[{class_index}].capabilities[{capability_index}] "
                            f"uses unknown attribute {attribute_name!r}"
                        )
                    if not attribute_definition.value_type.accepts(attribute_value):
                        raise PlanningProfileError(
                            f"capability_classes[{class_index}].capabilities[{capability_index}] "
                            f"attribute {attribute_name!r} must be "
                            f"{attribute_definition.value_type.value}"
                        )
        for effect_index, declaration in enumerate(self.operation_effects):
            operation = operations.get(declaration.operation)
            if operation is None:
                raise PlanningProfileError(
                    f"operation_effects[{effect_index}] uses unknown operation "
                    f"{declaration.operation}"
                )
            if not any(
                required in definitions
                and any(
                    fact.contract == required
                    for capability_class in self.capability_classes
                    for fact in capability_class.capabilities
                )
                for required in operation.required_capabilities
            ):
                raise PlanningProfileError(
                    f"operation_effects[{effect_index}] is not backed by a published "
                    "capability class"
                )
            parameter_names = {parameter.name for parameter in operation.parameters}
            documented_domains = {domain.domain_id for domain in self.state_domains}
            for effect in declaration.effects:
                if effect.domain not in documented_domains:
                    raise PlanningProfileError(
                        f"operation_effects[{effect_index}] references undocumented state domain "
                        f"{effect.domain!r}"
                    )
                for parameter in (effect.subject_parameter, effect.result_parameter):
                    if parameter is not None and parameter not in parameter_names:
                        raise PlanningProfileError(
                            f"operation_effects[{effect_index}] references unknown parameter "
                            f"{parameter!r}"
                        )

    def _body(self) -> JSONObject:
        """Return the digest body without the self-referential digest field."""
        return {
            "schema_version": self.schema_version,
            "authority": "deployment-owned",
            "identity": {
                "profile_id": self.profile_id,
                "revision": self.revision,
                "source": self.source,
            },
            "capability_classes": [
                capability_class.to_json() for capability_class in self.capability_classes
            ],
            **(
                {"operation_effects": [effect.to_json() for effect in self.operation_effects]}
                if self.schema_version == PLANNING_PROFILE_SCHEMA
                else {}
            ),
            **(
                {"state_domains": [domain.to_json() for domain in self.state_domains]}
                if self.schema_version == PLANNING_PROFILE_SCHEMA
                else {}
            ),
        }

    def to_json(self) -> JSONObject:
        """Serialize the frozen profile for Planner, Reviewer, and Repairer inputs."""
        return {**self._body(), "digest": self.profile_digest}


def load_optional_planning_profile(path: Path | None) -> DeploymentPlanningProfile | None:
    """Load an optional startup-frozen profile while preserving deployments without one."""
    return None if path is None else DeploymentPlanningProfile.load(path)
