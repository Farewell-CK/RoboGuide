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
from mission.models import CapabilityContractRef, JSONObject, JSONValue, MissionPlanError

PLANNING_PROFILE_SCHEMA = "roboguide.deployment-planning-profile/v0.1"
_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")


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
class DeploymentPlanningProfile:
    """Freeze deployment capability classes for one MI deliberation process."""

    profile_id: str
    revision: str
    source: str
    capability_classes: tuple[PlanningCapabilityClass, ...]
    profile_digest: str

    @classmethod
    def from_json(cls, value: object) -> DeploymentPlanningProfile:
        """Parse and digest-check a deployment-owned profile artifact."""
        item = _object(value, "deployment_planning_profile")
        _exact_keys(
            item,
            {"schema_version", "authority", "identity", "capability_classes", "digest"},
            "deployment_planning_profile",
        )
        if item["schema_version"] != PLANNING_PROFILE_SCHEMA:
            raise PlanningProfileError("unsupported deployment planning profile schema")
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
        )
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

    def _body(self) -> JSONObject:
        """Return the digest body without the self-referential digest field."""
        return {
            "schema_version": PLANNING_PROFILE_SCHEMA,
            "authority": "deployment-owned",
            "identity": {
                "profile_id": self.profile_id,
                "revision": self.revision,
                "source": self.source,
            },
            "capability_classes": [
                capability_class.to_json() for capability_class in self.capability_classes
            ],
        }

    def to_json(self) -> JSONObject:
        """Serialize the frozen profile for Planner, Reviewer, and Repairer inputs."""
        return {**self._body(), "digest": self.profile_digest}


def load_optional_planning_profile(path: Path | None) -> DeploymentPlanningProfile | None:
    """Load an optional startup-frozen profile while preserving deployments without one."""
    return None if path is None else DeploymentPlanningProfile.load(path)
