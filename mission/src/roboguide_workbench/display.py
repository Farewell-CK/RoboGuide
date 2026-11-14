"""Acknowledge visible references and confirmations independently of original command effects."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from mission.models import JSONObject

from roboguide_workbench.client import WorkbenchError
from roboguide_workbench.session import Session
from roboguide_workbench.workspace import WorkbenchSession


class DisplayPersistenceError(WorkbenchError):
    """Stop the frontend when visible references cannot be durably restored."""


def discard_confirmation(session: Session) -> None:
    """Revoke local confirmation without changing actual requests, commands or receipts."""
    if isinstance(session, WorkbenchSession):
        session.shown_draft = None
        session.suggestion = None
        session.pending_cancel = None


@dataclass
class DisplayTurn:
    """Keep unacknowledged worker references out of durable user-visible context."""

    session: Session

    def __post_init__(self) -> None:
        """Snapshot displayed references before the sole foreground worker starts."""
        session = self.session
        self.nodes = list(session.displayed_nodes)
        self.last_node, self.last_mission = session.last_node, session.last_mission
        self.last_raw, self.last_read_at = session.last_raw, session.last_read_at
        self.missions = (
            list(session.displayed_missions) if isinstance(session, WorkbenchSession) else []
        )
        self.draft: JSONObject | None = None
        self.suggestion: tuple[str, str] | None = None
        self.cancellation: JSONObject | None = None
        self.finished = False
        if isinstance(session, WorkbenchSession):
            if session.display_turn_id is not None:
                raise WorkbenchError("已有前台显示尚未收尾。")
            self.turn_id = uuid.uuid4().hex
            session.display_turn_id = self.turn_id
            session.reference_checkpoint = {
                "nodes": list(self.nodes),
                "missions": list(self.missions),
                "last_node": self.last_node,
                "last_mission": self.last_mission,
            }

    def stage(self) -> None:
        """Hold completed-worker confirmation until complete output and persistence succeed."""
        if isinstance(self.session, WorkbenchSession):
            self.draft, self.suggestion = self.session.shown_draft, self.session.suggestion
            self.cancellation = self.session.pending_cancel
        discard_confirmation(self.session)

    def acknowledge(self) -> None:
        """Publish the references actually displayed, then enable their exact confirmations."""
        if isinstance(self.session, WorkbenchSession):
            messages = self.session.store.data.get("messages")
            replies = (
                [
                    row
                    for row in messages
                    if isinstance(row, dict)
                    and row.get("role") == "assistant"
                    and row.get("turn_id") == self.turn_id
                ]
                if isinstance(messages, list)
                else []
            )
            for row in replies:
                row["displayed"] = True
            self.session.reference_checkpoint = None
            try:
                self.session.checkpoint()
            except BaseException:
                for row in replies:
                    row["displayed"] = False
                raise
            self.session.shown_draft, self.session.suggestion = self.draft, self.suggestion
            self.session.pending_cancel = self.cancellation
            self.session.display_turn_id = None
        self.finished = True

    def reject(self) -> None:
        """Restore only visible references after the worker drains, preserving real receipts."""
        if self.finished:
            return
        session = self.session
        discard_confirmation(session)
        session.displayed_nodes = list(self.nodes)
        session.last_node, session.last_mission = self.last_node, self.last_mission
        session.last_raw, session.last_read_at = self.last_raw, self.last_read_at
        if isinstance(session, WorkbenchSession):
            session.displayed_missions = list(self.missions)
            session.reference_checkpoint = None
            try:
                session.checkpoint()
            except (ValueError, OSError) as error:
                raise DisplayPersistenceError(
                    "可见对象引用未能保存，请恢复存储后重新进入会话。"
                ) from error
            finally:
                session.display_turn_id = None
        self.finished = True
