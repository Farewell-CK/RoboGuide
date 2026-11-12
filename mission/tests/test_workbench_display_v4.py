"""Shared visible-reference and confirmation boundaries across both terminal frontends."""

from __future__ import annotations

import asyncio
import builtins
import io
import json
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from mission.models import JSONObject
from roboguide_workbench import cli, terminal
from roboguide_workbench.actions import MissionActions
from roboguide_workbench.conversation import ConversationSession
from roboguide_workbench.persistence import SessionStore
from test_workbench import inventory, no_model, node
from test_workbench_display import ScriptedPrompt, command, drain_foreground, workbench
from test_workbench_v2 import ObservedController


class FailingOutput(io.StringIO):
    """Fail one original foreground response write or its actual flush."""

    def __init__(self, failure: str, marker: str) -> None:
        """Retain native stream behavior apart from one explicit I/O failure."""
        super().__init__()
        self.failure, self.marker = failure, marker
        self.failed = False

    def write(self, text: str) -> int:
        """Reject the original result before reporting complete display."""
        if self.failure == "write" and self.marker in text and not self.failed:
            self.failed = True
            super().write(text[:10])
            raise BrokenPipeError("injected display write failure")
        return super().write(text)

    def flush(self) -> None:
        """Expose output buffering failure independently of a successful text write."""
        if self.failure == "flush" and self.marker in self.getvalue() and not self.failed:
            self.failed = True
            raise BrokenPipeError("injected display flush failure")
        super().flush()


@pytest.mark.parametrize("failure", ["write", "flush", "checkpoint", "ack-checkpoint"])
def test_plain_unseen_plan_requires_redisplay_after_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Plain output failures revoke confirmation while preserving real Mission HTTP receipts."""
    with workbench(tmp_path) as (session, controller):
        output = FailingOutput(failure, "AwaitingExecution")
        monkeypatch.setattr(sys, "stdout", output)
        checkpoint = session.checkpoint
        checkpoint_failed = False

        def fail_once() -> None:
            """Fail after a real plan selected its draft but before ask can return that result."""
            nonlocal checkpoint_failed
            original_checkpoint = failure == "checkpoint" and session.shown_draft is not None
            ack_checkpoint = (
                failure == "ack-checkpoint"
                and session.reference_checkpoint is None
                and session.shown_draft is None
                and session.request_id is not None
            )
            if (original_checkpoint or ack_checkpoint) and not checkpoint_failed:
                checkpoint_failed = True
                raise OSError("injected display checkpoint failure")
            checkpoint()

        monkeypatch.setattr(session, "checkpoint", fail_once)
        step = 0
        original_receipts: list[JSONObject] = []

        def enter(prompt: str) -> str:
            """Try blind execute, then display and authorize the retained exact original draft."""
            nonlocal step
            step += 1
            if step == 1:
                return "/plan 检查仓库"
            if step == 2:
                assert session.shown_draft is None and session.suggestion is None
                assert controller.submissions == []
                original_receipts.extend(cast(list[JSONObject], session.store.data["commands"]))
                assert len(original_receipts) == 1 and original_receipts[0]["state"] == "complete"
                return "/execute"
            if step == 3:
                assert "先 /request" in output.getvalue() and controller.submissions == []
                return "/request"
            if step == 4:
                assert session.shown_draft is not None
                assert session.store.data["commands"] == original_receipts
                return "/execute"
            assert len(controller.submissions) == 1
            assert cast(list[JSONObject], session.store.data["commands"])[:1] == original_receipts
            return "/quit"

        monkeypatch.setattr(builtins, "input", enter)
        assert cli.interactive(session) == 0
        assert session.shown_draft is None and session.suggestion is None
        assert checkpoint_failed if failure in {"checkpoint", "ack-checkpoint"} else output.failed


class ReorderingController(ObservedController):
    """Return a new ordering after the original visible node or Mission list."""

    def __init__(self) -> None:
        """Keep independent fixture counters for the two typed reference domains."""
        super().__init__()
        self.inventory_calls = 0
        self.mission_calls = 0

    def inventory(self) -> JSONObject:
        """Refresh actual fixture bytes without promising the prior ordinal ordering."""
        self.inventory_calls += 1
        self.reads.append("inventory")
        ids = ("a", "b") if self.inventory_calls == 1 else ("b", "a")
        return inventory(*(node(identifier) for identifier in ids))

    def missions(self) -> JSONObject:
        """Expose a changed typed list of existing Mission fixtures."""
        self.mission_calls += 1
        self.reads.append("missions")
        ids = ("m1", "m2") if self.mission_calls == 1 else ("m2", "m1")
        return {
            "schema": "roboguide.state-query/v0.1",
            "records": [
                {
                    "object": {"object_type": "mission", "object_id": identifier},
                    "channel_id": "mission-lifecycle",
                    "value": {"lifecycle": "Running"},
                }
                for identifier in ids
            ],
        }


def private_session(tmp_path: Path, reader: ObservedController) -> ConversationSession:
    """Create one isolated private session without any model or command service."""
    store = SessionStore(tmp_path / "sessions", "references", {}, False)
    return ConversationSession(reader, no_model, store, MissionActions(reader, store))


@pytest.mark.parametrize("kind", ["nodes", "missions"])
@pytest.mark.parametrize("hidden", ["interrupt", "quit", "write", "flush"])
def test_hidden_refresh_preserves_visible_references_durably(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, hidden: str
) -> None:
    """Hidden, failed and still-running refreshes cannot replace displayed reference order."""
    reader = ReorderingController()
    session = private_session(tmp_path, reader)
    marker = "1. b" if kind == "nodes" else "1. m2"
    output = FailingOutput(hidden, marker)
    monkeypatch.setattr(sys, "stdout", output)
    original = session.ask
    entered, release = threading.Event(), threading.Event()
    calls = 0
    expected = ["a", "b"] if kind == "nodes" else ["m1", "m2"]

    def ask(text: str) -> str:
        """Pause the hidden refresh only after its normal checkpoints have actually run."""
        nonlocal calls
        result = original(text)
        if text == "/" + kind:
            calls += 1
            if calls == 2 and hidden in {"interrupt", "quit"}:
                entered.set()
                assert release.wait(timeout=5)
        return result

    monkeypatch.setattr(session, "ask", ask)

    def assert_saved() -> None:
        """Inspect on-disk original context without bypassing or replacing its held lock."""
        data = json.loads(session.store.path.read_text())
        assert data["context"][kind] == expected

    async def refresh() -> str:
        """Start a real refresh only after the original list was completely shown."""
        await drain_foreground()
        assert_saved()
        return "/" + kind

    async def mute() -> str:
        """Observe crash-safe persisted references while a fresh worker result remains hidden."""
        assert await asyncio.to_thread(entered.wait, 5)
        assert_saved()
        asyncio.get_running_loop().call_soon(release.set)
        if hidden == "quit":
            return "/quit"
        raise KeyboardInterrupt

    async def finish() -> str:
        """The restored in-memory and durable scopes agree after the original worker drains."""
        await drain_foreground()
        assert_saved()
        displayed = session.displayed_nodes if kind == "nodes" else session.displayed_missions
        assert displayed == expected
        assert session.shown_draft is None and session.suggestion is None
        return "/quit"

    steps = [command("/" + kind), refresh]
    if hidden in {"interrupt", "quit"}:
        steps.append(mute)
    if hidden != "quit":
        steps.append(finish)
    prompt = ScriptedPrompt(steps)
    prompt.install(monkeypatch)
    try:
        assert asyncio.run(terminal.interactive(session)) == 0
        assert_saved()
        assert session.store.data["commands"] == []
        if hidden in {"write", "flush"}:
            assert output.failed
    finally:
        session.store.close()
    # The real session is reopened under its normal lock; no hidden ordering survives restart.
    store = SessionStore(tmp_path / "sessions", "references", {}, True)
    try:
        resumed = ConversationSession(reader, no_model, store, MissionActions(reader, store))
        command_text = "/node #2" if kind == "nodes" else "/mission #2"
        result = resumed.ask(command_text)
        assert ("1. b" if kind == "nodes" else "Mission m2") in result
    finally:
        store.close()


def test_plain_failed_refresh_restores_last_seen_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plain terminal follows the last visible list after a real refresh output fails."""
    reader = ReorderingController()
    session = private_session(tmp_path, reader)
    output = FailingOutput("write", "1. b")
    monkeypatch.setattr(sys, "stdout", output)
    commands: Iterator[str] = iter(["/nodes", "/nodes", "/node #2", "/quit"])

    def enter(prompt: str) -> str:
        """Feed original explicit queries into the production plain frontend."""
        return next(commands)

    monkeypatch.setattr(builtins, "input", enter)
    try:
        assert cli.interactive(session) == 0
        assert output.failed and session.last_node == "b"
        assert session.displayed_nodes == ["b"]
    finally:
        session.store.close()


def test_visible_refresh_publishes_its_new_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful visible refresh publishes fresh references for later ordinal reads."""
    reader = ReorderingController()
    session = private_session(tmp_path, reader)
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    try:
        cli.display_question(session, "/nodes")
        cli.display_question(session, "/nodes")
        assert session.displayed_nodes == ["b", "a"]
        saved = json.loads(session.store.path.read_text())
        assert saved["context"]["nodes"] == ["b", "a"]
        assert "1. a" in session.ask("/node #2")
    finally:
        session.store.close()


@pytest.mark.parametrize("frontend", ["plain", "async"])
def test_persistent_reference_storage_failure_stops_dependent_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frontend: str
) -> None:
    """Failed durable rollback stops either frontend without authorizing a later command."""
    with workbench(tmp_path) as (session, controller):
        output = io.StringIO()
        monkeypatch.setattr(sys, "stdout", output)

        def unavailable_checkpoint() -> None:
            """Refuse both the original post-command checkpoint and the reference rollback."""
            raise OSError("injected persistent storage outage")

        monkeypatch.setattr(session, "checkpoint", unavailable_checkpoint)
        if frontend == "plain":
            inputs = 0

            def enter(prompt: str) -> str:
                """A second prompt would accept dependent work after failed rollback."""
                nonlocal inputs
                inputs += 1
                assert inputs == 1
                return "/plan 检查仓库"

            monkeypatch.setattr(builtins, "input", enter)
            assert cli.interactive(session) == 2
            assert inputs == 1
        else:

            async def blind_execute() -> str:
                """Resume input after the original worker reports the durable failure."""
                await drain_foreground()
                return "/execute"

            prompt = ScriptedPrompt([command("/plan 检查仓库"), blind_execute])
            prompt.install(monkeypatch)
            assert asyncio.run(terminal.interactive(session)) == 2
        assert session.shown_draft is None and session.suggestion is None
        assert controller.submissions == [] and session.request_id is not None
        commands = cast(list[JSONObject], session.store.data["commands"])
        assert len(commands) == 1 and commands[0]["state"] == "complete"
        assert commands[0]["request_id"] == session.request_id
        assert "可见对象引用未能保存" in output.getvalue()


@pytest.mark.parametrize("domain", ["node", "mission"])
def test_hidden_detail_cannot_replace_last_visible_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, domain: str
) -> None:
    """Singular implicit follow-ups retain the same visible identity before and after restart."""
    session = private_session(tmp_path, ObservedController())
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    old_id, hidden_id = ("a", "b") if domain == "node" else ("m1", "m2")
    entered, release = threading.Event(), threading.Event()
    original = session.ask

    def ask(text: str) -> str:
        """Block the second actual result after it reached the normal reference checkpoint."""
        result = original(text)
        if text == f"/{domain} {hidden_id}":
            entered.set()
            assert release.wait(timeout=5)
        return result

    monkeypatch.setattr(session, "ask", ask)

    async def refresh() -> str:
        """Begin a fresh literal read after the initial object was fully displayed."""
        await drain_foreground()
        return f"/{domain} {hidden_id}"

    async def interrupt() -> str:
        """Read the durable old identity while the new one is still hidden in the worker."""
        assert await asyncio.to_thread(entered.wait, 5)
        saved = json.loads(session.store.path.read_text())
        assert saved["context"]["last_" + domain] == old_id
        asyncio.get_running_loop().call_soon(release.set)
        raise KeyboardInterrupt

    async def finish() -> str:
        """Check the visible implicit reference after the hidden worker actually drained."""
        await drain_foreground()
        assert getattr(session, "last_" + domain) == old_id
        return "/quit"

    prompt = ScriptedPrompt([command(f"/{domain} {old_id}"), refresh, interrupt, finish])
    prompt.install(monkeypatch)
    try:
        assert asyncio.run(terminal.interactive(session)) == 0
    finally:
        session.store.close()
    store = SessionStore(tmp_path / "sessions", "references", {}, True)
    try:
        resumed = ConversationSession(
            ObservedController(), no_model, store, MissionActions(session.controller, store)
        )
        assert getattr(resumed, "last_" + domain) == old_id
    finally:
        store.close()
