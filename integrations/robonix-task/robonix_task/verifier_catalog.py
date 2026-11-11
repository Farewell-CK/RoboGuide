"""Prepare explicit deployment Catalog metadata from an authenticated registry read.

The caller authenticates the Controller transport and publishes the resulting Catalog
through normal deployment configuration. This pure converter is not live MI discovery,
World evidence, registration, physical verification or motion authority.
"""

from __future__ import annotations

import json
from typing import Any

from robonix_task.arrival_verifier import validate_config
from robonix_task.arrival_verifier_store import ArrivalVerificationError, digest, encode
from robonix_task.patrol import ARRIVAL_CONTRACT, MAX_ARRIVAL_AGE_MS

MARKER = "\nRegistered verifier deployment snapshot (not robot observation):\n"
MAX_BYTES = 2 * 1024 * 1024
_SOURCE_FIELDS = {
    "source_id",
    "revision",
    "tenant_id",
    "producer_principals",
    "verifier",
    "predicates",
    "max_age_ms",
    "stop_timeout_ms",
    "enabled",
    "continuous",
    "require_artifact",
}


class VerifierCatalogError(ValueError):
    """Reject incomplete, changed or ambiguously authorized deployment metadata."""


def _copy(value: Any) -> dict[str, Any]:
    """Detach bounded finite JSON without normalizing the original source strings."""
    try:
        raw = encode(value)
        if len(raw.encode()) > MAX_BYTES:
            raise VerifierCatalogError("deployment_document_exceeds_2mib")
        copied = json.loads(raw)
        if not isinstance(copied, dict):
            raise VerifierCatalogError("deployment_object_required")
        return copied
    except (ArrivalVerificationError, RecursionError) as error:
        raise VerifierCatalogError("invalid_deployment_json") from error


def _text(value: Any) -> bool:
    """Require exact bounded identifiers without wildcard or control-character authority."""
    return (
        isinstance(value, str)
        and bool(value.strip())
        and value == value.strip()
        and len(value) <= 256
        and "*" not in value
        and all(ord(c) >= 32 for c in value)
    )


def _positive(value: Any, ceiling: int = 18446744073709551615) -> bool:
    """Reject Boolean coercion and unbounded positive contract values."""
    return type(value) is int and 0 < value <= ceiling


def _strings(value: Any) -> bool:
    """Validate nonempty bounded exact sets without dropping duplicates or malformed items."""
    return (
        isinstance(value, list)
        and 1 <= len(value) <= 64
        and all(_text(v) for v in value)
        and len(set(value)) == len(value)
    )


def _contract(value: Any) -> bool:
    """Require the original closed canonical capability reference."""
    return (
        isinstance(value, dict)
        and set(value) == {"namespace", "name", "version"}
        and all(_text(v) for v in value.values())
    )


def _registry(view: dict[str, Any]) -> dict[str, Any]:
    """Validate the complete original view before selecting or describing a source."""
    if (
        set(view) != {"schema_version", "registry"}
        or view["schema_version"] != "roboguide.semantic-verifier-view/v0.1"
    ):
        raise VerifierCatalogError("original_registry_view_required")
    registry = view["registry"]
    if (
        not isinstance(registry, dict)
        or set(registry) != {"schema_version", "registry_id", "revision", "sources"}
        or registry["schema_version"] != "roboguide.semantic-verifier-registry/v0.1"
        or not _text(registry["registry_id"])
        or not _positive(registry["revision"])
        or not isinstance(registry["sources"], dict)
        or len(registry["sources"]) > 128
    ):
        raise VerifierCatalogError("registered_source_registry_required")
    for identity, source in registry["sources"].items():
        if (
            not _text(identity)
            or not isinstance(source, dict)
            or set(source) != _SOURCE_FIELDS
            or source["source_id"] != identity
            or not _positive(source["revision"])
            or not _text(source["tenant_id"])
            or not _strings(source["producer_principals"])
            or not _contract(source["verifier"])
            or not _strings(source["predicates"])
            or not _positive(source["max_age_ms"], 3600000)
            or not _positive(source["stop_timeout_ms"], 3600000)
            or any(
                type(source[k]) is not bool for k in ["enabled", "continuous", "require_artifact"]
            )
        ):
            raise VerifierCatalogError("malformed_original_verifier_source")
    return registry


def _arrival_capability(catalog: dict[str, Any]) -> dict[str, Any]:
    """Select an existing v0.3 capability without inventing operations or vocabulary."""
    if (
        set(catalog) != {"schema_version", "capabilities", "operations"}
        or catalog["schema_version"] != "roboguide.capability-catalog/v0.3"
        or not isinstance(catalog["capabilities"], list)
        or not catalog["capabilities"]
        or not isinstance(catalog["operations"], list)
        or not catalog["operations"]
    ):
        raise VerifierCatalogError("original_v03_capability_catalog_required")
    identities: set[str] = set()
    matches = []
    for capability in catalog["capabilities"]:
        if (
            not isinstance(capability, dict)
            or set(capability) != {"contract", "description", "attributes"}
            or not _contract(capability["contract"])
            or not isinstance(capability["description"], str)
            or not capability["description"].strip()
            or not isinstance(capability["attributes"], list)
        ):
            raise VerifierCatalogError("malformed_original_capability")
        identity = encode(capability["contract"])
        if identity in identities:
            raise VerifierCatalogError("duplicate_original_capability")
        identities.add(identity)
        if MARKER in capability["description"]:
            raise VerifierCatalogError("catalog_already_contains_verifier_snapshot")
        if capability["contract"] == ARRIVAL_CONTRACT:
            matches.append(capability)
    if len(matches) != 1:
        raise VerifierCatalogError("one_existing_arrival_capability_required")
    return matches[0]


def build_arrival_catalog(
    catalog: dict[str, Any], registry_view: dict[str, Any], source_config: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return detached Catalog metadata and provenance for one configured manual source.

    Only its existing arrival-capability description changes. The original Catalog
    digest and exact registry/config digests accompany the new declaration. Other
    tenants' sources are not exposed; their presence remains covered by the original
    registry digest. Re-publication starts from an explicitly retained original base.
    """
    original, view = _copy(catalog), _copy(registry_view)
    try:
        config = validate_config(source_config)
    except ArrivalVerificationError as error:
        raise VerifierCatalogError("invalid_exact_source_configuration") from error
    registry = _registry(view)
    source = registry["sources"].get(config["source_id"])
    predicates = sorted(t["predicate"] for t in config["targets"])
    expected = {
        "source_id": config["source_id"],
        "revision": config["revision"],
        "tenant_id": config["tenant_id"],
        "producer_principals": [config["producer_principal"]],
        "verifier": dict(ARRIVAL_CONTRACT),
        "predicates": predicates,
        "max_age_ms": MAX_ARRIVAL_AGE_MS,
        "stop_timeout_ms": MAX_ARRIVAL_AGE_MS,
        "enabled": True,
        "continuous": False,
        "require_artifact": False,
    }
    if source != expected:
        raise VerifierCatalogError("registered_source_differs_from_manual_publisher")
    for identity, other in registry["sources"].items():
        if (
            identity != config["source_id"]
            and other["enabled"]
            and other["tenant_id"] == config["tenant_id"]
            and other["verifier"] == ARRIVAL_CONTRACT
            and set(other["predicates"]) & set(predicates)
        ):
            raise VerifierCatalogError("ambiguous_enabled_arrival_source")
    updated = _copy(original)
    capability = _arrival_capability(updated)
    snapshot = {
        "schema_version": "roboguide.deployment-verifier-catalog-snapshot/v0.2",
        "snapshot_kind": "cached-deployment-contract-not-world-or-robot-observation",
        "arrival_method": "explicit-human-onsite-confirmation",
        "motion_authorized": False,
        "automatic_arrival_assessment": False,
        "registry_id": registry["registry_id"],
        "registry_revision": registry["revision"],
        "registry_digest": digest(registry),
        "registry_view_digest": digest(view),
        "source_config_digest": digest(config),
        "source": source,
        "target_references": config["targets"],
        "supported_task_satisfactions": [
            {
                "basis": "verifier-evidence",
                "expected_effect": predicate,
                "verifier": {
                    "contract": dict(ARRIVAL_CONTRACT),
                    "predicate": predicate,
                    "max_evidence_age_ms": MAX_ARRIVAL_AGE_MS,
                },
            }
            for predicate in predicates
        ],
        "binding_policy": "Each Task verifies only its own exact entity, Node and target's "
        "independent arrived-and-stopped goal. Its expected_effect must equal the exact "
        "predicate literally. These independent receipts do not prove simultaneous final "
        "coexistence, a persistent shared state or any stricter joint goal; "
        "do not add those claims.",
        "admission_rule": "Controller must resolve current source authority at admission; "
        "this metadata grants no motion, device-stop or shared-zone-clearance authority.",
    }
    capability["description"] += MARKER + encode(snapshot)
    updated = _copy(updated)
    provenance = {
        "schema_version": "roboguide.verifier-catalog-publication/v0.1",
        "input_catalog_digest": digest(original),
        "output_catalog_digest": digest(updated),
        "registry_view_digest": digest(view),
        "registry_digest": digest(registry),
        "source_config_digest": digest(config),
        "snapshot_digest": digest(snapshot),
        "transport_authenticated_by_converter": False,
        "caller_requirement": "Authenticate the original Controller registry read separately.",
        "motion_authorized": False,
        "source_registered_by_converter": False,
    }
    return updated, provenance
