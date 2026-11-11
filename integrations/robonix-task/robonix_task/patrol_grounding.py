"""Convert exact Controller sources into an unapplied fixed-patrol preparation snapshot.

This module performs no I/O and owns no deployment, verifier or motion authority. Current
reports can justify target provenance, never physical arrival, stopping or authorization.
"""

from __future__ import annotations

import json
import math
from typing import Any, TypeGuard, cast

from robonix_task.patrol import ARRIVAL_CONTRACT, ASSIGNMENTS, MAX_ARRIVAL_AGE_MS, arrival_predicate
from robonix_task.scene_targets import MAX_AGE_MS as MAX_SCENE_AGE_MS
from robonix_task.scene_targets import SCHEMA as SCENE_SCHEMA
from robonix_task.scene_targets import decode_scene_definition, validate_scene_targets

ENTITY_SCHEMA = "roboguide.world.physical-entity-reference/v0.1"
MAX_ENTITY_AGE_MS = 10000
MAX_INVENTORY_AGE_MS = 5000


class PatrolGroundingError(ValueError):
    """Reject malformed preparation input without promoting it into execution authority."""


def _copy(value: Any) -> Any:
    """Detach finite JSON while retaining original source fields and clocks for audit."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, OverflowError) as error:
        raise PatrolGroundingError("finite_json_preparation_input_required") from error


def _positive(value: Any) -> TypeGuard[int]:
    """Recognize a bounded positive integer without treating booleans as counters."""
    return type(value) is int and 0 < value <= 18446744073709551615


def _text(value: Any) -> bool:
    """Require an explicit bounded identifier rather than an inferred deployment reference."""
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 256


def _finite(value: Any) -> TypeGuard[int | float]:
    """Recognize finite bounded source ages, excluding bool and overflowing integers."""
    try:
        return (
            isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
        )
    except OverflowError:
        return False


def _record(
    records: list[Any],
    *,
    node_id: str,
    entity_id: str,
    object_type: str,
    owner: str,
    channel_id: str,
    schema: str,
    state_observed_at_ms: int,
    controller_now_ms: int,
    local_read_elapsed_ms: int,
    max_age_ms: int,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    """Select one exact World channel and charge only comparable retention/read clocks."""
    expected = {"class": "world", "object_type": object_type, "object_id": entity_id}
    selected = [
        r
        for r in records
        if isinstance(r, dict) and r.get("object") == expected and r.get("channel_id") == channel_id
    ]
    if len(selected) != 1:
        raise PatrolGroundingError("exact_world_channel_missing_or_ambiguous")
    record = selected[0]
    if (
        record.get("source") != f"node:{node_id}/{owner}"
        or record.get("semantic") != "reported"
        or record.get("stale") is not False
    ):
        raise PatrolGroundingError("world_source_or_freshness_unqualified")
    envelope = record.get("value")
    if not isinstance(envelope, dict) or envelope.get("payload_schema") != schema:
        raise PatrolGroundingError("world_payload_schema_mismatch")
    received, ttl = envelope.get("received_at_ms"), envelope.get("valid_for_ms")
    if (
        type(received) is not int
        or received < 0
        or not _positive(ttl)
        or ttl > max_age_ms
        or not _text(envelope.get("source_epoch"))
        or not _positive(envelope.get("sequence"))
        or "source_observed_at_ms" not in envelope
        or (
            envelope["source_observed_at_ms"] is not None
            and (
                type(envelope["source_observed_at_ms"]) is not int
                or envelope["source_observed_at_ms"] < 0
            )
        )
    ):
        raise PatrolGroundingError("world_source_time_epoch_sequence_or_ttl_unqualified")
    if received > state_observed_at_ms or not 0 <= controller_now_ms - received < ttl:
        raise PatrolGroundingError("world_controller_retention_expired_or_clock_reversed")
    retained = controller_now_ms - received + local_read_elapsed_ms
    if retained >= ttl:
        raise PatrolGroundingError("world_expired_during_local_read")
    body = envelope.get("value")
    if not isinstance(body, dict):
        raise PatrolGroundingError("world_payload_object_required")
    return record, body, retained


def _registry(value: dict[str, Any]) -> dict[str, Any]:
    """Require explicit one-to-one Control routing while retaining the original document."""
    registry = value.get("registry") if "registry" in value else value
    if not isinstance(registry, dict) or (
        registry.get("schema") != "roboguide.physical-entity-registry/v0.1"
        or not _text(registry.get("registry_id"))
        or not _positive(registry.get("revision"))
        or registry.get("routing_profile") != "one-routable-entity-per-node"
        or not isinstance(registry.get("entities"), list)
    ):
        raise PatrolGroundingError("accepted_physical_registry_required")
    entities = registry["entities"]
    if any(
        not isinstance(entity, dict)
        or set(entity) != {"entity_id", "node_id"}
        or not _text(entity.get("entity_id"))
        or not _text(entity.get("node_id"))
        for entity in entities
    ):
        raise PatrolGroundingError("physical_registry_route_malformed")
    if len({e["entity_id"] for e in entities}) != len(entities) or len(
        {e["node_id"] for e in entities}
    ) != len(entities):
        raise PatrolGroundingError("physical_registry_ambiguous_route")
    return registry


def _chassis(
    inspection: Any, node_id: str, controller_now_ms: int, local_read_elapsed_ms: int
) -> dict[str, Any]:
    """Recheck an original inspect_posture result without accepting a peer's diagnostic."""
    if not isinstance(inspection, dict):
        raise PatrolGroundingError("original_posture_inspection_required")
    evidence = inspection.get("evidence")
    if (
        inspection.get("schema_version") != "roboguide.reported-posture-inspection/v0.1"
        or inspection.get("node_id") != node_id
        or inspection.get("status") != "reported"
        or inspection.get("posture") not in {"standing", "down"}
        or inspection.get("motion_authorized") is not False
        or inspection.get("physical_effect_verified") is not False
        or not isinstance(evidence, dict)
        or evidence.get("source") != f"node:{node_id}/robot-observer"
        or evidence.get("channel_id") != "robot-state"
        or not _text(evidence.get("source_epoch"))
        or not _positive(evidence.get("sequence"))
    ):
        raise PatrolGroundingError("posture_inspection_source_or_value_unqualified")
    at, received = (
        evidence.get("controller_observed_at_ms"),
        evidence.get("controller_received_at_ms"),
    )
    remaining = evidence.get("remaining_valid_for_ms")
    if (
        type(at) is not int
        or type(received) is not int
        or not 0 <= received <= at <= controller_now_ms
        or not _positive(remaining)
        or remaining > 5000
        or controller_now_ms - at + local_read_elapsed_ms >= remaining
    ):
        raise PatrolGroundingError("posture_inspection_budget_expired_or_unqualified")
    original = evidence.get("original_value")
    age = evidence.get("aggregate_age_plus_controller_retention_ms")
    if (
        not isinstance(original, dict)
        or original.get("posture") != inspection["posture"]
        or original.get("stale") is not False
        or not _positive(original.get("stale_ms"))
        or original["stale_ms"] > 60000
        or not _finite(age)
        or not 0 <= age < min(5000, original["stale_ms"])
        or remaining > math.floor(min(5000, original["stale_ms"]) - age)
    ):
        raise PatrolGroundingError("posture_native_budget_or_original_value_unqualified")
    result = cast(dict[str, Any], _copy(inspection))
    result["original_inspection"] = _copy(inspection)
    result["evidence"]["remaining_valid_for_ms"] = remaining - local_read_elapsed_ms
    return result


def _target(
    record: dict[str, Any],
    body: dict[str, Any],
    retained_ms: int,
    *,
    entity_id: str,
    node_id: str,
    target_name: str,
) -> dict[str, Any]:
    """Verify a Node's original Scene sample and select only its exact fixed semantic target."""
    try:
        original = validate_scene_targets(body, entity_id=entity_id, node_id=node_id)
        definition = decode_scene_definition(original)
    except (TypeError, ValueError, OverflowError) as error:
        raise PatrolGroundingError("scene_target_source_invalid:" + str(error)) from error
    remaining = original.get("remaining_at_publisher_ms")
    if (
        original.get("availability") != "current"
        or not _positive(remaining)
        or retained_ms >= remaining
    ):
        raise PatrolGroundingError("scene_target_native_budget_expired_or_unavailable")
    selected = [r for r in definition["regions"] if r["name"] == target_name]
    if len(selected) != 1 or selected[0].get("stale") is not False:
        raise PatrolGroundingError("exact_current_target_missing_or_ambiguous")
    region = selected[0]
    map_id = definition["map_binding"]["map_id"]
    envelope = record["value"]
    return {
        "node_id": node_id,
        "entity_id": entity_id,
        "target_name": target_name,
        "map_id": map_id,
        "region_id": region["annotation_id"],
        "region": _copy(region),
        "map_binding_verified": True,
        "map_binding_verification_scope": "same-node-state-regions-state-source-consistency",
        "map_binding_atomicity_verified": False,
        "localization_verified": False,
        "immutable_map_revision_verified": False,
        "region_source_digest": original["region_source_digest"],
        "region_source": {
            "node_id": node_id,
            "map_id": map_id,
            "document_digest": original["region_source_digest"],
            "source": record["source"],
            "channel_id": record["channel_id"],
            "source_epoch": envelope["source_epoch"],
            "sequence": envelope["sequence"],
            "received_at_ms": envelope["received_at_ms"],
            "valid_for_ms": envelope["valid_for_ms"],
            "source_observed_at_ms": envelope["source_observed_at_ms"],
            "source_documents": _copy(original["source_documents"]),
            "original_remaining_at_publisher_ms": remaining,
            "known_retention_and_read_ms": retained_ms,
            "remaining_after_known_delay_ms": min(
                remaining - retained_ms, envelope["valid_for_ms"] - retained_ms
            ),
            "original_report": _copy(original),
        },
    }


def build_patrol_snapshot(
    world_state: dict[str, Any],
    inventory: dict[str, Any],
    physical_registry: dict[str, Any],
    chassis_inspections: dict[str, Any],
    *,
    local_read_elapsed_ms: int = 0,
) -> dict[str, Any]:
    """Assemble a non-executable snapshot from exact original central sources.

    Callers authenticate each Controller read and supply the entire elapsed local read
    budget; this pure converter cannot authenticate JSON or measure transport itself.
    Failed source checks become attributed gaps rather than borrowed targets. An accepted
    physical registry and a current per-Node World identity reference are both required.
    No missing verifier, photo or summary deployment is inferred from capability names.
    """
    if type(local_read_elapsed_ms) is not int or not 0 <= local_read_elapsed_ms <= 3600000:
        raise PatrolGroundingError("bounded_local_read_elapsed_ms_required")
    state, original_inventory, original_registry, inspections = _copy(
        [world_state, inventory, physical_registry, chassis_inspections]
    )
    if (
        not isinstance(state, dict)
        or state.get("schema") != "roboguide.state-query/v0.1"
        or type(state.get("observed_at_ms")) is not int
        or state["observed_at_ms"] < 0
        or not isinstance(state.get("records"), list)
        or not isinstance(original_inventory, dict)
        or original_inventory.get("schema_version") != "roboguide.inventory/v0.1"
        or type(original_inventory.get("observed_at_ms")) is not int
        or original_inventory["observed_at_ms"] < 0
        or not isinstance(original_inventory.get("nodes"), list)
        or not isinstance(original_registry, dict)
        or not isinstance(inspections, dict)
    ):
        raise PatrolGroundingError("original_controller_documents_required")
    clocks = [state["observed_at_ms"], original_inventory["observed_at_ms"]]
    clocks.extend(
        value["evidence"]["controller_observed_at_ms"]
        for value in inspections.values()
        if isinstance(value, dict)
        and isinstance(value.get("evidence"), dict)
        and type(value["evidence"].get("controller_observed_at_ms")) is int
        and value["evidence"]["controller_observed_at_ms"] >= 0
    )
    now = max(clocks)
    snapshot: dict[str, Any] = {
        "schema_version": "roboguide.patrol-grounding/v0.1",
        "physical_registry": original_registry,
        "inventory": original_inventory,
        "chassis": {},
        "targets": [],
        "source_evidence": {},
        "source_gaps": [],
        "controller_observed_at_ms": now,
        "local_read_elapsed_ms": local_read_elapsed_ms,
        "arrival_verifier": {
            "registered": False,
            "published": False,
            "contract": dict(ARRIVAL_CONTRACT),
            "max_evidence_age_ms": MAX_ARRIVAL_AGE_MS,
            "mode": "independent-onsite-attestation",
            "predicates": [],
        },
        "photo_support": {
            a.node_id: {"robonix_capture": False, "artifact_receipt": False} for a in ASSIGNMENTS
        },
        "summary_artifact_supported": False,
        "motion_authorized": False,
        "physical_effect_verified": False,
    }

    def gap(code: str, node_id: str = "") -> None:
        """Retain each bounded failed source check without erasing valid peer evidence."""
        snapshot["source_gaps"].append({"code": code[:512], "node_id": node_id})

    if now - original_inventory["observed_at_ms"] + local_read_elapsed_ms >= MAX_INVENTORY_AGE_MS:
        gap("current_inventory_expired_during_acquisition")
        return snapshot
    try:
        registry = _registry(original_registry)
    except PatrolGroundingError as error:
        gap(str(error))
        return snapshot
    for assignment in ASSIGNMENTS:
        node_id, entity_id = assignment.node_id, assignment.entity_id
        node = [
            n
            for n in original_inventory["nodes"]
            if isinstance(n, dict) and n.get("node_id") == node_id
        ]
        if len(node) != 1 or node[0].get("liveness") != "Reachable":
            gap("node_not_currently_reachable", node_id)
            continue
        routes = [r for r in registry["entities"] if r["entity_id"] == entity_id]
        if len(routes) != 1 or routes[0]["node_id"] != node_id:
            gap("fixed_physical_entity_route_unverified", node_id)
            continue
        try:
            reference, body, retained = _record(
                state["records"],
                node_id=node_id,
                entity_id=entity_id,
                object_type="physical-entity-reference",
                owner="physical-observer",
                channel_id="physical-entity-reference",
                schema=ENTITY_SCHEMA,
                state_observed_at_ms=state["observed_at_ms"],
                controller_now_ms=now,
                local_read_elapsed_ms=local_read_elapsed_ms,
                max_age_ms=MAX_ENTITY_AGE_MS,
            )
            if body != {"entity_id": entity_id}:
                raise PatrolGroundingError("exact_physical_entity_body_required")
        except PatrolGroundingError as error:
            gap(str(error), node_id)
            continue
        snapshot["source_evidence"][node_id] = {
            "physical_reference": reference,
            "reference_known_retention_and_read_ms": retained,
        }
        try:
            snapshot["chassis"][node_id] = _chassis(
                inspections.get(node_id), node_id, now, local_read_elapsed_ms
            )
        except PatrolGroundingError as error:
            gap(str(error), node_id)
        try:
            scene, body, retained = _record(
                state["records"],
                node_id=node_id,
                entity_id=entity_id,
                object_type="scene-targets",
                owner="physical-observer",
                channel_id="scene-targets",
                schema=SCENE_SCHEMA,
                state_observed_at_ms=state["observed_at_ms"],
                controller_now_ms=now,
                local_read_elapsed_ms=local_read_elapsed_ms,
                max_age_ms=MAX_SCENE_AGE_MS,
            )
            target = _target(
                scene,
                body,
                retained,
                entity_id=entity_id,
                node_id=node_id,
                target_name=assignment.target_name,
            )
        except PatrolGroundingError as error:
            gap(str(error), node_id)
            continue
        snapshot["source_evidence"][node_id]["scene_record"] = scene
        snapshot["targets"].append(target)
    if len(snapshot["targets"]) == len(ASSIGNMENTS):
        snapshot["arrival_verifier"]["predicates"] = [
            arrival_predicate(snapshot, a) for a in ASSIGNMENTS
        ]
    return snapshot


def arrival_registry_template(
    snapshot: dict[str, Any],
    *,
    registry_id: str,
    source_id: str,
    tenant_id: str,
    producer_principal: str,
    revision: int = 1,
) -> dict[str, Any]:
    """Prepare a disabled, unpublished verifier registry for explicit independent review.

    Supplying an intended producer is not proof that its identity is enrolled. The output
    is not a management command and must not be treated as installed or as a verdict.
    """
    if not all(
        _text(v) for v in (registry_id, source_id, tenant_id, producer_principal)
    ) or not _positive(revision):
        raise PatrolGroundingError("explicit_bounded_registry_source_tenant_and_principal_required")
    targets = snapshot.get("targets")
    if not isinstance(targets, list) or len(targets) != len(ASSIGNMENTS):
        raise PatrolGroundingError("both_original_targets_required_for_registry_template")
    for assignment in ASSIGNMENTS:
        selected = [
            t for t in targets if isinstance(t, dict) and t.get("node_id") == assignment.node_id
        ]
        if len(selected) != 1 or (
            selected[0].get("entity_id") != assignment.entity_id
            or selected[0].get("target_name") != assignment.target_name
            or selected[0].get("map_binding_verified") is not True
            or not _text(selected[0].get("map_id"))
            or not _text(selected[0].get("region_id"))
            or not _text(selected[0].get("region_source_digest"))
        ):
            raise PatrolGroundingError("both_original_targets_required_for_registry_template")
    predicates = [arrival_predicate(snapshot, a) for a in ASSIGNMENTS]
    registry = {
        "schema_version": "roboguide.semantic-verifier-registry/v0.1",
        "registry_id": registry_id,
        "revision": revision,
        "sources": {
            source_id: {
                "source_id": source_id,
                "revision": revision,
                "tenant_id": tenant_id,
                "producer_principals": [producer_principal],
                "verifier": dict(ARRIVAL_CONTRACT),
                "predicates": predicates,
                "max_age_ms": MAX_ARRIVAL_AGE_MS,
                "stop_timeout_ms": MAX_ARRIVAL_AGE_MS,
                "enabled": False,
                "continuous": False,
                "require_artifact": False,
            }
        },
    }
    return {
        "schema_version": "roboguide.patrol-verifier-preparation/v0.1",
        "registered": False,
        "published": False,
        "producer_identity_enrolled": False,
        "motion_authorized": False,
        "registry": registry,
    }
