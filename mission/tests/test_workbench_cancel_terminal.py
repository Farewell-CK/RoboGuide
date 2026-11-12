"""Completed cancellation races resolve local blocking without replacing unknown HTTP outcomes."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import cast

import pytest
from mission.models import JSONObject, JSONValue
from roboguide_workbench.actions import MissionActions, commands_clear
from roboguide_workbench.cancellation import CancellationActions
from roboguide_workbench.client import ReadOnlyController, WorkbenchError
from roboguide_workbench.conversation import ConversationSession
from roboguide_workbench.persistence import SessionStore
from roboguide_workbench.sessions import navigate, pending_commands
from test_requests import (
    FakeController,
    FakeInterpreter,
    FakePlanner,
    _assessment,
    _engine,
    _fixture_contracts,
    _inventory,
)
from test_workbench import no_model
from test_workbench_cancellation import controller_server, fixture_actions
from test_workbench_v2 import mission_server


def race_to_completion(
    cancellation: CancellationActions,
    state: JSONObject,
    monkeypatch: pytest.MonkeyPatch,
) -> JSONObject:
    """Complete the original Mission after its confirmation GET but before the actual POST."""
    post = cancellation._post

    def finish_then_post(path: str) -> JSONObject:
        """Deliver one real HTTP 409 under the production client's original command journal."""
        state["status"], state["post_status"] = "Completed", 409
        return post(path)

    monkeypatch.setattr(cancellation, "_post", finish_then_post)
    selected = cancellation.preview("mission", "m1")
    with pytest.raises(WorkbenchError, match="HTTP 409"):
        cancellation.confirm(selected)
    return cast(list[JSONObject], cancellation.actions.store.data["commands"])[-1]


def test_completed_race_survives_restart_and_resolves_without_cancellation_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exact Completed observation resolves only the local barrier after one refused POST."""
    with controller_server() as (controller, state, calls):
        cancellation = fixture_actions(tmp_path, controller)
        command = race_to_completion(cancellation, state, monkeypatch)
        original = copy.deepcopy(command)
        assert command["state"] == "pending" and not commands_clear([command])
        store = cancellation.actions.store
        old_path = store.path
        store.close()
        restored = SessionStore(
            old_path.parent, old_path.stem, {"controller": controller.endpoint}, True
        )
        cancellation = CancellationActions(
            controller, MissionActions(ReadOnlyController("http://127.0.0.1:9"), restored)
        )
        try:
            result = cancellation.observe()
            command = cast(list[JSONObject], restored.data["commands"])[-1]
            assert result["status"] == "Completed" and result["command_state"] == "resolved"
            assert "不表示取消成功" in cast(str, result["text"])
            assert "原取消 HTTP 回执：未知" in cast(str, result["text"])
            assert command["original_http_outcome"] == "unknown" and "response" not in command
            assert commands_clear([command])
            for field, value in original.items():
                if field != "state":
                    assert command[field] == value, field
            frozen = copy.deepcopy(command["resolution"])
            cancellation.observe()
            assert command["resolution"] == frozen
            assert calls.count(("POST", "/v1/missions/m1/cancel")) == 1
        finally:
            restored.close()
        restored = SessionStore(
            old_path.parent, old_path.stem, {"controller": controller.endpoint}, True
        )
        try:
            command = cast(list[JSONObject], restored.data["commands"])[-1]
            assert command["state"] == "resolved" and commands_clear([command])
            assert command["resolution"] == frozen
            assert command["original_http_outcome"] == "unknown"
        finally:
            restored.close()


def test_completed_resolution_allows_an_independent_original_mission_command_and_navigation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After terminal reconciliation, normal durable mutation and locked navigation work again."""
    mission_controller = FakeController(_inventory(*_fixture_contracts()))
    engine = _engine(
        tmp_path / "mission",
        FakeInterpreter([_assessment()]),
        FakePlanner(),
        mission_controller,
    )
    with controller_server() as (controller, state, calls), mission_server(engine) as endpoint:
        cancellation = fixture_actions(tmp_path, controller)
        cancellation.actions.connection = ReadOnlyController(endpoint)
        try:
            race_to_completion(cancellation, state, monkeypatch)
            session = ConversationSession(
                controller, no_model, cancellation.actions.store, cancellation.actions
            )
            assert pending_commands(session)
            with pytest.raises(WorkbenchError, match="原命令"):
                navigate(session, "/new next")
            cancellation.observe()
            assert not pending_commands(session)
            created = session.ask("/plan 另一个独立任务")
            assert "AwaitingExecution" in created and mission_controller.submissions == []
            original_command = copy.deepcopy(
                cast(list[JSONObject], session.store.data["commands"])[0]
            )
            assert "next" in (navigate(session, "/new next") or "")
            assert "cancel" in (navigate(session, "/resume cancel") or "")
            assert cast(list[JSONObject], session.store.data["commands"])[0] == original_command
            assert calls.count(("POST", "/v1/missions/m1/cancel")) == 1
        finally:
            cancellation.actions.store.close()


@pytest.mark.parametrize("status", ["Running", "Cancelling", "Failed"])
def test_noncompleted_or_unproven_stops_keep_the_original_barrier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    """Running, stopping and failed records cannot borrow successful completion or cancellation."""
    with controller_server() as (controller, state, calls):
        cancellation = fixture_actions(tmp_path, controller)
        try:
            command = race_to_completion(cancellation, state, monkeypatch)
            original = copy.deepcopy(command)
            state["status"] = status
            result = cancellation.observe()
            assert result["command_state"] == "pending" and command["state"] == "pending"
            assert not commands_clear([command]) and "resolution" not in command
            assert command["original_http_outcome"] == "unknown" and "response" not in command
            for field, value in original.items():
                assert command[field] == value, field
            assert calls.count(("POST", "/v1/missions/m1/cancel")) == 1
        finally:
            cancellation.actions.store.close()


def test_other_group_cannot_resolve_completed_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A different Group's read keeps all original rows unchanged and unresolved."""
    with controller_server() as (controller, state, calls):
        cancellation = fixture_actions(tmp_path, controller)
        try:
            command = race_to_completion(cancellation, state, monkeypatch)
            original = copy.deepcopy(command)
            before = cancellation.actions.store.path.read_bytes()
            state["group_id"] = "replacement-group"
            with pytest.raises(WorkbenchError, match="Group"):
                cancellation.observe()
            assert command == original and cancellation.actions.store.path.read_bytes() == before
            assert not commands_clear([command])
            assert calls.count(("POST", "/v1/missions/m1/cancel")) == 1
        finally:
            cancellation.actions.store.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("service", "mission"),
        ("action", "execute"),
        ("path", "/other"),
        ("body", {"new": "command"}),
        ("request_digest", "sha256:wrong"),
        ("operation_id", ""),
        ("accepted_at", "not-an-original-time"),
        ("origin", "http://127.0.0.1:9/path"),
    ],
)
def test_malformed_original_command_cannot_acquire_completed_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    """Every original effect identity and byte binding is checked before any observation read."""
    from mission.models import JSONValue

    with controller_server() as (controller, state, calls):
        cancellation = fixture_actions(tmp_path, controller)
        try:
            command = race_to_completion(cancellation, state, monkeypatch)
            command[field] = cast(JSONValue, value)
            before = len(calls)
            with pytest.raises(WorkbenchError):
                cancellation.observe()
            assert len(calls) == before and command["state"] == "pending"
            assert not commands_clear([command])
        finally:
            cancellation.actions.store.close()


def test_frozen_resolution_is_not_replaced_by_later_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Subsequent observations stay separate from the first exact terminal-resolution witness."""
    with controller_server() as (controller, state, _):
        cancellation = fixture_actions(tmp_path, controller)
        try:
            command = race_to_completion(cancellation, state, monkeypatch)
            cancellation.observe()
            frozen = copy.deepcopy(command["resolution"])
            state["status"] = "Running"
            result = cancellation.observe()
            assert result["status"] == "Running"
            assert cast(JSONObject, command["observation"])["status"] == "Running"
            assert command["resolution"] == frozen and commands_clear([command])
        finally:
            cancellation.actions.store.close()


def test_original_cancelled_resolution_rows_remain_readable_without_rewriting_their_witness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing v0.4 local Cancelled witnesses retain the original completed observation bytes."""
    with controller_server() as (controller, state, _):
        cancellation = fixture_actions(tmp_path, controller)
        try:
            command = race_to_completion(cancellation, state, monkeypatch)
            state["status"] = "Cancelled"
            cancellation.observe()
            del command["resolution"]
            observation = cast(JSONObject, command["observation"])
            for field in ("operation_id", "path", "request_digest", "accepted_at"):
                observation.pop(field)
            previous = copy.deepcopy(observation)
            cancellation.actions.store.save()
            assert commands_clear([command])
            cancellation.observe()
            assert command["observation"] == previous
            assert cast(JSONObject, command["latest_observation"])["status"] == "Cancelled"
            assert commands_clear([command]) and "resolution" not in command
        finally:
            cancellation.actions.store.close()


@pytest.mark.parametrize(
    "field",
    ["operation_id", "path", "request_digest", "accepted_at", "origin", "mission_id", "group_id"],
)
def test_completed_resolution_must_match_its_frozen_original_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    """A copied or changed terminal observation cannot authorize another local command."""
    with controller_server() as (controller, state, _):
        cancellation = fixture_actions(tmp_path, controller)
        try:
            command = race_to_completion(cancellation, state, monkeypatch)
            cancellation.observe()
            assert commands_clear([command])
            altered = copy.deepcopy(command)
            cast(JSONObject, altered["resolution"])[field] = "changed"
            assert not commands_clear([altered])
            assert commands_clear([command])
        finally:
            cancellation.actions.store.close()


def test_missing_original_terminal_observation_time_cannot_clear_the_barrier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An incomplete typed terminal witness cannot replace the actual receive observation."""
    with controller_server() as (controller, state, _):
        cancellation = fixture_actions(tmp_path, controller)
        try:
            command = race_to_completion(cancellation, state, monkeypatch)
            cancellation.observe()
            for value in (None, "not-a-time", "2026-11-11T10:00:00"):
                altered = copy.deepcopy(command)
                cast(JSONObject, altered["resolution"])["received_at"] = value
                assert not commands_clear([altered])
            invalid_statuses: list[JSONValue] = [None, [], {}]
            for status_value in invalid_statuses:
                altered = copy.deepcopy(command)
                cast(JSONObject, altered["resolution"])["status"] = status_value
                assert not commands_clear([altered])
            assert commands_clear([command])
        finally:
            cancellation.actions.store.close()
