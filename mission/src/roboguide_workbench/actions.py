"""Mission commands with durable identities and observation-only recovery."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext, suppress
from datetime import datetime
from threading import Event, Lock, Thread
from typing import cast
from urllib.parse import quote, urlsplit

from mission.models import JSONObject
from mission.request_record import MissionRequestLifecycle, MissionRequestRecord
from mission.submission_evidence import canonical_plan_digest
from mission.submission_intent import INTENT_REQUEST_SCHEMA

from roboguide_workbench.client import ReadOnlyController, WorkbenchError, identifier, request_json
from roboguide_workbench.persistence import SessionStore


def controller_cancel_identity(command: object) -> bool:
    """Qualify the original local operation and exact Controller bytes before any observation."""
    if not isinstance(command, dict):
        return False
    target, group, origin, operation, accepted = (
        command.get("mission_id"),
        command.get("group_id"),
        command.get("origin"),
        command.get("operation_id"),
        command.get("accepted_at"),
    )
    if (
        command.get("service") != "controller"
        or command.get("action") != "cancel"
        or command.get("method") != "POST"
        or not isinstance(target, str)
        or not isinstance(group, str)
        or not isinstance(origin, str)
        or not isinstance(operation, str)
        or not isinstance(accepted, str)
        or command.get("body") != {}
        or command.get("request_digest") != "sha256:" + hashlib.sha256(b"{}").hexdigest()
    ):
        return False
    try:
        identifier(target)
        identifier(group)
        identifier(operation)
        if datetime.fromisoformat(accepted).utcoffset() is None:
            return False
        parsed = urlsplit(origin)
        if (
            quote(target, safe="") != target
            or command.get("path") != "/v1/missions/" + target + "/cancel"
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            return False
    except ValueError:
        return False
    return True


def _resolved_controller_cancel(command: dict[str, object]) -> bool:
    """Use a frozen exact terminal witness, retaining compatibility with original Cancelled rows."""
    if (
        not controller_cancel_identity(command)
        or command.get("state") != "resolved"
        or command.get("original_http_outcome") != "unknown"
    ):
        return False
    frozen = "resolution" in command
    observation = command.get("resolution") if frozen else command.get("observation")
    if (
        not isinstance(observation, dict)
        or observation.get("schema_version") != "roboguide.workbench-cancel-observation/v0.1"
        or any(
            observation.get(field) != command.get(field)
            for field in ("origin", "mission_id", "group_id", "original_http_outcome")
        )
        or not isinstance(observation.get("status"), str)
        or observation.get("status")
        not in ({"Cancelled", "Completed"} if frozen else {"Cancelled"})
    ):
        return False
    received = observation.get("received_at")
    if not isinstance(received, str):
        return False
    try:
        if datetime.fromisoformat(received).utcoffset() is None:
            return False
    except ValueError:
        return False
    if frozen and any(
        observation.get(field) != command.get(field)
        for field in ("operation_id", "path", "request_digest", "accepted_at")
    ):
        return False
    return True


def commands_clear(commands: object) -> bool:
    """Accept completed receipts or exact locally resolved Controller cancellation observations."""
    if not isinstance(commands, list):
        return False
    return all(
        isinstance(command, dict)
        and (command.get("state") == "complete" or _resolved_controller_cancel(command))
        for command in commands
    )


def checked_request(raw: JSONObject) -> MissionRequestRecord:
    """Require explicit v0.5 intent; an old server cannot silently downgrade a plan-only request."""
    if raw.get("schema_version") != INTENT_REQUEST_SCHEMA:
        raise WorkbenchError("Mission Service 未返回显式提交意图合同，保留原命令等待核对。")
    try:
        record = MissionRequestRecord.from_json(raw)
        if (
            record.plan is not None
            and canonical_plan_digest(record.plan.to_json()) != record.draft_digest
        ):
            raise WorkbenchError("计划内容与原摘要不匹配。")
        return record
    except (ValueError, RuntimeError, TypeError, KeyError) as error:
        raise WorkbenchError("Mission Request 响应无效。") from error


class MissionActions:
    """Send only host-selected commands, preserving intent before the first HTTP byte."""

    def __init__(self, connection: ReadOnlyController, store: SessionStore) -> None:
        """Reuse the fixed Mission origin's independent TLS identity and local command journal."""
        self.connection = connection
        self.store = store

    def _post(
        self,
        path: str,
        body: JSONObject,
        key: str | None = None,
        *,
        timeout: float | None = None,
    ) -> JSONObject:
        """Send one command or original observation, without automatic replay or new keys."""
        headers = {"Content-Type": "application/json"}
        if self.connection.tenant is not None:
            headers["X-RoboGuide-Tenant"] = self.connection.tenant
        if key is not None:
            headers["Idempotency-Key"] = key
        return request_json(
            self.connection.endpoint + path,
            "POST",
            headers,
            json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(),
            self.connection.timeout if timeout is None else timeout,
            self.connection.context,
            (200, 201, 202),
        )

    def get(self, request_id: str) -> JSONObject:
        """Refresh a known Request without creating, approving or submitting anything."""
        raw = self.connection._get("/v1/mission-requests/" + quote(identifier(request_id), safe=""))
        record = checked_request(raw)
        if record.request_id != request_id:
            raise WorkbenchError("Mission Request 身份不匹配。")
        return raw

    def command(
        self,
        action: str,
        body: JSONObject,
        request_id: str | None = None,
        *,
        progress: Callable[[MissionRequestLifecycle], None] | None = None,
    ) -> JSONObject:
        """Persist the original command; unresolved commands block further mutations."""
        if action not in {"create", "messages", "execute", "approve", "cancel"}:
            raise WorkbenchError("未开放此 Mission 操作。")
        commands = self.store.data.get("commands")
        if not isinstance(commands, list):
            raise WorkbenchError("会话命令日志损坏。")
        if not commands_clear(commands):
            raise WorkbenchError("原命令结果尚未确定，请先 /observe；不会重发或创建替代请求。")
        path = "/v1/mission-requests"
        if action != "create":
            if request_id is None:
                raise WorkbenchError("缺少当前 Request ID。")
            path += "/" + quote(identifier(request_id), safe="") + "/" + action
        digest = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )
        command: JSONObject = {
            "path": path,
            "method": "POST",
            "idempotency_key": uuid.uuid4().hex,
            "body": body,
            "request_digest": digest,
            "state": "pending",
            "request_id": request_id,
        }
        commands.append(command)
        self.store.save()
        watcher = (
            self._watch_progress(command, progress)
            if progress is not None and action in {"create", "messages"}
            else nullcontext()
        )
        with watcher:
            raw = self._post(path, body, cast(str, command["idempotency_key"]))
        record = checked_request(raw)
        if request_id is not None and record.request_id != request_id:
            raise WorkbenchError("命令响应属于另一个 Request；保留待核对状态。")
        if (
            action == "create"
            and record.submission_intent is not None
            and (record.submission_intent.mode != body.get("submission_mode"))
        ):
            raise WorkbenchError("服务端返回的提交意图不匹配。")
        command.update({"state": "complete", "request_id": record.request_id, "response": raw})
        self.store.save()
        return raw

    @contextmanager
    def _watch_progress(
        self, command: JSONObject, notify: Callable[[MissionRequestLifecycle], None]
    ) -> Iterator[None]:
        """Display durable processing stages while the original response is pending."""
        stopped = Event()
        display_lock = Lock()
        original = dict(command)
        deadline = time.monotonic() + self.connection.timeout

        def display(lifecycle: MissionRequestLifecycle) -> None:
            """Order displays before the foreground command returns."""
            with display_lock:
                if not stopped.is_set() and time.monotonic() < deadline:
                    notify(lifecycle)

        watcher = Thread(
            target=self._poll_progress,
            args=(original, stopped, deadline, display),
            name="roboguide-mission-progress",
            daemon=True,
        )
        with suppress(RuntimeError):
            watcher.start()
        try:
            yield
        finally:
            # An in-flight observation can drain independently of the final command receipt.
            with display_lock:
                stopped.set()

    def _poll_progress(
        self,
        command: JSONObject,
        stopped: Event,
        deadline: float,
        display: Callable[[MissionRequestLifecycle], None],
    ) -> None:
        """Read only the original command and discard incomplete progress evidence."""
        request_id = command.get("request_id")
        previous: MissionRequestLifecycle | None = None
        delay = 0.2
        while not stopped.wait(min(delay, max(0.0, deadline - time.monotonic()))):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            delay = 1.0
            try:
                raw = self._command_observation(command, timeout=min(remaining, 0.5))
                if raw["status"] == "complete":
                    return
                candidate = raw.get("progress")
                if raw.get("executing") is not True or not isinstance(candidate, dict):
                    continue
                record = checked_request(candidate)
                if raw.get("request_id") != record.request_id or request_id not in {
                    None,
                    record.request_id,
                }:
                    return
                request_id = record.request_id
                if record.lifecycle != previous and record.lifecycle in {
                    MissionRequestLifecycle.RECEIVED,
                    MissionRequestLifecycle.INTERPRETING,
                    MissionRequestLifecycle.DRAFTED,
                    MissionRequestLifecycle.REVIEWING,
                    MissionRequestLifecycle.REPAIRING,
                    MissionRequestLifecycle.SUBMITTING,
                }:
                    display(record.lifecycle)
                    previous = record.lifecycle
            except (ValueError, OSError, RuntimeError):
                # A failed observation leaves the original foreground command in charge.
                continue

    def _command_observation(
        self, command: JSONObject, *, reconcile: bool = False, timeout: float | None = None
    ) -> JSONObject:
        """Qualify the authenticated original command envelope before using any receipt."""
        path = "/v1/http-commands/reconcile" if reconcile else "/v1/http-commands/observe"
        body: JSONObject = {
            "method": "POST",
            "path": command["path"],
            "idempotency_key": command["idempotency_key"],
        }
        raw = self._post(path, body) if timeout is None else self._post(path, body, timeout=timeout)
        if raw.get("schema_version") != "roboguide.http-command-observation/v0.1" or any(
            raw.get(field) != command.get(field)
            for field in ("path", "method", "idempotency_key", "request_digest")
        ):
            raise WorkbenchError("原命令观察身份不匹配。")
        if raw.get("status") not in ("complete", "pending"):
            raise WorkbenchError("原命令观察状态无效。")
        return raw

    def observe(self, *, reconcile: bool = False) -> JSONObject:
        """Observe the last original command under its exact scope, body digest and key."""
        commands = self.store.data.get("commands")
        if not isinstance(commands, list) or not commands or not isinstance(commands[-1], dict):
            raise WorkbenchError("没有可观察的原命令。")
        command = commands[-1]
        if command.get("service", "mission") != "mission":
            raise WorkbenchError(
                "该原命令属于 Controller，请使用取消观察路径；不会发送到 Mission Service。"
            )
        raw = self._command_observation(command, reconcile=reconcile)
        candidate = raw.get("response") if raw.get("status") == "complete" else raw.get("progress")
        if isinstance(candidate, dict) and candidate.get("schema_version") == INTENT_REQUEST_SCHEMA:
            record = checked_request(candidate)
            if command.get("request_id") not in {None, record.request_id}:
                raise WorkbenchError("原命令 Request 身份冲突。")
            command["request_id"] = record.request_id
        observed_id = raw.get("request_id")
        if observed_id is not None:
            if not isinstance(observed_id, str) or command.get("request_id") not in {
                None,
                observed_id,
            }:
                raise WorkbenchError("原命令观察中的 Request 身份冲突。")
            command["request_id"] = identifier(observed_id)
        if raw.get("status") == "complete":
            command["state"] = "complete"
        command["observation"] = raw
        self.store.save()
        return raw
