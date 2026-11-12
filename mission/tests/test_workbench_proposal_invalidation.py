"""A new command cannot leave an older natural-language action on terminal shortcuts."""

from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

import pytest
from roboguide_workbench import terminal
from roboguide_workbench.cli import display_question
from roboguide_workbench.client import WorkbenchError
from roboguide_workbench.sessions import navigate
from test_workbench import ScriptedTransport, descriptor
from test_workbench_display import ScriptedPrompt, command, drain_foreground, workbench


def proposal_transport() -> ScriptedTransport:
    """Propose one explicit original action without performing any model-owned operation."""
    return ScriptedTransport([{"queries": [descriptor("run")], "answer": "", "citations": []}])


@pytest.mark.parametrize(
    "command_text", ["/status", "/sessions", "/check", "/feedback", "/help", "/history"]
)
def test_new_commands_invalidate_the_preceding_action_proposal(
    tmp_path: Path, command_text: str
) -> None:
    """Early-return commands cannot preserve an actionable old proposal after a different result."""
    with workbench(tmp_path, proposal_transport()) as (session, controller):
        display_question(session, "/plan 保留原计划")
        display_question(session, "启动另一个任务")
        assert session.suggestion is not None
        try:
            display_question(session, command_text)
        except WorkbenchError:
            assert command_text == "/check"
        assert session.suggestion is None and controller.submissions == []
        with pytest.raises(WorkbenchError, match="待确认提议"):
            display_question(session, "/choose run")


def test_cancel_preview_leaves_no_old_f1_f2_or_choose_run_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Actual key handlers stay inert on a cancellation preview and only explicit cancel can act."""
    with workbench(tmp_path, proposal_transport()) as (session, controller):
        output = io.StringIO()
        monkeypatch.setattr(sys, "stdout", output)
        target: list[str] = []

        async def propose() -> str:
            """Wait for the original plan to be displayed before proposing another task."""
            await drain_foreground()
            assert session.request_id is not None
            target.append(session.request_id)
            return "启动另一个巡检任务"

        async def preview() -> str:
            """Display a different action scope while an earlier run suggestion exists."""
            await drain_foreground()
            assert session.suggestion is not None
            return "/cancel request " + target[0]

        async def press_keys() -> str:
            """Use both production shortcut bindings after the cancellation preview was flushed."""
            await drain_foreground()
            assert session.pending_cancel is not None and session.suggestion is None
            prompt.app.current_buffer.text = "user text"
            prompt.press("f1")
            prompt.press("f2")
            assert prompt.app.current_buffer.text == "user text"
            return "/choose run"

        async def confirm_cancel() -> str:
            """Refuse the old choose path and request a fresh cancellation preview."""
            await drain_foreground()
            assert controller.submissions == [] and session.pending_cancel is None
            assert "没有匹配的待确认提议" in output.getvalue()
            return "/cancel request " + target[0]

        async def choose_confirm() -> str:
            """Confirm only the exact cancellation just displayed."""
            await drain_foreground()
            return "/cancel confirm"

        async def finish() -> str:
            """No unrelated Mission was ever created and the intended Request is cancelled once."""
            await drain_foreground()
            assert controller.submissions == []
            assert session.actions.get(target[0])["lifecycle"] == "Cancelled"
            return "/quit"

        prompt = ScriptedPrompt(
            [
                command("/plan 保留原规划"),
                propose,
                preview,
                press_keys,
                confirm_cancel,
                choose_confirm,
                finish,
            ]
        )
        prompt.install(monkeypatch)
        assert asyncio.run(terminal.interactive(session)) == 0
        cancel_output = output.getvalue().split("待确认取消", 1)[1]
        assert "F1：" not in cancel_output


def test_same_session_navigation_discards_an_older_proposal(tmp_path: Path) -> None:
    """A no-op current-session selection is still a newer UI action than its old run proposal."""
    with workbench(tmp_path, proposal_transport()) as (session, controller):
        display_question(session, "启动另一个任务")
        assert session.suggestion is not None
        navigate(session, "/resume " + session.store.path.stem)
        assert session.suggestion is None and session.pending_cancel is None
        assert controller.submissions == []


def test_explicit_choose_run_still_uses_current_displayed_proposal(tmp_path: Path) -> None:
    """The current intentional choose command retains its original instruction."""
    with workbench(tmp_path, proposal_transport()) as (session, controller):
        display_question(session, "启动巡检")
        display_question(session, "/choose run")
        assert len(controller.submissions) == 1


@pytest.mark.parametrize("watch_command", ["/watch m1", "/watches", "/unwatch m1"])
def test_watch_controls_invalidate_idle_action_shortcuts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, watch_command: str
) -> None:
    """Frontend-only observation commands cannot leave a previous run on F2."""
    with workbench(tmp_path, proposal_transport()) as (session, controller):
        monkeypatch.setattr(sys, "stdout", io.StringIO())

        async def change_view() -> str:
            """Supersede a displayed action proposal through an observation control."""
            await drain_foreground()
            assert session.suggestion is not None
            return watch_command

        async def check_keys() -> str:
            """No actual current key binding can dispatch the stale original proposal."""
            assert session.suggestion is None and session.pending_cancel is None
            prompt.app.current_buffer.text = "user text"
            prompt.press("f2")
            assert prompt.app.current_buffer.text == "user text"
            return "/quit"

        prompt = ScriptedPrompt([command("启动任务"), change_view, check_keys])
        prompt.install(monkeypatch)
        assert asyncio.run(terminal.interactive(session)) == 0
        assert controller.submissions == []
