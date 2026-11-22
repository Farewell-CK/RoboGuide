"""Nonblocking terminal input with bounded independent read-only Mission watches."""

from __future__ import annotations

import asyncio
import copy
import inspect
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
from prompt_toolkit.data_structures import Size
from prompt_toolkit.filters import Condition, is_done
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import ConditionalContainer, FormattedTextControl, HSplit, Layout, Window
from prompt_toolkit.output.vt100 import Vt100_Output
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth

from roboguide_workbench.activity import TurnActivity, activity_summary, displayed_activity
from roboguide_workbench.answers import bounded_history
from roboguide_workbench.display import DisplayPersistenceError, DisplayTurn, discard_confirmation
from roboguide_workbench.evidence import QueryStatus
from roboguide_workbench.terminal_tools import (
    COMMAND_HINTS,
    DetailPager,
    WorkbenchCompleter,
    wrap_cells,
)
from roboguide_workbench.views import render_mission, safe_text
from roboguide_workbench.workspace import EXTRA_HELP, WorkbenchSession

COMMANDS = list(COMMAND_HINTS)

# A short queue keeps a fast typist from losing questions while the provider or
# Controller is working.  It is deliberately local to this terminal process;
# queued text is not a new permission to perform a Mission effect.
MAX_QUEUED_REQUESTS = 3
SLOW_NOTICE_DELAYS = (15.0, 30.0)
PREVIEW_INTERVAL = 0.15
PREVIEW_TAIL_CHARS = 1024
PREVIEW_MAX_LINES = 3

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
        """Notify when the displayed Mission changes and drain the current GET on cancel."""
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
                if rendered != prior:
                    self.emit("任务更新 · " + rendered)
                    prior = rendered
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


def _message_preview(text: str, width: int = 80) -> str:
    """Fit an unsent-message preview by terminal cells without altering the queued original."""
    text = safe_text(" ".join(text.split()))
    if get_cwidth(text) <= width:
        return text
    result = ""
    for char in text:
        if get_cwidth(result + char) > max(0, width - 1):
            break
        result += char
    return result + "…"


def _mount_composer_context(
    prompt: PromptSession[str],
    status: Callable[[], str],
    preview: Callable[[], str],
    detail: Callable[[], str],
) -> None:
    """Keep live context out of input and scrollback, including when CPR is disabled."""
    layout = getattr(prompt, "layout", None)
    if not isinstance(layout, Layout):
        return
    layout.container = HSplit(
        [
            ConditionalContainer(
                Window(FormattedTextControl(detail), dont_extend_height=True),
                filter=~is_done & Condition(lambda: bool(detail())),
            ),
            ConditionalContainer(
                Window(
                    FormattedTextControl(preview),
                    dont_extend_height=True,
                ),
                filter=~is_done & Condition(lambda: bool(preview())),
            ),
            ConditionalContainer(
                Window(FormattedTextControl(status), style="class:composer-status", height=1),
                filter=~is_done,
            ),
            layout.container,
        ]
    )


async def interactive(session: WorkbenchSession) -> int:
    """Run a scrollback-friendly chat shell with serialized, bounded foreground work."""
    keys = KeyBindings()
    foreground: asyncio.Task[None] | None = None
    queued: deque[tuple[str, JSONObject, list[JSONObject]]] = deque()
    visible_references = session.query_references()
    visible_history = bounded_history(session.store.data.get("messages"))
    available_details = displayed_activity(session.store.data)
    pager: DetailPager | None = None
    retained_input = ""
    muted = False
    unavailable = False
    closing = False
    output = sys.stdout
    started_at: float | None = None
    phase = "待命"
    loop = asyncio.get_running_loop()
    stream_tail = ""
    preview_timer: asyncio.TimerHandle | None = None
    visible_preview = ""

    @keys.add("enter")
    def submit_line(event: KeyPressEvent) -> None:
        """Accept a highlighted completion into the draft before any submission."""
        if pager is not None:
            return
        buffer = event.app.current_buffer
        completion = buffer.complete_state
        if completion is not None and completion.current_completion is not None:
            buffer.apply_completion(completion.current_completion)
            return
        buffer.validate_and_handle()

    @keys.add("escape", "enter")
    def insert_line_break(event: KeyPressEvent) -> None:
        """Insert a line break with Alt/Meta-Enter while keeping Enter as submit."""
        if pager is None:
            event.app.current_buffer.insert_text("\n")

    @keys.add("c-j")
    def submit_control_j(event: KeyPressEvent) -> None:
        """Follow the same completion and submission rules for Ctrl-J."""
        submit_line(event)

    @keys.add("f1")
    def choose_first(event: KeyPressEvent) -> None:
        """Choose the displayed planning or single-action option with an explicit key press."""
        if pager is not None or (foreground and not foreground.done()):
            return
        proposal = session.suggestion
        if proposal:
            selected = "plan" if proposal[0] in {"plan", "run"} else proposal[0]
            event.app.current_buffer.text = "/choose " + selected
            event.app.current_buffer.validate_and_handle()

    @keys.add("f2")
    def choose_run(event: KeyPressEvent) -> None:
        """Choose execution only when that exact option is currently displayed."""
        if pager is not None or (foreground and not foreground.done()):
            return
        if session.suggestion and session.suggestion[0] in {"plan", "run"}:
            event.app.current_buffer.text = "/choose run"
            event.app.current_buffer.validate_and_handle()

    @keys.add("escape", "up")
    def edit_pending(event: KeyPressEvent) -> None:
        """Take the last unsent message back into an empty composer without executing it."""
        if pager is None and queued and not event.app.current_buffer.text:
            text, _, _ = queued.pop()
            event.app.current_buffer.text = text
            event.app.current_buffer.cursor_position = len(text)
            refresh_composer()

    @keys.add("escape", "backspace")
    def delete_pending(event: KeyPressEvent) -> None:
        """Withdraw only the last unsent message; the running operation keeps its identity."""
        if pager is None and queued:
            text, _, _ = queued.pop()
            notify("已撤回待发消息：" + safe_text(text))
            refresh_composer()

    detail_open = Condition(lambda: pager is not None)

    def open_details() -> None:
        """Open only the already acknowledged snapshot, leaving any draft intact."""
        nonlocal pager
        if available_details:
            pager = DetailPager(available_details)
            refresh_composer()
        else:
            notify("本会话暂无可展开的工具记录。")

    @keys.add("f3")
    def toggle_details(event: KeyPressEvent) -> None:
        """Toggle the local details panel without changing input or session history."""
        nonlocal pager
        if pager is None:
            event.app.current_buffer.cancel_completion()
            open_details()
        else:
            pager = None
            refresh_composer()

    @keys.add("escape", filter=detail_open, eager=True)
    def close_details(event: KeyPressEvent) -> None:
        """Return to the original composer and cursor position."""
        nonlocal pager
        pager = None
        refresh_composer()

    @keys.add("up", filter=detail_open)
    @keys.add("down", filter=detail_open)
    @keys.add("pageup", filter=detail_open)
    @keys.add("pagedown", filter=detail_open)
    def scroll_details(event: KeyPressEvent) -> None:
        """Scroll only the detail panel while the draft stays in the original buffer."""
        if pager is not None:
            size = terminal_size()
            steps: dict[Keys | str, int] = {
                Keys.Up: -1,
                Keys.Down: 1,
                Keys.PageUp: -pager.page_size(size.rows),
                Keys.PageDown: pager.page_size(size.rows),
            }
            pager.render(size.columns, size.rows, steps[event.key_sequence[-1].key])
            refresh_composer()

    @keys.add("left", filter=detail_open)
    @keys.add("right", filter=detail_open)
    def browse_turns(event: KeyPressEvent) -> None:
        """Select an older or newer acknowledged turn using the same local snapshot."""
        if pager is not None:
            pager.change_turn(1 if event.key_sequence[-1].key == Keys.Left else -1)
            refresh_composer()

    completer = WorkbenchCompleter(lambda: visible_references)
    # Keep the constructor surface used by the plain test harness.  The live
    # session receives the richer multiline behavior through attributes below.
    try:
        # Keep the production composer genuinely multiline.  The small fallback retains
        # compatibility with the repository's minimal PromptSession test doubles.
        prompt: PromptSession[str] = PromptSession(
            multiline=True,
            completer=completer,
            key_bindings=keys,
        )
    except TypeError:
        prompt = PromptSession(
            completer=completer,
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
            "composer-status": "bold",
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

    def terminal_size() -> Size:
        """Locate the current viewport for the composer and bounded display."""
        with suppress(AttributeError, OSError, ValueError):
            return prompt.output.get_size()
        return Size(rows=24, columns=100)

    def composer_status() -> str:
        """Keep the phase and queued count visible before fitting optional keyboard hints."""
        width = max(1, terminal_size().columns - 1)
        if started_at is None:
            value = "就绪"
            hints = ["Enter 发送", "/help"]
            if available_details:
                hints.append("F3 详情")
            else:
                hints.append("Alt-Enter 换行")
            if session.last_state:
                hints.append(f"状态对象 {safe_text(session.last_state)}")
        elif muted:
            value, hints = "正在收尾", []
        else:
            value = "正在回复" if visible_preview else phase
            hints = ["Ctrl-C 停止显示"]
        if queued:
            value += f" · 待发 {len(queued)}/{MAX_QUEUED_REQUESTS}"
        for hint in hints:
            candidate = f"{value} · {hint}"
            if get_cwidth(candidate) <= width:
                value = candidate
        return _message_preview(value, width)

    def preview_text() -> str:
        """Show only the newest few public lines above an independently editable draft."""
        if pager is not None or not visible_preview.strip() or muted or closing:
            return ""
        size = terminal_size()
        if size.rows < 8:
            return ""
        width = max(2, min(160, size.columns - 1))
        lines = [
            chunk
            for line in visible_preview.rstrip().split("\n")
            for chunk in wrap_cells(safe_text(line), width)
        ]
        return "\n".join(lines[-min(PREVIEW_MAX_LINES, size.rows // 6) :])

    def detail_text() -> str:
        """Render the panel inside the live prompt, separate from completed scrollback."""
        if pager is None:
            return ""
        size = terminal_size()
        return pager.render(size.columns, size.rows)

    _mount_composer_context(prompt, composer_status, preview_text, detail_text)

    def refresh_composer() -> None:
        """Request a redraw after progress arrives from the worker thread."""
        with suppress(RuntimeError, AttributeError):
            loop.call_soon_threadsafe(prompt.app.invalidate)

    def update_progress(message: str) -> None:
        """Update the input status without adding routine phases to scrollback."""

        def record() -> None:
            """Publish a changed phase on the terminal owner without exposing raw tool messages."""
            nonlocal phase
            if message != phase and not muted and not closing:
                phase = message
                refresh_composer()

        loop.call_soon_threadsafe(record)

    previous_progress = getattr(session, "progress", None)
    session.progress = update_progress  # type: ignore[attr-defined]
    previous_stream = getattr(session, "stream_output", None)

    def notify(message: str) -> None:
        """Keep status output visible without interrupting the drain of original operations."""
        try:
            # Notifications carry no confirmation authority and use patch_stdout so a
            # watch update cannot overwrite the live input buffer.
            print("\n".join(safe_text(line) for line in message.split("\n")), flush=True)
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
            "  直接提问：有哪些设备？它还有多少电？\n  也可直接说目标：让狗1巡检东区，先只规划。\n",
            file=output,
            flush=True,
        )

    manager = WatchManager(session, notify)

    def clear_preview() -> None:
        """Drop transient text before committing the authoritative response to scrollback."""
        nonlocal preview_timer, stream_tail, visible_preview
        if preview_timer is not None:
            preview_timer.cancel()
            preview_timer = None
        stream_tail = visible_preview = ""

    def publish_preview() -> None:
        """Refresh the live composer after a short batch of public deltas arrives."""
        nonlocal preview_timer, visible_preview
        preview_timer = None
        if not muted and not closing:
            visible_preview = stream_tail
            refresh_composer()

    def update_stream(delta: str) -> None:
        """Batch a bounded text tail without delaying a short fragment until final response."""

        def record() -> None:
            """Schedule at most one preview redraw per interval on the terminal owner."""
            nonlocal preview_timer, stream_tail
            if not delta or muted or closing or started_at is None:
                return
            stream_tail = (stream_tail + delta)[-PREVIEW_TAIL_CHARS:]
            if preview_timer is None:
                preview_timer = loop.call_later(PREVIEW_INTERVAL, publish_preview)

        loop.call_soon_threadsafe(record)

    if hasattr(session, "stream_output"):
        session.stream_output = update_stream

    async def display(
        result: str,
        suggestion: tuple[str, str] | None = None,
        *,
        status: QueryStatus = "complete",
        activity_line: str = "",
    ) -> bool:
        """Flush a role-labeled, wrapped assistant turn before acknowledging its state."""

        def write() -> bool:
            """Flush the captured real stream while terminal input is safely suspended."""
            if muted:
                return False
            width = max(20, min(160, terminal_size().columns))
            label = {"complete": "", "partial": " · 部分结果", "failed": " · 本轮回复未完成"}[
                status
            ]
            print("\n── RG · 助手" + label, file=output, flush=True)
            lines = result.splitlines() or [""]
            first_line = True
            for line in lines:
                # Wrap long evidence lines so the scrollback retains the complete bounded
                # projection; a visual width limit must never turn facts into an ellipsis.
                chunks = wrap_cells(safe_text(line), max(8, width - 5))
                for chunk in chunks:
                    print(("RG > " if first_line else "     ") + chunk, file=output, flush=True)
                    first_line = False
            if activity_line:
                for chunk in wrap_cells(activity_line + " · F3 详情", width - 1):
                    print(chunk, file=output, flush=True)
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
        nonlocal visible_references, visible_history, available_details
        started_at, phase = time.monotonic(), "处理中"
        refresh_composer()
        turn = DisplayTurn(session)

        def sanitize_activity(text: str) -> str:
            """Use the existing local evidence redactor for every retained detail."""
            from roboguide_workbench.feedback import redact_activity_text

            return redact_activity_text(text, getattr(session.router, "key", "") or "")

        trace = TurnActivity(turn.turn_id, sanitize_activity)
        session.activity = trace
        session.input_references = original_references
        session.input_history = original_history

        async def slow_feedback() -> None:
            """Print occasional real waiting notices without a ticking redraw or fake progress."""
            for delay in SLOW_NOTICE_DELAYS:
                await asyncio.sleep(delay)
                if not muted and not closing and started_at is not None:
                    notify(f"· {phase} · 已等待 {time.monotonic() - started_at:.0f}s，仍在处理。")

        waiting = asyncio.create_task(slow_feedback())
        try:
            result = await asyncio.to_thread(session.ask, text)
            clear_preview()
            turn.stage()
            if not muted:
                displayed = await display(
                    trace.reply or result,
                    turn.suggestion,
                    status=session.query_status,
                    activity_line=activity_summary(trace),
                )
                if displayed and not muted:
                    turn.acknowledge()
        except (ValueError, OSError) as error:
            clear_preview()
            if not muted:
                try:
                    await display("操作未完成：" + safe_text(str(error)), status="failed")
                except (ValueError, OSError):
                    pass
        finally:
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
            clear_preview()
            session.input_references = None
            session.input_history = None
            session.activity = None
            try:
                turn.reject()
            except DisplayPersistenceError as error:
                unavailable = True
                notify("会话已暂停：" + safe_text(str(error)))
            muted = False
            visible_references = session.query_references()
            visible_history = bounded_history(session.store.data.get("messages"))
            available_details = displayed_activity(session.store.data)
            started_at, phase = None, "待命"
            refresh_composer()
            if not closing and not unavailable and queued:
                next_text, original_references, original_history = queued.popleft()
                notify(
                    f"继续处理队列中的问题（剩余 {len(queued)} 条）。\n"
                    + "待发问题："
                    + _message_preview(next_text, 100)
                )
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
            width = max(20, min(160, terminal_size().columns))
            lines = [
                f"待发 {index}. {_message_preview(item[0], width - 12)}"
                for index, item in enumerate(queued, 1)
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
                        clear_preview()
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
                if text == "/details":
                    open_details()
                    continue
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
                    refresh_composer()
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
                if foreground and not foreground.done() and text in {"/help", "/status"}:
                    if text == "/help":
                        notify(EXTRA_HELP)
                    else:
                        elapsed = time.monotonic() - started_at if started_at is not None else 0.0
                        notify(
                            f"会话 {_session_label()} · {phase} · 已等待 {elapsed:.1f}s"
                            f" · 待发 {len(queued)}/{MAX_QUEUED_REQUESTS}"
                        )
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
                        available_details = displayed_activity(session.store.data)
                        pager = None
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
