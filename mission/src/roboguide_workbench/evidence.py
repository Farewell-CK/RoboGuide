"""Local structured query outcomes; display text never determines evidence completeness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from mission.models import JSONObject

QueryStatus = Literal["complete", "partial", "failed"]


@dataclass(frozen=True)
class ReadResult:
    """Keep host-rendered facts and every original subread's availability together."""

    text: str
    complete: bool = True
    components: tuple[JSONObject, ...] = ()
