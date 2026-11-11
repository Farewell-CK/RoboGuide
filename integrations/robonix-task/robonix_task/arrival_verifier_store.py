"""Journal manual arrival reports independently from the robot Pilot's storage.

Before-send publication records survive restart. Their uncertain delivery is never
automatically replayed or assigned a newer observation time.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA = "roboguide.onsite-arrival-store/v0.1"
MAX_REPORTS = 8192


class ArrivalVerificationError(RuntimeError):
    """Reject detached evidence without an action or automatic evidence resend."""


def encode(value: Any) -> str:
    """Serialize finite retained JSON deterministically without changing source strings."""
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError, OverflowError) as error:
        raise ArrivalVerificationError("finite_json_required") from error


def digest(value: Any) -> str:
    """Protect immutable journal input and retained publication state with content digests."""
    return "sha256:" + hashlib.sha256(encode(value).encode()).hexdigest()


class ArrivalVerifierStore:
    """Persist the exact original report, source sequence, evidence and returned receipt."""

    def __init__(self, database: Path) -> None:
        """Open only this publisher's versioned journal using fully durable SQLite writes."""
        self.database = database
        database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS arrival_format "
                "(singleton INTEGER PRIMARY KEY CHECK(singleton=1), schema TEXT NOT NULL)"
            )
            rows = connection.execute("SELECT schema FROM arrival_format").fetchall()
            if rows and rows != [(SCHEMA,)]:
                raise ArrivalVerificationError("unsupported_arrival_store_generation")
            connection.execute("INSERT OR IGNORE INTO arrival_format VALUES (1,?)", (SCHEMA,))
            connection.execute(
                "CREATE TABLE IF NOT EXISTS arrival_reports "
                "(observation_id TEXT PRIMARY KEY, original_json TEXT NOT NULL, "
                "original_digest TEXT NOT NULL, state_json TEXT NOT NULL, "
                "state_digest TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS arrival_sequences "
                "(source_id TEXT NOT NULL, revision INTEGER NOT NULL, sequence INTEGER NOT NULL, "
                "PRIMARY KEY(source_id,revision))"
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Keep writer transactions short and close every connection before transport I/O."""
        connection = sqlite3.connect(self.database, timeout=1)
        try:
            connection.execute("PRAGMA synchronous=FULL")
            with connection:
                yield connection
        finally:
            connection.close()

    def _decode(self, row: tuple[Any, ...]) -> dict[str, Any]:
        """Validate both original input and publication-state digests before returning them."""
        original, original_digest, state, state_digest = row
        document, progress = json.loads(original), json.loads(state)
        if (
            digest(document) != original_digest
            or digest(progress) != state_digest
            or progress.get("status") not in {"publishing", "receipted", "unknown", "expired"}
        ):
            raise ArrivalVerificationError("arrival_journal_integrity_failure")
        return {"original": document, **progress}

    def get(self, observation_id: str) -> dict[str, Any] | None:
        """Read a saved receipt or uncertain publication without refreshing source clocks."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT original_json,original_digest,state_json,state_digest "
                "FROM arrival_reports WHERE observation_id=?",
                (observation_id,),
            ).fetchone()
        return None if row is None else self._decode(row)

    def prepare(
        self, original: dict[str, Any], evidence: dict[str, Any] | None, *, received_at_ms: int
    ) -> dict[str, Any]:
        """Claim one fresh observation and sequence durably before any publication request."""
        observation_id = original["confirmation"]["observation_id"]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM arrival_reports WHERE observation_id=?", (observation_id,)
            ).fetchone():
                raise ArrivalVerificationError("observation_already_claimed")
            if (
                connection.execute("SELECT COUNT(*) FROM arrival_reports").fetchone()[0]
                >= MAX_REPORTS
            ):
                raise ArrivalVerificationError("arrival_journal_capacity_exhausted")
            frozen = json.loads(encode(evidence))
            if frozen is not None:
                source = (frozen["source_id"], frozen["source_revision"])
                prior = connection.execute(
                    "SELECT sequence FROM arrival_sequences WHERE source_id=? AND revision=?",
                    source,
                ).fetchone()
                frozen["sequence"] = (int(prior[0]) if prior else 0) + 1
                if frozen["sequence"] > 9223372036854775807:
                    raise ArrivalVerificationError("arrival_sequence_exhausted")
                connection.execute(
                    "INSERT INTO arrival_sequences VALUES (?,?,?) ON CONFLICT(source_id,revision) "
                    "DO UPDATE SET sequence=excluded.sequence",
                    (*source, frozen["sequence"]),
                )
            state = {
                "status": "publishing" if frozen is not None else "unknown",
                "evidence": frozen,
                "receipt": None,
                "report_received_at_ms": received_at_ms,
            }
            connection.execute(
                "INSERT INTO arrival_reports VALUES (?,?,?,?,?)",
                (observation_id, encode(original), digest(original), encode(state), digest(state)),
            )
        return {"original": json.loads(encode(original)), **state}

    def finish(self, observation_id: str, receipt: dict[str, Any] | None) -> dict[str, Any]:
        """Retain a checked response or known-before-send expiry without changing original input."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT original_json,original_digest,state_json,state_digest "
                "FROM arrival_reports WHERE observation_id=?",
                (observation_id,),
            ).fetchone()
            if row is None:
                raise ArrivalVerificationError("arrival_report_missing")
            current = self._decode(row)
            if current["status"] != "publishing":
                raise ArrivalVerificationError("arrival_report_already_final")
            state = {
                "status": "receipted" if receipt is not None else "expired",
                "evidence": current["evidence"],
                "receipt": receipt,
                "report_received_at_ms": current["report_received_at_ms"],
            }
            connection.execute(
                "UPDATE arrival_reports SET state_json=?,state_digest=? WHERE observation_id=?",
                (encode(state), digest(state), observation_id),
            )
        return {"original": current["original"], **state}
