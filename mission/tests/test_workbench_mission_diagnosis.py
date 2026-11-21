"""Mission diagnosis distinguishes original receipt gaps from execution outcomes."""

from __future__ import annotations

import copy
import io
from contextlib import redirect_stdout
from pathlib import Path
from typing import NoReturn

import pytest
from mission.models import JSONObject, JSONValue
from roboguide_workbench.actions import MissionActions
from roboguide_workbench.cli import display_question
from roboguide_workbench.client import ReadOnlyController, WorkbenchError
from roboguide_workbench.conversation import ConversationSession
from roboguide_workbench.mission_diagnosis import read_mission_diagnosis
from roboguide_workbench.persistence import SessionStore
from test_workbench import no_model
from test_workbench_agent_loop import Responses, _call, _message, _response
from test_workbench_dialogue import _router
from test_workbench_v2 import ObservedController

MISSION_ID = "mission-map"
GROUP_ID = "group-map"
EXECUTION_ID = "attempt-9:group-map-7:publish-1"


def _attempt(
    task: str = "publish", *, execution: str = EXECUTION_ID, status: str = "Unknown"
) -> JSONObject:
    """Return a Controller-shaped attempt without borrowing another task's evidence."""
    return {
        "execution_id": execution,
        "mission_id": MISSION_ID,
        "group_id": GROUP_ID,
        "role_id": task + "-role",
        "task_id": task,
        "node_id": "dog1",
        "status": status,
    }


class DiagnosisController(ReadOnlyController):
    """Expose only a fixture Mission, its attempts, and an original stop record."""

    def __init__(self, variant: str = "manual") -> None:
        """Keep fixtures and read counts independent between diagnostic turns."""
        super().__init__("http://127.0.0.1:9")
        self.variant = variant
        self.reads: list[str] = []

    def mission(self, mission_id: str) -> JSONObject:
        """Return a Cancelling Mission with distinct terminal and unresolved tasks."""
        self.reads.append("mission")
        assert mission_id == MISSION_ID
        return {
            "mission_id": mission_id,
            "group_id": GROUP_ID,
            "status": "Cancelling",
            "tasks": [
                {"task_id": "build", "status": "Completed"},
                {"task_id": "publish", "status": "Active"},
            ],
        }

    def missions(self) -> JSONObject:
        """Give the Agent a genuine projected identity before targeted discovery."""
        self.reads.append("missions")
        result = ObservedController().missions()
        records = result["records"]
        assert isinstance(records, list)
        for row in records:
            assert isinstance(row, dict)
            obj = row["object"]
            assert isinstance(obj, dict)
            obj["object_id"] = MISSION_ID
            if row["channel_id"] == "mission-lifecycle":
                row["value"] = {"lifecycle": "Cancelling"}
        return result

    def mission_attempts(self, mission_id: str) -> JSONObject:
        """Keep the completed build beside the unknown publish attempt."""
        self.reads.append("attempts")
        assert mission_id == MISSION_ID
        other = _attempt("build", execution="attempt:build", status="Completed")
        publish = _attempt()
        rows: list[JSONValue] = [other, publish]
        if self.variant == "wrong-Mission":
            publish["mission_id"] = "other"
        if self.variant == "two-open":
            rows.append(_attempt("build", execution="attempt:retry", status="Running"))
        if self.variant == "historical":
            publish["group_id"] = "retired-group"
        return {"attempts": rows}

    def execution_safety_stop(self, execution_id: str) -> JSONObject:
        """Return manual evidence, an unavailable receipt, or a different identity."""
        self.reads.append("stop")
        assert execution_id == EXECUTION_ID
        if self.variant == "denied":
            raise WorkbenchError("查询未取得有效结果：HTTP 403。")
        if self.variant == "no-record":
            raise WorkbenchError("查询未取得有效结果：HTTP 404。")
        return {
            "schema_version": "roboguide.safety-stop-view/v0.2",
            "command_receipt_is_stop_proof": False,
            "purpose": {
                "node_id": "dog1",
                "accepted_at": None,
                "stopped_at": None,
                "safe_proof": None,
                "reason": "operation safety declaration or current session is unavailable",
                "record": {
                    "phase": "manual_escalation",
                    "confirmed_at": None,
                    "identity": {
                        "execution_id": execution_id,
                        "group_id": GROUP_ID,
                        "role_id": "publish-role",
                        "task_ref": {
                            "mission_id": "other" if self.variant == "wrong-stop" else MISSION_ID,
                            "task_id": "publish",
                        },
                    },
                },
            },
        }


def test_diagnosis_reports_the_original_unknown_and_missing_stop_receipts() -> None:
    """The completed build cannot hide the publish attempt's original Unknown."""
    client = DiagnosisController()
    result, raw = read_mission_diagnosis(client, MISSION_ID)
    assert client.reads == ["mission", "attempts", "stop"]
    assert result.complete and len(result.text.splitlines()) <= 8
    assert "关注步骤 publish" in result.text and "原执行 Unknown" in result.text
    assert "需要人工处理" in result.text and "安全声明或当时的会话不可用" in result.text
    assert "取消接收回执、执行终止记录、安全状态观测" in result.text
    assert "关注步骤 build" not in result.text
    assert set(raw) == {"mission", "execution_attempts", "safety_stop"}


@pytest.mark.parametrize("variant", ["denied", "no-record", "wrong-stop"])
def test_unavailable_or_foreign_stop_remains_partial(variant: str) -> None:
    """A failed evidence read preserves the attempt fact without adopting the stop record."""
    client = DiagnosisController(variant)
    result, raw = read_mission_diagnosis(client, MISSION_ID)
    assert client.reads == ["mission", "attempts", "stop"]
    assert not result.complete and "原执行 Unknown" in result.text and "无法核对" in result.text
    assert "需要人工处理" not in result.text and "safety_stop" not in raw


@pytest.mark.parametrize("variant", ["wrong-Mission", "historical", "two-open"])
def test_diagnosis_never_selects_an_unjoined_or_ambiguous_attempt(variant: str) -> None:
    """Old groups, foreign rows and ambiguous candidates cannot authorize a deeper lookup."""
    client = DiagnosisController(variant)
    result, raw = read_mission_diagnosis(client, MISSION_ID)
    assert client.reads == ["mission", "attempts"]
    assert not result.complete and "safety_stop" not in raw
    if variant == "two-open":
        chosen, _ = read_mission_diagnosis(client, MISSION_ID, "publish")
        assert chosen.complete and client.reads[-1] == "stop"


def test_completed_sibling_has_no_inferred_stop_gap() -> None:
    """Selecting the completed step does not attach the other step's stop record."""
    client = DiagnosisController()
    result, raw = read_mission_diagnosis(client, MISSION_ID, "build")
    assert result.complete and client.reads == ["mission", "attempts"]
    assert "当前步骤 build · Completed" in result.text
    assert "Unknown 尝试" in result.text and "缺少可核对" not in result.text
    assert "safety_stop" not in raw


def test_native_diagnosis_renders_host_facts_instead_of_the_final_model_claim(
    tmp_path: Path,
) -> None:
    """The native tool shares CLI diagnosis and retains raw evidence outside model output."""
    client = DiagnosisController()
    store = SessionStore(tmp_path / "sessions", "diagnosis", {}, False)
    scripted = Responses(
        [
            _response(_call("read_active_missions", {}, "find")),
            _response(_call("read_mission_diagnosis", {"mission_id": MISSION_ID, "task_id": ""})),
            _response(_message("已经停稳并释放地图资源。")),
        ]
    )
    session = ConversationSession(
        client, lambda: _router(scripted), store, MissionActions(client, store)
    )
    try:
        text = session.ask("找出处于取消中的任务，再查它一直等什么。")
        assert "原执行 Unknown" in text and "需要人工处理" in text
        assert "已经停稳并释放" not in text
        assert "safety_stop" in (session.last_raw or {})
        assert client.reads == ["missions", "mission", "attempts", "stop"]
        assert store.data["commands"] == []
    finally:
        store.close()


def test_mission_diagnosis_command_with_explicit_task(tmp_path: Path) -> None:
    """The direct CLI command can select a literal task without calling a model."""
    client = DiagnosisController("two-open")
    store = SessionStore(tmp_path / "sessions", "diagnosis", {}, False)

    def unavailable_model() -> NoReturn:
        """Fail if an explicit read tries to instantiate the model router."""
        raise AssertionError("command must bypass the model")

    session = ConversationSession(client, unavailable_model, store, MissionActions(client, store))
    try:
        text = session.ask(f"/diagnose {MISSION_ID} publish")
        assert "关注步骤 publish" in text and client.reads == ["mission", "attempts", "stop"]
        assert session.query_status == "complete" and store.data["commands"] == []
    finally:
        store.close()


def test_natural_cancellation_question_uses_the_single_live_target(tmp_path: Path) -> None:
    """A plain question resolves a unique cancellation and follow-up without a model."""
    client = DiagnosisController()
    store = SessionStore(tmp_path / "sessions", "single", {}, False)
    session = ConversationSession(client, no_model, store, MissionActions(client, store))
    try:
        text = session.ask("这个任务为什么还在取消中？")
        assert "原执行 Unknown" in text and "需要人工处理" in text
        assert "当前身份可见 Mission" not in text
        assert client.reads == ["missions", "mission", "attempts", "stop"]
        assert session.last_mission == MISSION_ID and store.data["commands"] == []
        text = session.ask("刚才那个任务怎么一直没取消完呢？")
        assert "关注步骤 publish" in text
        assert client.reads[4:] == ["mission", "attempts", "stop"]
    finally:
        store.close()


class MultipleCancellations(DiagnosisController):
    """Give natural selection two source-qualified candidates with distinct identities."""

    def missions(self) -> JSONObject:
        """Put the diagnosable fixture Mission second in the current list."""
        result = super().missions()
        rows = result["records"]
        assert isinstance(rows, list)
        first = copy.deepcopy(rows)
        for record in first:
            assert isinstance(record, dict)
            obj = record["object"]
            assert isinstance(obj, dict)
            obj["object_id"] = "mission-other"
        return {**result, "records": [*first, *rows]}


def test_natural_choice_uses_the_acknowledged_list_after_resume(tmp_path: Path) -> None:
    """A spoken ordinal resolves the same displayed Mission after session recovery."""
    client = MultipleCancellations()
    directory = tmp_path / "sessions"
    store = SessionStore(directory, "menu", {}, False)
    session = ConversationSession(client, no_model, store, MissionActions(client, store))
    with redirect_stdout(io.StringIO()) as output:
        try:
            display_question(session, "为什么任务一直在取消中？")
            lines = output.getvalue().splitlines()
            assert any(line.startswith("1.") and "mission-other" in line for line in lines)
            assert any(line.startswith("2.") and MISSION_ID in line for line in lines)
            assert session.diagnosis_selection and client.reads == ["missions"]
        finally:
            store.close()
        store = SessionStore(directory, "menu", {}, True)
        restored = ConversationSession(client, no_model, store, MissionActions(client, store))
        try:
            display_question(restored, "第三个")
            assert "请回复刚才列表里显示的序号" in output.getvalue()
            assert client.reads == ["missions"]
            display_question(restored, "查第二个")
            assert "关注步骤 publish" in output.getvalue()
            assert client.reads == ["missions", "mission", "attempts", "stop"]
            assert restored.last_mission == MISSION_ID and not restored.diagnosis_selection
            assert store.data["commands"] == []
        finally:
            store.close()


def test_unseen_diagnosis_menu_cannot_supply_an_ordinal(tmp_path: Path) -> None:
    """Reject a menu when output fails before its choices become visible."""
    client = MultipleCancellations()
    directory = tmp_path / "sessions"
    store = SessionStore(directory, "unseen", {}, False)
    session = ConversationSession(client, no_model, store, MissionActions(client, store))

    class BrokenOutput(io.StringIO):
        """Simulate a terminal that closes before the user sees the menu."""

        def write(self, value: str, /) -> int:
            """Reject the write before the normal acknowledgement boundary."""
            raise OSError("output closed")

    try:
        with redirect_stdout(BrokenOutput()), pytest.raises(OSError, match="output closed"):
            display_question(session, "这个任务为什么还在取消中？")
        assert not session.diagnosis_selection and session.displayed_missions == []
        assert client.reads == ["missions"]
    finally:
        store.close()
    reopened = SessionStore(directory, "unseen", {}, True)
    try:
        fresh = ConversationSession(client, no_model, reopened, MissionActions(client, reopened))
        assert not fresh.diagnosis_selection and fresh.displayed_missions == []
    finally:
        reopened.close()
