"""Explicit, displayed-target cancellation with durable one-shot command observations."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import cast
from urllib.parse import quote

from mission.models import JSONObject
from mission.request_record import MissionRequestLifecycle

from roboguide_workbench.actions import (
    MissionActions,
    checked_request,
    commands_clear,
    controller_cancel_identity,
)
from roboguide_workbench.client import ReadOnlyController, WorkbenchError, identifier, request_json
from roboguide_workbench.views import render_mission, safe_text

PREVIEW_SCHEMA = "roboguide.workbench-cancel-preview/v0.1"
OBSERVATION_SCHEMA = "roboguide.workbench-cancel-observation/v0.1"
TERMINAL = frozenset({"Completed", "Failed", "Cancelled"})


def _target(value: str) -> str:
    """Keep identities literal and refuse routes the current server cannot decode exactly."""
    value = identifier(value)
    if quote(value, safe="") != value:
        raise WorkbenchError("当前取消 API 不支持此 ID 的路径编码；未发送取消命令。")
    return value


def render_preview(selection: JSONObject) -> str:
    """Show the exact cancellation scope without claiming that any operation has been sent."""
    target, kind = selection.get("target"), selection.get("kind")
    return (
        f"待确认取消 {kind} {safe_text(str(target))}\n"
        f"来源：{safe_text(str(selection.get('origin')))}\n"
        f"当前状态：{safe_text(str(selection.get('status')))}\n"
        + (
            f"计划版本：{selection.get('draft_revision')}；摘要：{selection.get('draft_digest')}\n"
            if kind == "request"
            else f"Group：{safe_text(str(selection.get('group_id')))}\n"
        )
        + "尚未发送取消。确认此对象请使用 /cancel confirm。\n"
        + (
            "这会放弃未提交的规划请求，不代表取消运行中的 Mission。"
            if kind == "request"
            else "接收取消请求、正在取消、记录取消终态分别展示；不宣称设备物理停止。"
        )
    )


class CancellationActions:
    """Cancel only a fresh exact displayed target through its original service authority."""

    def __init__(self, controller: ReadOnlyController, actions: MissionActions) -> None:
        """Keep Controller and Mission transport identities independent in one local journal."""
        self.controller, self.actions = controller, actions

    def _request(self, target: str) -> JSONObject:
        """Read one nonaccepted Request under the Mission Service's current authorization."""
        raw = self.actions.get(target)
        record = checked_request(raw)
        if record.lifecycle in {
            MissionRequestLifecycle.ACCEPTED,
            MissionRequestLifecycle.SUBMITTING,
        }:
            raise WorkbenchError(
                "该请求已提交或提交结果未确定；请核对后使用 /cancel mission "
                + safe_text(record.mission_id)
            )
        if record.lifecycle is MissionRequestLifecycle.CANCELLED:
            raise WorkbenchError("Request 已为 Cancelled；未发送新取消命令。")
        return raw

    def preview(self, kind: str, target: str) -> JSONObject:
        """Freeze one original target and its displayed state before any command is admitted."""
        target = _target(target)
        if kind == "request":
            raw = self._request(target)
            return {
                "schema_version": PREVIEW_SCHEMA,
                "kind": kind,
                "target": target,
                "origin": self.actions.connection.endpoint,
                "status": raw["lifecycle"],
                "mission_id": raw["mission_id"],
                "draft_revision": raw["draft_revision"],
                "draft_digest": raw["draft_digest"],
                "updated_at_ms": raw["updated_at_ms"],
            }
        if kind != "mission":
            raise WorkbenchError("请使用 /cancel request ID 或 /cancel mission ID。")
        raw = self.controller.mission(target)
        render_mission(raw, target)
        if raw["status"] in TERMINAL or raw["status"] == "Cancelling":
            raise WorkbenchError(
                f"Mission 当前为 {raw['status']}；请查询或跟踪原进度，未发送新取消命令。"
            )
        return {
            "schema_version": PREVIEW_SCHEMA,
            "kind": kind,
            "target": target,
            "origin": self.controller.endpoint,
            "status": raw["status"],
            "mission_id": target,
            "group_id": raw["group_id"],
        }

    def confirm(self, selection: JSONObject) -> JSONObject:
        """Recheck the exact displayed target, then send its one original journaled command."""
        if selection.get("schema_version") != PREVIEW_SCHEMA:
            raise WorkbenchError("没有已展示的取消选择，请先 /cancel request|mission ID。")
        kind, target = selection.get("kind"), selection.get("target")
        if not isinstance(kind, str) or not isinstance(target, str):
            raise WorkbenchError("取消选择无效。")
        commands = self.actions.store.data.get("commands")
        if not commands_clear(commands):
            raise WorkbenchError("原命令结果尚未确定，请先 /observe；不会重发取消命令。")
        current = self.preview(kind, target)
        if current != selection:
            raise WorkbenchError("取消对象状态或版本已改变，请重新 /cancel 查看；未发送命令。")
        if kind == "request":
            response = self.actions.command("cancel", {}, target)
            record = checked_request(response)
            if record.lifecycle is not MissionRequestLifecycle.CANCELLED:
                raise WorkbenchError("Request 未返回 Cancelled；保留实际回执并核对原状态。")
            return {
                "kind": kind,
                "target": target,
                "status": "Cancelled",
                "response": response,
                "text": "Request " + safe_text(target) + "：Cancelled。未发送 Mission 取消。",
            }
        return self._cancel_mission(current)

    def _cancel_mission(self, selection: JSONObject) -> JSONObject:
        """Persist the original Controller command before I/O without inventing HTTP idempotency."""
        target = _target(cast(str, selection["target"]))
        commands = self.actions.store.data["commands"]
        if not isinstance(commands, list) or not commands_clear(commands):
            raise WorkbenchError("原命令结果尚未确定，请先 /observe。")
        command: JSONObject = {
            "service": "controller",
            "action": "cancel",
            "origin": self.controller.endpoint,
            "path": "/v1/missions/" + target + "/cancel",
            "method": "POST",
            "operation_id": uuid.uuid4().hex,
            "body": {},
            "request_digest": "sha256:" + hashlib.sha256(b"{}").hexdigest(),
            "mission_id": target,
            "group_id": selection["group_id"],
            "state": "pending",
            "original_http_outcome": "unknown",
            "accepted_at": datetime.now(UTC).isoformat(),
        }
        commands.append(command)
        self.actions.store.save()
        response = self._post(cast(str, command["path"]))
        if response.get("status") not in {"Cancelling", "Cancelled"}:
            raise WorkbenchError("Controller 取消响应无效；原结果未知，请 /observe，不会重发。")
        command.update(
            {"state": "complete", "original_http_outcome": "received", "response": response}
        )
        self.actions.store.save()
        status = cast(str, response["status"])
        return {
            "kind": "mission",
            "target": target,
            "status": status,
            "response": response,
            "text": (
                "Controller 已接收 Mission "
                + safe_text(target)
                + " 的取消请求；当前记录："
                + status
                + "。\n"
                + (
                    "正在取消，请 /watch 或 /mission 读取后续状态；尚未确认取消终态。"
                    if status == "Cancelling"
                    else "Controller 已记录 Cancelled；该软件记录不证明设备物理停止。"
                )
            ),
        }

    def _post(self, path: str) -> JSONObject:
        """Send the Controller cancel exactly once under its own TLS and tenant scope."""
        headers = {"Content-Type": "application/json"}
        if self.controller.tenant is not None:
            headers["X-RoboGuide-Tenant"] = self.controller.tenant
        return request_json(
            self.controller.endpoint + path,
            "POST",
            headers,
            b"{}",
            self.controller.timeout,
            self.controller.context,
            (200, 202),
        )

    def owns_latest(self) -> bool:
        """Select Controller observation only for the actual latest journaled cancellation."""
        commands = self.actions.store.data.get("commands")
        return bool(
            isinstance(commands, list)
            and commands
            and isinstance(commands[-1], dict)
            and commands[-1].get("service") == "controller"
            and commands[-1].get("action") == "cancel"
        )

    def observe(self) -> JSONObject:
        """Read current Mission state without claiming an unavailable original HTTP receipt."""
        if not self.owns_latest():
            raise WorkbenchError("没有当前 Controller 取消命令；请观察原 Mission Service 命令。")
        commands = cast(list[JSONObject], self.actions.store.data["commands"])
        command = commands[-1]
        target = command.get("mission_id")
        if (
            not controller_cancel_identity(command)
            or not isinstance(target, str)
            or command.get("origin") != self.controller.endpoint
            or not isinstance(command.get("state"), str)
            or command.get("state") not in {"pending", "complete", "resolved"}
            or (command.get("state") == "resolved" and not commands_clear([command]))
        ):
            raise WorkbenchError("原 Controller 取消命令来源或对象不匹配。")
        raw = self.controller.mission(target)
        facts = render_mission(raw, target)
        if raw.get("group_id") != command.get("group_id"):
            raise WorkbenchError("原 Controller 取消命令 Group 身份冲突。")
        original = command.get("original_http_outcome")
        if not isinstance(original, str) or original not in {"received", "unknown"}:
            raise WorkbenchError("原 Controller 取消回执状态无效。")
        observed: JSONObject = {
            "schema_version": OBSERVATION_SCHEMA,
            "origin": self.controller.endpoint,
            "mission_id": target,
            "group_id": command["group_id"],
            "status": raw["status"],
            "original_http_outcome": original,
            "received_at": datetime.now(UTC).isoformat(),
            "operation_id": command["operation_id"],
            "path": command["path"],
            "request_digest": command["request_digest"],
            "accepted_at": command["accepted_at"],
        }
        # Original v0.4 resolved rows used their Cancelled observation as the witness. Keep
        # those bytes intact; newer rows store a separate immutable resolution witness.
        if command.get("state") == "resolved" and "resolution" not in command:
            command["latest_observation"] = observed
        else:
            command["observation"] = observed
        if command.get("state") == "pending" and raw["status"] in {"Cancelled", "Completed"}:
            command["state"] = "resolved"
            command["resolution"] = dict(observed)
        self.actions.store.save()
        text = (
            "原取消 HTTP 回执："
            + ("已收到" if original == "received" else "未知；本次只观察实际状态，不补造成功回执")
            + "。没有重发。\n"
            + facts
        )
        if raw["status"] == "Cancelled":
            text += "\n已观察到原 Mission/Group 的 Cancelled 记录；该记录不证明设备物理停止。"
        elif raw["status"] == "Completed":
            text += (
                "\n已观察到原 Mission/Group 的 Completed 记录；任务已完成，不表示取消成功。"
                "原取消 HTTP 结果保持不变，未重新发送取消。"
            )
        elif command["state"] == "pending":
            text += "\n原命令仍待核对；后续写操作保持阻止。"
        return {**observed, "command_state": command["state"], "text": text}
