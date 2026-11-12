"""Cache bounded original Scene region definitions without acquiring execution authority."""

from __future__ import annotations

import copy
import hashlib
import http.client
import json
import math
import re
import socket
import threading
import time
from collections.abc import Callable
from typing import Any

from robonix_task.physical_observation import entity_reference

SCHEMA = "roboguide.world.scene-targets/v0.2"
MAX_AGE_MS = 15000
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_VALUE_BYTES = 48 * 1024
_PATHS = frozenset({"/api/state", "/api/regions"})
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REGION_REQUIRED = frozenset({"annotation_id", "kind", "name", "points", "stale"})
_REGION_ALLOWED = _REGION_REQUIRED | {"theta", "created_at", "updated_at", "stale_reason"}
_FIELDS = frozenset(
    {
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
)


def _canonical(value: Any) -> bytes:
    """Serialize the exact finite selected fields for stable source identities and bounds."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _finite(value: Any) -> bool:
    """Recognize finite physical coordinates without accepting booleans or huge integers."""
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _text(value: Any, limit: int) -> bool:
    """Recognize one nonblank bounded source string without rewriting its original text."""
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _binding(value: Any) -> dict[str, Any]:
    """Validate only original map binding fields, never infer them from map names or poses."""
    if not isinstance(value, dict) or set(value) != {"map_id", "mode", "generation", "source"}:
        raise ValueError("scene_map_binding_fields_required")
    if (
        not _text(value["map_id"], 256)
        or (
            value["mode"] is not None
            and (not isinstance(value["mode"], str) or len(value["mode"]) > 64)
        )
        or not _text(value["source"], 64)
        or (
            value["generation"] is not None
            and (type(value["generation"]) is not int or value["generation"] < 0)
        )
    ):
        raise ValueError("scene_map_binding_unavailable")
    return copy.deepcopy(value)


def _regions(value: Any) -> list[dict[str, Any]]:
    """Keep finite bounded original region fields, rejecting ambiguous identities and geometry."""
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError("bounded_scene_regions_required")
    regions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for region in value:
        if not isinstance(region, dict) or not _REGION_REQUIRED <= set(region) <= _REGION_ALLOWED:
            raise ValueError("scene_region_fields_invalid")
        if (
            region["kind"] != "region"
            or not _text(region["annotation_id"], 256)
            or not _text(region["name"], 256)
            or type(region["stale"]) is not bool
            or region["annotation_id"] in seen
        ):
            raise ValueError("scene_region_identity_invalid")
        points = region["points"]
        if (
            not isinstance(points, list)
            or not 3 <= len(points) <= 256
            or any(
                not isinstance(point, list)
                or len(point) != 2
                or not all(_finite(coordinate) for coordinate in point)
                for point in points
            )
        ):
            raise ValueError("scene_region_polygon_invalid")
        area = sum(
            points[index][0] * points[(index + 1) % len(points)][1]
            - points[(index + 1) % len(points)][0] * points[index][1]
            for index in range(len(points))
        )
        if not _finite(area) or abs(area) <= 1e-9:
            raise ValueError("scene_region_polygon_degenerate")
        if any(
            key in region and region[key] is not None and not _finite(region[key])
            for key in ("theta", "created_at", "updated_at")
        ) or (
            "stale_reason" in region
            and (not isinstance(region["stale_reason"], str) or len(region["stale_reason"]) > 512)
        ):
            raise ValueError("scene_region_metadata_invalid")
        seen.add(region["annotation_id"])
        regions.append(copy.deepcopy(region))
    return sorted(regions, key=lambda region: str(region["annotation_id"]))


def scene_target_digest(
    entity_id: str, node_id: str, map_binding: dict[str, Any], regions: list[dict[str, Any]]
) -> str:
    """Hash only canonical original target definitions, excluding changing poses and clocks."""
    return (
        "sha256:"
        + hashlib.sha256(
            _canonical(
                {
                    "physical_entity_id": entity_id,
                    "source_node_id": node_id,
                    "map_binding": map_binding,
                    "regions": regions,
                }
            )
        ).hexdigest()
    )


def decode_scene_definition(value: Any) -> dict[str, Any]:
    """Verify exact original UTF8 definition bytes before parsing any target coordinates."""
    if not isinstance(value, dict) or not isinstance(value.get("definition_json"), str):
        raise ValueError("scene_targets_original_definition_required")
    try:
        raw = value["definition_json"].encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("scene_targets_definition_utf8_required") from error
    if len(raw) > MAX_VALUE_BYTES or value.get("region_source_digest") != (
        "sha256:" + hashlib.sha256(raw).hexdigest()
    ):
        raise ValueError("scene_targets_definition_digest_invalid")
    definition = _document(raw)
    if set(definition) != {"physical_entity_id", "source_node_id", "map_binding", "regions"}:
        raise ValueError("scene_targets_definition_fields_invalid")
    if any(
        not isinstance(definition[key], str) or definition[key] != value.get(key)
        for key in ("physical_entity_id", "source_node_id")
    ):
        raise ValueError("scene_targets_definition_identity_mismatch")
    _binding(definition["map_binding"])
    regions = _regions(definition["regions"])
    if regions != definition["regions"]:
        raise ValueError("scene_targets_definition_region_order_invalid")
    if _canonical(definition) != raw:
        raise ValueError("scene_targets_original_canonical_definition_required")
    return copy.deepcopy(definition)


def validate_scene_targets(
    value: Any, *, entity_id: str | None = None, node_id: str | None = None
) -> dict[str, Any]:
    """Validate the exact current report shape and digest without renewing its source lifetime."""
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise ValueError("scene_targets_fields_invalid")
    if not isinstance(value["physical_entity_id"], str) or not isinstance(
        value["source_node_id"], str
    ):
        raise ValueError("scene_targets_explicit_identity_required")
    entity_reference(value["physical_entity_id"])
    entity_reference(value["source_node_id"])
    if (
        value["schema_version"] != SCHEMA
        or entity_id is not None
        and value["physical_entity_id"] != entity_id
        or node_id is not None
        and value["source_node_id"] != node_id
        or value["availability"] != "current"
        or value["localization_verified"] is not False
        or value["arrival_verified"] is not False
        or value["max_age_ms"] != MAX_AGE_MS
        or type(value["max_age_ms"]) is not int
        or not _text(value["reason"], 256)
    ):
        raise ValueError("scene_targets_current_identity_required")
    for key in ("sample_started_at_ns", "sample_completed_at_ns"):
        if type(value[key]) is not int or value[key] <= 0:
            raise ValueError("scene_targets_receipt_required")
    if value["sample_completed_at_ns"] < value["sample_started_at_ns"]:
        raise ValueError("scene_targets_source_wall_clock_regressed")
    age, remaining = value["sample_age_ms"], value["remaining_at_publisher_ms"]
    if (
        type(age) is not int
        or not 0 <= age < MAX_AGE_MS
        or type(remaining) is not int
        or not 0 < remaining <= MAX_AGE_MS - age
    ):
        raise ValueError("scene_targets_budget_invalid")
    decode_scene_definition(value)
    documents = value["source_documents"]
    if not isinstance(documents, dict) or set(documents) != {"state", "regions"}:
        raise ValueError("scene_targets_document_sources_required")
    for name, fields in (
        ("state", {"path", "body_sha256", "confirmation_body_sha256"}),
        ("regions", {"path", "body_sha256"}),
    ):
        document = documents[name]
        if not isinstance(document, dict) or set(document) != fields:
            raise ValueError("scene_targets_document_fields_invalid")
        if document["path"] != "/api/" + name or any(
            not isinstance(document[field], str) or _DIGEST.fullmatch(document[field]) is None
            for field in fields - {"path"}
        ):
            raise ValueError("scene_targets_document_digest_invalid")
    if len(json.dumps(value, allow_nan=False).encode()) > MAX_VALUE_BYTES:
        raise ValueError("scene_targets_world_payload_too_large")
    return copy.deepcopy(value)


class _DeadlineSocket(socket.socket):
    """Charge every blocking read to one original monotonic request deadline."""

    def __init__(self, timeout_seconds: float) -> None:
        """Retain the same finite budget for connecting, sending, headers and body reads."""
        super().__init__(socket.AF_INET, socket.SOCK_STREAM)
        self.deadline = time.monotonic() + timeout_seconds

    def _budget(self) -> None:
        """Prevent an incrementally streamed response from renewing its original budget."""
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("scene_total_read_budget_exhausted")
        self.settimeout(remaining)

    def connect(self, address: Any) -> None:
        """Connect to the caller's fixed literal loopback address under the same deadline."""
        self._budget()
        super().connect(address)

    def sendall(self, data: Any, flags: int = 0) -> None:
        """Charge request transmission to the same original deadline."""
        self._budget()
        super().sendall(data, flags)

    def recv_into(self, buffer: Any, nbytes: int = 0, flags: int = 0) -> int:
        """Bound every HTTPResponse file read, including incremental headers and chunks."""
        self._budget()
        return super().recv_into(buffer, nbytes, flags)


def read_scene_document(path: str, timeout_seconds: float) -> bytes:
    """GET one fixed loopback Scene path with proxies, redirects and oversized bodies refused."""
    if path not in _PATHS or not 0 < timeout_seconds <= 5:
        raise ValueError("fixed_scene_read_required")
    connection = http.client.HTTPConnection("127.0.0.1", 50107, timeout=timeout_seconds)
    try:
        transport = _DeadlineSocket(timeout_seconds)
        connection.sock = transport
        transport.connect(("127.0.0.1", 50107))
        connection.request("GET", path, headers={"Accept": "application/json"})
        with connection.getresponse() as response:
            if response.status != 200:
                raise ValueError("scene_http_status_unavailable")
            raw = response.read(MAX_BODY_BYTES + 1)
    finally:
        connection.close()
    if len(raw) > MAX_BODY_BYTES:
        raise ValueError("scene_http_body_too_large")
    return raw


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate JSON keys instead of silently changing an original source definition."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("scene_duplicate_json_key")
        result[key] = value
    return result


def _document(raw: bytes) -> dict[str, Any]:
    """Parse bounded finite original JSON objects without admitting duplicate source fields."""
    if not isinstance(raw, bytes) or len(raw) > MAX_BODY_BYTES:
        raise ValueError("bounded_scene_http_body_required")
    value = json.loads(raw, object_pairs_hook=_unique_object)
    if not isinstance(value, dict):
        raise ValueError("scene_json_object_required")
    _canonical(value)
    return value


class SceneTargetObserver:
    """Own a readonly cached Scene projection sampled serially by the existing sensor worker."""

    def __init__(
        self,
        entity_id: str,
        node_id: str,
        *,
        reader: Callable[[str, float], bytes] = read_scene_document,
        clock: Callable[[], int] = time.monotonic_ns,
        wall_clock: Callable[[], int] = time.time_ns,
    ) -> None:
        """Freeze deployment identity and initialize unknown evidence without reading HTTP."""
        entity_reference(entity_id)
        entity_reference(node_id)
        self.entity_id, self.node_id = entity_id, node_id
        self.reader, self.clock, self.wall_clock = reader, clock, wall_clock
        self.lock = threading.RLock()
        self.started_mono: int | None = None
        self.value: dict[str, Any] = self._empty("unknown", "scene_not_sampled")

    def _empty(self, availability: str, reason: str) -> dict[str, Any]:
        """Return unknowns without retaining a prior map as current after source failure."""
        return {
            "schema_version": SCHEMA,
            "physical_entity_id": self.entity_id,
            "source_node_id": self.node_id,
            "availability": availability,
            "sample_started_at_ns": None,
            "sample_completed_at_ns": None,
            "sample_age_ms": None,
            "max_age_ms": MAX_AGE_MS,
            "remaining_at_publisher_ms": 0,
            "definition_json": None,
            "region_source_digest": None,
            "source_documents": {},
            "localization_verified": False,
            "arrival_verified": False,
            "reason": reason,
        }

    def sample(self) -> None:
        """Read fixed state/regions/state, rejecting cross-binding and failed samples."""
        started, wall_started = self.clock(), self.wall_clock()
        try:
            originals: list[bytes] = []
            for path in ("/api/state", "/api/regions", "/api/state"):
                remaining = 5 - (self.clock() - started) / 1_000_000_000
                if not 0 < remaining <= 5:
                    raise ValueError("scene_sampling_budget_exhausted")
                originals.append(self.reader(path, remaining))
            state, region_document, confirmation = map(_document, originals)
            binding = _binding(state.get("map_binding"))
            if _binding(confirmation.get("map_binding")) != binding:
                raise ValueError("scene_map_binding_changed_during_read")
            if (
                region_document.get("ok") is not True
                or not isinstance(region_document.get("annotations"), list)
                or len(region_document["annotations"]) > 256
            ):
                raise ValueError("scene_annotations_unavailable")
            candidates = []
            for annotation in region_document["annotations"]:
                if not isinstance(annotation, dict):
                    raise ValueError("scene_annotation_object_required")
                if annotation.get("kind") == "region":
                    candidates.append(
                        {key: value for key, value in annotation.items() if key in _REGION_ALLOWED}
                    )
            regions = _regions(candidates)
            value = self._empty("current", "current_scene_http_definitions_not_localization")
            definition_json = _canonical(
                {
                    "physical_entity_id": self.entity_id,
                    "source_node_id": self.node_id,
                    "map_binding": binding,
                    "regions": regions,
                }
            ).decode("utf-8")
            value.update(
                definition_json=definition_json,
                region_source_digest="sha256:"
                + hashlib.sha256(definition_json.encode()).hexdigest(),
                source_documents={
                    "state": {
                        "path": "/api/state",
                        "body_sha256": "sha256:" + hashlib.sha256(originals[0]).hexdigest(),
                        "confirmation_body_sha256": "sha256:"
                        + hashlib.sha256(originals[2]).hexdigest(),
                    },
                    "regions": {
                        "path": "/api/regions",
                        "body_sha256": "sha256:" + hashlib.sha256(originals[1]).hexdigest(),
                    },
                },
            )
            elapsed = self.clock() - started
            if elapsed < 0 or elapsed >= 5_000_000_000:
                raise ValueError("scene_sampling_budget_exhausted")
            value.update(
                sample_started_at_ns=wall_started,
                sample_completed_at_ns=self.wall_clock(),
                sample_age_ms=math.ceil(elapsed / 1_000_000),
                remaining_at_publisher_ms=MAX_AGE_MS - math.ceil(elapsed / 1_000_000),
            )
            validate_scene_targets(value)
        except (
            OSError,
            http.client.HTTPException,
            ValueError,
            TypeError,
            KeyError,
            OverflowError,
            RecursionError,
        ) as error:
            value = self._empty("unavailable", "scene_read_failed:" + type(error).__name__)
            value.update(
                sample_started_at_ns=wall_started, sample_completed_at_ns=self.wall_clock()
            )
        with self.lock:
            self.started_mono = started
            self.value = value

    def read(self) -> dict[str, Any]:
        """Read the cache and charge monotonic time without Scene calls or TTL renewal."""
        with self.lock:
            value, started = copy.deepcopy(self.value), self.started_mono
        if started is None:
            return value
        elapsed = self.clock() - started
        if elapsed < 0:
            unavailable = self._empty("unavailable", "scene_monotonic_clock_regressed")
            unavailable.update(
                sample_started_at_ns=value["sample_started_at_ns"],
                sample_completed_at_ns=value["sample_completed_at_ns"],
            )
            return unavailable
        value["sample_age_ms"] = math.ceil(elapsed / 1_000_000)
        if value["availability"] == "current":
            value["remaining_at_publisher_ms"] = max(0, MAX_AGE_MS - value["sample_age_ms"])
            if value["remaining_at_publisher_ms"] == 0:
                value.update(availability="expired", reason="scene_cache_expired")
        return value
