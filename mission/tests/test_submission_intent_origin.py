"""Keep admitted origins compatible with plan-only Requests and independent execution gates."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from jsonschema import Draft202012Validator, ValidationError  # type: ignore[import-untyped]
from mission.grounding_context import GroundingContextError
from mission.grounding_reader import HttpMissionGroundingReader
from mission.models import JSONObject
from mission.origin_evidence import ORIGIN_SCHEMA
from mission.requests import (
    MissionRequestEngine,
    MissionRequestError,
    MissionRequestLifecycle,
    MissionRequestRecord,
    MissionRequestStore,
)
from test_origin_grounding import OriginTransport, origin_fixture
from test_requests import (
    FakeController,
    FakeInterpreter,
    FakePlanner,
    _assessment,
    _catalog,
    _fixture_contracts,
    _inventory,
)
from test_submission_intent_schema import request_registry


def validate_request(raw: JSONObject) -> None:
    """Resolve the public Request and embedded origin contracts from repository files only."""
    schema = json.loads(
        Path("contracts/mission/request-v0.5/mission-request.schema.json").read_bytes()
    )
    Draft202012Validator(schema, registry=request_registry()).validate(raw)


def origin_engine(
    path: Path,
    transport: OriginTransport,
    controller: FakeController,
    *,
    risk_approval: bool = False,
) -> MissionRequestEngine:
    """Use production grounding, codecs and SQLite with deterministic model/controller ports."""
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
    return MissionRequestEngine(
        MissionRequestStore(path),
        FakeInterpreter([_assessment()]),
        FakePlanner(),
        controller,
        _catalog(),
        frozenset(_fixture_contracts()) if risk_approval else frozenset(),
        grounding_reader=reader,
    )


@pytest.mark.parametrize("risk_approval", [False, True])
def test_origin_draft_v05_survives_restart_without_borrowing_execute_or_approval(
    tmp_path: Path, risk_approval: bool
) -> None:
    """Historical origin bytes remain valid evidence without supplying action permission."""
    path = tmp_path / "requests.sqlite3"
    manifest, original = origin_fixture()
    transport = OriginTransport(manifest, original)
    controller = FakeController(_inventory(*_fixture_contracts()))
    engine = origin_engine(path, transport, controller, risk_approval=risk_approval)
    waiting = engine.create("只规划返回 origin-one，等待审阅", "draft_only")
    assert waiting.lifecycle is MissionRequestLifecycle.AWAITING_EXECUTION
    context = waiting.grounding_context
    assert context is not None and context.schema_version == "roboguide.grounding-context/v0.4"
    assert not context.gaps
    assert context.memory_evidence[0].content == json.loads(original)
    assert len(transport.content_reads) == 1 and controller.submissions == []
    validate_request(waiting.to_json())

    restarted = origin_engine(path, transport, controller, risk_approval=risk_approval)
    restored = restarted.get(waiting.request_id)
    assert restored.to_json() == waiting.to_json()
    assert len(transport.content_reads) == 1
    validate_request(restored.to_json())
    assert restored.draft_digest is not None
    for revision, digest in (
        (restored.draft_revision + 1, restored.draft_digest),
        (restored.draft_revision, "sha256:" + "0" * 64),
    ):
        with pytest.raises(MissionRequestError, match="exact"):
            restarted.execute(restored.request_id, revision, digest)
    with pytest.raises(MissionRequestError, match="not awaiting approval"):
        restarted.approve(restored.request_id, restored.draft_revision, restored.draft_digest)
    assert controller.submissions == []

    result = restarted.execute(restored.request_id, restored.draft_revision, restored.draft_digest)
    if risk_approval:
        assert result.lifecycle is MissionRequestLifecycle.AWAITING_APPROVAL
        assert controller.submissions == []
        validate_request(result.to_json())
        result = restarted.approve(
            restored.request_id, restored.draft_revision, restored.draft_digest
        )
    assert result.lifecycle is MissionRequestLifecycle.ACCEPTED
    assert len(controller.submissions) == 1
    assert result.grounding_context == context
    validate_request(result.to_json())
    with pytest.raises(MissionRequestError):
        restarted.execute(restored.request_id, restored.draft_revision, restored.draft_digest)
    assert len(controller.submissions) == 1 and len(transport.content_reads) == 1


@pytest.mark.parametrize("mutation", ["future_context", "future_content", "provenance", "motion"])
def test_origin_v05_does_not_admit_unknown_generations_or_forged_evidence(
    tmp_path: Path, mutation: str
) -> None:
    """The schema bridge retains closed versions and the original semantic restoration checks."""
    controller = FakeController(_inventory(*_fixture_contracts()))
    engine = origin_engine(
        tmp_path / "requests.sqlite3", OriginTransport(*origin_fixture()), controller
    )
    waiting = engine.create("只规划返回 origin-one", "draft_only")
    original = waiting.to_json()
    changed = deepcopy(original)
    context = cast(JSONObject, changed["grounding_context"])
    content = cast(JSONObject, cast(list[JSONObject], context["memory_evidence"])[0]["content"])
    if mutation == "future_context":
        context["schema_version"] = "roboguide.grounding-context/v0.5"
    elif mutation == "future_content":
        content["schema_version"] = "roboguide.reported-origin/v0.2"
    elif mutation == "provenance":
        content["source_execution_id"] = "foreign-execution"
    else:
        content["motion_authorized"] = True
    if mutation != "provenance":
        with pytest.raises(ValidationError):
            validate_request(changed)
    with pytest.raises(GroundingContextError):
        MissionRequestRecord.from_json(changed)
    assert engine.get(waiting.request_id).to_json() == original
    assert controller.submissions == []
