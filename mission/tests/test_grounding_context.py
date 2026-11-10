"""Deterministic tests for the State and Memory Mission grounding boundary."""

from __future__ import annotations

import http.client
import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
from mission.grounding_context import (
    MAX_GROUNDING_CONTEXT_BYTES,
    GroundingContextError,
    GroundingContextSizeError,
    GroundingContextSnapshot,
    GroundingFreshness,
    GroundingGap,
    MemoryContentStatus,
    admitted_physical_entity_ids,
    dialogue_digest,
)
from mission.grounding_reader import (
    GroundingAcquisitionError,
    GroundingReadError,
    HttpMissionGroundingReader,
    UrllibGroundingJsonTransport,
)
from mission.models import JSONObject, JSONValue
from mission.request_record import DialogueSpeaker, DialogueTurn, DialogueTurnKind


class FakeGroundingTransport:
    """Return exact State and Memory documents without network access."""

    def __init__(self, responses: Mapping[str, JSONObject | Exception]) -> None:
        """Retain scripted URL responses and an inspectable request trace."""
        self._responses = dict(responses)
        self.calls: list[tuple[str, float]] = []

    def get_json(self, url: str, timeout_seconds: float) -> JSONObject:
        """Return one scripted object or raise its scripted transport failure."""
        self.calls.append((url, timeout_seconds))
        response = self._responses[url]
        if isinstance(response, Exception):
            raise response
        return response


class SequenceGroundingTransport:
    """Return per-source response sequences for bounded acquisition retry tests."""

    def __init__(self, responses: Mapping[str, list[JSONObject | Exception]]) -> None:
        """Retain finite response queues and an inspectable request trace."""
        self._responses = {url: list(items) for url, items in responses.items()}
        self.calls: list[tuple[str, float]] = []

    def get_json(self, url: str, timeout_seconds: float) -> JSONObject:
        """Return the next response or raise the next scripted failure for one source."""
        self.calls.append((url, timeout_seconds))
        response = self._responses[url].pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _dialogue(content: str = "把急救包送到一楼前台") -> tuple[DialogueTurn, ...]:
    """Build one stable user instruction for context identity tests."""
    return (
        DialogueTurn(
            "turn-0001",
            DialogueSpeaker.USER,
            DialogueTurnKind.INSTRUCTION,
            content,
            1,
        ),
    )


def _world_record(
    object_id: str,
    *,
    semantic: str = "observed",
    stale: bool = False,
    received_at_ms: int = 20,
) -> JSONObject:
    """Build one source-aware World State API record."""
    return {
        "object": {"class": "world", "object_type": "place", "object_id": object_id},
        "semantic": semantic,
        "source": "node:camera-a/local-system:perception",
        "channel_id": "semantic-places",
        "value": {
            "payload_schema": "roboguide.test-place/v0.1",
            "value": {"label": "一楼前台"},
            "source_observed_at_ms": 9000,
            "received_at_ms": received_at_ms,
            "valid_for_ms": 100,
            "confidence_millionths": 900000,
            "source_epoch": "session-a",
            "sequence": 2,
        },
        "stale": stale,
    }


def _node_record() -> JSONObject:
    """Build one live deployment record that must not enter Mission grounding."""
    return {
        "object": {"class": "node", "object_type": "node", "object_id": "dog-a"},
        "semantic": "reported",
        "source": "node:dog-a",
        "channel_id": "health",
        "value": {"health": "online"},
        "stale": False,
    }


def _memory_manifest(
    memory_id: str,
    *,
    kind: str = "semantic",
    scope: str = "global",
) -> JSONObject:
    """Build one generic Memory catalog manifest with no embedded content bytes."""
    return {
        "schema": "roboguide.memory-manifest/v0.1",
        "selector": {"memory_id": memory_id, "revision_id": "r1"},
        "kind": kind,
        "provider_id": "semantic-memory",
        "owner": {"owner": "robo_guide", "component": "semantic-index"},
        "scope": {"kind": scope},
        "visibility": "discoverable",
        "payload_schema": "roboguide.semantic-place/v0.1",
        "media_type": "application/json",
        "artifact": None,
        "source_mission_id": None,
        "source_execution_id": None,
        "source_task_ref": None,
        "created_at": 15,
    }


def _spatial_memory_view(memory_id: str) -> JSONObject:
    """Build the typed map catalog adapter shape emitted by the Rust Artifact facade."""
    return {
        "schema": "roboguide.memory-manifest-view/v0.1",
        "selector": {"memory_id": memory_id, "revision_id": "r1"},
        "kind": "spatial",
        "provider_id": "typed-map-catalog",
        "owner": {"owner": "node", "node_id": "dog-a", "local_system_id": None},
        "scope": {"kind": "global"},
        "visibility": "exchangeable",
        "payload_schema": "roboguide.spatial-memory/v0.1",
        "media_type": "application/octet-stream",
        "artifact": {"content_digest": f"sha256:{'0' * 64}", "byte_size": 4},
        "source_mission_id": None,
        "source_execution_id": None,
        "source_task_ref": None,
        "created_at": 15,
        "typed_extension": "map",
        "status": "Published",
    }


def _reader(
    transport: FakeGroundingTransport,
    admitted_world_payload_schemas: frozenset[str] = frozenset({"roboguide.test-place/v0.1"}),
) -> HttpMissionGroundingReader:
    """Compose one bounded reader over fixed test origins."""
    return HttpMissionGroundingReader(
        "http://controller.test",
        "http://artifact.test",
        2.0,
        16,
        16,
        transport,
        admitted_world_payload_schemas=admitted_world_payload_schemas,
    )


def test_snapshot_round_trip_preserves_stable_identity_and_rejects_tampering() -> None:
    """Snapshot ordering and digest validation make deliberation evidence immutable."""
    transport = FakeGroundingTransport(
        {
            "http://controller.test/v1/state/records?object_class=world": {
                "schema": "roboguide.state-query/v0.1",
                "records": [_world_record("place-z"), _world_record("place-a")],
            },
            "http://artifact.test/v1/memories": {
                "schema": "roboguide.memory-catalog/v0.1",
                "memories": [_memory_manifest("memory-z"), _memory_manifest("memory-a")],
            },
        }
    )

    snapshot = _reader(transport).capture("request-test", _dialogue(), 30)
    restored = GroundingContextSnapshot.from_json(snapshot.to_json())
    reversed_transport = FakeGroundingTransport(
        {
            "http://controller.test/v1/state/records?object_class=world": {
                "schema": "roboguide.state-query/v0.1",
                "records": [_world_record("place-a"), _world_record("place-z")],
            },
            "http://artifact.test/v1/memories": {
                "schema": "roboguide.memory-catalog/v0.1",
                "memories": [_memory_manifest("memory-a"), _memory_manifest("memory-z")],
            },
        }
    )
    reversed_snapshot = _reader(reversed_transport).capture("request-test", _dialogue(), 30)

    assert restored == snapshot
    assert reversed_snapshot == snapshot
    tampered = snapshot.to_json()
    cast(list[JSONObject], tampered["state_evidence"])[0]["value"] = {"label": "changed"}
    with pytest.raises(GroundingContextError, match="digest"):
        GroundingContextSnapshot.from_json(tampered)
    assert all(
        cast(JSONObject, item.value)["label"] == "一楼前台" for item in snapshot.state_evidence
    )


def test_snapshot_nested_json_cannot_mutate_digest_bound_evidence() -> None:
    """Evidence access returns copies so nested mutation cannot detach content from its digest."""
    transport = FakeGroundingTransport(
        {
            "http://controller.test/v1/state/records?object_class=world": {
                "schema": "roboguide.state-query/v0.1",
                "records": [_world_record("place-a")],
            },
            "http://artifact.test/v1/memories": {
                "schema": "roboguide.memory-catalog/v0.1",
                "memories": [_memory_manifest("memory-a")],
            },
        }
    )
    snapshot = _reader(transport).capture("request-test", _dialogue(), 30)

    state_value = cast(JSONObject, snapshot.state_evidence[0].value)
    state_value["label"] = "changed"
    memory_owner = snapshot.memory_evidence[0].owner
    memory_owner["component"] = "changed"

    serialized = snapshot.to_json()
    state = cast(list[JSONObject], serialized["state_evidence"])[0]
    memory = cast(list[JSONObject], serialized["memory_evidence"])[0]
    assert cast(JSONObject, state["value"])["label"] == "一楼前台"
    assert cast(JSONObject, memory["owner"])["component"] == "semantic-index"
    assert GroundingContextSnapshot.from_json(serialized) == snapshot


def test_snapshot_restore_rejects_noncanonical_evidence_order() -> None:
    """Persisted snapshots cannot assign a second identity to reordered equivalent evidence."""
    transport = FakeGroundingTransport(
        {
            "http://controller.test/v1/state/records?object_class=world": {
                "schema": "roboguide.state-query/v0.1",
                "records": [_world_record("place-a"), _world_record("place-b")],
            },
            "http://artifact.test/v1/memories": {
                "schema": "roboguide.memory-catalog/v0.1",
                "memories": [],
            },
        }
    )
    persisted = _reader(transport).capture("request-test", _dialogue(), 30).to_json()
    cast(list[JSONObject], persisted["state_evidence"]).reverse()

    with pytest.raises(GroundingContextError, match="canonical order"):
        GroundingContextSnapshot.from_json(persisted)


def test_reader_admits_world_evidence_but_excludes_live_deployment_and_commitments() -> None:
    """Only attributed World facts cross the boundary; Node and desired facts stay out."""
    transport = FakeGroundingTransport(
        {
            "http://controller.test/v1/state/records?object_class=world": {
                "schema": "roboguide.state-query/v0.1",
                "records": [
                    _node_record(),
                    _world_record("fresh-place"),
                    _world_record("stale-place", stale=True),
                    _world_record("desired-place", semantic="desired"),
                    _world_record("committed-place", semantic="committed"),
                ],
            },
            "http://artifact.test/v1/memories": {
                "schema": "roboguide.memory-catalog/v0.1",
                "memories": [],
            },
        }
    )

    snapshot = _reader(transport).capture("request-test", _dialogue(), 30)

    assert {item.object_id for item in snapshot.state_evidence} == {
        "fresh-place",
        "stale-place",
    }
    by_id = {item.object_id: item for item in snapshot.state_evidence}
    assert by_id["fresh-place"].freshness is GroundingFreshness.FRESH
    assert by_id["stale-place"].freshness is GroundingFreshness.STALE
    assert by_id["fresh-place"].source_observed_at_ms == 9000
    assert by_id["fresh-place"].received_at_ms == 20
    assert transport.calls[0][0].endswith("/v1/state/records?object_class=world")


def test_world_state_requires_explicit_payload_schema_admission() -> None:
    """World classification alone never authorizes evidence for every Mission model context."""
    transport = FakeGroundingTransport(
        {
            "http://controller.test/v1/state/records?object_class=world": {
                "schema": "roboguide.state-query/v0.1",
                "records": [_world_record("place-a")],
            },
            "http://artifact.test/v1/memories": {
                "schema": "roboguide.memory-catalog/v0.1",
                "memories": [],
            },
        }
    )

    snapshot = _reader(transport, frozenset()).capture("request-test", _dialogue(), 30)

    assert snapshot.state_evidence == ()


def test_filtered_robot_world_reports_preserve_unknown_pose_and_exclude_node_facts() -> None:
    """Explicit physical schema admission never promotes Node status or unknown pose quality."""
    schema = "roboguide.world.robot-physical-observation/v0.1"
    record = _world_record("entity-lite3-dog1", semantic="reported")
    envelope = cast(JSONObject, record["value"])
    envelope["payload_schema"] = schema
    envelope["value"] = {
        "physical_entity_id": "entity-lite3-dog1",
        "posture": {"availability": "unavailable", "reported_posture": None},
        "mapping": {
            "pose_measurement_time": None,
            "localization_quality": None,
            "localization_verified": False,
        },
        "arrival_verified": False,
        "device_stop_verified": False,
        "motion_authorized": False,
    }
    node = json.loads(json.dumps(record))
    node["object"]["class"] = "node"
    transport = FakeGroundingTransport(
        {
            "http://controller.test/v1/state/records?object_class=world": {
                "schema": "roboguide.state-query/v0.1",
                "observed_at_ms": 30,
                "records": [record, node, _node_record()],
            },
            "http://artifact.test/v1/memories": {
                "schema": "roboguide.memory-catalog/v0.1",
                "memories": [],
            },
        }
    )
    snapshot = _reader(transport, frozenset({schema})).capture("physical-report", _dialogue(), 30)
    assert len(snapshot.state_evidence) == 1
    assert snapshot.state_evidence[0].semantic == "reported"
    assert cast(JSONObject, snapshot.state_evidence[0].value)["mapping"] == {
        "pose_measurement_time": None,
        "localization_quality": None,
        "localization_verified": False,
    }
    assert admitted_physical_entity_ids(snapshot) == frozenset()
    assert GroundingContextSnapshot.from_json(snapshot.to_json()) == snapshot


def test_only_explicit_fresh_entity_reference_can_ground_a_patrol_actor() -> None:
    """Physical observations do not grant identity; stale entity refs cannot bind Actors."""
    schema = "roboguide.world.physical-entity-reference/v0.1"
    records: list[JSONValue] = []
    for entity, stale in [("entity-lite3-dog1", False), ("entity-lite3-dog2", True)]:
        record = _world_record(entity, semantic="reported", stale=stale)
        payload = cast(JSONObject, record["value"])
        payload["payload_schema"] = schema
        payload["value"] = {"entity_id": entity}
        records.append(record)
    transport = FakeGroundingTransport(
        {
            "http://controller.test/v1/state/records?object_class=world": {
                "schema": "roboguide.state-query/v0.1",
                "records": records,
            },
            "http://artifact.test/v1/memories": {
                "schema": "roboguide.memory-catalog/v0.1",
                "memories": [],
            },
        }
    )
    snapshot = _reader(transport, frozenset({schema})).capture("entity-report", _dialogue(), 30)
    assert admitted_physical_entity_ids(snapshot) == frozenset({"entity-lite3-dog1"})


def _physical_world_record() -> JSONObject:
    """Build an original bounded diagnostic report with independent Controller receipt time."""
    record = _world_record("entity-lite3-dog1", semantic="reported")
    cast(JSONObject, record["object"])["object_type"] = "robot-physical-observation"
    envelope = cast(JSONObject, record["value"])
    envelope["payload_schema"] = "roboguide.world.robot-physical-observation/v0.1"
    envelope["valid_for_ms"] = 5000
    envelope["value"] = {
        "physical_entity_id": "entity-lite3-dog1",
        "posture": {
            "availability": "reported_current",
            "reported_posture": "standing",
            "aggregate_age_ms": 100,
            "source_stale_ms": 5000,
            "remaining_at_publisher_ms": 4900,
        },
        "mapping": {
            "response_availability": "reported_current_response",
            "response_age_ms": 100,
            "reported_pose": {"x": 1, "y": 2, "theta": 0},
            "pose_measurement_time": None,
            "localization_quality": None,
            "map_revision_id": None,
            "localization_verified": False,
        },
        "arrival_verified": False,
        "device_stop_verified": False,
        "motion_authorized": False,
    }
    return record


def _capture_physical_report(
    record: JSONObject, *, observed_at_ms: JSONValue = 22
) -> GroundingContextSnapshot:
    """Read one report with same-Controller time metadata while keeping source clocks untouched."""
    schema = "roboguide.world.robot-physical-observation/v0.1"
    transport = FakeGroundingTransport(
        {
            "http://controller.test/v1/state/records?object_class=world": {
                "schema": "roboguide.state-query/v0.1",
                "observed_at_ms": observed_at_ms,
                "records": [record],
            },
            "http://artifact.test/v1/memories": {
                "schema": "roboguide.memory-catalog/v0.1",
                "memories": [],
            },
        }
    )
    return _reader(transport, frozenset({schema})).capture("physical-age", _dialogue(), 999999)


@pytest.mark.parametrize(
    "change",
    ["remaining-one", "outer-renewal", "missing-budget", "inflated-budget", "false-posture"],
)
def test_physical_profile_rejects_expired_or_false_current_inner_posture(change: str) -> None:
    """Outer receive freshness cannot revive a nearly expired or unqualified posture report."""
    record = _physical_world_record()
    envelope = cast(JSONObject, record["value"])
    posture = cast(JSONObject, cast(JSONObject, envelope["value"])["posture"])
    if change == "remaining-one":
        posture.update(aggregate_age_ms=4999, remaining_at_publisher_ms=1)
    elif change == "outer-renewal":
        posture.update(aggregate_age_ms=6000, remaining_at_publisher_ms=4900)
    elif change == "missing-budget":
        posture.pop("remaining_at_publisher_ms")
    elif change == "inflated-budget":
        posture["remaining_at_publisher_ms"] = 5000
    else:
        posture["reported_posture"] = "unknown"
    snapshot = _capture_physical_report(record)
    assert snapshot.state_evidence == ()
    assert any("physical_profile_posture" in gap.detail for gap in snapshot.gaps)
    assert record["stale"] is False


@pytest.mark.parametrize("observed_at_ms", [19, None, True, "22"])
def test_physical_profile_refuses_uncomparable_or_rolled_back_controller_clock(
    observed_at_ms: JSONValue,
) -> None:
    """Only a nonnegative same-Controller receive/observation difference qualifies reports."""
    snapshot = _capture_physical_report(_physical_world_record(), observed_at_ms=observed_at_ms)
    assert snapshot.state_evidence == ()
    assert any("physical_profile_controller_clock" in gap.detail for gap in snapshot.gaps)


def test_physical_profile_rejects_mapping_response_after_known_retention() -> None:
    """A fresh outer record cannot extend a Mapping HTTP response beyond ten seconds."""
    record = _physical_world_record()
    value = cast(JSONObject, cast(JSONObject, record["value"])["value"])
    cast(JSONObject, value["mapping"])["response_age_ms"] = 9999
    snapshot = _capture_physical_report(record)
    assert snapshot.state_evidence == ()
    assert any("physical_profile_mapping_response_expired" in gap.detail for gap in snapshot.gaps)


def test_unknown_posture_does_not_allow_an_expired_battery_to_remain_current() -> None:
    """Each current physical component requires its original remaining source budget."""
    record = _physical_world_record()
    value = cast(JSONObject, cast(JSONObject, record["value"])["value"])
    posture = cast(JSONObject, value["posture"])
    posture["availability"] = "unknown"
    posture["reported_posture"] = None
    posture["remaining_at_publisher_ms"] = 0
    value["battery"] = {
        "availability": "reported_current",
        "percent": 75,
        "remaining_at_publisher_ms": 1,
    }
    snapshot = _capture_physical_report(record)
    assert snapshot.state_evidence == ()
    assert any("battery_expired" in gap.detail for gap in snapshot.gaps)


def test_physical_profile_accounts_for_local_monotonic_read_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET and local parsing delay consumes the remaining budget without cross-host subtraction."""
    clock = iter((0, 5_000_000))
    monkeypatch.setattr("mission.grounding_reader.time.monotonic_ns", lambda: next(clock))
    record = _physical_world_record()
    value = cast(JSONObject, cast(JSONObject, record["value"])["value"])
    cast(JSONObject, value["posture"]).update(aggregate_age_ms=4995, remaining_at_publisher_ms=5)
    snapshot = _capture_physical_report(record, observed_at_ms=20)
    assert snapshot.state_evidence == ()
    assert any("physical_profile_posture_expired" in gap.detail for gap in snapshot.gaps)


@pytest.mark.parametrize("component", ["posture", "battery", "mapping"])
@pytest.mark.parametrize("delayed_source", ["memory", "planning-world"])
def test_physical_profile_expires_during_later_acquisition_before_snapshot_freeze(
    component: str, delayed_source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful initial State read cannot preserve facts expired by later evidence reads."""
    clock = [0]
    monkeypatch.setattr("mission.grounding_reader.time.monotonic_ns", lambda: clock[0])

    class DelayedMemoryTransport(FakeGroundingTransport):
        """Advance only virtual local time during the actual separate Memory GET."""

        def get_json(self, url: str, timeout_seconds: float) -> JSONObject:
            """Keep original records and consume a bounded virtual Memory-read interval."""
            result = super().get_json(url, timeout_seconds)
            if delayed_source == "memory" and url.endswith("/v1/memories"):
                clock[0] = 10_000_000
            return result

    def delayed_planning_read(_self: HttpMissionGroundingReader, _gaps: object) -> None:
        """Consume virtual time in the last optional evidence read, after State and Memory."""
        clock[0] = 10_000_000

    if delayed_source == "planning-world":
        monkeypatch.setattr(
            HttpMissionGroundingReader, "_read_planning_world_evidence", delayed_planning_read
        )
    record = _physical_world_record()
    value = cast(JSONObject, cast(JSONObject, record["value"])["value"])
    posture = cast(JSONObject, value["posture"])
    if component == "posture":
        posture.update(aggregate_age_ms=4995, remaining_at_publisher_ms=5)
    else:
        posture.update(availability="unknown", reported_posture=None, remaining_at_publisher_ms=0)
        if component == "battery":
            posture["aggregate_age_ms"] = 4995
            value["battery"] = {
                "availability": "reported_current",
                "percent": 75,
                "remaining_at_publisher_ms": 5,
            }
        else:
            cast(JSONObject, value["mapping"])["response_age_ms"] = 9995
    before = json.dumps(record, sort_keys=True)
    schema = "roboguide.world.robot-physical-observation/v0.1"
    transport = DelayedMemoryTransport(
        {
            "http://controller.test/v1/state/records?object_class=world": {
                "schema": "roboguide.state-query/v0.1",
                "observed_at_ms": 20,
                "records": [record],
            },
            "http://artifact.test/v1/memories": {
                "schema": "roboguide.memory-catalog/v0.1",
                "memories": [_memory_manifest("preserved-memory")],
            },
        }
    )
    snapshot = _reader(transport, frozenset({schema})).capture("slow-acquisition", _dialogue(), 30)
    assert snapshot.state_evidence == ()
    assert len(snapshot.memory_evidence) == 1
    assert any(
        "before snapshot freeze" in gap.detail and f"physical_profile_{component}" in gap.detail
        for gap in snapshot.gaps
    )
    assert json.dumps(record, sort_keys=True) == before


def test_physical_profile_keeps_original_value_ttl_epoch_and_freshness() -> None:
    """Qualification changes inclusion only; immutable evidence retains every original field."""
    record = _physical_world_record()
    before = json.dumps(record, sort_keys=True)
    snapshot = _capture_physical_report(record)
    assert len(snapshot.state_evidence) == 1
    evidence = snapshot.state_evidence[0]
    envelope = cast(JSONObject, record["value"])
    assert evidence.value == envelope["value"]
    assert evidence.valid_for_ms == envelope["valid_for_ms"]
    assert evidence.source_epoch == envelope["source_epoch"]
    assert evidence.source_observed_at_ms == 9000
    assert evidence.freshness is GroundingFreshness.FRESH
    assert json.dumps(record, sort_keys=True) == before
    assert "#physical-profile=v0.1" in snapshot.selection_policy_ref


@pytest.mark.parametrize("availability", ["unknown", "unavailable"])
def test_physical_profile_allows_unknown_posture_without_promoting_it(
    availability: str,
) -> None:
    """Unknown diagnostics remain explicit instead of being rewritten as current posture."""
    record = _physical_world_record()
    value = cast(JSONObject, cast(JSONObject, record["value"])["value"])
    cast(JSONObject, value["posture"]).update(
        availability=availability, reported_posture=None, remaining_at_publisher_ms=0
    )
    snapshot = _capture_physical_report(record)
    assert len(snapshot.state_evidence) == 1
    assert cast(JSONObject, snapshot.state_evidence[0].value)["posture"] == value["posture"]


def test_physical_profile_rejects_entity_mismatch_without_affecting_other_schemas() -> None:
    """A report cannot refer to a different entity, while ordinary World rules stay unchanged."""
    record = _physical_world_record()
    value = cast(JSONObject, cast(JSONObject, record["value"])["value"])
    value["physical_entity_id"] = "other-entity"
    snapshot = _capture_physical_report(record)
    assert snapshot.state_evidence == ()
    assert any("physical_profile_entity_mismatch" in gap.detail for gap in snapshot.gaps)
    transport = FakeGroundingTransport(
        {
            "http://controller.test/v1/state/records?object_class=world": {
                "schema": "roboguide.state-query/v0.1",
                "records": [_world_record("ordinary-place"), _node_record(), record],
            },
            "http://artifact.test/v1/memories": {
                "schema": "roboguide.memory-catalog/v0.1",
                "memories": [],
            },
        }
    )
    ordinary = _reader(transport).capture("ordinary", _dialogue(), 30)
    assert [item.object_id for item in ordinary.state_evidence] == ["ordinary-place"]
    assert "#physical-profile=" not in ordinary.selection_policy_ref


def test_reader_exposes_only_global_semantic_experience_or_spatial_metadata() -> None:
    """Memory discovery supplies provenance metadata, never payload content or local scope."""
    transport = FakeGroundingTransport(
        {
            "http://controller.test/v1/state/records?object_class=world": {
                "schema": "roboguide.state-query/v0.1",
                "records": [],
            },
            "http://artifact.test/v1/memories": {
                "schema": "roboguide.memory-catalog/v0.1",
                "memories": [
                    _memory_manifest("semantic-global"),
                    _memory_manifest("experience-global", kind="experience"),
                    _memory_manifest("spatial-global", kind="spatial"),
                    _spatial_memory_view("typed-spatial-global"),
                    _memory_manifest("execution-global", kind="execution"),
                    _memory_manifest("semantic-local", scope="local"),
                ],
            },
        }
    )

    snapshot = _reader(transport).capture("request-test", _dialogue(), 30)

    assert {item.memory_id for item in snapshot.memory_evidence} == {
        "semantic-global",
        "experience-global",
        "spatial-global",
        "typed-spatial-global",
    }
    assert all(
        item.content_status is MemoryContentStatus.METADATA_ONLY
        for item in snapshot.memory_evidence
    )
    assert [call[0] for call in transport.calls] == [
        "http://controller.test/v1/state/records?object_class=world",
        "http://artifact.test/v1/memories",
    ]


def test_reader_excludes_valid_execution_group_memory_without_malformed_gap() -> None:
    """A supported non-Global scope is policy-excluded rather than rejected as malformed."""
    scoped = _memory_manifest("group-memory")
    scoped["scope"] = {"kind": "execution_group", "execution_group_id": "group-a"}
    transport = FakeGroundingTransport(
        {
            "http://controller.test/v1/state/records?object_class=world": {
                "schema": "roboguide.state-query/v0.1",
                "records": [],
            },
            "http://artifact.test/v1/memories": {
                "schema": "roboguide.memory-catalog/v0.1",
                "memories": [scoped],
            },
        }
    )

    snapshot = _reader(transport).capture("request-test", _dialogue(), 30)

    assert snapshot.memory_evidence == ()
    assert snapshot.gaps == ()


def test_reader_consumes_shared_rust_facade_contract_fixtures() -> None:
    """Python normalization accepts the same exact fixtures checked by Rust facade tests."""
    fixture_root = Path("contracts/mission/grounding-context-v0.1/fixtures")
    state = cast(
        JSONObject,
        json.loads((fixture_root / "controller-state-query.json").read_text(encoding="utf-8")),
    )
    memory = cast(
        JSONObject,
        json.loads((fixture_root / "memory-catalog.json").read_text(encoding="utf-8")),
    )
    transport = FakeGroundingTransport(
        {
            "http://controller.test/v1/state/records?object_class=world": state,
            "http://artifact.test/v1/memories": memory,
        }
    )

    snapshot = _reader(transport, frozenset({"roboguide.semantic-place/v0.1"})).capture(
        "request-test", _dialogue(), 30
    )

    assert [item.object_id for item in snapshot.state_evidence] == ["front-desk"]
    assert [item.memory_id for item in snapshot.memory_evidence] == ["semantic-front-desk"]


def test_independent_source_failure_is_recorded_as_a_fail_soft_gap() -> None:
    """One unavailable projection does not erase evidence captured from the other source."""
    transport = FakeGroundingTransport(
        {
            "http://controller.test/v1/state/records?object_class=world": GroundingReadError(
                "controller unavailable"
            ),
            "http://artifact.test/v1/memories": {
                "schema": "roboguide.memory-catalog/v0.1",
                "memories": [_memory_manifest("semantic-global")],
            },
        }
    )

    snapshot = _reader(transport).capture("request-test", _dialogue(), 30)

    assert snapshot.state_evidence == ()
    assert [item.memory_id for item in snapshot.memory_evidence] == ["semantic-global"]
    assert [(gap.code, gap.source) for gap in snapshot.gaps] == [
        ("state_unavailable", "controller-state")
    ]
    assert [url for url, _timeout in transport.calls].count(
        "http://controller.test/v1/state/records?object_class=world"
    ) == 1


def test_transient_acquisition_failure_is_retried_before_snapshot_capture() -> None:
    """A recovered source read contributes evidence without leaving a misleading final gap."""
    state_url = "http://controller.test/v1/state/records?object_class=world"
    memory_url = "http://artifact.test/v1/memories"
    transport = SequenceGroundingTransport(
        {
            state_url: [
                GroundingAcquisitionError("controller temporarily unavailable"),
                {
                    "schema": "roboguide.state-query/v0.1",
                    "records": [_world_record("front-desk")],
                },
            ],
            memory_url: [{"schema": "roboguide.memory-catalog/v0.1", "memories": []}],
        }
    )
    reader = HttpMissionGroundingReader(
        "http://controller.test",
        "http://artifact.test",
        2.0,
        16,
        16,
        transport,
        admitted_world_payload_schemas=frozenset({"roboguide.test-place/v0.1"}),
        max_acquisition_attempts=2,
    )

    snapshot = reader.capture("request-test", _dialogue(), 30)

    assert [item.object_id for item in snapshot.state_evidence] == ["front-desk"]
    assert snapshot.gaps == ()
    assert [url for url, _timeout in transport.calls].count(state_url) == 2
    assert [url for url, _timeout in transport.calls].count(memory_url) == 1


def test_exhausted_acquisition_retry_becomes_one_fail_soft_gap() -> None:
    """Repeated transient failure stays bounded and reaches the snapshot as one source gap."""
    state_url = "http://controller.test/v1/state/records?object_class=world"
    transport = SequenceGroundingTransport(
        {
            state_url: [
                GroundingAcquisitionError("temporary failure one"),
                GroundingAcquisitionError("temporary failure two"),
            ],
            "http://artifact.test/v1/memories": [
                {"schema": "roboguide.memory-catalog/v0.1", "memories": []}
            ],
        }
    )
    reader = HttpMissionGroundingReader(
        "http://controller.test",
        "http://artifact.test",
        2.0,
        16,
        16,
        transport,
        max_acquisition_attempts=2,
    )

    snapshot = reader.capture("request-test", _dialogue(), 30)

    assert [url for url, _timeout in transport.calls].count(state_url) == 2
    assert [(gap.code, gap.source) for gap in snapshot.gaps] == [
        ("state_unavailable", "controller-state")
    ]
    assert "after 2 attempts" in snapshot.gaps[0].detail


def test_malformed_source_items_are_rejected_instead_of_silently_normalized() -> None:
    """Missing State values and unknown Memory schemas remain gaps, never fabricated evidence."""
    state = _world_record("place-a")
    cast(JSONObject, state["value"]).pop("value")
    memory = _memory_manifest("memory-a")
    memory["schema"] = "roboguide.memory-manifest/v9"
    transport = FakeGroundingTransport(
        {
            "http://controller.test/v1/state/records?object_class=world": {
                "schema": "roboguide.state-query/v0.1",
                "records": [state],
            },
            "http://artifact.test/v1/memories": {
                "schema": "roboguide.memory-catalog/v0.1",
                "memories": [memory],
            },
        }
    )

    snapshot = _reader(transport).capture("request-test", _dialogue(), 30)

    assert snapshot.state_evidence == ()
    assert snapshot.memory_evidence == ()
    assert {gap.code for gap in snapshot.gaps} == {
        "state_record_rejected",
        "memory_manifest_rejected",
    }


def test_http_protocol_interruption_is_normalized_as_a_grounding_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interrupted response body cannot escape the fail-soft transport boundary."""

    def interrupted_open(
        opener: object,
        request: object,
        data: object = None,
        timeout: float = 0,
    ) -> object:
        """Simulate a peer closing a response before its declared body is complete."""
        del opener, request, data, timeout
        raise http.client.IncompleteRead(b'{"schema":', 20)

    monkeypatch.setattr("urllib.request.OpenerDirector.open", interrupted_open)
    transport = UrllibGroundingJsonTransport()

    with pytest.raises(GroundingReadError, match="request failed"):
        transport.get_json("http://controller.test/v1/state/records", 1.0)


def test_malformed_item_diagnostics_are_count_bounded() -> None:
    """A large malformed source produces a bounded trace with an omission count."""
    transport = FakeGroundingTransport(
        {
            "http://controller.test/v1/state/records?object_class=world": {
                "schema": "roboguide.state-query/v0.1",
                "records": [None] * 5_000,
            },
            "http://artifact.test/v1/memories": {
                "schema": "roboguide.memory-catalog/v0.1",
                "memories": [],
            },
        }
    )
    reader = HttpMissionGroundingReader(
        "http://controller.test",
        "http://artifact.test",
        2.0,
        1,
        1,
        transport,
        admitted_world_payload_schemas=frozenset({"roboguide.test-place/v0.1"}),
        max_gaps=4,
    )

    snapshot = reader.capture("request-test", _dialogue(), 30)

    assert len(snapshot.gaps) == 4
    summary = next(gap for gap in snapshot.gaps if gap.code == "grounding_diagnostics_truncated")
    assert "4997" in summary.detail
    encoded = json.dumps(snapshot.to_json(), separators=(",", ":")).encode()
    assert len(encoded) <= MAX_GROUNDING_CONTEXT_BYTES


def test_reader_trims_evidence_to_the_complete_snapshot_byte_limit() -> None:
    """Stable-tail trimming bounds model input even when admitted records are individually large."""
    records: list[JSONValue] = [_world_record(f"place-{index:02d}") for index in range(8)]
    for raw in records:
        record = cast(JSONObject, raw)
        value = cast(JSONObject, record["value"])
        value["value"] = {"label": "x" * 100_000}
    transport = FakeGroundingTransport(
        {
            "http://controller.test/v1/state/records?object_class=world": {
                "schema": "roboguide.state-query/v0.1",
                "records": records,
            },
            "http://artifact.test/v1/memories": {
                "schema": "roboguide.memory-catalog/v0.1",
                "memories": [],
            },
        }
    )

    snapshot = _reader(transport).capture("request-test", _dialogue(), 30)

    assert 0 < len(snapshot.state_evidence) < len(records)
    assert any(gap.code == "grounding_snapshot_truncated" for gap in snapshot.gaps)
    encoded = json.dumps(
        snapshot.to_json(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    assert len(encoded) <= MAX_GROUNDING_CONTEXT_BYTES


def test_snapshot_constructor_rejects_an_oversized_serialized_context() -> None:
    """Direct and restored snapshots share the same final serialized-size invariant."""
    digest = dialogue_digest(tuple(turn.to_json() for turn in _dialogue()))

    with pytest.raises(GroundingContextSizeError, match="byte limit"):
        GroundingContextSnapshot.create(
            request_id="request-test",
            dialogue_digest=digest,
            captured_at_ms=30,
            gaps=(GroundingGap("oversized", "test", "x" * MAX_GROUNDING_CONTEXT_BYTES),),
        )
