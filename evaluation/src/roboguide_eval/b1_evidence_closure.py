"""Bounded observation of a live world's terminal evidence publication barrier."""

from __future__ import annotations

import json
import re
import time
import urllib.request
from collections.abc import Callable
from http.client import HTTPException
from pathlib import Path
from typing import Any

CLOSURE_SCHEMA = "roboguide.shared-world-closure/v0.1"
ARCHIVE_SCHEMA = "roboguide.e1.evidence-closure-archive/v0.1"


def _fetch(url: str, timeout: float) -> Any:
    """Read a small producer status; network/format failure remains unavailable."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            raw = response.read(16_385)
        return json.loads(raw) if len(raw) <= 16_384 else None
    except (OSError, ValueError, HTTPException):
        return None


def collect_evidence_closure(
    run: Path,
    endpoint: str,
    mission_id: str,
    *,
    budget_seconds: float = 30,
    fetch: Callable[[str, float], Any] = _fetch,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Wait for the exact producer to seal, retaining incomplete evidence separately.

    This barrier concerns archive readiness only. It neither changes a SUT
    failure nor supplies a benchmark outcome. A terminal execution can precede
    its producer's terminal snapshot, and an arbitrary fixed delay proves no
    publication. Legacy profiles do not opt into this additional observation.
    """
    if not 0 < budget_seconds <= 300:
        raise ValueError("evidence closure budget must be in (0, 300]")
    deadline = monotonic() + budget_seconds
    snapshots: list[Any] = []
    reason = "producer_closure_observation_budget_exhausted"
    complete = False
    session_digest: str | None = None
    for _ in range(64):
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        value = fetch(endpoint.rstrip("/") + "/v1/executions/closure", min(2, remaining))
        snapshots.append(value)
        if isinstance(value, dict) and value.get("schema_version") == CLOSURE_SCHEMA:
            if value.get("mission_id") not in (None, mission_id):
                reason = "producer_closure_identity_mismatch"
                break
            digest = value.get("execution_session_digest")
            valid_digest = isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
            if valid_digest:
                if session_digest is not None and digest != session_digest:
                    reason = "producer_closure_identity_mismatch"
                    break
                session_digest = digest
            if value.get("phase") in {"closed", "failed", "unavailable"}:
                valid_closed = (
                    value.get("phase") == "closed"
                    and value.get("finished") is True
                    and value.get("mission_id") == mission_id
                    and bool(valid_digest)
                    and value.get("error") is None
                )
                complete = (
                    valid_closed
                    and value.get("summary_published") is True
                    and value.get("evidence_flushed") is True
                )
                # The producer closes execution before publishing its final files.
                # Only the actual seal, or an explicit terminal publication error,
                # ends observation; the original deadline is never renewed.
                if valid_closed and not complete:
                    remaining = deadline - monotonic()
                    if remaining > 0:
                        sleep(min(0.5, remaining))
                    continue
                reason = "producer_evidence_sealed" if complete else "producer_evidence_incomplete"
                break
        remaining = deadline - monotonic()
        if remaining > 0:
            sleep(min(0.5, remaining))
    document = {
        "schema_version": ARCHIVE_SCHEMA,
        "run_id": run.name,
        "mission_id": mission_id,
        "complete": complete,
        "reason": reason,
        "failure_owner": "NONE" if complete else "HARNESS_ARCHIVAL",
        "observations": snapshots,
    }
    (run / "evidence-closure-archive.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return document
