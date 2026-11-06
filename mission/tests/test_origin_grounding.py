"""Verify admitted origin bytes and durable grounding without authorizing any execution."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from mission.grounding_context import GroundingContextError, GroundingContextSnapshot
from mission.grounding_reader import HttpMissionGroundingReader
from mission.models import JSONObject
from mission.origin_evidence import ORIGIN_SCHEMA, canonical_origin
from mission.request_record import DialogueSpeaker, DialogueTurn, DialogueTurnKind


def origin_fixture() -> tuple[JSONObject, bytes]:
    """Build original canonical provider bytes and an independently discoverable manifest."""
    content: JSONObject = {
        "schema_version": ORIGIN_SCHEMA,
        "node_id": "dog-2",
        "memory_id": "origin-one",
        "captured_at_ns": 100000000000,
        "attempt_id": "execution-one",
        "source_mission_id": "mission-one",
        "source_task_id": "capture",
        "source_execution_id": "execution-one",
        "position": {
            "map_id": "map-one",
            "x_m": 1.25,
            "y_m": -2.5,
            "yaw_rad": 0.4,
            "map_revision": None,
            "frame_basis": "local Mapping map; shared physical frame unverified",
        },
        "mapping_observation": {
            "availability": "current",
            "age_ms": 5,
            "received_at_ns": 99995000000,
            "value": {
                "map_id": "map-one",
                "has_map": True,
                "mode": "localization",
                "pose": {"x": 1.25, "y": -2.5, "theta": 0.4},
            },
        },
        "pose_measurement_time": None,
        "localization_verified": False,
        "motion_authorized": False,
    }
    raw = canonical_origin(content)
    manifest: JSONObject = {
        "schema": "roboguide.memory-manifest/v0.1",
        "selector": {"memory_id": "origin-one", "revision_id": "r1"},
        "kind": "experience",
        "provider_id": "reported-origins",
        "owner": {"owner": "node", "node_id": "dog-2", "local_system_id": "robot-observer"},
        "scope": {"kind": "global"},
        "visibility": "exchangeable",
        "payload_schema": ORIGIN_SCHEMA,
        "media_type": "application/json",
        "artifact": {
            "content_digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
            "byte_size": len(raw),
        },
        "source_mission_id": "mission-one",
        "source_execution_id": "execution-one",
        "source_task_ref": {"mission_id": "mission-one", "task_id": "capture"},
        "created_at": 100000,
    }
    return manifest, raw


class OriginTransport:
    """Serve exact original bytes independently of catalog metadata for corruption tests."""

    def __init__(self, manifest: JSONObject, raw: bytes) -> None:
        """Keep a private manifest and track whether any content read occurred."""
        self.manifest = deepcopy(manifest)
        self.raw = raw
        self.content_reads: list[str] = []

    def get_json(self, url: str, timeout_seconds: float) -> JSONObject:
        """Return only the two expected formal projection documents."""
        if url == "https://controller/v1/state/records?object_class=world":
            return {"schema": "roboguide.state-query/v0.1", "observed_at_ms": 200000, "records": []}
        assert url == "https://artifact/v1/memories"
        return {"schema": "roboguide.memory-catalog/v0.1", "memories": [self.manifest]}

    def get_bytes(self, url: str, timeout_seconds: float) -> bytes:
        """Return exact bytes without silently normalizing their hash or length."""
        self.content_reads.append(url)
        assert url.startswith("https://artifact/v1/artifacts/")
        return self.raw


def capture(transport: OriginTransport, *, admit: bool = True) -> GroundingContextSnapshot:
    """Run the real reader with a coordinate-free user instruction and explicit schema admission."""
    reader = HttpMissionGroundingReader(
        "https://controller",
        "https://artifact",
        2,
        8,
        8,
        transport,
        admitted_memory_content_schemas=frozenset({ORIGIN_SCHEMA}) if admit else frozenset(),
        content_transport=transport,
    )
    return reader.capture(
        "return-request",
        (
            DialogueTurn(
                "turn-one",
                DialogueSpeaker.USER,
                DialogueTurnKind.INSTRUCTION,
                "请生成返回共享实验起点 origin-one 的计划，等待审阅",
                200000,
            ),
        ),
        200000,
    )


def test_real_reader_admits_exact_content_and_restores_v4() -> None:
    """The snapshot binds real Artifact bytes, provenance and historical-only limitations."""
    manifest, raw = origin_fixture()
    transport = OriginTransport(manifest, raw)
    context = capture(transport)
    assert context.schema_version == "roboguide.grounding-context/v0.4"
    assert len(transport.content_reads) == 1
    assert context.memory_evidence[0].content == json.loads(raw)
    assert not context.gaps
    restored = GroundingContextSnapshot.from_json(context.to_json())
    assert restored.to_json() == context.to_json()
    copied = restored.memory_evidence[0].content
    assert copied is not None
    copied["motion_authorized"] = True
    assert restored.memory_evidence[0].content != copied


def test_default_keeps_metadata_and_local_scope_never_reads_bytes() -> None:
    """Unconfigured content and private origins stay out of model content inputs."""
    manifest, raw = origin_fixture()
    transport = OriginTransport(manifest, raw)
    context = capture(transport, admit=False)
    assert context.memory_evidence[0].content is None
    assert not transport.content_reads
    private = deepcopy(manifest)
    private["scope"] = {"kind": "local"}
    transport = OriginTransport(private, raw)
    assert not capture(transport).memory_evidence
    assert not transport.content_reads


@pytest.mark.parametrize(
    "mutation", ["bytes", "provenance", "map", "motion", "clock", "size", "digest"]
)
def test_corrupt_or_relabelled_content_is_a_gap(mutation: str) -> None:
    """A fresh catalog entry cannot authorize bad bytes or a changed original source."""
    manifest, raw = origin_fixture()
    if mutation == "bytes":
        raw += b" "
    elif mutation == "provenance":
        manifest["source_execution_id"] = "other-execution"
    elif mutation in {"map", "motion", "clock"}:
        content = cast(JSONObject, json.loads(raw))
        if mutation == "motion":
            content["motion_authorized"] = True
        elif mutation == "clock":
            content["captured_at_ns"] = 500000000000
        else:
            cast(JSONObject, content["position"])["map_id"] = "other-map"
        raw = canonical_origin(content)
        manifest["artifact"] = {
            "content_digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
            "byte_size": len(raw),
        }
    elif mutation == "size":
        cast(JSONObject, manifest["artifact"])["byte_size"] = 10000000
    else:
        cast(JSONObject, manifest["artifact"])["content_digest"] = "../substituted-target"
    context = capture(OriginTransport(manifest, raw))
    assert context.memory_evidence[0].content is None
    assert any(gap.code == "memory_content_rejected" for gap in context.gaps)


def test_old_generation_downgrade_and_tampered_restore_refuse() -> None:
    """Neither changing the wrapper version nor rewriting stored content is accepted."""
    context = capture(OriginTransport(*origin_fixture())).to_json()
    downgraded = deepcopy(context)
    downgraded["schema_version"] = "roboguide.grounding-context/v0.3"
    with pytest.raises(GroundingContextError):
        GroundingContextSnapshot.from_json(downgraded)
    memory = cast(list[JSONObject], context["memory_evidence"])
    cast(JSONObject, memory[0]["content"])["memory_id"] = "relabelled"
    with pytest.raises(GroundingContextError, match="provenance"):
        GroundingContextSnapshot.from_json(context)


def test_request_engine_persists_content_and_holds_for_approval(tmp_path: Path) -> None:
    """A real Request store carries admitted origins through model ports without submitting."""
    from mission.request_engine import MissionRequestEngine
    from mission.request_record import MissionRequestLifecycle
    from mission.request_store import MissionRequestStore
    from test_requests import (
        FakeController,
        FakeInterpreter,
        FakePlanner,
        _assessment,
        _catalog,
        _fixture_contracts,
        _inventory,
    )

    transport = OriginTransport(*origin_fixture())
    reader = HttpMissionGroundingReader(
        "https://controller",
        "https://artifact",
        2,
        8,
        8,
        transport,
        admitted_memory_content_schemas=frozenset({ORIGIN_SCHEMA}),
        content_transport=transport,
    )
    store = MissionRequestStore(tmp_path / "request.sqlite3")
    interpreter = FakeInterpreter([_assessment()])
    planner = FakePlanner()
    controller = FakeController(_inventory())
    engine = MissionRequestEngine(
        store,
        interpreter,
        planner,
        controller,
        _catalog(),
        frozenset({_fixture_contracts()[0]}),
        grounding_reader=reader,
    )
    waiting = engine.create("生成返回共享实验起点 origin-one 的计划，等待审阅")
    assert waiting.lifecycle == MissionRequestLifecycle.AWAITING_APPROVAL
    assert not controller.submissions
    assert waiting.grounding_context is not None
    assert waiting.grounding_context.schema_version == "roboguide.grounding-context/v0.4"
    assert interpreter.calls[0][1].context_digest == planner.calls[0][2].context_digest
    restored = MissionRequestStore(tmp_path / "request.sqlite3").get(waiting.request_id)
    assert restored is not None and restored.grounding_context is not None
    assert restored.grounding_context.to_json() == waiting.grounding_context.to_json()
