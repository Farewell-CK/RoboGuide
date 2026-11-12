"""Bounded foreground queue and safety behavior for the scrollback chat terminal."""

from __future__ import annotations

import asyncio
import io
import json
import sys
import threading
from pathlib import Path

import pytest
from roboguide_workbench import terminal
from test_workbench import ScriptedTransport, descriptor, router
from test_workbench_display import ScriptedPrompt, command, drain_foreground, workbench
from test_workbench_display_v4 import ReorderingController, private_session


def test_busy_questions_run_once_in_order_after_the_current_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A question entered during a slow turn is queued and executed exactly once afterward."""
    entered, release = threading.Event(), threading.Event()
    second_finished = threading.Event()
    calls: list[str] = []

    with workbench(tmp_path) as (session, _controller):
        original = session.ask

        def ask(text: str) -> str:
            """Hold the first real session writer while allowing the terminal to accept input."""
            calls.append(text)
            result = original(text)
            if text == "/help":
                entered.set()
                assert release.wait(timeout=5)
            if text == "/nodes":
                second_finished.set()
            return result

        monkeypatch.setattr(session, "ask", ask)
        output = io.StringIO()
        monkeypatch.setattr(sys, "stdout", output)

        async def queue_next() -> str:
            """Submit a second read while the first writer is still held."""
            assert await asyncio.to_thread(entered.wait, 5)
            return "/nodes"

        async def release_and_quit() -> str:
            """Release the first writer and wait for the queued read to finish once."""
            release.set()
            assert await asyncio.to_thread(second_finished.wait, 5)
            await drain_foreground()
            assert calls == ["/help", "/nodes"]
            return "/quit"

        prompt = ScriptedPrompt([command("/help"), queue_next, release_and_quit])
        prompt.install(monkeypatch)
        assert asyncio.run(terminal.interactive(session)) == 0

    assert "已加入待处理队列（1/3）" in output.getvalue()
    assert output.getvalue().count("── RG · 助手") == 2


def test_busy_action_command_is_preserved_and_never_implicitly_queued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Approval-like commands are retained for explicit resubmission after the current turn."""
    entered, release = threading.Event(), threading.Event()

    with workbench(tmp_path) as (session, _controller):
        original = session.ask

        def ask(text: str) -> str:
            """Hold a read-only command long enough to exercise the safety boundary."""
            result = original(text)
            if text == "/help":
                entered.set()
                assert release.wait(timeout=5)
            return result

        monkeypatch.setattr(session, "ask", ask)
        output = io.StringIO()
        monkeypatch.setattr(sys, "stdout", output)

        async def reject_action() -> str:
            """Enter an approval while the first turn is active and then release the read."""
            assert await asyncio.to_thread(entered.wait, 5)
            return "/execute"

        async def finish() -> str:
            """Finish the first read without replaying the retained action automatically."""
            release.set()
            await drain_foreground()
            return "/quit"

        prompt = ScriptedPrompt([command("/help"), reject_action, finish])
        prompt.install(monkeypatch)
        assert asyncio.run(terminal.interactive(session)) == 0

    text = output.getvalue()
    assert "/execute 不会排队" in text
    assert "输入已保留" in text
    assert '"state"' not in text


@pytest.mark.parametrize(
    "text",
    [
        "/node #1",
        "/mission #2",
        "/compare",
        "/raw",
        "/check",
        "第二个节点有什么能力？",
        "刚才那个任务呢？",
    ],
)
def test_context_dependent_input_is_not_reinterpreted_after_a_queued_refresh(text: str) -> None:
    """A preceding refresh cannot silently redirect an ordinal or implicit target."""
    assert terminal._is_queueable(text) is False


@pytest.mark.parametrize("mode", ["grounded", "facts"])
def test_queued_implicit_reference_keeps_the_original_visible_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """A pronoun entered during a different Node read keeps its originally displayed identity."""
    transport = ScriptedTransport(
        [
            {"queries": [descriptor("node", value="a")], "answer": "", "citations": []},
            {"queries": [], "answer": "原节点 a 的能力见 [E1]", "citations": ["E1"]},
        ]
        if mode == "grounded"
        else [descriptor("node", value="a")]
    )
    session = private_session(tmp_path, ReorderingController())
    session.router_factory = lambda: router(transport)
    session.answer_mode = mode
    session.ask("/node a")
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = session.ask

    def ask(text: str) -> str:
        """Hold a new selection while the user submits a reference to the old displayed Node."""
        result = original(text)
        if text == "/node b":
            entered.set()
            assert release.wait(5)
        elif text == "它有什么功能？":
            finished.set()
        return result

    monkeypatch.setattr(session, "ask", ask)
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    async def queue_pronoun() -> str:
        """Type a pronoun before the new object has appeared on the terminal."""
        assert await asyncio.to_thread(entered.wait, 5)
        return "它有什么功能？"

    async def finish() -> str:
        """Assert both routing and the actual fresh read preserve the original displayed ID."""
        release.set()
        assert await asyncio.to_thread(finished.wait, 5)
        await drain_foreground()
        assert session.last_node == "a"
        assert session.input_references is None
        assert session.input_history is None
        supplied = json.loads(str(transport.payloads[0]["input"]))
        assert supplied["references"]["last_node"] == "a"
        if mode == "grounded":
            assert all(row["text"] != "/node b" for row in supplied["history"])
        return "/quit"

    prompt = ScriptedPrompt([command("/node b"), queue_pronoun, finish])
    prompt.install(monkeypatch)
    try:
        assert asyncio.run(terminal.interactive(session)) == 0
    finally:
        release.set()
        session.store.close()
