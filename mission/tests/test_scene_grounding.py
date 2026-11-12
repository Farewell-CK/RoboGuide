"""Exercise exact Scene provenance, source budgets and immutable MI inclusion."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import cast

import pytest
from mission.grounding_context import (
    GroundingContextError,
    GroundingContextSnapshot,
    GroundingFreshness,
)
from mission.grounding_reader import HttpMissionGroundingReader
from mission.models import JSONObject, JSONValue
from mission.request_record import DialogueSpeaker, DialogueTurn, DialogueTurnKind
from mission.scene_grounding import SCENE_TARGETS_SCHEMA, qualify_scene_targets

STATE_URL = "http://controller.test/v1/state/records?object_class=world"
MEMORY_URL = "http://artifact.test/v1/memories"


def _record() -> JSONObject:
    """Create one synthetic producer's canonical report, not a peer's copied map."""
    value: JSONObject = {
        "schema_version": SCENE_TARGETS_SCHEMA,
        "physical_entity_id": "entity-dog1",
        "source_node_id": "dog1",
        "availability": "current",
        "sample_started_at_ns": 1_000_000_000,
        "sample_completed_at_ns": 1_010_000_000,
        "sample_age_ms": 100,
        "max_age_ms": 15000,
        "remaining_at_publisher_ms": 14900,
        "map_binding": {
            "map_id": "local-map",
            "mode": "localization",
            "generation": 1,
            "source": "env",
        },
        "regions": [
            {
                "annotation_id": "anno.tea",
                "kind": "region",
                "name": "茶水间门口",
                "points": [[0, 0], [1, 0], [1, 1], [0, 1]],
                "stale": False,
                "theta": None,
                "created_at": 1,
                "updated_at": 2,
                "stale_reason": "",
            }
        ],
        "source_documents": {
            "state": {
                "path": "/api/state",
                "body_sha256": "sha256:" + "1" * 64,
                "confirmation_body_sha256": "sha256:" + "2" * 64,
            },
            "regions": {"path": "/api/regions", "body_sha256": "sha256:" + "3" * 64},
        },
        "localization_verified": False,
        "arrival_verified": False,
        "reason": "reported Scene target projection; no motion authority",
    }
    canonical = {
        key: value[key]
        for key in ("physical_entity_id", "source_node_id", "map_binding", "regions")
    }
    value.pop("map_binding")
    value.pop("regions")
    _set_definition(value, canonical)
    return {
        "object": {"class": "world", "object_type": "scene-targets", "object_id": "entity-dog1"},
        "semantic": "reported",
        "source": "node:dog1/physical-observer",
        "channel_id": "scene-targets",
        "value": {
            "payload_schema": SCENE_TARGETS_SCHEMA,
            "value": value,
            "source_observed_at_ms": 999999999,
            "received_at_ms": 20,
            "valid_for_ms": 15000,
            "confidence_millionths": None,
            "source_epoch": "session-one",
            "sequence": 4,
        },
        "stale": False,
    }


def _body(record: JSONObject) -> JSONObject:
    """Select the mutable fixture body solely for explicit fault construction."""
    return cast(JSONObject, cast(JSONObject, record["value"])["value"])


def _definition(record: JSONObject) -> JSONObject:
    """Parse the test definition string without selecting a transported numeric mirror."""
    return cast(JSONObject, json.loads(cast(str, _body(record)["definition_json"])))


def _set_definition(
    body: JSONObject, definition: JSONObject, *, update_digest: bool = True
) -> None:
    """Create exact string fixtures; an explicit false flag models retained stale digests."""
    raw = json.dumps(definition, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    body["definition_json"] = raw
    if update_digest:
        body["region_source_digest"] = "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def _memory() -> JSONObject:
    """Keep independent valid metadata during delayed-source rejection tests."""
    return {
        "schema": "roboguide.memory-manifest/v0.1",
        "selector": {"memory_id": "preserved-memory", "revision_id": "r1"},
        "kind": "experience",
        "provider_id": "fixture-provider",
        "owner": {"owner": "node", "node_id": "dog1", "local_system_id": "fixture"},
        "scope": {"kind": "global"},
        "visibility": "discoverable",
        "payload_schema": "fixture.memory/v0.1",
        "media_type": "application/json",
        "artifact": None,
        "source_mission_id": None,
        "source_execution_id": None,
        "source_task_ref": None,
        "created_at": 1,
    }


class Transport:
    """Offer original finite JSON behind the real reader without opening a network."""

    def __init__(self, records: list[JSONValue], observed_at_ms: JSONValue = 22) -> None:
        """Retain exact records and same-Controller read metadata for one capture."""
        self.records = records
        self.observed_at_ms = observed_at_ms

    def get_json(self, url: str, timeout_seconds: float) -> JSONObject:
        """Return only the fixed origins used by the normal World/Memory reader."""
        if url == STATE_URL:
            return {
                "schema": "roboguide.state-query/v0.1",
                "observed_at_ms": self.observed_at_ms,
                "records": self.records,
            }
        assert url == MEMORY_URL and timeout_seconds > 0
        return {"schema": "roboguide.memory-catalog/v0.1", "memories": [_memory()]}


def _reader(
    transport: Transport, *, schemas: frozenset[str] | None = None
) -> HttpMissionGroundingReader:
    """Admit this source explicitly without changing the generic World-only policy."""
    return HttpMissionGroundingReader(
        "http://controller.test",
        "http://artifact.test",
        2,
        16,
        16,
        transport,
        admitted_world_payload_schemas=frozenset({SCENE_TARGETS_SCHEMA})
        if schemas is None
        else schemas,
    )


def _capture(reader: HttpMissionGroundingReader) -> GroundingContextSnapshot:
    """Freeze one ordinary instruction while retaining original evidence identities."""
    dialogue = (
        DialogueTurn(
            "turn-one", DialogueSpeaker.USER, DialogueTurnKind.INSTRUCTION, "准备双狗巡检", 1
        ),
    )
    return reader.capture("scene-preparation", dialogue, 999999999999)


def test_current_scene_report_preserves_original_values_clocks_and_reported_limits() -> None:
    """Admission does not upgrade region provenance into localization or arrival."""
    record = _record()
    before = json.dumps(record, sort_keys=True)
    snapshot = _capture(_reader(Transport([record])))
    assert len(snapshot.state_evidence) == 1
    item = snapshot.state_evidence[0]
    assert item.value == _body(record)
    assert item.semantic == "reported" and item.freshness is GroundingFreshness.FRESH
    assert item.source_epoch == "session-one" and item.sequence == 4
    assert item.valid_for_ms == 15000 and item.source_observed_at_ms == 999999999
    assert "#scene-targets-profile=v0.2" in snapshot.selection_policy_ref
    assert GroundingContextSnapshot.from_json(snapshot.to_json()) == snapshot
    assert json.dumps(record, sort_keys=True) == before
    assert snapshot.gaps == ()


@pytest.mark.parametrize(
    "mode,generation", [(None, None), ("", None), ("localization", None), ("", 0), (None, 1)]
)
def test_scene_unknown_binding_fields_remain_in_exact_definition(
    mode: JSONValue,
    generation: JSONValue,
) -> None:
    """Target definitions preserve null generation and empty mode without pose authority."""
    record = _record()
    definition = _definition(record)
    binding = cast(JSONObject, definition["map_binding"])
    binding.update(mode=mode, generation=generation)
    _set_definition(_body(record), definition)
    before = json.dumps(record, sort_keys=True)
    snapshot = _capture(_reader(Transport([record])))
    assert len(snapshot.state_evidence) == 1
    selected = cast(JSONObject, snapshot.state_evidence[0].value)
    assert json.loads(cast(str, selected["definition_json"]))["map_binding"] == binding
    assert selected["localization_verified"] is False and selected["arrival_verified"] is False
    assert "map_binding" not in selected and "regions" not in selected
    assert json.dumps(record, sort_keys=True) == before


def test_definition_change_cannot_reuse_the_original_digest() -> None:
    """Changing unknown fields cannot retain a digest made over different exact bytes."""
    record = _record()
    definition = _definition(record)
    cast(JSONObject, definition["map_binding"]).update(mode="", generation=None)
    _set_definition(_body(record), definition, update_digest=False)
    snapshot = _capture(_reader(Transport([record])))
    assert snapshot.state_evidence == ()
    assert any("exact_definition_digest_mismatch" in gap.detail for gap in snapshot.gaps)


@pytest.mark.parametrize(
    "field,value",
    [
        ("mode", True),
        ("mode", 1),
        ("mode", "x" * 65),
        ("generation", False),
        ("generation", -1),
        ("generation", 1.0),
        ("map_id", ""),
        ("source", ""),
    ],
)
def test_scene_nullable_binding_rejects_invalid_types(field: str, value: JSONValue) -> None:
    """Nullable source values do not relax required map identity or field types."""
    record = _record()
    definition = _definition(record)
    cast(JSONObject, definition["map_binding"])[field] = value
    _set_definition(_body(record), definition)
    snapshot = _capture(_reader(Transport([record])))
    assert snapshot.state_evidence == ()
    assert any("map_binding_invalid" in gap.detail for gap in snapshot.gaps)


@pytest.mark.parametrize("availability", ["unknown", "unavailable", "expired"])
def test_scene_noncurrent_reports_are_excluded_with_a_gap(availability: str) -> None:
    """A fresh outer envelope cannot revive a failed or old Scene sampling result."""
    record = _record()
    _body(record)["availability"] = availability
    snapshot = _capture(_reader(Transport([record])))
    assert snapshot.state_evidence == ()
    assert any("scene_targets_not_current" in gap.detail for gap in snapshot.gaps)


@pytest.mark.parametrize(
    "change,reason",
    [
        ("foreign-source", "source_or_entity"),
        ("producer-mismatch", "source_or_entity"),
        ("entity-mismatch", "source_or_entity"),
        ("foreign-channel", "source_or_entity"),
        ("wrong-type", "source_or_entity"),
        ("observed", "source_or_entity"),
        ("stale", "not_current"),
        ("epoch", "epoch_or_sequence"),
        ("sequence", "epoch_or_sequence"),
        ("age", "sample_expired"),
        ("negative-age", "sample_expired"),
        ("inflated-budget", "sample_expired"),
        ("old-inner", "sample_expired"),
        ("outer-renewal", "sample_expired"),
        ("max-age", "sample_expired"),
        ("receipt-order", "source_receipt"),
        ("canonical-digest", "exact_definition_digest"),
        ("changed-target", "exact_definition_digest"),
        ("changed-map", "exact_definition_digest"),
        ("source-digest", "source_document_digest"),
        ("source-path", "source_document_path"),
        ("source-confirmation", "source_document_path"),
        ("duplicate-region", "region_order_or_uniqueness"),
        ("degenerate", "polygon_degenerate"),
        ("nonfinite", "definition_nonfinite"),
        ("boolean-point", "polygon_coordinate"),
        ("region-stale", "region_identity_or_staleness"),
        ("generation", "map_binding"),
        ("localization", "effect_authority"),
        ("arrival", "effect_authority"),
    ],
)
def test_scene_rejects_cross_source_expiry_digest_and_geometry_faults(
    change: str, reason: str
) -> None:
    """A source cannot borrow another body, revive data or alter its original goals."""
    record = _record()
    body = _body(record)
    definition = _definition(record)
    region = cast(JSONObject, cast(list[JSONValue], definition["regions"])[0])
    envelope = cast(JSONObject, record["value"])
    documents = cast(JSONObject, body["source_documents"])
    state_document = cast(JSONObject, documents["state"])
    if change == "foreign-source":
        record["source"] = "node:dog2/physical-observer"
    elif change == "producer-mismatch":
        body["source_node_id"] = "dog2"
    elif change == "entity-mismatch":
        cast(JSONObject, record["object"])["object_id"] = "entity-dog2"
    elif change == "foreign-channel":
        record["channel_id"] = "robot-state"
    elif change == "wrong-type":
        cast(JSONObject, record["object"])["object_type"] = "robot"
    elif change == "observed":
        record["semantic"] = "observed"
    elif change == "stale":
        record["stale"] = True
    elif change == "epoch":
        envelope["source_epoch"] = None
    elif change == "sequence":
        envelope["sequence"] = True
    elif change == "age":
        body["sample_age_ms"] = 15000
    elif change == "negative-age":
        body["sample_age_ms"] = -1
    elif change == "inflated-budget":
        body["remaining_at_publisher_ms"] = 14901
    elif change == "old-inner":
        body.update(sample_age_ms=14999, remaining_at_publisher_ms=1)
    elif change == "outer-renewal":
        envelope["valid_for_ms"] = 1
    elif change == "max-age":
        body["max_age_ms"] = 15001
    elif change == "receipt-order":
        body["sample_completed_at_ns"] = 1
    elif change == "canonical-digest":
        body["region_source_digest"] = "sha256:" + "0" * 64
    elif change == "changed-target":
        region["name"] = "foreign target"
    elif change == "changed-map":
        cast(JSONObject, definition["map_binding"])["map_id"] = "other-map"
    elif change == "source-digest":
        state_document["body_sha256"] = "not-a-digest"
    elif change == "source-path":
        state_document["path"] = "http://foreign.test/api/state"
    elif change == "source-confirmation":
        state_document.pop("confirmation_body_sha256")
    elif change == "duplicate-region":
        cast(list[JSONValue], definition["regions"]).append(deepcopy(region))
    elif change == "degenerate":
        region["points"] = [[0, 0], [1, 1], [2, 2]]
    elif change == "nonfinite":
        region["points"] = [[0, 0], [float("nan"), 0], [1, 1]]
    elif change == "boolean-point":
        region["points"] = [[0, 0], [True, 0], [1, 1]]
    elif change == "region-stale":
        region["stale"] = True
    elif change == "generation":
        cast(JSONObject, definition["map_binding"])["generation"] = True
    elif change == "localization":
        body["localization_verified"] = True
    elif change == "arrival":
        body["arrival_verified"] = True
    else:
        raise AssertionError(change)
    if change in {
        "changed-target",
        "changed-map",
        "duplicate-region",
        "degenerate",
        "nonfinite",
        "boolean-point",
        "region-stale",
        "generation",
    }:
        _set_definition(
            body, definition, update_digest=change not in {"changed-target", "changed-map"}
        )
    snapshot = _capture(_reader(Transport([record])))
    assert snapshot.state_evidence == ()
    assert any("scene_targets_" + reason in gap.detail for gap in snapshot.gaps)
    assert len(snapshot.memory_evidence) == 1


@pytest.mark.parametrize("observed", [None, True, 19, "22"])
def test_scene_requires_same_controller_observation_clock(observed: JSONValue) -> None:
    """Source-local wall times cannot replace a missing or rolled-back Controller clock."""
    snapshot = _capture(_reader(Transport([_record()], observed)))
    assert snapshot.state_evidence == ()
    assert any("controller_clock" in gap.detail for gap in snapshot.gaps)


@pytest.mark.parametrize("late_source", ["memory", "semantic", "planning"])
def test_scene_budget_is_rechecked_after_all_acquisition_before_freeze(
    monkeypatch: pytest.MonkeyPatch,
    late_source: str,
) -> None:
    """Initially valid targets expire during later reads without removing independent Memory."""
    clock = [0]
    monkeypatch.setattr("mission.grounding_reader.time.monotonic_ns", lambda: clock[0])

    class DelayedTransport(Transport):
        """Model slow Memory without sleeping or mutating source timestamps."""

        def get_json(self, url: str, timeout_seconds: float) -> JSONObject:
            """Consume the original budget only after State has already been read."""
            if url == MEMORY_URL and late_source == "memory":
                clock[0] = 10_000_000
            return super().get_json(url, timeout_seconds)

    def delay_optional_read(self: HttpMissionGroundingReader, gaps: object) -> None:
        """Model the last optional evidence acquisition after initial State qualification."""
        del self, gaps
        clock[0] = 10_000_000

    if late_source != "memory":
        method = (
            "_read_semantic_evidence"
            if late_source == "semantic"
            else "_read_planning_world_evidence"
        )
        monkeypatch.setattr(HttpMissionGroundingReader, method, delay_optional_read)
    record = _record()
    _body(record).update(sample_age_ms=14995, remaining_at_publisher_ms=5)
    before = json.dumps(record, sort_keys=True)
    snapshot = _capture(_reader(DelayedTransport([record], 20)))
    assert snapshot.state_evidence == ()
    assert len(snapshot.memory_evidence) == 1
    assert any(
        "before snapshot freeze: scene_targets_sample_expired" in gap.detail
        for gap in snapshot.gaps
    )
    assert json.dumps(record, sort_keys=True) == before


def test_scene_opt_in_does_not_admit_node_records_or_unlisted_schemas() -> None:
    """New schema qualification preserves World-only filtering and explicit admission."""
    node = _record()
    cast(JSONObject, node["object"])["class"] = "node"
    assert _capture(_reader(Transport([node]))).state_evidence == ()
    assert _capture(_reader(Transport([_record()]), schemas=frozenset())).state_evidence == ()


def test_real_precision_coordinates_survive_numeric_outer_transport_as_exact_string() -> None:
    """Numeric parsing by an intermediate runtime never touches the authoritative definition."""
    record = _record()
    definition = _definition(record)
    region = cast(JSONObject, cast(list[JSONValue], definition["regions"])[0])
    region["points"] = [
        [1.6169695854376407, -2.2241165304059414],
        [11.399093130268493, -2.2241165304059414],
        [11.399093130268493, 1.8489674036424497],
        [1.6169695854376407, -0.12282490304588523],
    ]
    region["created_at"] = 1793583987.5237079
    _set_definition(_body(record), definition)
    original_string = cast(str, _body(record)["definition_json"])
    original_digest = _body(record)["region_source_digest"]
    transported = cast(JSONObject, json.loads(json.dumps(record), parse_float=lambda _: 123.0))
    snapshot = _capture(_reader(Transport([transported])))
    assert len(snapshot.state_evidence) == 1
    value = cast(JSONObject, snapshot.state_evidence[0].value)
    assert value["definition_json"] == original_string
    assert value["region_source_digest"] == original_digest
    decoded = json.loads(original_string)
    assert decoded["regions"][0]["points"][1][0] == 11.399093130268493
    assert decoded["regions"][0]["created_at"] == 1793583987.5237079
    changed = deepcopy(transported)
    _body(changed)["definition_json"] = original_string.replace(
        "11.399093130268493", "11.399093130268492"
    )
    rejected = _capture(_reader(Transport([changed])))
    assert rejected.state_evidence == ()
    assert any("exact_definition_digest_mismatch" in gap.detail for gap in rejected.gaps)


@pytest.mark.parametrize("field", ["physical_entity_id", "source_node_id"])
def test_inner_definition_identity_must_equal_the_authenticated_outer_source(field: str) -> None:
    """A valid byte digest cannot make another robot's definition belong to this source."""
    record = _record()
    definition = _definition(record)
    definition[field] = "foreign-dog"
    _set_definition(_body(record), definition)
    snapshot = _capture(_reader(Transport([record])))
    assert snapshot.state_evidence == ()
    assert any("definition_identity_mismatch" in gap.detail for gap in snapshot.gaps)


@pytest.mark.parametrize(
    "malformation,reason",
    [
        ("duplicate-root", "duplicate_key"),
        ("duplicate-nested", "duplicate_key"),
        ("nan", "nonfinite_constant"),
        ("infinity", "nonfinite_constant"),
        ("overflow", "polygon_coordinate_invalid"),
        ("malformed", "definition_json_invalid"),
        ("surrogate", "definition_invalid_utf8"),
        ("typed-mirror", "schema_or_fields_invalid"),
        ("old-schema", "schema_or_fields_invalid"),
    ],
)
def test_definition_string_remains_strict_json_and_has_no_competing_mirror(
    malformation: str,
    reason: str,
) -> None:
    """Matching exact digests do not admit ambiguous JSON, nonfinite data or old typed contracts."""
    record = _record()
    body = _body(record)
    raw = cast(str, body["definition_json"])
    if malformation == "duplicate-root":
        raw = raw.replace("{", '{"physical_entity_id":"foreign-dog",', 1)
    elif malformation == "duplicate-nested":
        raw = raw.replace('"map_id":"local-map"', '"map_id":"foreign-map","map_id":"local-map"')
    elif malformation in {"nan", "infinity", "overflow"}:
        scalar = {"nan": "NaN", "infinity": "Infinity", "overflow": "1e400"}[malformation]
        raw = raw.replace('"points":[[0,0]', '"points":[[' + scalar + ",0]")
    elif malformation == "malformed":
        raw = "{"
    elif malformation == "surrogate":
        raw = "\ud800"
    elif malformation == "typed-mirror":
        body["map_binding"] = {"map_id": "competing"}
    elif malformation == "old-schema":
        body["schema_version"] = "roboguide.world.scene-targets/v0.1"
    body["definition_json"] = raw
    if malformation != "surrogate":
        body["region_source_digest"] = "sha256:" + hashlib.sha256(raw.encode()).hexdigest()
    snapshot = _capture(_reader(Transport([record])))
    assert snapshot.state_evidence == ()
    assert any(reason in gap.detail for gap in snapshot.gaps)


def test_scene_validation_work_consumes_the_original_remaining_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A target cannot stay admitted when polygon/digest verification itself outlives its budget."""
    record = _record()
    _body(record).update(sample_age_ms=14995, remaining_at_publisher_ms=5)
    clock = [0]
    original_sha256 = hashlib.sha256

    def delayed_digest(raw: bytes) -> object:
        """Model bounded digest computation taking time without changing original content."""
        clock[0] = 10_000_000
        return original_sha256(raw)

    monkeypatch.setattr("mission.scene_grounding.time.monotonic_ns", lambda: clock[0])
    monkeypatch.setattr("mission.scene_grounding.hashlib.sha256", delayed_digest)
    with pytest.raises(GroundingContextError, match="expired_before_validation_completed"):
        qualify_scene_targets(
            cast(JSONObject, record["object"]),
            cast(JSONObject, record["value"]),
            source=record["source"],
            channel_id=record["channel_id"],
            semantic=record["semantic"],
            stale=record["stale"],
            controller_observed_at_ms=20,
            local_read_elapsed_ms=0,
        )
