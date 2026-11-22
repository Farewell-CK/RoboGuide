"""Local structured query outcomes; display text never determines evidence completeness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from mission.models import JSONObject

from roboguide_workbench.client import WorkbenchError

QueryStatus = Literal["complete", "partial", "failed"]
READ_COSTS = {"read_explanation": 3, "read_mission_diagnosis": 3, "read_state": 3}


@dataclass(frozen=True)
class ReadResult:
    """Keep host-rendered facts and every original subread's availability together."""

    text: str
    complete: bool = True
    components: tuple[JSONObject, ...] = ()
    summary: str | None = None
    detail: str | None = None
    wait_for_user: bool = False


class OriginalOperationPending(WorkbenchError):
    """An admitted original operation is journaled and still needs observation."""


def command_count(data: JSONObject) -> int:
    """Read the journal generation without consulting network state."""
    commands = data.get("commands")
    return len(commands) if isinstance(commands, list) else 0


def new_pending_command(data: JSONObject, before: int) -> bool:
    """Distinguish a newly journaled original from a rejected preflight or an older fence."""
    commands = data.get("commands")
    return (
        isinstance(commands, list)
        and len(commands) > before
        and isinstance(commands[-1], dict)
        and commands[-1].get("state") == "pending"
    )
