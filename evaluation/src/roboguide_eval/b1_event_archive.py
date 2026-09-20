"""Bounded Controller event acquisition; no execution or admission authority."""

from __future__ import annotations

import json
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from http.client import HTTPException
from pathlib import Path
from typing import Any
from urllib.parse import quote

from roboguide_eval.b1_provenance import plan_digest, request_failure

ARCHIVE_FILE = "controller-event-archive.json"
ARCHIVE_SCHEMA = "roboguide.e1.controller-event-archive/v0.1"
TERMINAL = {"Completed", "Failed", "Cancelled"}


@dataclass(frozen=True)
class EventArchiveLimits:
    """Bound calls, memory, response bytes and elapsed acquisition time."""

    page_size: int = 100
    max_pages: int = 256
    max_page_bytes: int = 8 * 1024 * 1024
    max_total_bytes: int = 64 * 1024 * 1024
    timeout_seconds: float = 2.0
    budget_seconds: float = 30.0
    tail_confirmation_seconds: float = 0.1

    def __post_init__(self) -> None:
        """Reject invalid budgets rather than silently weakening acquisition bounds."""
        if (
            not 1 <= self.page_size <= 1000
            or any(
                value <= 0
                for value in (
                    self.max_pages,
                    self.max_page_bytes,
                    self.max_total_bytes,
                    self.timeout_seconds,
                    self.budget_seconds,
                )
            )
            or self.tail_confirmation_seconds < 0
        ):
            raise ValueError("invalid Controller event archive limits")


class ArchiveIncomplete(Exception):
    """Carry an acquisition reason without attributing a SUT execution failure."""


def atomic_json(path: Path, document: Any) -> None:
    """Publish complete JSON atomically; interrupted writes never replace evidence."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def events_required(request: dict[str, Any]) -> bool:
    """Respect existing pre-submission failure boundaries; acceptance always needs events."""
    if not request:
        return False
    if request.get("lifecycle") == "Accepted":
        return True
    failure = request_failure(request)
    return not bool(failure)


class EventCollector:
    """Retain every bounded response before validation and advance only verified cursors."""

    def __init__(self, directory: Path, limits: EventArchiveLimits) -> None:
        """Start one isolated acquisition attempt with a monotonic deadline."""
        self.directory = directory
        self.limits = limits
        self.deadline = time.monotonic() + limits.budget_seconds
        self.total_bytes = 0
        self.requests = 0
        self.pages = 0
        self.cursor = 0
        self.events: list[dict[str, Any]] = []
        self.event_ids: set[str] = set()

    def remaining(self) -> float:
        """Fail before further I/O after the acquisition time budget expires."""
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ArchiveIncomplete("time_budget_exhausted")
        return remaining

    def fetch(self, url: str) -> Any:
        """Save bounded raw HTTP bytes and metadata even for rejected or partial pages."""
        timeout = min(self.remaining(), self.limits.timeout_seconds)
        self.requests += 1
        stem = self.directory / f"response-{self.requests:04d}"
        raw = bytearray()
        metadata: dict[str, Any] = {"url": url, "status": None, "error": None}
        error = None
        byte_limit = min(self.limits.max_page_bytes, self.limits.max_total_bytes - self.total_bytes)
        if byte_limit <= 0:
            raise ArchiveIncomplete("total_byte_budget_exhausted")
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
                metadata["status"] = response.status
                while len(raw) <= byte_limit:
                    self.remaining()
                    # read1 avoids an unbounded trickle within one large read().
                    block = response.read1(min(65536, byte_limit + 1 - len(raw)))
                    if not block:
                        if response.length not in {None, 0}:
                            raise ArchiveIncomplete("response_truncated")
                        break
                    raw.extend(block)
        except urllib.error.HTTPError as failure:
            metadata["status"] = failure.code
            error = "http_failure"
            try:
                raw.extend(failure.read1(byte_limit + 1))
            except (OSError, HTTPException):
                pass
            finally:
                failure.close()
        except (OSError, HTTPException):
            error = "transport_failure"
        except ArchiveIncomplete as failure:
            error = str(failure)
        finally:
            self.total_bytes += len(raw)
            stem.with_suffix(".body").write_bytes(raw)
            metadata.update(bytes=len(raw), error=error)
            atomic_json(stem.with_suffix(".json"), metadata)
        if error:
            raise ArchiveIncomplete(error)
        self.remaining()
        if len(raw) > byte_limit:
            raise ArchiveIncomplete("response_size_limit")
        if metadata["status"] != 200:
            raise ArchiveIncomplete("http_status_invalid")
        try:
            return json.loads(raw)
        except (ValueError, UnicodeError) as failure:
            raise ArchiveIncomplete("response_json_invalid") from failure

    def page(self, endpoint: str) -> bool:
        """Read one strictly ordered page, allowing gaps but never deduplicating evidence."""
        if self.pages >= self.limits.max_pages:
            raise ArchiveIncomplete("page_budget_exhausted")
        self.pages += 1
        document = self.fetch(
            f"{endpoint}/v1/events?after={self.cursor}&limit={self.limits.page_size}"
        )
        if not isinstance(document, dict) or set(document) != {"events"}:
            raise ArchiveIncomplete("page_shape_invalid")
        page = document["events"]
        if not isinstance(page, list) or len(page) > self.limits.page_size:
            raise ArchiveIncomplete("page_shape_invalid")
        for event in page:
            if not _valid_event(event):
                raise ArchiveIncomplete("event_shape_invalid")
            sequence = event["sequence"]
            if sequence <= self.cursor or event["event_id"] in self.event_ids:
                raise ArchiveIncomplete("event_order_or_duplicate")
            self.cursor = sequence
            self.event_ids.add(event["event_id"])
            self.events.append(event)
        return bool(page)


def _valid_event(event: Any) -> bool:
    """Check the existing Controller envelope without interpreting its domain payload."""
    return bool(
        isinstance(event, dict)
        and type(event.get("sequence")) is int
        and 0 < event["sequence"] < 2**64
        and type(event.get("timestamp_ms")) is int
        and event["timestamp_ms"] >= 0
        and all(
            isinstance(event.get(key), str) and event[key] for key in ("event_id", "payload_schema")
        )
        and all(
            key in event and (event[key] is None or isinstance(event[key], str))
            for key in ("correlation_id", "causation_id")
        )
        and isinstance(event.get("payload"), dict)
        and bool(event["payload"])
    )


def terminal_anchor(document: Any, request: dict[str, Any]) -> dict[str, Any]:
    """Require a fresh terminal view scoped to the actual accepted Mission and Group."""
    sent = request.get("submission_evidence")
    if not (
        isinstance(document, dict)
        and isinstance(sent, dict)
        and document.get("mission_id") == request.get("mission_id")
        and document.get("group_id") == sent.get("controller_group_id")
        and bool(document.get("group_id"))
        and isinstance(document.get("status"), str)
        and document["status"] in TERMINAL
    ):
        raise ArchiveIncomplete("terminal_mission_unconfirmed")
    return {key: document[key] for key in ("mission_id", "group_id", "status")}


def collect_controller_events(
    run: Path,
    endpoint: str,
    request: dict[str, Any],
    limits: EventArchiveLimits | None = None,
) -> dict[str, Any]:
    """Publish a terminal durable prefix, never a global or future-event snapshot.

    The API has no snapshot token. A terminal Mission must bracket acquisition;
    two empty reads at the same cursor confirm the observed tail. New events
    reset that confirmation. Endless writers or any uncertainty exhaust a bound
    and leave raw evidence plus an incomplete status, never a partial events.json.
    """
    limits = limits or EventArchiveLimits()
    root = run / "controller-event-pages"
    root.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="attempt-", dir=root))
    status: dict[str, Any] = {
        "schema_version": ARCHIVE_SCHEMA,
        "run_id": run.name,
        "state": "collecting",
        "failure_owner": "EVIDENCE_COLLECTOR",
        "attempt_directory": str(directory.relative_to(run)),
        "limits": asdict(limits),
    }
    atomic_json(run / ARCHIVE_FILE, status)
    collector = EventCollector(directory, limits)
    try:
        if not events_required(request):
            status.update(
                state="not_required", reason="pre_submission_boundary", failure_owner=None
            )
        else:
            mission_id = quote(str(request.get("mission_id")), safe="")
            mission_url = f"{endpoint.rstrip('/')}/v1/missions/{mission_id}"
            before = terminal_anchor(collector.fetch(mission_url), request)
            while True:
                if collector.page(endpoint.rstrip("/")):
                    continue
                after = terminal_anchor(collector.fetch(mission_url), request)
                if after != before:
                    raise ArchiveIncomplete("terminal_mission_changed")
                time.sleep(min(limits.tail_confirmation_seconds, collector.remaining()))
                if not collector.page(endpoint.rstrip("/")):
                    final_mission = collector.fetch(mission_url)
                    if terminal_anchor(final_mission, request) != before:
                        raise ArchiveIncomplete("terminal_mission_changed")
                    break
            document = {"events": collector.events}
            atomic_json(run / "mission.json", final_mission)
            atomic_json(run / "events.json", document)
            status.update(
                state="complete",
                failure_owner=None,
                reason="terminal_tail_confirmed",
                scope="terminal_durable_prefix",
                terminal_mission=before,
                events_digest=plan_digest(document),
                tail_confirmations=2,
            )
    except ArchiveIncomplete as failure:
        status.update(state="incomplete", reason=str(failure))
    status.update(
        event_count=len(collector.events), last_sequence=collector.cursor, pages=collector.pages
    )
    atomic_json(directory / "result.json", status)
    atomic_json(run / ARCHIVE_FILE, status)
    return status


def archive_evidence_error(
    run: Path, status: Any, events: Any, request: dict[str, Any]
) -> str | None:
    """Fence stale or partial acquisition separately from unchanged SUT failure ownership."""
    if (
        status is None
        and not (run / ARCHIVE_FILE).exists()
        and not (run / "controller-event-pages").exists()
    ):
        return None  # Explicit compatibility with historical evidence without a sidecar.
    if (
        not isinstance(status, dict)
        or status.get("schema_version") != ARCHIVE_SCHEMA
        or status.get("run_id") != run.name
    ):
        return "controller_event_archive_invalid"
    if status.get("state") == "not_required" and not events_required(request):
        return None
    if status.get("state") != "complete":
        return "controller_event_archive_incomplete"
    if (
        status.get("scope") != "terminal_durable_prefix"
        or status.get("tail_confirmations") != 2
        or status.get("events_digest") != plan_digest(events)
    ):
        return "controller_event_archive_invalid"
    try:
        terminal_anchor(status.get("terminal_mission"), request)
    except ArchiveIncomplete:
        return "controller_event_archive_invalid"
    return None
