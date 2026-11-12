"""Durable observation-only posture verification jobs in the existing MI database."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from mission.runtime_work_store import encode
from mission.submission_evidence import canonical_plan_digest

SCHEMA = "roboguide.mi-posture-verification/v0.1"
MAX_JOBS = 8192
MAX_ACTIVE_JOBS = 64


class PostureVerificationError(RuntimeError):
    """Refuse unsupported, detached or uncertain original posture evidence."""


class PostureVerificationStore:
    """Persist frozen admission inputs, observation anchors and publication receipts."""

    def __init__(self, database: Path) -> None:
        """Create namespaced tables without changing historical Request records."""
        self.database = database
        database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS mission_posture_format (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), schema TEXT NOT NULL)""")
            prior = connection.execute("SELECT schema FROM mission_posture_format").fetchall()
            if prior and prior != [(SCHEMA,)]:
                raise PostureVerificationError("unknown MI posture storage generation")
            connection.execute(
                "INSERT OR IGNORE INTO mission_posture_format VALUES (1,?)", (SCHEMA,)
            )
            connection.execute("""CREATE TABLE IF NOT EXISTS mission_posture_jobs (
                job_id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
                input_digest TEXT NOT NULL, input_json TEXT NOT NULL,
                status TEXT NOT NULL, state_json TEXT NOT NULL, state_digest TEXT NOT NULL,
                UNIQUE(mission_id,tenant_id,input_digest))""")
            connection.execute("""CREATE TABLE IF NOT EXISTS mission_posture_sequences (
                source_id TEXT NOT NULL, source_revision INTEGER NOT NULL,
                sequence INTEGER NOT NULL, PRIMARY KEY(source_id,source_revision))""")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Use short fully durable transactions owned by the existing MI process lease."""
        connection = sqlite3.connect(self.database, timeout=5)
        try:
            connection.execute("PRAGMA synchronous=FULL")
            with connection:
                yield connection
        finally:
            connection.close()

    def admit(self, documents: list[dict[str, Any]]) -> None:
        """Freeze all explicitly declared goals before the sole Mission admission effect."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for document in documents:
                digest = canonical_plan_digest(document)
                job_id = "posture-" + digest.removeprefix("sha256:")
                prior = connection.execute(
                    "SELECT input_digest,input_json FROM mission_posture_jobs WHERE job_id=?",
                    (job_id,),
                ).fetchone()
                if prior is not None:
                    if prior != (digest, encode(document)):
                        raise PostureVerificationError("original posture input changed")
                    continue
                if (
                    connection.execute("SELECT COUNT(*) FROM mission_posture_jobs").fetchone()[0]
                    >= MAX_JOBS
                ):
                    raise PostureVerificationError(
                        "retained posture verification capacity exhausted"
                    )
                if (
                    connection.execute(
                        "SELECT COUNT(*) FROM mission_posture_jobs "
                        "WHERE status NOT IN ('complete','manual')"
                    ).fetchone()[0]
                    >= MAX_ACTIVE_JOBS
                ):
                    raise PostureVerificationError("active posture verification capacity exhausted")
                connection.execute(
                    "INSERT INTO mission_posture_jobs VALUES (?,?,?,?,?,'pending','{}',?)",
                    (
                        job_id,
                        document["mission_id"],
                        document["tenant_id"],
                        digest,
                        encode(document),
                        canonical_plan_digest({"status": "pending", "state": {}}),
                    ),
                )

    def jobs(self, *, mission_id: str | None = None, active: bool = False) -> list[dict[str, Any]]:
        """Read original jobs while detecting accidental source-document corruption."""
        where, parameters = (" WHERE mission_id=?", (mission_id,)) if mission_id else ("", ())
        if active:
            where += (" AND" if where else " WHERE") + " status NOT IN ('complete','manual')"
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT job_id,input_digest,input_json,status,state_json,state_digest "
                "FROM mission_posture_jobs" + where + " ORDER BY job_id",
                parameters,
            ).fetchall()
        result = []
        for identity, digest, source, status, state, state_digest in rows:
            document = json.loads(source)
            progress = json.loads(state)
            if (
                canonical_plan_digest(document) != digest
                or identity != "posture-" + digest.removeprefix("sha256:")
                or canonical_plan_digest({"status": status, "state": progress}) != state_digest
                or status not in {"pending", "observing", "publishing", "complete", "manual"}
            ):
                raise PostureVerificationError("original posture input or progress digest differs")
            result.append(
                {
                    "job_id": identity,
                    "input": document,
                    "status": status,
                    "state": progress,
                }
            )
        return result

    def save(self, job: dict[str, Any]) -> None:
        """Persist observation progress without altering the original goal or plan binding."""
        with self._connect() as connection:
            changed = connection.execute(
                "UPDATE mission_posture_jobs SET status=?,state_json=?,state_digest=? "
                "WHERE job_id=? AND input_digest=?",
                (
                    job["status"],
                    encode(job["state"]),
                    canonical_plan_digest({"status": job["status"], "state": job["state"]}),
                    job["job_id"],
                    canonical_plan_digest(job["input"]),
                ),
            ).rowcount
            if changed != 1:
                raise PostureVerificationError("posture job disappeared or changed")

    def prepare_publication(self, job: dict[str, Any], evidence: dict[str, Any]) -> None:
        """Allocate the durable source sequence and freeze the complete before-I/O payload."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            source = (evidence["source_id"], evidence["source_revision"])
            prior = connection.execute(
                "SELECT sequence FROM mission_posture_sequences "
                "WHERE source_id=? AND source_revision=?",
                source,
            ).fetchone()
            evidence["sequence"] = (int(prior[0]) if prior else 0) + 1
            connection.execute(
                "INSERT INTO mission_posture_sequences VALUES (?,?,?) "
                "ON CONFLICT(source_id,source_revision) DO UPDATE SET sequence=excluded.sequence",
                (*source, evidence["sequence"]),
            )
            job["state"]["evidence"] = evidence
            job["status"] = "publishing"
            connection.execute(
                "UPDATE mission_posture_jobs SET status=?,state_json=?,state_digest=? "
                "WHERE job_id=?",
                (
                    job["status"],
                    encode(job["state"]),
                    canonical_plan_digest({"status": job["status"], "state": job["state"]}),
                    job["job_id"],
                ),
            )
