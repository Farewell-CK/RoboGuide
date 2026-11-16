"""Nonblocking terminal input with bounded independent read-only Mission watches."""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
import re
import sys
import time
from collections import deque
from collections.abc import Callable
from contextlib import suppress

from mission.models import JSONObject
from prompt_toolkit import PromptSession
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.application.current import set_app
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.output.vt100 import Vt100_Output
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth

from roboguide_workbench.answers import bounded_history
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
    "/state",
    "/state-fields",
    "/sessions",
    "/new",
    "/resume",
    "/check",
    "/cancel",
    "/feedback",
    "/queue",
    "/dequeue",
    "/edit-queued",
]

# A short queue keeps a fast typist from losing questions while the provider or
# Controller is working.  It is deliberately local to this terminal process;
# queued text is not a new permission to perform a Mission effect.
MAX_QUEUED_REQUESTS = 3

# Read-only commands can safely run after the current writer drains.  Watches
# and quit are handled immediately below because they own separate lifecycle
# semantics.
QUEUEABLE_COMMANDS = {
    "/explain",
    "/help",
    "/history",
    "/mission",
    "/missions",
    "/node",
    "/nodes",
    "/sessions",
    "/status",
    "/state",
    "/state-fields",
}


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


def _command_name(text: str) -> str:
    """Return the slash command token without interpreting its arguments."""
    return text.partition(" ")[0]


def _is_queueable(text: str) -> bool:
    """Queue independent reads only; a preceding turn may replace displayed references."""
    if re.search(
        r"#\d+|第.{0,4}[个项台只]|刚才|上面|上述|前面|[这那][个台只些]|"
        r"\b(?:that|those|it|second|first|previous|above)\b",
        text,
        re.IGNORECASE,
    ):
        return False
    if not text.startswith("/"):
        return True
    if (
        _command_name(text) in {"/node", "/mission", "/explain", "/state"}
        and not text.partition(" ")[2].strip()
    ):
        return False
    return _command_name(text) in QUEUEABLE_COMMANDS


def _prompt_supports_option(prompt: PromptSession[str], option: str) -> bool:
    """Detect supported prompt options without expanding the test double's surface."""
    with suppress(TypeError, ValueError):
        return option in inspect.signature(prompt.prompt_async).parameters
    return False


def _wrapped_lines(line: str, width: int) -> list[str]:
    """Keep complete text while wrapping by terminal cells, including Chinese characters."""
    result: list[str] = []
    current: list[str] = []
    cells = 0
    for char in line:
        char_cells = get_cwidth(char)
        if current and cells + char_cells > width:
            result.append("".join(current))
            current, cells = [], 0
        current.append(char)
        cells += char_cells
    result.append("".join(current))
    return result


async def interactive(session: WorkbenchSession) -> int:
    """Run a scrollback-friendly chat shell with serialized, bounded foreground work."""
    keys = KeyBindings()
    foreground: asyncio.Task[None] | None = None
    queued: deque[tuple[str, JSONObject, list[JSONObject]]] = deque()
    visible_references = session.query_references()
    visible_history = bounded_history(session.store.data.get("messages"))
    retained_input = ""
    muted = False
    unavailable = False
    closing = False
    output = sys.stdout
    started_at: float | None = None
    phase = "待命"
    loop = asyncio.get_running_loop()
    stream_buffer = ""
    previewed = ""
    preview_tasks: list[asyncio.Task[None]] = []
    preview_lock = asyncio.Lock()

    @keys.add("enter")
    def submit_line(event: KeyPressEvent) -> None:
        """Submit the composer with Enter, including when multiline editing is enabled."""
        event.app.current_buffer.validate_and_handle()

    @keys.add("escape", "enter")
    def insert_line_break(event: KeyPressEvent) -> None:
        """Insert a line break with Alt/Meta-Enter while keeping Enter as submit."""
        event.app.current_buffer.insert_text("\n")

    @keys.add("c-j")
    def submit_control_j(event: KeyPressEvent) -> None:
        """Offer a terminal-friendly submit key for clients that do not send Enter."""
        event.app.current_buffer.validate_and_handle()

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

    @keys.add("escape", "up")
    def edit_pending(event: KeyPressEvent) -> None:
        """Take the last unsent message back into an empty composer without executing it."""
        if queued and not event.app.current_buffer.text:
            text, _, _ = queued.pop()
            event.app.current_buffer.text = text
            event.app.current_buffer.cursor_position = len(text)
            refresh_toolbar()

    @keys.add("escape", "backspace")
    def delete_pending(event: KeyPressEvent) -> None:
        """Withdraw only the last unsent message; the running operation keeps its identity."""
        if queued:
            text, _, _ = queued.pop()
            notify("已撤回待发消息：" + safe_text(text))
            refresh_toolbar()

    # Keep the constructor surface used by the plain test harness.  The live
    # session receives the richer multiline behavior through attributes below.
    try:
        # Keep the production composer genuinely multiline.  The small fallback retains
        # compatibility with the repository's minimal PromptSession test doubles.
        prompt: PromptSession[str] = PromptSession(
            multiline=True,
            completer=WordCompleter(COMMANDS, sentence=True),
            key_bindings=keys,
        )
    except TypeError:
        prompt = PromptSession(
            completer=WordCompleter(COMMANDS, sentence=True),
            key_bindings=keys,
        )
        prompt.multiline = True
    prompt.prompt_continuation = lambda width, line_number, is_soft_wrap: "· "
    prompt.show_frame = False
    terminal_output = getattr(prompt, "output", None)
    if isinstance(terminal_output, Vt100_Output):
        terminal_output.enable_cpr = False
    prompt.style = Style.from_dict(
        {
            "frame.border": "#5f6f86",
            "prompt": "bold #81a9ed",
            "bottom-toolbar": "bg:#202631 #c1cad8",
            "completion-menu.completion": "bg:#202631 #c1cad8",
            "completion-menu.completion.current": "bg:#35455d #ffffff",
        }
    )
    prompt.reserve_space_for_menu = 3

    def _session_label() -> str:
        """Read the local session identifier without reaching into worker state."""
        path = getattr(getattr(session, "store", None), "path", None)
        return safe_text(getattr(path, "stem", "local"))

    def _model_label() -> str:
        """Read the configured model label or a stable local fallback."""
        return safe_text(str(getattr(session, "model_label", "未配置")))

    def toolbar() -> str:
        """Render connection, elapsed work, progress and queue count in the bottom status bar."""
        waiting = f" · 队列 {len(queued)}/{MAX_QUEUED_REQUESTS}" if queued else ""
        if foreground and not foreground.done():
            return f" {phase}{waiting} · Ctrl-C 停止显示，原请求继续收尾"
        selected = f" · 状态对象 {safe_text(session.last_state)}" if session.last_state else ""
        return f" 就绪{selected}{waiting} · Enter 发送 · Alt-Enter 换行 · / 命令"

    prompt.bottom_toolbar = toolbar
    # The right prompt remains visible on terminals which cannot answer CPR, where
    # prompt_toolkit deliberately hides its screen-bottom toolbar.
    prompt.rprompt = toolbar
    prompt.refresh_interval = 0
    prompt.app.refresh_interval = None

    def refresh_toolbar() -> None:
        """Request a redraw after progress arrives from the worker thread."""
        with suppress(RuntimeError, AttributeError):
            loop.call_soon_threadsafe(prompt.app.invalidate)

    def update_progress(message: str) -> None:
        """Retain compact public processing steps in scrollback without timer redraws."""

        def record() -> None:
            """Publish a changed phase on the terminal owner without exposing raw tool messages."""
            nonlocal phase
            value = safe_text(message)
            if value != phase:
                phase = value
                if not muted and not closing:
                    notify("· " + value)
                refresh_toolbar()

        loop.call_soon_threadsafe(record)

    previous_progress = getattr(session, "progress", None)
    session.progress = update_progress  # type: ignore[attr-defined]
    previous_stream = getattr(session, "stream_output", None)

    def notify(message: str) -> None:
        """Keep status output visible without interrupting the drain of original operations."""
        try:
            # Notifications carry no confirmation authority and use patch_stdout so a
            # watch update cannot overwrite the live input buffer.
            print(safe_text(message), flush=True)
        except (ValueError, OSError):
            pass

    def print_header() -> None:
        """Print one compact, scrollback-preserving connection header for a new shell."""
        endpoint = safe_text(str(getattr(session.controller, "endpoint", "unknown")))
        tenant = safe_text(str(getattr(session.controller, "tenant", None) or "由服务端身份确定"))
        print(
            f"\n  RoboGuide Chat    {_model_label()}\n"
            f"  {endpoint} · tenant={tenant}\n"
            f"  会话 {_session_label()} · /help 查看命令 · /quit 退出",
            file=output,
            flush=True,
        )
        print(
            "  直接提问，例如：有哪些设备？它还有多少电？\n"
            "  /state-fields 查看状态对象；/help 查看命令。\n",
            file=output,
            flush=True,
        )

    manager = WatchManager(session, notify)

    async def show_preview(text: str) -> None:
        """Flush actual public text deltas while keeping input editing and final ACK separate."""
        nonlocal previewed
        async with preview_lock:
            if muted or closing:
                return

            def write() -> None:
                """Write public text without reasoning or incomplete tool arguments."""
                nonlocal previewed
                if not previewed:
                    print("\n── RG · 生成中", file=output, flush=True)
                print(
                    ("RG > " if not previewed else "     ")
                    + safe_text(text).replace("\\u000a", "\n"),
                    file=output,
                    flush=True,
                )
                previewed += text

            with set_app(prompt.app):
                await run_in_terminal(write)

    def update_stream(delta: str) -> None:
        """Buffer real tokens into short lines and schedule their rendering on the UI owner."""

        def record() -> None:
            """Avoid full input-frame refreshes for each individual Provider token."""
            nonlocal stream_buffer
            if muted or closing:
                return
            stream_buffer += delta
            while len(stream_buffer) >= 80 or "\n" in stream_buffer:
                split = stream_buffer.find("\n") + 1 if "\n" in stream_buffer else 80
                text, stream_buffer = stream_buffer[:split], stream_buffer[split:]
                preview_tasks.append(asyncio.create_task(show_preview(text)))

        loop.call_soon_threadsafe(record)

    if hasattr(session, "stream_output"):
        session.stream_output = update_stream

    async def display(result: str, suggestion: tuple[str, str] | None = None) -> bool:
        """Flush a role-labeled, wrapped assistant turn before acknowledging its state."""

        def write() -> bool:
            """Flush the captured real stream while terminal input is safely suspended."""
            if muted:
                return False
            width = 100
            with suppress(AttributeError, OSError, ValueError):
                width = max(20, min(160, prompt.output.get_size().columns))
            print("\n── RG · 助手", file=output, flush=True)
            lines = result.splitlines() or [""]
            first_line = True
            for line in lines:
                # Wrap long evidence lines so the scrollback retains the complete bounded
                # projection; a visual width limit must never turn facts into an ellipsis.
                chunks = _wrapped_lines(safe_text(line), max(8, width - 5))
                for chunk in chunks:
                    print(("RG > " if first_line else "     ") + chunk, file=output, flush=True)
                    first_line = False
            if suggestion is not None:
                if suggestion[0] in {"plan", "run"}:
                    shortcut = "F1：只规划；F2：开始执行流程。"
                else:
                    shortcut = "F1：确认上面显示的动作。"
                print(shortcut, file=output, flush=True)
            print("──", file=output, flush=True)
            return True

        with set_app(prompt.app):
            return await run_in_terminal(write)

    async def execute(
        text: str,
        original_references: JSONObject | None = None,
        original_history: list[JSONObject] | None = None,
    ) -> None:
        """Run one serialized turn and start the next queued question after its receipt drains."""
        nonlocal muted, unavailable, foreground, started_at, phase
        nonlocal visible_references, visible_history
        nonlocal stream_buffer, previewed
        started_at, phase = time.monotonic(), "处理中"
        stream_buffer, previewed = "", ""
        preview_tasks.clear()
        refresh_toolbar()
        turn = DisplayTurn(session)
        session.input_references = original_references
        session.input_history = original_history
        try:
            result = await asyncio.to_thread(session.ask, text)
            await asyncio.sleep(0)
            if stream_buffer:
                preview_tasks.append(asyncio.create_task(show_preview(stream_buffer)))
                stream_buffer = ""
            if preview_tasks:
                await asyncio.gather(*preview_tasks)
            turn.stage()
            if not muted:
                remaining_text = (
                    result[len(previewed) :].strip()
                    if previewed and result.startswith(previewed)
                    else result
                )
                displayed = await display(remaining_text or "回复完成。", turn.suggestion)
                if displayed and not muted:
                    turn.acknowledge()
        except (ValueError, OSError) as error:
            if not muted:
                try:
                    await display("操作未完成：" + safe_text(str(error)))
                except (ValueError, OSError):
                    pass
        finally:
            if preview_tasks:
                await asyncio.gather(*preview_tasks, return_exceptions=True)
            stream_buffer = ""
            session.input_references = None
            session.input_history = None
            try:
                turn.reject()
            except DisplayPersistenceError as error:
                unavailable = True
                notify("会话已暂停：" + safe_text(str(error)))
            muted = False
            visible_references = session.query_references()
            visible_history = bounded_history(session.store.data.get("messages"))
            if started_at is not None and not muted and not closing:
                notify(f"· 本轮结束 · {time.monotonic() - started_at:.1f}s")
            started_at, phase = None, "待命"
            refresh_toolbar()
            if not closing and not unavailable and queued:
                next_text, original_references, original_history = queued.popleft()
                notify(f"继续处理队列中的问题（剩余 {len(queued)} 条）。")
                foreground = asyncio.create_task(
                    execute(next_text, original_references, original_history)
                )

    def clear_queue(reason: str) -> None:
        """Drop only local pending questions and state exactly how many were not started."""
        if queued:
            count = len(queued)
            queued.clear()
            notify(f"{reason}；已移除 {count} 条尚未开始的问题。")

    async def read_prompt(default: str = "") -> str:
        """Read one prompt, restoring a rejected action as an editable default when supported."""

        def composer() -> str:
            """Keep unsent message previews directly above the current input buffer."""
            lines = [
                f"待发 {index}. {safe_text(item[0])[:100]}" for index, item in enumerate(queued, 1)
            ]
            if lines:
                lines.append("Alt-Up 取回编辑 · Alt-Backspace 撤回末条 · /queue 管理")
            return "\n".join([*lines, "你 > "])

        if _prompt_supports_option(prompt, "show_frame"):
            # prompt_async resets show_frame on every call unless it is explicit.
            return await prompt.prompt_async(composer, default=default, show_frame=False)
        if default and _prompt_supports_option(prompt, "default"):
            return await prompt.prompt_async(composer, default=default)
        if default:
            with suppress(AttributeError):
                prompt.app.current_buffer.text = default
        return await prompt.prompt_async(composer)

    print_header()
    with patch_stdout():
        try:
            while True:
                try:
                    text = (await read_prompt(retained_input)).strip()
                    if retained_input:
                        retained_input = ""
                except KeyboardInterrupt:
                    retained_input = ""
                    if foreground and not foreground.done():
                        muted = True
                        clear_queue("已停止前台显示；原操作继续收尾，未发送任务取消")
                        notify("已停止前台显示；原操作继续收尾，未发送任务取消。")
                    else:
                        notify("已取消当前输入；没有发送任务。")
                    continue
                except EOFError:
                    closing = True
                    break
                if unavailable:
                    closing = True
                    break
                if text in {"/quit", "/exit"}:
                    closing = True
                    break
                if _command_name(text) in {"/queue", "/dequeue", "/edit-queued"}:
                    command, _, argument = text.partition(" ")
                    if command == "/queue":
                        notify(
                            "待发消息：\n"
                            + "\n".join(f"{i}. {item[0]}" for i, item in enumerate(queued, 1))
                            if queued
                            else "当前没有待发消息。"
                        )
                    else:
                        try:
                            index = int(argument) if argument else len(queued)
                            if not 1 <= index <= len(queued):
                                raise ValueError
                            item = queued[index - 1]
                            del queued[index - 1]
                            if command == "/edit-queued":
                                retained_input = item[0]
                                notify("已取回待发消息，请编辑后重新发送。")
                            else:
                                notify("已撤回待发消息：" + item[0])
                        except ValueError:
                            notify("请用 /queue 查看序号，再 /edit-queued 1 或 /dequeue 1。")
                    refresh_toolbar()
                    continue
                if not foreground or foreground.done():
                    if _command_name(text) in {"/watch", "/watches", "/unwatch"}:
                        session.suggestion = None
                        session.pending_cancel = None
                if text == "/watches":
                    notify(
                        "跟踪：" + ", ".join(map(safe_text, manager.tasks))
                        if manager.tasks
                        else "当前没有任务跟踪；用 /watch MissionID 开始。"
                    )
                    continue
                if text.startswith("/unwatch "):
                    target = text.partition(" ")[2].strip()
                    if target not in manager.tasks:
                        notify("没有正在跟踪这个任务；用 /watches 查看当前跟踪。")
                    else:
                        await manager.stop(target)
                        notify("已停止本地跟踪：" + target + "；任务仍按原流程运行。")
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
                    if not _is_queueable(text):
                        retained_input = text
                        label = (
                            _command_name(text) if text.startswith("/") else "依赖当前对象的输入"
                        )
                        notify(
                            f"当前请求仍在处理；{label} 不会排队。"
                            "输入已保留，请在当前回复完成后重新提交。"
                        )
                        continue
                    if len(queued) >= MAX_QUEUED_REQUESTS:
                        retained_input = text
                        notify(
                            f"待处理队列已满（{MAX_QUEUED_REQUESTS} 条）；输入已保留，"
                            "请等待后重新提交。"
                        )
                        continue
                    queued.append(
                        (text, copy.deepcopy(visible_references), copy.deepcopy(visible_history))
                    )
                    notify(
                        f"已加入待处理队列（{len(queued)}/{MAX_QUEUED_REQUESTS}），"
                        f"将在当前回复显示并保存后处理：{safe_text(text)}\n"
                        "Alt-Up 取回编辑；/queue 查看，/dequeue 序号 撤回。"
                    )
                    continue
                if _command_name(text) in {"/new", "/resume"}:
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
                        visible_references = session.query_references()
                        visible_history = bounded_history(session.store.data.get("messages"))
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
            closing = True
            muted = True
            clear_queue("会话结束")
            await manager.stop()
            if foreground and not foreground.done():
                notify("正在等待原请求收尾并保存回执…")
                await foreground
            discard_confirmation(session)
            if callable(previous_progress):
                session.progress = previous_progress  # type: ignore[attr-defined]
            if hasattr(session, "stream_output"):
                session.stream_output = previous_stream
    return 2 if unavailable else 0
