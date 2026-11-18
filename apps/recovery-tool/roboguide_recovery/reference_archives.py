"""Resolve exact original State inputs from completed immutable Archive publications."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .archive_references import _intent
from .snapshot_references import canonical, digest

MAX_SEGMENT = 16 * 1024 * 1024
MAX_METADATA = 128 * 1024 * 1024
FIELDS = {
    "sequence",
    "event_id",
    "timestamp_ms",
    "correlation_id",
    "causation_id",
    "payload_schema",
    "payload_json",
}


def _read(path: Path, maximum: int) -> bytes:
    """Read only bounded original regular files beneath real absolute directory ancestors."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("reference Archive path is not an absolute declared root")
    for parent in path.parents:
        if not stat.S_ISDIR(parent.lstat().st_mode):
            raise ValueError("reference Archive path traverses a symlink or non-directory")
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("reference Archive publication requires a regular original file")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as stream:
        if (
            not stat.S_ISREG(os.fstat(stream.fileno()).st_mode)
            or os.fstat(stream.fileno()).st_size > maximum
        ):
            raise ValueError("reference Archive original file exceeds its byte bound")
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("reference Archive original file grew beyond its byte bound")
    return raw


class ReferenceArchiveResolver:
    """Keep one bounded exact segment while resolving original inputs through durable receipts."""

    def __init__(
        self, connection: sqlite3.Connection, roots: Mapping[str, Path] | None = None
    ) -> None:
        """Use original declared destinations, or explicit verified-bundle relocation bindings."""
        self.connection = connection
        self.roots = roots
        self.cached_range = (0, 0)
        self.cached_events: dict[int, dict[str, Any]] = {}

    def __call__(self, sequence: int, kind: str) -> Any:
        """Resolve an archived event only after its original completed publication proof agrees."""
        if not self.cached_range[0] <= sequence <= self.cached_range[1]:
            self._segment(sequence)
        row = self.cached_events.get(sequence)
        if row is None:
            raise ValueError("reference Archive original event is absent from its complete segment")
        payload = json.loads(row["payload_json"])
        if (
            not isinstance(payload, dict)
            or set(payload) != {kind}
            or not isinstance(payload[kind], dict)
        ):
            raise ValueError("reference Archive original event kind differs")
        return payload[kind]

    def _segment(self, sequence: int) -> None:
        """Read a single original segment without reconstructing authority from a filename alone."""
        candidates = self.connection.execute(
            "SELECT start_sequence,end_sequence,manifest_json FROM event_archive_manifests "
            "WHERE start_sequence<=?1 AND end_sequence>=?1 LIMIT 2",
            (sequence,),
        ).fetchall()
        if len(candidates) != 1:
            raise ValueError("reference Archive original manifest is absent or overlapping")
        start, end, encoded_manifest = candidates[0]
        if not isinstance(encoded_manifest, str) or len(encoded_manifest.encode()) > 4096:
            raise ValueError("reference Archive original manifest is invalid")
        manifest = json.loads(encoded_manifest)
        floor_row = self.connection.execute(
            "SELECT value FROM event_log_metadata WHERE key='history_floor'"
        ).fetchone()
        floor = int(floor_row[0]) if floor_row is not None else 0
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema") != "roboguide.event-archive/v2"
            or manifest.get("start_sequence") != start
            or manifest.get("end_sequence") != end
            or not 0 < start <= sequence <= end <= floor
            or end - start + 1 > 8192
        ):
            raise ValueError("reference Archive requires its original covered content manifest")
        commands = self.connection.execute(
            "SELECT command_id,intent_json,state,receipt_json FROM event_archive_operations "
            "WHERE json_extract(intent_json,'$.manifest.start_sequence')=?1 "
            "AND json_extract(intent_json,'$.manifest.end_sequence')=?2 LIMIT 2",
            (start, end),
        ).fetchall()
        if len(commands) != 1:
            raise ValueError("reference Archive original operation is missing or ambiguous")
        identity, encoded_intent, state, receipt = commands[0]
        if not isinstance(encoded_intent, str) or len(encoded_intent.encode()) > MAX_METADATA:
            raise ValueError("reference Archive original intent exceeds its byte bound")
        intent = _intent(json.loads(encoded_intent), identity)
        authority = self.connection.execute(
            "SELECT value FROM event_log_metadata WHERE key='archive_authority_id'"
        ).fetchone()
        if (
            state != "compacted"
            or not isinstance(receipt, str)
            or intent["manifest"] != manifest
            or authority != (intent["request"]["authority_id"],)
        ):
            raise ValueError("reference Archive lacks its original completed publication receipt")
        destination = intent["request"]["destination"]
        if self.roots is None:
            root = Path(destination)
        else:
            relocated = self.roots.get(destination)
            if relocated is None:
                raise ValueError("reference Archive destination is absent from the complete bundle")
            root = relocated
        directory = (
            root
            / "archives-v1"
            / hashlib.sha256(intent["request"]["authority_id"].encode()).hexdigest()
            / ("archive-" + hashlib.sha256(identity.encode()).hexdigest())
        )
        publication = json.loads(_read(directory / "publication.json", MAX_METADATA))
        if (
            not isinstance(publication, dict)
            or set(publication) != {"schema", "intent", "target_owner", "checkpoint_digest"}
            or publication["schema"] != "roboguide.content-archive/v0.1"
            or publication["intent"] != intent
            or digest(canonical(publication)) != receipt
            or publication["checkpoint_digest"] != digest(canonical(intent["checkpoint"]))
        ):
            raise ValueError("reference Archive original publication differs from its SQL receipt")
        for name, expected in (
            ("intent.json", canonical(intent)),
            ("checkpoint.json", canonical(intent["checkpoint"])),
            ("target-owner.json", canonical(publication["target_owner"])),
        ):
            if _read(directory / name, MAX_METADATA) != expected:
                raise ValueError("reference Archive original publication metadata changed")
        raw = _read(directory / "events.jsonl", MAX_SEGMENT)
        if digest(raw) != manifest["segment_digest"] or not raw.endswith(b"\n"):
            raise ValueError("reference Archive original event segment checksum differs")
        events: dict[int, dict[str, Any]] = {}
        for offset, line in enumerate(raw.splitlines()):
            row = json.loads(line)
            if (
                not isinstance(row, dict)
                or set(row) != FIELDS
                or type(row["sequence"]) is not int
                or row["sequence"] != start + offset
                or not isinstance(row["payload_json"], str)
                or len(events) >= 8192
            ):
                raise ValueError("reference Archive original event envelope or sequence differs")
            events[row["sequence"]] = row
        if len(events) != manifest["event_count"] or len(events) != end - start + 1:
            raise ValueError("reference Archive original event segment is incomplete")
        self.cached_range, self.cached_events = (start, end), events
