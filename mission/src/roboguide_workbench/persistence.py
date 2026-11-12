"""Private atomic session files with exclusive local ownership and scope binding."""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from pathlib import Path

from mission.models import JSONObject

from roboguide_workbench.client import WorkbenchError, decode_object

LEGACY_SCHEMA = "roboguide.workbench-session/v0.2"
SCHEMA = "roboguide.workbench-session/v0.3"
MAX_SESSION_BYTES = 8 * 1024 * 1024


class SessionStore:
    """Own one private session and reject concurrent or cross-origin continuation."""

    def __init__(self, directory: Path, session_id: str, scope: JSONObject, resume: bool) -> None:
        """Acquire a non-following exclusive lock before reading or creating session data."""
        migrated_name = bool(re.fullmatch(r"[A-Za-z0-9_-]{77,80}-v03", session_id))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", session_id) and not (resume and migrated_name):
            raise WorkbenchError("会话 ID 只能包含 1–80 位字母、数字、下划线或横线。")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if directory.is_symlink() or directory.stat().st_uid != os.getuid():
            raise WorkbenchError("会话目录必须由当前用户持有且不是符号链接。")
        if directory.stat().st_mode & 0o077:
            raise WorkbenchError("会话目录权限必须为 0700。")
        self.path = directory / (session_id + ".json")
        self.lock = os.open(
            directory / (session_id + ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if resume:
                descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(descriptor, "rb") as source:
                    status = os.fstat(source.fileno())
                    if status.st_uid != os.getuid() or status.st_mode & 0o077:
                        raise WorkbenchError("会话文件必须由当前用户持有且权限为 0600。")
                    raw = source.read(MAX_SESSION_BYTES + 1)
                if len(raw) > MAX_SESSION_BYTES:
                    raise WorkbenchError("会话超过读取上限。")
                self.data = decode_object(raw)
                if (
                    self.data.get("schema") not in {SCHEMA, LEGACY_SCHEMA}
                    or self.data.get("scope") != scope
                    or (migrated_name and self.data.get("schema") != SCHEMA)
                ):
                    raise WorkbenchError("会话来源或身份不匹配；请使用原连接配置或新建会话。")
                if (
                    set(self.data) != {"schema", "scope", "context", "messages", "commands"}
                    or not isinstance(self.data["context"], dict)
                    or not isinstance(self.data["messages"], list)
                    or not isinstance(self.data["commands"], list)
                ):
                    raise WorkbenchError("会话结构损坏。")
                if self.data["schema"] == LEGACY_SCHEMA:
                    # Keep the original file byte-for-byte and acquire the new file's own lease.
                    migrated = self.path.with_name(session_id + "-v03.json")
                    new_lock = os.open(
                        directory / (session_id + "-v03.lock"),
                        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                        0o600,
                    )
                    try:
                        fcntl.flock(new_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        if migrated.exists():
                            raise WorkbenchError("已存在升级副本，请恢复 " + session_id + "-v03")
                    except BaseException:
                        os.close(new_lock)
                        raise
                    os.close(self.lock)
                    self.lock = new_lock
                    self.path = migrated
                    self.data["schema"] = SCHEMA
                    self.save()

            else:
                if self.path.exists():
                    raise WorkbenchError("会话已存在，请使用 --resume。")
                self.data = {
                    "schema": SCHEMA,
                    "scope": scope,
                    "context": {},
                    "messages": [],
                    "commands": [],
                }
                self.save()
        except BaseException:
            os.close(self.lock)
            raise

    def save(self) -> None:
        """Durably publish complete local state before dependent network effects may begin."""
        raw = json.dumps(self.data, ensure_ascii=False, allow_nan=False).encode()
        if len(raw) > MAX_SESSION_BYTES:
            raise WorkbenchError("会话记录已满，请新建会话；未发出新命令。")
        descriptor, temporary = tempfile.mkstemp(prefix=".session-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def close(self) -> None:
        """Release this process's session lease without deleting any original history."""
        if self.lock >= 0:
            os.close(self.lock)
            self.lock = -1

    def adopt(self, other: SessionStore) -> None:
        """Transfer an already acquired session lease while retaining the frontend's Store owner."""
        if other.lock < 0 or other is self:
            raise WorkbenchError("新会话租约无效。")
        self.close()
        self.path, self.data, self.lock = other.path, other.data, other.lock
        other.lock = -1
