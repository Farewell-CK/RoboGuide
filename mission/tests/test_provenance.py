"""Original MI attribution, actual transport metadata, SQL atomicity and compatibility checks."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from mission.models import JSONObject
from mission.provenance import (
    MAX_PROVIDER_CALLS,
    current_trace,
    ensure_provider_budget,
    provider_call,
    snapshot,
    trace_deliberation,
)
from mission.request_engine import MissionRequestEngine
from mission.request_record import MissionRequestError
from mission.request_store import MissionRequestStore
from mission.responses import _ResponsesClient
from mission.submission_evidence import canonical_plan_digest
from test_planners import FakeTransport, _local_settings, _response
from test_requests import (
    FakeController,
    FakeInterpreter,
    FakePlanner,
    SequenceClock,
    SequenceIds,
    _assessment,
    _catalog,
    _engine,
    _fixture_contracts,
    _inventory,
)


def _rows(value: JSONObject) -> list[JSONObject]:
    """Return typed page entries from the public durable provenance response."""
    return cast(list[JSONObject], value["snapshots"])


def test_actual_transport_model_is_retained_separately_from_requested_model() -> None:
    """A software Responses payload's returned routing metadata is never inferred from settings."""
    response = _response({"value": True})
    response.update({"model": "observed-route", "id": "independent-response"})
    client = _ResponsesClient(
        _local_settings(), {"OPENAI_API_KEY": "software-test-only"}, FakeTransport([response])
    )
    with trace_deliberation("request", "input"):
        client._request(
            model="requested-alias",
            instructions="rules",
            input_text="input",
            schema_name="result",
            schema={"type": "object"},
        )
        trace = current_trace("request")
        assert trace is not None
        call = json.loads(trace.calls[0])
        assert call["requested_model"] == "requested-alias"
        assert call["observed_model"] == "observed-route"
        assert call["response_id"] == "independent-response"
        assert call["response_digest"] == canonical_plan_digest(response)
        assert "Authorization" not in trace.calls[0]
        assert current_trace("another-request") is None
    assert current_trace("request") is None


def test_missing_model_and_failed_call_stay_explicitly_unavailable() -> None:
    """A configured alias and a transport failure do not manufacture an observed model identity."""
    with trace_deliberation("request", "input"):
        provider_call({"model": "alias"}, {"status": "completed"}, None, 4, 5)
        original = OSError("transport lost")
        provider_call({"model": "alias"}, None, original, 6, 7)
        trace = current_trace("request")
        assert trace is not None
        calls = [json.loads(call) for call in trace.calls]
        assert calls[0]["observed_model"] is None
        assert calls[1]["response_digest"] is None
        assert calls[1]["error_type"] == "OSError"
        assert len({call["call_id"] for call in calls}) == 2


def test_provider_limit_refuses_before_an_unrecordable_call() -> None:
    """Fence the next side effect before its evidence budget is exhausted."""
    with trace_deliberation("request", "input"):
        for ordinal in range(MAX_PROVIDER_CALLS):
            ensure_provider_budget()
            provider_call({}, {}, None, ordinal, ordinal)
        with pytest.raises(ValueError, match="before dispatch"):
            ensure_provider_budget()
        trace = current_trace("request")
        assert trace is not None and len(trace.calls) == MAX_PROVIDER_CALLS


def test_concurrent_and_nested_traces_do_not_mix_request_evidence() -> None:
    """Keep worker and nested trace evidence scoped to its original Request."""

    def worker(identity: str) -> str:
        """Publish one software producer input in an independently scoped context."""
        with trace_deliberation(identity, identity):
            provider_call({"model": identity}, {}, None, 1, 2)
            with trace_deliberation("nested", "nested"):
                provider_call({"model": "nested"}, {}, None, 2, 3)
            trace = current_trace(identity)
            assert trace is not None and len(trace.calls) == 1
            return cast(str, json.loads(trace.calls[0])["requested_model"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(worker, ("first", "second"))) == ["first", "second"]


def test_provenance_sql_failure_rolls_back_request_and_all_new_evidence(tmp_path: Path) -> None:
    """Roll back Request, provenance and configuration identities together."""
    engine = _engine(
        tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), FakeController(_inventory())
    )
    record = engine.create("software parser contract")
    database = tmp_path / "requests.sqlite3"
    store = MissionRequestStore(database)
    before = store.provenance(record.request_id)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TRIGGER fail_request BEFORE UPDATE ON mission_requests "
            "BEGIN SELECT RAISE(ABORT,'injected Request write failure'); END"
        )
    updated = replace(record, updated_at_ms=record.updated_at_ms + 1)
    evidence = snapshot(
        "{}",
        record.request_id,
        "next-input",
        canonical_plan_digest(updated.to_json()),
        None,
        record.draft_revision,
        record.lifecycle.value,
    )
    with pytest.raises(sqlite3.DatabaseError, match="injected"):
        store.save(updated, provenance=evidence)
    assert store.get(record.request_id) == record
    assert store.provenance(record.request_id) == before
    assert store.deliberation_configuration(record.request_id, "next-input") is None


def test_configuration_change_blocks_original_approval_without_model_or_post(
    tmp_path: Path,
) -> None:
    """Restart cannot approve the old draft under a different model or approval-policy identity."""
    catalog = _catalog()
    risk = frozenset({_fixture_contracts()[0]})
    first_controller = FakeController(_inventory())
    store = MissionRequestStore(tmp_path / "requests.sqlite3")
    first = MissionRequestEngine(
        store,
        FakeInterpreter([_assessment()]),
        FakePlanner(),
        first_controller,
        catalog,
        risk,
        SequenceIds(),
        SequenceClock(),
        provider_identity={"model": "first"},
    )
    record = first.create("software parser contract")
    assert record.draft_digest is not None
    controller = FakeController(_inventory())
    interpreter = FakeInterpreter([])
    second = MissionRequestEngine(
        store,
        interpreter,
        FakePlanner(),
        controller,
        catalog,
        risk,
        SequenceIds(),
        SequenceClock(),
        provider_identity={"model": "changed"},
    )
    with pytest.raises(MissionRequestError, match="configuration changed"):
        second.approve(record.request_id, record.draft_revision, record.draft_digest)
    assert interpreter.calls == [] and controller.submissions == []
    assert second.get(record.request_id) == record


def test_bounded_pages_and_original_configuration_survive_restart(tmp_path: Path) -> None:
    """Page immutable original evidence without renewing its receive times."""
    engine = _engine(
        tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), FakeController(_inventory())
    )
    record = engine.create("software parser contract")
    store = MissionRequestStore(tmp_path / "requests.sqlite3")
    complete = _rows(store.provenance(record.request_id, limit=64))
    first = store.provenance(record.request_id, limit=2)
    second = store.provenance(
        record.request_id, limit=64, after_sequence=cast(int, first["next_sequence"])
    )
    assert _rows(first) + _rows(second) == complete
    assert first["has_more"] is True and second["has_more"] is False
    assert complete[-1]["configuration"] == complete[0]["configuration"]
    assert complete[-1]["request_record_digest"] == canonical_plan_digest(record.to_json())
    assert first["availability"] == "recorded"
    with pytest.raises(MissionRequestError, match="bounds"):
        store.provenance(record.request_id, limit=1000)


def test_legacy_request_never_acquires_invented_original_provenance(tmp_path: Path) -> None:
    """New passive metadata on a historical Request remains explicitly partial."""
    engine = _engine(
        tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), FakeController(_inventory())
    )
    record = engine.create("software parser contract")
    store = MissionRequestStore(tmp_path / "legacy.sqlite3")
    store.save(record)
    assert store.provenance(record.request_id)["availability"] == "legacy_unavailable"
    evidence = snapshot(
        "{}",
        record.request_id,
        "input",
        canonical_plan_digest(record.to_json()),
        None,
        record.draft_revision,
        record.lifecycle.value,
    )
    store.save(record, provenance=evidence)
    assert store.provenance(record.request_id)["availability"] == "partial_legacy"
    assert store.deliberation_configuration(record.request_id, "input") is None


def test_live_prompt_change_is_rejected_before_transport(tmp_path: Path) -> None:
    """Prevent a changed Prompt from running under the original startup identity."""
    from mission.provider_errors import MissionProviderError

    prompt = tmp_path / "planner.md"
    prompt.write_text("original declared Prompt")
    settings = _local_settings()
    settings = replace(settings, prompts=replace(settings.prompts, planner_path=prompt))
    transport = FakeTransport([])
    client = _ResponsesClient(settings, {"OPENAI_API_KEY": "software-test-only"}, transport)
    assert client._load_prompt(prompt) == "original declared Prompt"
    prompt.write_text("changed declared Prompt")
    with pytest.raises(MissionProviderError, match="changed since startup"):
        client._load_prompt(prompt)
    assert transport.requests == []
