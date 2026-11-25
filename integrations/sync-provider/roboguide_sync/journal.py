"""Durable SQLite journal and `roboguide.safe-state-observation/v0.2` producer."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .schemas import canonical_json

TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELED"})
NON_REPLAYABLE_STATES = frozenset({"PENDING", "RUNNING"})


class ConflictError(ValueError):
    """Raised when an execution identity conflicts with an already-admitted request digest."""


class SyncJournal:
    """Persist admitted sync/choreography executions and fail-closed on process restart."""

    def __init__(self, db_path: str | Path) -> None:
        """Open SQLite journal and fail-close any interrupted executions as UNKNOWN."""
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).resolve().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS executions (
                    handle TEXT PRIMARY KEY,
                    attempt_id TEXT NOT NULL UNIQUE,
                    execution_generation INTEGER NOT NULL,
                    operation TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    cancel_command_id TEXT,
                    safe_state_confirmed INTEGER NOT NULL DEFAULT 0,
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    created_ms INTEGER NOT NULL,
                    updated_ms INTEGER NOT NULL
                )
                """
            )
            # Fail-closed crash recovery: mark interrupted PENDING/RUNNING rows UNKNOWN.
            now_ms = int(time.time() * 1000)
            self._conn.execute(
                """
                UPDATE executions
                SET state='UNKNOWN',
                    detail='process restarted; interrupted motion not replayed',
                    safe_state_confirmed=0,
                    updated_ms=?
                WHERE state IN ('PENDING', 'RUNNING')
                """,
                (now_ms,),
            )
            self._conn.commit()

    def close(self) -> None:
        """Close the SQLite journal connection."""
        with self._lock:
            self._conn.close()

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any]:
        """Convert one SQLite row into a normalized dictionary."""
        if row is None:
            raise KeyError("unknown execution handle")
        out = dict(row)
        out["request"] = json.loads(out.pop("request_json"))
        out["metrics"] = json.loads(out.pop("metrics_json"))
        out["cancel_requested"] = bool(out["cancel_requested"])
        out["safe_state_confirmed"] = bool(out["safe_state_confirmed"])
        return out

    def admit(
        self,
        *,
        attempt_id: str,
        execution_generation: int,
        operation: str,
        canonical_body: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """Idempotently admit one execution; reject mismatched digest for existing `attempt_id`."""
        req_text = canonical_json(canonical_body)
        digest = hashlib.sha256(req_text.encode("utf-8")).hexdigest()
        now_ms = int(time.time() * 1000)
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM executions WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            if existing is not None:
                if existing["request_digest"] != digest:
                    raise ConflictError(
                        f"attempt_id {attempt_id!r} already admitted with different request payload"
                    )
                return self._decode(existing), False

            handle = f"sync-{uuid.uuid4().hex[:16]}"
            self._conn.execute(
                """
                INSERT INTO executions (
                    handle, attempt_id, execution_generation, operation, request_digest,
                    request_json, state, detail, cancel_requested, cancel_command_id,
                    safe_state_confirmed, metrics_json, created_ms, updated_ms
                ) VALUES (?, ?, ?, ?, ?, ?, 'PENDING', 'admitted', 0, NULL, 0, '{}', ?, ?)
                """,
                (
                    handle,
                    attempt_id,
                    execution_generation,
                    operation,
                    digest,
                    req_text,
                    now_ms,
                    now_ms,
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM executions WHERE handle=?", (handle,)
            ).fetchone()
            return self._decode(row), True

    def get(self, handle: str) -> dict[str, Any]:
        """Return persisted execution row by `handle` or raise `KeyError`."""
        with self._lock:
            return self._decode(
                self._conn.execute("SELECT * FROM executions WHERE handle=?", (handle,)).fetchone()
            )

    def get_by_attempt_id(self, attempt_id: str) -> dict[str, Any] | None:
        """Return existing row for `attempt_id` if present, else `None`."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM executions WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            return self._decode(row) if row is not None else None

    def mark_running(self, handle: str, detail: str = "running") -> dict[str, Any]:
        """Transition a `PENDING` execution row to `RUNNING`."""
        now_ms = int(time.time() * 1000)
        with self._lock:
            self._conn.execute(
                (
                    "UPDATE executions SET state='RUNNING', detail=?, updated_ms=? "
                    "WHERE handle=? AND state='PENDING'"
                ),
                (detail, now_ms, handle),
            )
            self._conn.commit()
            return self.get(handle)

    def mark_terminal(
        self,
        handle: str,
        state: str,
        detail: str,
        *,
        metrics: dict[str, Any] | None = None,
        safe_state_confirmed: bool = False,
    ) -> dict[str, Any]:
        """Persist immutable terminal outcome and safe-state flag for `handle`."""
        if state not in TERMINAL_STATES:
            raise ValueError(f"invalid terminal state: {state!r}")
        now_ms = int(time.time() * 1000)
        metrics_txt = canonical_json(metrics or {})
        with self._lock:
            cur = self.get(handle)
            if cur["state"] in TERMINAL_STATES or cur["state"] == "UNKNOWN":
                return cur
            self._conn.execute(
                """
                UPDATE executions
                SET state=?, detail=?, metrics_json=?, safe_state_confirmed=?, updated_ms=?
                WHERE handle=?
                """,
                (state, detail, metrics_txt, int(bool(safe_state_confirmed)), now_ms, handle),
            )
            self._conn.commit()
            return self.get(handle)

    def request_cancel(self, handle: str, command_id: str | None = None) -> dict[str, Any]:
        """Record cancel intent and settle `PENDING` rows immediately as `CANCELED`."""
        now_ms = int(time.time() * 1000)
        with self._lock:
            cur = self.get(handle)
            cmd_id = cur["cancel_command_id"] or command_id or f"cancel-{cur['attempt_id']}"
            self._conn.execute(
                (
                    "UPDATE executions SET cancel_requested=1, cancel_command_id=?, "
                    "updated_ms=? WHERE handle=?"
                ),
                (cmd_id, now_ms, handle),
            )
            # If still PENDING (never entered RUNNING motion loop), settle CANCELED immediately.
            if cur["state"] == "PENDING":
                self._conn.execute(
                    """
                    UPDATE executions
                    SET state='CANCELED', detail='canceled before motion start',
                        safe_state_confirmed=1, updated_ms=?
                    WHERE handle=?
                    """,
                    (now_ms, handle),
                )
            self._conn.commit()
            return self.get(handle)

    def safe_state_observations(self) -> dict[str, Any]:
        """Return bounded `roboguide.safe-state-observation/v0.2` batch payload."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM executions
                WHERE state IN ('FAILED', 'CANCELED') AND safe_state_confirmed=1
                ORDER BY updated_ms DESC LIMIT 256
                """
            ).fetchall()
        observations: list[dict[str, Any]] = []
        used_bytes = 32
        for r in rows:
            if r["state"] == "FAILED":
                obs: dict[str, Any] = {
                    "execution_id": r["attempt_id"],
                    "execution_generation": int(r["execution_generation"]),
                    "command_id": None,
                    "terminal_state": "FAILED",
                    "safe_state": True,
                    "assurance": "device-observation",
                }
            else:  # CANCELED
                cmd_id = r["cancel_command_id"] or f"cancel-{r['attempt_id']}"
                obs = {
                    "execution_id": r["attempt_id"],
                    "execution_generation": int(r["execution_generation"]),
                    "command_id": cmd_id,
                    "safe_state": True,
                    "assurance": "device-observation",
                }
            add = len(canonical_json(obs).encode("utf-8")) + 1
            if used_bytes + add > 32_768:
                break
            used_bytes += add
            observations.append(obs)
        return {"observations": observations}
