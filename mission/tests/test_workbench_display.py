"""Terminal confirmation requires complete displayed output, including interrupted real commands."""

from __future__ import annotations

import asyncio
import io
import sys
import threading
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from mission.models import JSONObject
from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.input import DummyInput
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.output import DummyOutput
from roboguide_workbench import terminal
from roboguide_workbench.actions import MissionActions
from roboguide_workbench.client import ReadOnlyController
from roboguide_workbench.conversation import ConversationSession
from roboguide_workbench.persistence import SessionStore
from test_requests import (
    FakeController,
    FakeInterpreter,
    FakePlanner,
    _assessment,
    _engine,
    _fixture_contracts,
    _inventory,
)
from test_workbench import ScriptedTransport, descriptor, no_model, router
from test_workbench_v2 import ObservedController, mission_server

Step = Callable[[], Awaitable[str]]


@contextmanager
def workbench(
    tmp_path: Path, transport: ScriptedTransport | None = None
) -> Iterator[tuple[ConversationSession, FakeController]]:
    """Use production Mission HTTP and SQLite with deterministic software-only model fixtures."""
    controller = FakeController(_inventory(*_fixture_contracts()))
    engine = _engine(
        tmp_path, FakeInterpreter([_assessment(), _assessment()]), FakePlanner(), controller
    )
    with mission_server(engine) as endpoint:
        store = SessionStore(tmp_path / "sessions", "display", {}, False)
        selected = router(transport) if transport is not None else None
        session = ConversationSession(
            ObservedController(),
            (lambda: selected) if selected is not None else no_model,
            store,
            MissionActions(ReadOnlyController(endpoint), store),
        )
        try:
            yield session, controller
        finally:
            store.close()


def command(text: str) -> Step:
    """Return one explicit user input without invoking any session operation directly."""

    async def enter() -> str:
        """Deliver the original input to the production terminal loop."""
        return text

    return enter


async def drain_foreground() -> None:
    """Wait for the actual terminal worker and its output without relying on timing sleeps."""
    pending = [
        task
        for task in asyncio.all_tasks()
        if getattr(task.get_coro(), "__qualname__", "").endswith("interactive.<locals>.execute")
    ]
    assert len(pending) <= 1
    for task in pending:
        await asyncio.wait_for(asyncio.shield(task), timeout=5)


class ScriptedPrompt:
    """Drive original terminal input and key bindings while retaining a real application output."""

    def __init__(self, steps: list[Step]) -> None:
        """Keep deterministic inputs and a noninteractive application for display suspension."""
        self.steps = iter(steps)
        self.app: Application[None] = Application(
            layout=Layout(Window(BufferControl(buffer=Buffer()))),
            input=DummyInput(),
            output=DummyOutput(),
        )
        self.keys = KeyBindings()

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Replace only terminal input and asynchronous stdout redirection in the harness."""

        def create(*, completer: WordCompleter, key_bindings: KeyBindings) -> PromptSession[str]:
            """Capture the production shortcut handlers without opening an actual TTY."""
            self.keys = key_bindings
            return cast(PromptSession[str], self)

        monkeypatch.setattr(terminal, "PromptSession", create)
        monkeypatch.setattr(terminal, "patch_stdout", nullcontext)

    async def prompt_async(self, message: str) -> str:
        """Deliver one scripted user action per original prompt."""
        return await next(self.steps)()

    def press(self, key: str) -> None:
        """Invoke the actual shortcut handler against the current input buffer."""
        bindings = self.keys.get_bindings_for_keys((key,))
        assert len(bindings) == 1
        event = cast(KeyPressEvent, SimpleNamespace(app=self.app))
        bindings[0].handler(event)


def block_result(
    monkeypatch: pytest.MonkeyPatch, session: ConversationSession, blocked: str
) -> tuple[threading.Event, threading.Event]:
    """Pause after the real command has committed and selected its undisplayed result."""
    original = session.ask
    entered, release = threading.Event(), threading.Event()

    def ask(text: str) -> str:
        """Keep real receipts and late selection changes while the terminal remains responsive."""
        result = original(text)
        if text == blocked:
            entered.set()
            assert release.wait(timeout=5)
        return result

    monkeypatch.setattr(session, "ask", ask)
    return entered, release


@pytest.mark.parametrize("modify", [False, True])
def test_muted_plan_requires_a_fresh_display_before_execute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, modify: bool
) -> None:
    """Hidden initial or modified plans cannot submit until /request actually displays them."""
    with workbench(tmp_path) as (session, controller):
        blocked = "/answer 改为检查东侧" if modify else "/plan 检查仓库"
        entered, release = block_result(monkeypatch, session, blocked)
        output = io.StringIO()
        monkeypatch.setattr(sys, "stdout", output)
        original_receipt: list[JSONObject] = []

        async def interrupt() -> str:
            """Mute while the background operation still owns the newly selected draft."""
            assert await asyncio.to_thread(entered.wait, 5)
            assert session.shown_draft is not None
            asyncio.get_running_loop().call_soon(release.set)
            raise KeyboardInterrupt

        async def blind_execute() -> str:
            """Require late background selections to be revoked after the actual command drains."""
            await drain_foreground()
            assert session.shown_draft is None and session.suggestion is None
            assert controller.submissions == []
            original_receipt.extend(cast(list[JSONObject], session.store.data["commands"]))
            assert all(receipt["state"] == "complete" for receipt in original_receipt)
            return "/execute"

        async def redisplay() -> str:
            """Observe the refused blind execution before requesting the original retained draft."""
            await drain_foreground()
            assert controller.submissions == []
            assert "先 /request" in output.getvalue()
            return "/request"

        async def execute_displayed() -> str:
            """Authorize only the exact current draft after successful terminal output."""
            await drain_foreground()
            assert session.shown_draft is not None
            assert "AwaitingExecution" in output.getvalue()
            return "/execute"

        async def finish() -> str:
            """Check one real submission and unchanged receipts from the muted commands."""
            await drain_foreground()
            assert len(controller.submissions) == 1
            receipts = cast(list[JSONObject], session.store.data["commands"])
            assert receipts[: len(original_receipt)] == original_receipt
            assert len(receipts) == len(original_receipt) + 1
            return "/quit"

        steps = [command(blocked), interrupt, blind_execute, redisplay, execute_displayed, finish]
        if modify:

            async def modify_displayed() -> str:
                """Display the initial draft before starting the revision that will be muted."""
                await drain_foreground()
                assert session.shown_draft is not None
                return blocked

            steps[:1] = [command("/plan 检查仓库"), modify_displayed]
        prompt = ScriptedPrompt(steps)
        prompt.install(monkeypatch)
        assert asyncio.run(terminal.interactive(session)) == 0
        assert "已停止前台显示" in output.getvalue()
        assert session.shown_draft is None and session.suggestion is None


def test_muted_suggestion_and_active_shortcuts_cannot_create_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither shortcuts during foreground work nor a hidden proposal confer action permission."""
    transport = ScriptedTransport([{"queries": [descriptor("run")], "answer": "", "citations": []}])
    with workbench(tmp_path, transport) as (session, controller):
        text = "请让机器人巡检仓库"
        entered, release = block_result(monkeypatch, session, text)
        output = io.StringIO()
        monkeypatch.setattr(sys, "stdout", output)

        async def interrupt() -> str:
            """Refuse reading the proposal before its foreground output has finished."""
            assert await asyncio.to_thread(entered.wait, 5)
            assert session.suggestion is not None
            prompt.app.current_buffer.text = "original input"
            prompt.press("f1")
            prompt.press("f2")
            assert prompt.app.current_buffer.text == "original input"
            asyncio.get_running_loop().call_soon(release.set)
            raise KeyboardInterrupt

        async def blind_choose() -> str:
            """A hidden suggestion is unavailable to both shortcuts and explicit /choose."""
            await drain_foreground()
            assert session.suggestion is None
            prompt.press("f1")
            prompt.press("f2")
            assert prompt.app.current_buffer.text == "original input"
            return "/choose run"

        async def finish() -> str:
            """Verify the original Mission command port was never entered."""
            await drain_foreground()
            assert session.store.data["commands"] == []
            assert controller.submissions == []
            assert "尚未发出操作" not in output.getvalue()
            return "/quit"

        prompt = ScriptedPrompt([command(text), interrupt, blind_choose, finish])
        prompt.install(monkeypatch)
        assert asyncio.run(terminal.interactive(session)) == 0


@pytest.mark.parametrize("failure", ["write", "flush"])
def test_failed_output_revokes_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """An incomplete write or flush cannot authorize confirming a plan through Mission HTTP."""

    class FailingOutput(io.StringIO):
        """Fail the original response write or its actual flush once."""

        failed = False

        def write(self, text: str) -> int:
            """Expose a partial response followed by the actual stream error."""
            if failure == "write" and "AwaitingExecution" in text and not self.failed:
                self.failed = True
                super().write(text[:20])
                raise BrokenPipeError("injected incomplete output")
            return super().write(text)

        def flush(self) -> None:
            """Refuse successful display acknowledgement until the real flush completes."""
            if failure == "flush" and "AwaitingExecution" in self.getvalue() and not self.failed:
                self.failed = True
                raise BrokenPipeError("injected incomplete output")
            super().flush()

    with workbench(tmp_path) as (session, controller):
        output = FailingOutput()
        monkeypatch.setattr(sys, "stdout", output)

        async def blind_execute() -> str:
            """Try confirming after output failed rather than after a complete display."""
            await drain_foreground()
            assert session.shown_draft is None and session.suggestion is None
            assert "injected incomplete output" in output.getvalue()
            return "/execute"

        async def finish() -> str:
            """Keep the original completed plan receipt without submitting a Mission."""
            await drain_foreground()
            assert controller.submissions == []
            receipts = cast(list[JSONObject], session.store.data["commands"])
            assert len(receipts) == 1 and receipts[0]["state"] == "complete"
            return "/quit"

        prompt = ScriptedPrompt([command("/plan 检查仓库"), blind_execute, finish])
        prompt.install(monkeypatch)
        assert asyncio.run(terminal.interactive(session)) == 0


def test_quit_drains_actual_effects_and_receipts_without_showing_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Quitting suppresses the late result while preserving the actual explicit /run submission."""
    with workbench(tmp_path) as (session, controller):
        entered, release = block_result(monkeypatch, session, "/run 检查仓库")
        output = io.StringIO()
        monkeypatch.setattr(sys, "stdout", output)

        async def quit_waiting() -> str:
            """Quit only after the real operation has happened and before its worker returns."""
            assert await asyncio.to_thread(entered.wait, 5)
            asyncio.get_running_loop().call_soon(release.set)
            return "/quit"

        prompt = ScriptedPrompt([command("/run 检查仓库"), quit_waiting])
        prompt.install(monkeypatch)
        assert asyncio.run(terminal.interactive(session)) == 0
        assert len(controller.submissions) == 1
        receipts = cast(list[JSONObject], session.store.data["commands"])
        assert len(receipts) == 1 and receipts[0]["state"] == "complete"
        assert "Accepted" not in output.getvalue()
        assert session.shown_draft is None and session.suggestion is None


def test_muting_execute_preserves_the_original_worker_selection_until_drained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Muting display must not race an already authorized execution's fresh-draft comparison."""
    with workbench(tmp_path) as (session, controller):
        entered, release = threading.Event(), threading.Event()
        original = session.actions.get
        output = io.StringIO()
        monkeypatch.setattr(sys, "stdout", output)

        def get(request_id: str) -> JSONObject:
            """Pause the actual refresh while the worker still needs its original selection."""
            result = original(request_id)
            entered.set()
            assert release.wait(timeout=5)
            return result

        monkeypatch.setattr(session.actions, "get", get)

        async def execute_displayed() -> str:
            """Confirm the initially displayed exact draft through the normal terminal command."""
            await drain_foreground()
            assert session.shown_draft is not None
            return "/execute"

        async def interrupt() -> str:
            """Stop waiting for display without mutating the worker's execution prerequisites."""
            assert await asyncio.to_thread(entered.wait, 5)
            asyncio.get_running_loop().call_soon(release.set)
            raise KeyboardInterrupt

        async def finish() -> str:
            """Observe the one actual completed execution and its preserved original receipts."""
            await drain_foreground()
            assert len(controller.submissions) == 1
            receipts = cast(list[JSONObject], session.store.data["commands"])
            assert len(receipts) == 2 and all(item["state"] == "complete" for item in receipts)
            assert "Accepted" not in output.getvalue()
            assert session.shown_draft is None and session.suggestion is None
            return "/quit"

        prompt = ScriptedPrompt([command("/plan 检查仓库"), execute_displayed, interrupt, finish])
        prompt.install(monkeypatch)
        assert asyncio.run(terminal.interactive(session)) == 0
