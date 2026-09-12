"""Deterministic tests for the State and Memory Mission grounding boundary."""

from __future__ import annotations

from collections.abc import Mapping
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
        "owner": {"owner": "roboguide", "component": "semantic-index"},
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


def _reader(transport: FakeGroundingTransport) -> HttpMissionGroundingReader:
    """Compose one bounded reader over fixed test origins."""
    return HttpMissionGroundingReader(
        "http://controller.test",
        "http://artifact.test",
        2.0,
        16,
        16,
        transport,
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
    }
    assert all(
        item.content_status is MemoryContentStatus.METADATA_ONLY
        for item in snapshot.memory_evidence
    )
    assert [call[0] for call in transport.calls] == [
        "http://controller.test/v1/state/records?object_class=world",
        "http://artifact.test/v1/memories",
    ]


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
