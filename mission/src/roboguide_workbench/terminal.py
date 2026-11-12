"""Nonblocking terminal input with bounded independent read-only Mission watches."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
from collections.abc import Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.application.current import set_app
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.patch_stdout import patch_stdout

from roboguide_workbench.display import DisplayPersistenceError, DisplayTurn, discard_confirmation
from roboguide_workbench.views import render_mission, safe_text
from roboguide_workbench.workspace import WorkbenchSession

COMMANDS = [
    "/nodes",
    "/node",
    "/missions",
    "/mission",
    "/compare",
    "/explain",
    "/watch",
    "/watches",
    "/unwatch",
    "/plan",
    "/plan-detail",
    "/run",
    "/request",
    "/answer",
    "/answer-to",
    "/execute",
    "/approve",
    "/choose",
    "/observe",
    "/reconcile",
    "/history",
    "/help",
    "/raw",
    "/reset",
    "/quit",
    "/status",
    "/sessions",
    "/new",
    "/resume",
    "/check",
    "/cancel",
    "/feedback",
]


class WatchManager:
    """Own up to three bounded watches which never mutate conversation selection or storage."""

    def __init__(
        self,
        session: WorkbenchSession,
        emit: Callable[[str], None],
        *,
        interval: float = 2,
        samples: int = 150,
    ) -> None:
        """Retain isolated reader settings so foreground timeout changes cannot race watchers."""
        self.session, self.emit = session, emit
        self.reader = copy.copy(session.controller)
        self.interval, self.samples = interval, samples
        self.tasks: dict[str, asyncio.Task[None]] = {}

    def start(self, target: str) -> None:
        """Resolve an original displayed identity once and admit at most three watchers."""
        mission = self.session._mission_target(target)
        if mission in self.tasks:
            self.emit("已在跟踪 " + safe_text(mission))
            return
        if len(self.tasks) >= 3:
            self.emit("最多同时跟踪三个任务。")
            return
        self.tasks[mission] = asyncio.create_task(self._watch(mission))

    async def _watch(self, mission: str) -> None:
        """Notify only changed observations; cancellation drains the current bounded GET."""
        prior = ""
        try:
            for _ in range(self.samples):
                read = asyncio.create_task(asyncio.to_thread(self.reader.mission, mission))
                try:
                    raw = await asyncio.shield(read)
                except asyncio.CancelledError:
                    try:
                        await read
                    except (ValueError, OSError):
                        pass
                    raise
                rendered = render_mission(raw, mission)
                signature = json.dumps(raw, sort_keys=True)
                if signature != prior:
                    self.emit("任务更新 · " + rendered)
                    prior = signature
                if raw.get("status") in {"Completed", "Failed", "Cancelled"}:
                    return
                await asyncio.sleep(self.interval)
        except (ValueError, OSError) as error:
            self.emit("跟踪暂停：" + safe_text(str(error)))
        finally:
            self.tasks.pop(mission, None)

    async def stop(self, target: str | None = None) -> None:
        """Stop local observation only and await every actually running GET."""
        selected = (
            list(self.tasks.values())
            if target is None
            else [self.tasks[target]]
            if target in self.tasks
            else []
        )
        for task in selected:
            task.cancel()
        await asyncio.gather(*selected, return_exceptions=True)


async def interactive(session: WorkbenchSession) -> int:
    """Keep input live while one foreground operation and three independent watches progress."""
    keys = KeyBindings()
    foreground: asyncio.Task[None] | None = None
    muted = False
    unavailable = False
    output = sys.stdout

    @keys.add("f1")
    def choose_first(event: KeyPressEvent) -> None:
        """Choose the displayed planning or single-action option with an explicit key press."""
        if foreground and not foreground.done():
            return
        proposal = session.suggestion
        if proposal:
            selected = "plan" if proposal[0] in {"plan", "run"} else proposal[0]
            event.app.current_buffer.text = "/choose " + selected
            event.app.current_buffer.validate_and_handle()

    @keys.add("f2")
    def choose_run(event: KeyPressEvent) -> None:
        """Choose execution only when that exact option is currently displayed."""
        if foreground and not foreground.done():
            return
        if session.suggestion and session.suggestion[0] in {"plan", "run"}:
            event.app.current_buffer.text = "/choose run"
            event.app.current_buffer.validate_and_handle()

    prompt: PromptSession[str] = PromptSession(
        completer=WordCompleter(COMMANDS, sentence=True),
        key_bindings=keys,
    )

    def toolbar() -> str:
        """Render local scope without sampling a worker's unacknowledged object references."""
        from roboguide_workbench.status import status_line

        if foreground and not foreground.done():
            return "RG · 正在处理当前请求；后台任务仍可跟踪"
        return status_line(session)

    prompt.bottom_toolbar = toolbar
    prompt.app.refresh_interval = 0.5

    def notify(message: str) -> None:
        """Keep best-effort status output from interrupting the drain of original operations."""
        try:
            print(message)
        except (ValueError, OSError):
            pass

    manager = WatchManager(session, notify)

    async def display(result: str, has_suggestion: bool = False) -> bool:
        """Acknowledge the complete original output rather than an asynchronous stdout queue."""

        def write() -> bool:
            """Flush the captured real stream while terminal input is safely suspended."""
            if muted:
                return False
            print("\nRG > " + result, file=output, flush=True)
            if has_suggestion:
                print(
                    "F1：仅规划/确认所示动作；F2：开始执行流程（仅在显示该选项时有效）。",
                    file=output,
                    flush=True,
                )
            return True

        with set_app(prompt.app):
            return await run_in_terminal(write)

    async def execute(text: str) -> None:
        """Let the sole session writer finish even if the user stops waiting for its display."""
        nonlocal muted, unavailable
        turn = DisplayTurn(session)
        try:
            result = await asyncio.to_thread(session.ask, text)
            turn.stage()
            if not muted:
                displayed = await display(result, turn.suggestion is not None)
                if displayed and not muted:
                    turn.acknowledge()
        except (ValueError, OSError) as error:
            if not muted:
                try:
                    await display("操作未完成：" + safe_text(str(error)))
                except (ValueError, OSError):
                    pass
        finally:
            try:
                turn.reject()
            except DisplayPersistenceError as error:
                unavailable = True
                notify("会话已暂停：" + safe_text(str(error)))
            muted = False

    with patch_stdout():
        try:
            while True:
                try:
                    text = (await prompt.prompt_async("你 > ")).strip()
                except KeyboardInterrupt:
                    if foreground and not foreground.done():
                        muted = True
                        notify("已停止前台显示；原操作继续收尾，未发送任务取消。")
                    continue
                except EOFError:
                    break
                if unavailable:
                    break
                if text in {"/quit", "/exit"}:
                    break
                if not foreground or foreground.done():
                    if text.partition(" ")[0] in {"/watch", "/watches", "/unwatch"}:
                        session.suggestion = None
                        session.pending_cancel = None
                if text == "/watches":
                    notify("跟踪：" + ", ".join(map(safe_text, manager.tasks)))
                    continue
                if text.startswith("/unwatch "):
                    await manager.stop(text.partition(" ")[2].strip())
                    continue
                if text == "/watch" or text.startswith("/watch "):
                    if foreground and not foreground.done():
                        notify("请等当前对象查询完成后再选择跟踪目标。")
                    else:
                        try:
                            manager.start(text.partition(" ")[2].strip())
                        except ValueError as error:
                            notify(safe_text(str(error)))
                    continue
                if not text:
                    continue
                if foreground and not foreground.done():
                    notify("上一条请求正在处理；输入未排队。任务跟踪仍可查看或停止。")
                    continue
                if text.partition(" ")[0] in {"/new", "/resume"}:
                    from roboguide_workbench.sessions import navigate

                    try:
                        await manager.stop()
                        changed = navigate(session, text)
                        try:
                            displayed = await display(changed or "会话未改变。")
                        except (ValueError, OSError) as error:
                            raise DisplayPersistenceError(
                                "会话已切换但提示未完整显示；请重新启动并明确 --resume 会话名。"
                            ) from error
                        if not displayed:
                            raise DisplayPersistenceError("会话切换提示未显示，请重新进入会话。")
                        manager = WatchManager(session, notify)
                    except DisplayPersistenceError as error:
                        unavailable = True
                        notify("会话已暂停：" + safe_text(str(error)))
                        break
                    except (ValueError, OSError) as error:
                        notify("切换失败：" + safe_text(str(error)))
                    continue
                foreground = asyncio.create_task(execute(text))
        finally:
            muted = True
            await manager.stop()
            if foreground and not foreground.done():
                notify("正在等待原请求收尾并保存回执…")
                await foreground
            discard_confirmation(session)
    return 2 if unavailable else 0
