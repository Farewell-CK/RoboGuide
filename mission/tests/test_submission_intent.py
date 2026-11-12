"""Verify persistent plan-only authority and exact execution grants without physical effects."""

from __future__ import annotations

import json
import subprocess
import sys
import types
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from mission.models import JSONObject
from mission.request_record import (
    MissionRequestError,
    MissionRequestLifecycle,
    MissionRequestRecord,
)
from mission.request_store import MissionRequestStore
from mission.submission_intent import SubmissionIntent
from test_requests import (
    FakeController,
    FakeInterpreter,
    FakePlanner,
    _assessment,
    _engine,
    _fixture_contracts,
    _inventory,
)

ORIGINAL_SHA = "d68efc080a3c35a74e6acc70adcd12470c13f0e2"


def original_source(path: str) -> str:
    """Read the pinned genuine predecessor; shallow CI checkouts explicitly lack that evidence."""
    result = subprocess.run(
        ["git", "show", f"{ORIGINAL_SHA}:{path}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode:
        pytest.skip("pinned original source unavailable in this checkout")
    return result.stdout


def test_plan_only_survives_restart_edit_and_exact_execute(tmp_path: Path) -> None:
    """A reviewed draft creates no submission until an exact explicit execution command."""
    controller = FakeController(_inventory(*_fixture_contracts()))
    engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
    record = engine.create("先规划，不执行", "draft_only")
    assert record.lifecycle is MissionRequestLifecycle.AWAITING_EXECUTION
    assert record.to_json()["schema_version"] == "roboguide.mission-request/v0.5"
    assert controller.submissions == []
    engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
    restored = engine.get(record.request_id)
    assert restored.submission_intent == SubmissionIntent("draft_only")
    revised = engine.add_message(record.request_id, "先检查东侧")
    assert revised.draft_revision > record.draft_revision
    with pytest.raises(MissionRequestError, match="exact"):
        engine.execute(record.request_id, record.draft_revision, record.draft_digest or "")
    assert controller.submissions == []
    done = engine.execute(record.request_id, revised.draft_revision, revised.draft_digest or "")
    assert done.lifecycle is MissionRequestLifecycle.ACCEPTED
    assert len(controller.submissions) == 1
    with pytest.raises(MissionRequestError):
        engine.execute(record.request_id, revised.draft_revision, revised.draft_digest or "")
    assert len(controller.submissions) == 1


def test_execute_does_not_approve_and_edit_invalidates_grant(tmp_path: Path) -> None:
    """Submission intent never supplies approver permission or a later draft's authorization."""
    controller = FakeController(_inventory(*_fixture_contracts()))
    engine = _engine(
        tmp_path,
        FakeInterpreter([_assessment(), _assessment()]),
        FakePlanner(),
        controller,
        frozenset(_fixture_contracts()),
    )
    record = engine.create("只规划", "draft_only")
    with pytest.raises(MissionRequestError):
        engine.approve(record.request_id, record.draft_revision, record.draft_digest or "")
    waiting = engine.execute(record.request_id, record.draft_revision, record.draft_digest or "")
    assert waiting.lifecycle is MissionRequestLifecycle.AWAITING_APPROVAL
    revised = engine.add_message(record.request_id, "修改路径")
    assert revised.lifecycle is MissionRequestLifecycle.AWAITING_EXECUTION
    assert revised.submission_intent == SubmissionIntent("draft_only")
    engine.execute(record.request_id, revised.draft_revision, revised.draft_digest or "")
    done = engine.approve(record.request_id, revised.draft_revision, revised.draft_digest or "")
    assert done.lifecycle is MissionRequestLifecycle.ACCEPTED and len(controller.submissions) == 1


def test_legacy_and_explicit_run_keep_normal_policy(tmp_path: Path) -> None:
    """Opt-in intent does not turn existing callers into plan-only requests."""
    for index, mode in enumerate((None, "submit_when_ready")):
        controller = FakeController(_inventory(*_fixture_contracts()))
        engine = _engine(
            tmp_path / str(index), FakeInterpreter([_assessment()]), FakePlanner(), controller
        )
        record = engine.create("执行", mode)
        assert record.lifecycle is MissionRequestLifecycle.ACCEPTED
        assert len(controller.submissions) == 1
        assert (record.submission_intent is None) == (mode is None)


def test_plan_failure_retry_and_clarification_keep_intent(tmp_path: Path) -> None:
    """A failed deliberation or clarification answer cannot silently grant permission to submit."""
    controller = FakeController(_inventory(*_fixture_contracts()))
    engine = _engine(
        tmp_path,
        FakeInterpreter([_assessment("去哪里？"), _assessment()]),
        FakePlanner(),
        controller,
    )
    record = engine.create("只规划", "draft_only")
    assert record.lifecycle is MissionRequestLifecycle.NEEDS_CLARIFICATION
    record = engine.add_message(record.request_id, "东侧")
    store = MissionRequestStore(tmp_path / "requests.sqlite3")
    store.save(replace(record, lifecycle=MissionRequestLifecycle.FAILED))
    engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
    assert engine.retry(record.request_id).lifecycle is MissionRequestLifecycle.AWAITING_EXECUTION
    assert controller.submissions == []


def test_genuine_previous_decoder_refuses_v05_and_legacy_injected_intent(tmp_path: Path) -> None:
    """Load actual pre-change decoder code and prove new authority is not interpreted as v0.4."""
    engine = _engine(
        tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), FakeController(_inventory())
    )
    raw = engine.create("只规划", "draft_only").to_json()
    code = original_source("mission/src/mission/request_record.py")
    previous = types.ModuleType("mission._previous_request_record")
    sys.modules[previous.__name__] = previous
    try:
        exec(compile(code, "original-request-record.py", "exec"), previous.__dict__)
        with pytest.raises(
            previous.MissionRequestError, match="unsupported Mission Request schema"
        ):
            previous.MissionRequestRecord.from_json(raw)
    finally:
        sys.modules.pop(previous.__name__, None)
    old = cast(JSONObject, json.loads(json.dumps(raw)))
    old["schema_version"] = "roboguide.mission-request/v0.4"
    with pytest.raises(MissionRequestError, match="legacy"):
        MissionRequestRecord.from_json(old)


def test_mode_cannot_be_downgraded_in_storage(tmp_path: Path) -> None:
    """The same original request cannot lose its opted-in guard through a later save."""
    engine = _engine(
        tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), FakeController(_inventory())
    )
    record = engine.create("只规划", "draft_only")
    with pytest.raises(MissionRequestError, match="immutable"):
        MissionRequestStore(tmp_path / "requests.sqlite3").save(
            replace(record, submission_intent=None)
        )


def test_corrupt_grant_cannot_restore_or_submit(tmp_path: Path) -> None:
    """A forged digest or revision cannot borrow a real draft's later execution authorization."""
    engine = _engine(
        tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), FakeController(_inventory())
    )
    record = engine.create("只规划", "draft_only")
    with pytest.raises(MissionRequestError, match="detached"):
        replace(
            record,
            submission_intent=SubmissionIntent("draft_only", record.draft_revision, "sha256:wrong"),
        )
