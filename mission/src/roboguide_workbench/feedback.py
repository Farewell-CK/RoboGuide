"""Create bounded private feedback archives from the current local workbench evidence only."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import stat
import uuid
import zipfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import parse_qsl, quote, quote_plus, urlencode, urlsplit, urlunsplit

from mission.models import JSONObject, JSONValue

from roboguide_workbench.client import WorkbenchError
from roboguide_workbench.routing import ResponsesRouter
from roboguide_workbench.workspace import WorkbenchSession

FEEDBACK_SCHEMA = "roboguide.feedback/v0.1"
MAX_ENTRY_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = 4 * 1024 * 1024
MAX_TEXT_BYTES = 16 * 1024
MAX_LIST_ITEMS = 128
MAX_DEPTH = 16
MAX_NODES = 20_000
MAX_OMISSIONS = 128
MAX_JSON_TEXT_BYTES = 1024 * 1024
MAX_JSON_TEXT_SEGMENTS = 128
REDACTED = "[REDACTED]"
_JSON_START = re.compile(r'[\[{"]|\\+u0022', re.IGNORECASE)
_ESCAPED_JSON_START = re.compile(
    r'[\[{](?:\s|\\+(?:[nrt]|u0020|u000[9ad]))*\\+(?:"|u0022)', re.IGNORECASE
)
_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_PEM = re.compile(
    r"-----BEGIN ([A-Z0-9 ]*(?:PRIVATE KEY|CERTIFICATE))-----"
    r".*?(?:-----END \1-----|\Z)",
    re.DOTALL,
)
_AUTH_HEADER = re.compile(
    r"(?im)((?:proxy-authorization|authorization|set-cookie|cookie|x-api-key)\s*[:=]\s*)"
    r"[^\r\n]+"
)
_QUOTED_CREDENTIAL_VALUE = (
    r'"(?:\\.|[^"\\\r\n])*(?:"|\\?(?=\r?$))'
    r"|'(?:\\.|[^'\\\r\n])*(?:'|\\?(?=\r?$))"
)
_CLI_CREDENTIAL = re.compile(
    r"(?im)(--(?:api-key|access-token|refresh-token|token|password|client-secret)(?:=|\s+))"
    r"(?:" + _QUOTED_CREDENTIAL_VALUE + r"|[^\s]+)"
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?im)((?:[\w.-]*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|"
    r"client[_-]?(?:secret|key)|private[_-]?key|token|password|passwd|authorization|"
    r"credential|secret|cookie)[\w.-]*|key)"
    r"[\"']?\s*[:=]\s*)(?:" + _QUOTED_CREDENTIAL_VALUE + r"|[^\s,;}]+)"
)
_SK_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}")
_AUTH_TOKEN = re.compile(r"(?i)\b(?:Bearer|Basic)\s+[^\s,;\"']+")


class _Redactor:
    """Copy and bound JSON evidence while recording all redactions and omitted material."""

    def __init__(self, secrets: Iterable[str]) -> None:
        """Keep only explicitly supplied credential values and never inspect process environment."""
        supplied = {value for value in secrets if value}
        variants = {
            variant
            for value in supplied
            for variant in (
                value,
                quote(value, safe=""),
                quote_plus(value),
                json.dumps(value)[1:-1],
            )
        }
        self.secrets = sorted(variants, key=len, reverse=True)
        self.redactions = 0
        self.omissions: list[JSONObject] = []
        self.extra_omissions = 0
        self.nodes = 0

    def omit(self, path: str, reason: str, count: int | None = None) -> None:
        """Retain bounded explicit gaps without storing omitted source content."""
        if len(self.omissions) >= MAX_OMISSIONS:
            self.extra_omissions += 1
            return
        entry: JSONObject = {"path": path, "reason": reason}
        if count is not None:
            entry["omitted_count"] = count
        self.omissions.append(entry)

    def text(self, value: str, path: str = "text", depth: int = 0) -> str:
        """Scrub encoded JSON and known/common credentials before exporting original log text."""
        if depth > MAX_DEPTH or len(value.encode("utf-8", errors="backslashreplace")) > (
            MAX_JSON_TEXT_BYTES
        ):
            self.omit(path, "text_redaction_budget_exceeded")
            return "[OMITTED]"
        value = self._embedded_json(value, path, depth)
        for secret in self.secrets:
            value, count = re.subn(re.escape(secret), lambda match: REDACTED, value)
            self.redactions += count
        for pattern, replacement in (
            (_PEM, REDACTED),
            (_SK_KEY, REDACTED),
            (_AUTH_HEADER, r"\1" + REDACTED),
            (_AUTH_TOKEN, REDACTED),
            (_CLI_CREDENTIAL, r"\1" + REDACTED),
        ):
            value, count = pattern.subn(replacement, value)
            self.redactions += count
        value, count = _CREDENTIAL_ASSIGNMENT.subn(self._assignment, value)
        self.redactions += count
        return _URL.sub(self._url, value)

    @staticmethod
    def _assignment(match: re.Match[str]) -> str:
        """Retain value quoting so a sanitized embedded JSON document stays parseable."""
        prefix = match.group(1)
        original_value = match.group()[len(prefix) :]
        if original_value.startswith(('"', "'")):
            return prefix + original_value[0] + REDACTED + original_value[0]
        return prefix + REDACTED

    def _escaped_json(self, fragment: str, path: str, depth: int) -> str:
        """Decode only JSON string escapes in one complete bounded encoded log fragment."""
        # Host logs may contain a JSON fragment with its outer string quotes removed.
        # A JSON decoder handles quotes, backslashes and Unicode consistently; malformed
        # fragments are omitted instead of falling back to their potentially secret text.
        try:
            decoded: object = json.loads('"' + fragment + '"')
            if not isinstance(decoded, str) or decoded == fragment:
                raise ValueError("not a distinct encoded JSON fragment")
        except (ValueError, RecursionError):
            self.omit(path, "encoded_json_unavailable")
            return "[OMITTED ENCODED JSON]"
        sanitized = self.text(decoded, path, depth + 1)
        return json.dumps(sanitized, ensure_ascii=True)[1:-1]

    def _embedded_json(self, value: str, path: str, depth: int) -> str:
        """Scrub complete embedded documents and recursively serialized string values."""
        decoder = json.JSONDecoder()
        cursor = 0
        pieces: list[str] = []
        count = 0
        while marker := _JSON_START.search(value, cursor):
            count += 1
            if count > MAX_JSON_TEXT_SEGMENTS:
                self.omit(path, "json_segment_budget_exceeded")
                return "[OMITTED]"
            start = marker.start()
            try:
                decoded, end = decoder.raw_decode(value, start)
            except (ValueError, RecursionError):
                encoded_start = start
                if value[start] == '"':
                    while encoded_start > cursor and value[encoded_start - 1] == "\\":
                        encoded_start -= 1
                if (
                    _ESCAPED_JSON_START.match(value, start)
                    or value[start] == "\\"
                    or encoded_start < start
                ):
                    end = value.find("\n", start)
                    end = len(value) if end < 0 else end
                    fragment = value[encoded_start:end].rstrip("\r")
                    pieces.extend(
                        (value[cursor:encoded_start], self._escaped_json(fragment, path, depth))
                    )
                    cursor = encoded_start + len(fragment)
                    continue
                pieces.append(value[cursor : start + 1])
                cursor = start + 1
                continue
            sanitized = self.clean(decoded, path + ".encoded_json", depth + 1)
            pieces.extend(
                (value[cursor:start], json.dumps(sanitized, ensure_ascii=True, allow_nan=False))
            )
            cursor = end
        pieces.append(value[cursor:])
        return "".join(pieces)

    def _url(self, match: re.Match[str]) -> str:
        """Keep URL origin/path but omit userinfo, query values and fragments from diagnostics."""
        value = match.group()
        try:
            parsed = urlsplit(value)
            authority = parsed.netloc
            if "@" in authority:
                authority = REDACTED + "@" + authority.rsplit("@", 1)[1]
                self.redactions += 1
            query = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=128)
            if query:
                self.redactions += len(query)
            if parsed.fragment:
                self.redactions += 1
            return urlunsplit(
                (
                    parsed.scheme,
                    authority,
                    parsed.path,
                    urlencode([(key, REDACTED) for key, _ in query]),
                    REDACTED if parsed.fragment else "",
                )
            )
        except ValueError:
            self.redactions += 1
            return "[REDACTED_URL]"

    @staticmethod
    def _sensitive_key(key: str) -> bool:
        """Redact credential fields while retaining original idempotency command identities."""
        key = re.sub(r"[^a-z0-9]", "", key.lower())
        if key in {"idempotencykey", "requestdigest", "draftdigest"}:
            return False
        return key in {"key", "token", "cookie", "setcookie", "clientkey", "privatekey"} or any(
            part in key
            for part in (
                "apikey",
                "accesstoken",
                "refreshtoken",
                "authtoken",
                "clientsecret",
                "privatekey",
                "token",
                "password",
                "passwd",
                "authorization",
                "credential",
                "secret",
            )
        )

    def clean(self, value: object, path: str, depth: int = 0) -> JSONValue:
        """Copy bounded values; never mutate or reinterpret original command/evidence documents."""
        self.nodes += 1
        if depth > MAX_DEPTH or self.nodes > MAX_NODES:
            self.omit(path, "structure_budget_exceeded")
            return "[OMITTED]"
        if value is None or isinstance(value, bool | int):
            return value
        if isinstance(value, float):
            if math.isfinite(value):
                return value
            self.omit(path, "nonfinite_number")
            return "[UNAVAILABLE]"
        if isinstance(value, str):
            text = self.text(value, path, depth)
            encoded = text.encode("utf-8", errors="backslashreplace")
            if len(encoded) > MAX_TEXT_BYTES:
                self.omit(path, "text_truncated", len(encoded) - MAX_TEXT_BYTES)
                return encoded[:MAX_TEXT_BYTES].decode("utf-8", errors="ignore") + "…[TRUNCATED]"
            return text
        if isinstance(value, list | tuple):
            if len(value) > MAX_LIST_ITEMS:
                self.omit(path, "oldest_items_omitted", len(value) - MAX_LIST_ITEMS)
            return [
                self.clean(item, f"{path}[{index}]", depth + 1)
                for index, item in enumerate(
                    value[-MAX_LIST_ITEMS:], max(0, len(value) - MAX_LIST_ITEMS)
                )
            ]
        if isinstance(value, dict):
            if len(value) > MAX_LIST_ITEMS:
                self.omit(path, "fields_omitted", len(value) - MAX_LIST_ITEMS)
            result: JSONObject = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= MAX_LIST_ITEMS:
                    break
                if not isinstance(key, str):
                    self.omit(path, "nontext_field")
                    continue
                safe_key = self.text(key, path + ".field", depth + 1)[:256]
                if self._sensitive_key(key):
                    result[safe_key] = REDACTED
                    self.redactions += 1
                else:
                    result[safe_key] = self.clean(item, path + "." + safe_key, depth + 1)
            return result
        self.omit(path, "unsupported_value")
        return "[UNAVAILABLE]"


def _json(value: object) -> bytes:
    """Encode finite diagnostic JSON without depending on the host terminal encoding."""
    return json.dumps(value, ensure_ascii=True, allow_nan=False, indent=2).encode()


def _runtime(session: WorkbenchSession) -> JSONObject:
    """Project fixed nonsecret runtime metadata without opening config or credential files."""
    router = session.router
    model: JSONObject = {"availability": "not_loaded"}
    if isinstance(router, ResponsesRouter):
        model = {
            "availability": "loaded",
            "model": router.settings.llm.model,
            "provider": router.settings.provider.name,
            "authentication_configured": bool(router.key),
        }
    return {
        "python": platform.python_version(),
        "platform": platform.system(),
        "platform_release": platform.release(),
        "session_id": session.store.path.stem,
        "controller": {
            "endpoint": session.controller.endpoint,
            "tenant": session.controller.tenant,
            "timeout_seconds": session.controller.timeout,
        },
        "mission_service": {
            "endpoint": session.actions.connection.endpoint,
            "tenant": session.actions.connection.tenant,
            "timeout_seconds": session.actions.connection.timeout,
        },
        "model": model,
    }


def _entries(session: WorkbenchSession, redactor: _Redactor, created_at: str) -> dict[str, bytes]:
    """Render only the current session and observations with explicit per-entry omissions."""
    sources: dict[str, object] = {
        "session.json": session.store.data,
        "observations.json": {
            "last_read_at": session.last_read_at,
            "last_raw": session.last_raw,
            "last_evidence": getattr(session, "evidence", []),
            "query_status": session.query_status,
        },
        "runtime.json": _runtime(session),
    }
    entries: dict[str, bytes] = {}
    for name, source in sources.items():
        raw = _json(redactor.clean(source, name))
        if len(raw) > MAX_ENTRY_BYTES:
            redactor.omit(name, "entry_byte_budget_exceeded", len(raw))
            raw = _json({"availability": "omitted", "reason": "entry_byte_budget_exceeded"})
        entries[name] = raw
    session_copy = cast(JSONObject, json.loads(entries["session.json"]))
    messages = session_copy.get("messages")
    lines: list[str] = []
    if isinstance(messages, list):
        for message in messages:
            if isinstance(message, dict):
                lines.append(
                    f"{message.get('at', '')} {message.get('role', '')}: {message.get('text', '')}"
                )
    if lines:
        log = "\n".join(lines).encode("utf-8", errors="backslashreplace")
        if len(log) > MAX_ENTRY_BYTES:
            redactor.omit("conversation.log", "entry_byte_budget_exceeded", len(log))
        else:
            entries["conversation.log"] = log
    entries["README.txt"] = (
        b"RoboGuide local feedback bundle\n"
        b"Contains redacted conversation, command receipts, observations and runtime metadata.\n"
        b"No network request was made and no email was sent.\n"
        b"Remote Controller/Mission/Node log files were not collected.\n"
        b"Original session files and command outcomes are unchanged.\n"
        b"See manifest.json for bounds, omissions and hashes of the exported bytes.\n"
    )
    entries["manifest.json"] = _json(
        {
            "schema": FEEDBACK_SCHEMA,
            "created_at": created_at,
            "session_id": redactor.text(session.store.path.stem),
            "local_only": True,
            "redacted": True,
            "redaction_count": redactor.redactions,
            "remote_logs": "not_collected",
            "limits": {
                "entry_bytes": MAX_ENTRY_BYTES,
                "bundle_bytes": MAX_BUNDLE_BYTES,
                "text_bytes": MAX_TEXT_BYTES,
                "list_items": MAX_LIST_ITEMS,
                "structure_nodes": MAX_NODES,
                "structure_depth": MAX_DEPTH,
                "json_text_bytes": MAX_JSON_TEXT_BYTES,
                "json_text_segments": MAX_JSON_TEXT_SEGMENTS,
            },
            "omissions": redactor.omissions,
            "additional_omission_count": redactor.extra_omissions,
            "entries": {
                name: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                for name, raw in entries.items()
            },
        }
    )
    if sum(len(raw) for raw in entries.values()) > MAX_BUNDLE_BYTES:
        raise WorkbenchError("反馈包超过本地打包上限；原始日志未改动。")
    return entries


def _private_directory(path: Path) -> tuple[Path, int]:
    """Walk only real directories and retain an owned private output fd through publication."""
    if ".." in path.parts:
        raise WorkbenchError("反馈目录不能含父目录跳转。")
    absolute = path.absolute()
    descriptor = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in absolute.parts[1:]:
            try:
                child = os.open(
                    component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                )
            except FileNotFoundError:
                os.mkdir(component, mode=0o700, dir_fd=descriptor)
                child = os.open(
                    component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                )
            os.close(descriptor)
            descriptor = child
        status = os.fstat(descriptor)
        if status.st_uid != os.getuid() or stat.S_IMODE(status.st_mode) != 0o700:
            raise WorkbenchError("反馈目录必须由当前用户持有且权限为 0700。")
        return absolute, descriptor
    except BaseException:
        os.close(descriptor)
        raise


def create_feedback(
    session: WorkbenchSession,
    output_dir: Path | None = None,
    *,
    secrets: Iterable[str] = (),
) -> Path:
    """Publish one private redacted ZIP without network effects or original-session mutation."""
    known_secrets = list(secrets)
    if isinstance(session.router, ResponsesRouter) and session.router.key:
        known_secrets.append(session.router.key)
    redactor = _Redactor(known_secrets)
    created = datetime.now(UTC)
    entries = _entries(session, redactor, created.isoformat())
    directory, directory_fd = _private_directory(
        output_dir or session.store.path.parent / "feedback"
    )
    filename = "feedback-" + created.strftime("%Y%m%dT%H%M%S%fZ-") + uuid.uuid4().hex + ".zip"
    temporary = "." + filename + ".tmp"
    linked = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, raw in entries.items():
                    info = zipfile.ZipInfo(name, created.timetuple()[:6])
                    info.external_attr = (stat.S_IFREG | 0o600) << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, raw)
            output.flush()
            if os.fstat(output.fileno()).st_size > MAX_BUNDLE_BYTES:
                raise WorkbenchError("反馈包超过本地打包上限；原始日志未改动。")
            os.fsync(output.fileno())
        os.link(
            temporary,
            filename,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
        linked = True
        os.unlink(temporary, dir_fd=directory_fd)
        os.fsync(directory_fd)
        return directory / filename
    except BaseException:
        if linked:
            os.unlink(filename, dir_fd=directory_fd)
        raise
    finally:
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)
