"""Scope-bound local session navigation without discarding unresolved original commands."""

from __future__ import annotations

import os
import stat
import uuid
from typing import cast

from mission.models import JSONObject

from roboguide_workbench.actions import MissionActions, commands_clear
from roboguide_workbench.client import WorkbenchError, decode_object
from roboguide_workbench.persistence import MAX_SESSION_BYTES, SCHEMA, SessionStore
from roboguide_workbench.views import safe_text
from roboguide_workbench.workspace import WorkbenchSession


def pending_commands(session: WorkbenchSession) -> bool:
    """Retain every unknown command as a barrier to moving to a different local session."""
    return not commands_clear(session.store.data.get("commands"))


def list_sessions(session: WorkbenchSession) -> str:
    """List at most one hundred readable session headers matching the exact current scope."""
    lines = ["当前连接与身份范围内的会话（最多 100 个）："]
    count = 0
    for path in sorted(session.store.path.parent.glob("*.json"))[:1000]:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as source:
                metadata = os.fstat(source.fileno())
                if (
                    metadata.st_uid != os.getuid()
                    or metadata.st_mode & 0o077
                    or not stat.S_ISREG(metadata.st_mode)
                ):
                    continue
                raw = source.read(MAX_SESSION_BYTES + 1)
            if len(raw) > MAX_SESSION_BYTES:
                continue
            data = decode_object(raw)
            if data.get("scope") != session.store.data["scope"] or data.get("schema") != SCHEMA:
                continue
            context = data.get("context")
            if not isinstance(context, dict):
                continue
            marker = " · 当前" if path == session.store.path else ""
            lines.append(
                safe_text(path.stem)
                + marker
                + " · Request="
                + safe_text(str(context.get("request_id") or "无"))
            )
            count += 1
            if count == 100:
                break
        except (ValueError, OSError):
            continue
    return "\n".join(lines)


def navigate(session: WorkbenchSession, text: str) -> str | None:
    """Switch an idle frontend under original locks while keeping its owning Store object stable."""
    command, _, target = text.partition(" ")
    if command not in {"/new", "/resume"}:
        return None
    session.suggestion = None
    session.pending_cancel = None
    if pending_commands(session):
        raise WorkbenchError("原命令尚未确定，请先 /observe 或 /reconcile；不能切换会话绕过。")
    target = target.strip()
    if command == "/resume" and not target:
        raise WorkbenchError("用 /sessions 查看名称，再使用 /resume 名称。")
    target = target or uuid.uuid4().hex
    if target == session.store.path.stem:
        return "已在当前会话：" + safe_text(target)
    scope = cast(JSONObject, session.store.data["scope"])
    replacement = SessionStore(session.store.path.parent, target, scope, command == "/resume")
    try:
        fresh = type(session)(
            session.controller,
            session.router_factory,
            replacement,
            MissionActions(session.actions.connection, replacement),
        )
        for name in (
            "answer_mode",
            "tool_limit",
            "call_limit",
            "turn_seconds",
            "progress",
            "model_label",
            "config_path",
        ):
            if hasattr(session, name):
                setattr(fresh, name, getattr(session, name))
        session.checkpoint()
    except BaseException:
        replacement.close()
        raise
    original = session.store
    original.adopt(replacement)
    fresh.store = original
    fresh.actions.store = original
    session.__dict__.clear()
    session.__dict__.update(fresh.__dict__)
    return (
        "已切换会话："
        + safe_text(session.store.path.stem)
        + "；历史状态需重新查询，执行前先 /request。"
    )
