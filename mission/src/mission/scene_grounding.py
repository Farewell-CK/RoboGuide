"""Qualify attributed Scene target reports without granting localization or motion authority."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from typing import cast

from mission.grounding_context import GroundingContextError
from mission.models import JSONObject, JSONValue

SCENE_TARGETS_SCHEMA = "roboguide.world.scene-targets/v0.2"
MAX_SCENE_AGE_MS = 15000
MAX_SCENE_BYTES = 48 * 1024
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REPORT_FIELDS = {
    "schema_version",
    "physical_entity_id",
    "source_node_id",
    "availability",
    "sample_started_at_ns",
    "sample_completed_at_ns",
    "sample_age_ms",
    "max_age_ms",
    "remaining_at_publisher_ms",
    "definition_json",
    "region_source_digest",
    "source_documents",
    "localization_verified",
    "arrival_verified",
    "reason",
}
_REGION_REQUIRED = {"annotation_id", "kind", "name", "points", "stale"}
_REGION_OPTIONAL = {"theta", "created_at", "updated_at", "stale_reason"}


def _object(value: JSONValue, label: str) -> JSONObject:
    """Require an actual JSON object and retain a stable attributed rejection label."""
    if not isinstance(value, dict):
        raise GroundingContextError("scene_targets_" + label + "_object_required")
    return value


def _text(value: JSONValue, maximum: int) -> bool:
    """Admit bounded nonblank source text without interpreting it as an instruction."""
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def _identifier(value: JSONValue) -> bool:
    """Refuse malformed physical and producer identifiers before forming an exact source."""
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _number(value: JSONValue) -> bool:
    """Accept finite physical numbers while rejecting booleans and integer overflow."""
    try:
        return type(value) in {int, float} and math.isfinite(cast(int | float, value))
    except OverflowError:
        return False


def _digest(value: JSONValue) -> bool:
    """Recognize an exact SHA256 attribution, without claiming access to its source bytes."""
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _validate_region(value: JSONValue) -> str:
    """Validate each original polygon and return its identity for uniqueness/order checks."""
    region = _object(value, "region")
    if not _REGION_REQUIRED <= region.keys() <= _REGION_REQUIRED | _REGION_OPTIONAL:
        raise GroundingContextError("scene_targets_region_fields_invalid")
    identity = region.get("annotation_id")
    if (
        not _text(identity, 256)
        or region.get("kind") != "region"
        or not _text(region.get("name"), 256)
        or region.get("stale") is not False
    ):
        raise GroundingContextError("scene_targets_region_identity_or_staleness_invalid")
    points = region.get("points")
    if not isinstance(points, list) or not 3 <= len(points) <= 256:
        raise GroundingContextError("scene_targets_polygon_size_invalid")
    coordinates: list[tuple[float, float]] = []
    for point in points:
        if not isinstance(point, list) or len(point) != 2 or any(not _number(p) for p in point):
            raise GroundingContextError("scene_targets_polygon_coordinate_invalid")
        coordinates.append((float(cast(int | float, point[0])), float(cast(int | float, point[1]))))
    area = sum(
        coordinates[index][0] * coordinates[(index + 1) % len(coordinates)][1]
        - coordinates[(index + 1) % len(coordinates)][0] * coordinates[index][1]
        for index in range(len(coordinates))
    )
    if not math.isfinite(area) or abs(area) <= 1e-9:
        raise GroundingContextError("scene_targets_polygon_degenerate")
    for key in ("theta", "created_at", "updated_at"):
        entry = region.get(key)
        if entry is not None and not _number(entry):
            raise GroundingContextError("scene_targets_region_scalar_invalid")
    reason = region.get("stale_reason")
    if reason is not None and (not isinstance(reason, str) or len(reason) > 512):
        raise GroundingContextError("scene_targets_region_reason_invalid")
    return cast(str, identity)


def _validate_source_documents(value: JSONValue) -> None:
    """Validate exact HTTP paths and digest declarations, not inaccessible original bytes."""
    documents = _object(value, "source_documents")
    if documents.keys() != {"state", "regions"}:
        raise GroundingContextError("scene_targets_source_document_set_invalid")
    for name, path, fields in (
        ("state", "/api/state", {"path", "body_sha256", "confirmation_body_sha256"}),
        ("regions", "/api/regions", {"path", "body_sha256"}),
    ):
        document = _object(documents[name], "source_document")
        if document.keys() != fields or document.get("path") != path:
            raise GroundingContextError("scene_targets_source_document_path_or_fields_invalid")
        if any(not _digest(document[key]) for key in fields - {"path"}):
            raise GroundingContextError("scene_targets_source_document_digest_invalid")


def _unique_definition_object(pairs: list[tuple[str, JSONValue]]) -> JSONObject:
    """Reject duplicate keys at every definition depth instead of choosing one interpretation."""
    result: JSONObject = {}
    for key, value in pairs:
        if key in result:
            raise GroundingContextError("scene_targets_definition_duplicate_key")
        result[key] = value
    return result


def _reject_nonfinite_constant(value: str) -> None:
    """Reject non-JSON numeric constants before they enter target geometry."""
    raise GroundingContextError("scene_targets_definition_nonfinite_constant:" + value)


def decode_scene_definition(report: JSONObject) -> JSONObject:
    """Hash exact transported UTF8 definition bytes, then independently validate their content.

    A JSON string crosses Node/Rust numeric decoding unchanged. Its parsed fields are the
    sole target definition; no separately transported float mirror is accepted or hashed.
    Original HTTP response digests remain separate attributed provenance declarations.
    """
    text = report.get("definition_json")
    if not isinstance(text, str):
        raise GroundingContextError("scene_targets_definition_string_required")
    try:
        raw = text.encode("utf-8")
    except UnicodeEncodeError as error:
        raise GroundingContextError("scene_targets_definition_invalid_utf8") from error
    if not 0 < len(raw) <= MAX_SCENE_BYTES:
        raise GroundingContextError("scene_targets_definition_size_invalid")
    if report.get("region_source_digest") != "sha256:" + hashlib.sha256(raw).hexdigest():
        raise GroundingContextError("scene_targets_exact_definition_digest_mismatch")
    try:
        parsed: JSONValue = json.loads(
            text,
            object_pairs_hook=_unique_definition_object,
            parse_constant=_reject_nonfinite_constant,
        )
    except GroundingContextError:
        raise
    except (ValueError, RecursionError) as error:
        raise GroundingContextError("scene_targets_definition_json_invalid") from error
    definition = _object(parsed, "definition")
    if definition.keys() != {"physical_entity_id", "source_node_id", "map_binding", "regions"}:
        raise GroundingContextError("scene_targets_definition_fields_invalid")
    if any(
        definition.get(key) != report.get(key) for key in ("physical_entity_id", "source_node_id")
    ):
        raise GroundingContextError("scene_targets_definition_identity_mismatch")
    binding = _object(definition.get("map_binding"), "map_binding")
    generation, mode = binding.get("generation"), binding.get("mode")
    if (
        binding.keys() != {"map_id", "mode", "generation", "source"}
        or not _text(binding.get("map_id"), 256)
        or (mode is not None and (not isinstance(mode, str) or len(mode) > 64))
        or not _text(binding.get("source"), 64)
        or (generation is not None and (type(generation) is not int or generation < 0))
    ):
        raise GroundingContextError("scene_targets_map_binding_invalid")
    regions = definition.get("regions")
    if not isinstance(regions, list) or len(regions) > 64:
        raise GroundingContextError("scene_targets_regions_limit_invalid")
    identities = [_validate_region(region) for region in regions]
    if identities != sorted(set(identities)):
        raise GroundingContextError("scene_targets_region_order_or_uniqueness_invalid")
    return definition


def qualify_scene_targets(
    object_ref: JSONObject,
    envelope: JSONObject,
    *,
    source: JSONValue,
    channel_id: JSONValue,
    semantic: JSONValue,
    stale: JSONValue,
    controller_observed_at_ms: JSONValue,
    local_read_elapsed_ms: int | None,
) -> None:
    """Admit only current, bounded, exact-source Scene targets using comparable read clocks.

    Source document hashes are attributed provenance declarations; original HTTP bytes are
    not transported here. Exact original target-definition string bytes are hashed. Source
    wall timestamps are retained, never subtracted from Controller or monotonic clocks.
    """
    qualification_started_ns = time.monotonic_ns()
    value = _object(envelope.get("value"), "report")
    if value.keys() != _REPORT_FIELDS or value.get("schema_version") != SCENE_TARGETS_SCHEMA:
        raise GroundingContextError("scene_targets_schema_or_fields_invalid")
    entity, producer = value.get("physical_entity_id"), value.get("source_node_id")
    if (
        not _identifier(entity)
        or not _identifier(producer)
        or object_ref != {"class": "world", "object_type": "scene-targets", "object_id": entity}
        or source != f"node:{producer}/physical-observer"
        or channel_id != "scene-targets"
        or semantic != "reported"
    ):
        raise GroundingContextError("scene_targets_source_or_entity_mismatch")
    if value.get("availability") != "current" or stale is not False:
        raise GroundingContextError("scene_targets_not_current")
    if (
        value.get("localization_verified") is not False
        or value.get("arrival_verified") is not False
    ):
        raise GroundingContextError("scene_targets_effect_authority_not_provided")
    epoch, sequence = envelope.get("source_epoch"), envelope.get("sequence")
    if not _text(epoch, 256) or type(sequence) is not int or sequence < 0:
        raise GroundingContextError("scene_targets_epoch_or_sequence_missing")
    started, completed = value.get("sample_started_at_ns"), value.get("sample_completed_at_ns")
    if (
        type(started) is not int
        or started <= 0
        or type(completed) is not int
        or completed < started
    ):
        raise GroundingContextError("scene_targets_source_receipt_invalid")
    received, ttl = envelope.get("received_at_ms"), envelope.get("valid_for_ms")
    if (
        type(controller_observed_at_ms) is not int
        or type(received) is not int
        or received < 0
        or controller_observed_at_ms < received
        or type(local_read_elapsed_ms) is not int
        or local_read_elapsed_ms < 0
        or type(ttl) is not int
        or ttl <= 0
    ):
        raise GroundingContextError("scene_targets_controller_clock_unavailable_or_rollback")
    retained = controller_observed_at_ms - received + local_read_elapsed_ms
    age, remaining = value.get("sample_age_ms"), value.get("remaining_at_publisher_ms")
    if (
        value.get("max_age_ms") != MAX_SCENE_AGE_MS
        or type(value.get("max_age_ms")) is not int
        or type(age) is not int
        or not 0 <= age < MAX_SCENE_AGE_MS
        or type(remaining) is not int
        or not 0 < remaining <= MAX_SCENE_AGE_MS - age
        or remaining <= retained
        or retained >= ttl
    ):
        raise GroundingContextError("scene_targets_sample_expired_or_unqualified")
    decode_scene_definition(value)
    _validate_source_documents(value.get("source_documents"))
    try:
        serialized = json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    except (ValueError, OverflowError, UnicodeEncodeError) as error:
        raise GroundingContextError("scene_targets_nonfinite_json") from error
    if len(serialized) > MAX_SCENE_BYTES:
        raise GroundingContextError("scene_targets_payload_limit_exceeded")
    reason = value.get("reason")
    if not _text(reason, 256):
        raise GroundingContextError("scene_targets_reason_invalid")
    qualification_elapsed_ns = time.monotonic_ns() - qualification_started_ns
    if qualification_elapsed_ns < 0:
        raise GroundingContextError("scene_targets_validation_monotonic_clock_rollback")
    final_retained = retained + math.ceil(qualification_elapsed_ns / 1_000_000)
    if final_retained >= remaining or final_retained >= ttl:
        raise GroundingContextError("scene_targets_sample_expired_before_validation_completed")
