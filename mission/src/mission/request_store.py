"""SQLite persistence for complete Mission Request deliberation projections."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from mission.grounding_context import GroundingContextSnapshot
from mission.request_record import MissionRequestError, MissionRequestRecord, _json_object


class MissionRequestStore:
    """Persist Mission Request projections in a process-local SQLite database."""

    def __init__(self, database: Path) -> None:
        """Create the database parent and versioned request table."""
        database.parent.mkdir(parents=True, exist_ok=True)
        self._database = database
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS mission_requests (
                    request_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL UNIQUE,
                    document_json TEXT NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS mission_grounding_contexts (
                    context_digest TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    document_json TEXT NOT NULL,
                    captured_at_ms INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS mission_grounding_contexts_request_id
                ON mission_grounding_contexts(request_id, captured_at_ms, context_digest)
                """
            )

    def _connect(self) -> sqlite3.Connection:
        """Open one short-lived SQLite connection with bounded lock waiting."""
        return sqlite3.connect(self._database, timeout=30.0)

    def save(self, record: MissionRequestRecord) -> None:
        """Atomically persist the request projection and its immutable current context."""
        document = json.dumps(
            record.to_json(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        with self._lock, self._connect() as connection:
            if record.grounding_context is not None:
                self._save_grounding_context(connection, record.grounding_context)
            connection.execute(
                """
                INSERT INTO mission_requests(request_id, mission_id, document_json, updated_at_ms)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(request_id) DO UPDATE SET
                    mission_id = excluded.mission_id,
                    document_json = excluded.document_json,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (record.request_id, record.mission_id, document, record.updated_at_ms),
            )

    def _save_grounding_context(
        self,
        connection: sqlite3.Connection,
        context: GroundingContextSnapshot,
    ) -> None:
        """Insert one digest-addressed snapshot once and reject any conflicting replacement."""
        document = json.dumps(
            context.to_json(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        row = connection.execute(
            """
            SELECT request_id, document_json
            FROM mission_grounding_contexts
            WHERE context_digest = ?
            """,
            (context.context_digest,),
        ).fetchone()
        if row is not None:
            if str(row[0]) != context.request_id or str(row[1]) != document:
                raise MissionRequestError(
                    "grounding context digest conflicts with durable evidence"
                )
            return
        connection.execute(
            """
            INSERT INTO mission_grounding_contexts(
                context_digest, request_id, document_json, captured_at_ms
            ) VALUES (?, ?, ?, ?)
            """,
            (
                context.context_digest,
                context.request_id,
                document,
                context.captured_at_ms,
            ),
        )

    def get(self, request_id: str) -> MissionRequestRecord | None:
        """Return one request projection without changing lifecycle."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT document_json FROM mission_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
        if row is None:
            return None
        decoded: object = json.loads(str(row[0]))
        return MissionRequestRecord.from_json(_json_object(decoded, "mission request"))

    def grounding_context(
        self, request_id: str, context_digest: str
    ) -> GroundingContextSnapshot | None:
        """Return one immutable historical snapshot by owning request and exact digest."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT document_json
                FROM mission_grounding_contexts
                WHERE request_id = ? AND context_digest = ?
                """,
                (request_id, context_digest),
            ).fetchone()
        if row is None:
            return None
        return GroundingContextSnapshot.from_json(json.loads(str(row[0])))

    def records(self) -> tuple[MissionRequestRecord, ...]:
        """Return all requests in stable identity order for conservative startup recovery."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT document_json FROM mission_requests ORDER BY request_id"
            ).fetchall()
        return tuple(
            MissionRequestRecord.from_json(_json_object(json.loads(str(row[0])), "mission request"))
            for row in rows
        )
