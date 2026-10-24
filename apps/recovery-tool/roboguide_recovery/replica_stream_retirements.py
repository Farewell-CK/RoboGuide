"""Verify Root7 original stream-close/end evidence and retained release digests."""

from __future__ import annotations

from typing import Any

from . import replica_content as content
from .snapshot_references import OperationBudget


def validate(identity: dict[str, Any], tables: dict[str, Any], budget: OperationBudget) -> None:
    """Require original worker-close or process-end evidence, retaining receipt/Unknown."""
    for key, raw in tables["content_stream_retirements"].items():
        budget.check()
        value = content._closed(
            raw, {"schema", "receiver", "original", "budget", "process_end", "retired_ms"}
        )
        original = tables["content_streams"].get(key)
        content._budget(value["budget"])
        if (
            value["schema"] != "roboguide.replica-content-stream-retirement/v0.1"
            or value["receiver"] != identity
            or original is None
            or value["original"] != original
            or not content._completed(value["budget"], value["retired_ms"])
        ):
            raise ValueError("Replica stream retirement changed original receive/decision")
        closed = original["worker_closed_ms"]
        if closed is not None:
            if (
                type(closed) is not int
                or not original["intent"]["budget"]["admitted_ms"] <= closed < 2**64
                or value["process_end"] is not None
            ):
                raise ValueError("Replica stream retirement lost actual original worker close")
        else:
            end = content._process_end(original["intent"]["budget"], value["process_end"])
            observer = end["observer"]
            if (
                any(
                    observer[field] != value["budget"][field]
                    for field in ("boot_id", "namespace", "pid_namespace", "pid", "start_ticks")
                )
                or not value["budget"]["admitted_ms"]
                <= observer["admitted_ms"]
                <= value["retired_ms"]
            ):
                raise ValueError("Replica stream end observation differs from original decision")
