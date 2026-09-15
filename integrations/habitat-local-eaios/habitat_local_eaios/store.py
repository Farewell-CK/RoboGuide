"""Durable local-handle and lifecycle storage for the Habitat bridge."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import TypedDict

from .backend import LocalExecutionOutcome
from .model import CanonicalMobilityInvocation, IntegrationError

TERMINAL_STATES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


class StoredExecution(TypedDict):
    """Stable in-process representation of one durable bridge execution row."""

    execution_id: str
    request_key: str
    invocation: CanonicalMobilityInvocation
    state: str
    detail: str
    cancel_requested: bool
    local_outcome: dict[str, object] | None
    created_at_unix_ms: int
    updated_at_unix_ms: int


class ExecutionStore:
    """Owns bridge-local handles without claiming RoboGuide execution authority."""

    def __init__(self, database: Path) -> None:
        """Create the SQLite store and fail interrupted local work terminally on restart."""
        database.parent.mkdir(parents=True, exist_ok=True)
        self._database = database
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS executions (
                    execution_id TEXT PRIMARY KEY,
                    request_key TEXT NOT NULL UNIQUE,
                    invocation_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    local_outcome_json TEXT,
                    created_at_unix_ms INTEGER NOT NULL,
                    updated_at_unix_ms INTEGER NOT NULL
                )
                """
            )
            now = _unix_time_ms()
            connection.execute(
                """
                UPDATE executions
                   SET state = 'FAILED',
                       detail = 'Habitat bridge restarted before local execution terminated',
                       updated_at_unix_ms = ?
                 WHERE state IN ('ACCEPTED', 'RUNNING')
                """,
                (now,),
            )

    def create_or_get(
        self, invocation: CanonicalMobilityInvocation
    ) -> tuple[StoredExecution, bool]:
        """Create one idempotent accepted local handle or return its existing row."""
        request_key = invocation.request_key()
        invocation_json = json.dumps(
            invocation.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM executions WHERE request_key = ?", (request_key,)
            ).fetchone()
            if row is not None:
                return self._decode(row), False
            now = _unix_time_ms()
            execution_id = f"habitat-{request_key[:24]}"
            connection.execute(
                """
                INSERT INTO executions(
                    execution_id, request_key, invocation_json, state, detail,
                    created_at_unix_ms, updated_at_unix_ms
                ) VALUES (?, ?, ?, 'ACCEPTED', ?, ?, ?)
                """,
                (
                    execution_id,
                    request_key,
                    invocation_json,
                    "accepted by Habitat Local EAIOS",
                    now,
                    now,
                ),
            )
            created = connection.execute(
                "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
            if created is None:
                raise IntegrationError("local execution disappeared after durable creation")
            return self._decode(created), True

    def get(self, execution_id: str) -> StoredExecution | None:
        """Read one local execution without advancing or rewriting its lifecycle."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
            return None if row is None else self._decode(row)

    def active_execution(self) -> StoredExecution | None:
        """Return the sole accepted/running execution when one owns the simulator."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM executions WHERE state IN ('ACCEPTED', 'RUNNING')"
            ).fetchall()
            if len(rows) > 1:
                raise IntegrationError("multiple active Habitat executions violate local ownership")
            return None if not rows else self._decode(rows[0])

    def mark_running(self, execution_id: str, detail: str) -> None:
        """Persist the true transition from accepted work to an initialized Habitat execution."""
        self._transition(execution_id, ("ACCEPTED",), "RUNNING", detail, None)

    def mark_terminal(self, execution_id: str, outcome: LocalExecutionOutcome) -> None:
        """Persist one terminal state only after the local backend returns terminal evidence."""
        if outcome.state not in TERMINAL_STATES:
            raise IntegrationError(f"invalid terminal local state {outcome.state!r}")
        self._transition(
            execution_id,
            ("ACCEPTED", "RUNNING"),
            outcome.state,
            outcome.detail,
            outcome.as_dict(),
        )

    def mark_failed(self, execution_id: str, detail: str) -> None:
        """Record a terminal local failure without synthesizing successful execution evidence."""
        self._transition(execution_id, ("ACCEPTED", "RUNNING"), "FAILED", detail, None)

    def request_cancel(self, execution_id: str) -> StoredExecution | None:
        """Persist cancellation intent without changing the observed execution state."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
            if row is None:
                return None
            if str(row["state"]) not in TERMINAL_STATES:
                connection.execute(
                    """
                    UPDATE executions
                       SET cancel_requested = 1,
                           detail = 'cancellation request accepted; awaiting local terminal state',
                           updated_at_unix_ms = ?
                     WHERE execution_id = ?
                    """,
                    (_unix_time_ms(), execution_id),
                )
            updated = connection.execute(
                "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
            if updated is None:
                raise IntegrationError("local execution disappeared while requesting cancellation")
            return self._decode(updated)

    def cancellation_requested(self, execution_id: str) -> bool:
        """Return whether a nonterminal worker has an accepted cancellation request."""
        execution = self.get(execution_id)
        return execution is not None and execution["cancel_requested"]

    def _transition(
        self,
        execution_id: str,
        expected_states: tuple[str, ...],
        state: str,
        detail: str,
        outcome: dict[str, object] | None,
    ) -> None:
        """Apply one checked lifecycle transition atomically or reject stale worker output."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT state FROM executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
            if row is None:
                raise IntegrationError(f"unknown local execution {execution_id!r}")
            current = str(row["state"])
            if current not in expected_states:
                raise IntegrationError(f"invalid local execution transition {current} -> {state}")
            outcome_json = (
                None
                if outcome is None
                else json.dumps(outcome, sort_keys=True, separators=(",", ":"))
            )
            connection.execute(
                """
                UPDATE executions
                   SET state = ?, detail = ?, local_outcome_json = ?, updated_at_unix_ms = ?
                 WHERE execution_id = ?
                """,
                (state, detail[:2_000], outcome_json, _unix_time_ms(), execution_id),
            )

    def _connect(self) -> sqlite3.Connection:
        """Open one short-lived SQLite connection suitable for HTTP worker threads."""
        connection = sqlite3.connect(self._database, timeout=30.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _decode(self, row: sqlite3.Row) -> StoredExecution:
        """Decode one trusted local row and revalidate its retained canonical invocation."""
        try:
            raw_invocation = json.loads(str(row["invocation_json"]))
            outcome_raw = row["local_outcome_json"]
            outcome = None if outcome_raw is None else json.loads(str(outcome_raw))
        except json.JSONDecodeError as error:
            raise IntegrationError("local execution store contains invalid JSON") from error
        invocation = CanonicalMobilityInvocation.from_request({"invocation": raw_invocation})
        if outcome is not None and not isinstance(outcome, dict):
            raise IntegrationError("local execution outcome is not an object")
        normalized_outcome = (
            None if outcome is None else {str(key): value for key, value in outcome.items()}
        )
        return StoredExecution(
            execution_id=str(row["execution_id"]),
            request_key=str(row["request_key"]),
            invocation=invocation,
            state=str(row["state"]),
            detail=str(row["detail"]),
            cancel_requested=bool(row["cancel_requested"]),
            local_outcome=normalized_outcome,
            created_at_unix_ms=int(row["created_at_unix_ms"]),
            updated_at_unix_ms=int(row["updated_at_unix_ms"]),
        )


def _unix_time_ms() -> int:
    """Return wall-clock milliseconds for adapter-local durable audit metadata."""
    return time.time_ns() // 1_000_000
