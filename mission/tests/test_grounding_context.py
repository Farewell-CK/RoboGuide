"""Deterministic tests for the State and Memory Mission grounding boundary."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
from mission.grounding_context import (
    GroundingContextError,
    GroundingContextSnapshot,
    GroundingFreshness,
    MemoryContentStatus,
)
from mission.grounding_reader import GroundingReadError, HttpMissionGroundingReader
from mission.models import JSONObject
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
