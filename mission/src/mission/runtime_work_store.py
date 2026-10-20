"""Single-writer original runtime MI work and before-I/O effect journal.

This store owns provider/HTTP intent and receipts, never Controller or device
authority. An interrupted effect remains uncertain and cannot be executed again.
"""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from mission.submission_evidence import canonical_plan_digest

SCHEMA = "roboguide.runtime-mi-work/v0.1"
MAX_WORKS = 1024
MAX_EFFECTS = 16
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_STORE_BYTES = 64 * 1024 * 1024


class RuntimeWorkError(RuntimeError):
    """Refuse unknown, changed, expired or uncertain original work."""


def kernel_time() -> dict[str, Any]:
    """Read actual Linux boot/time namespace and suspend-inclusive monotonic time."""
    before = Path("/proc/self/ns/time").stat()
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    elapsed = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    after = Path("/proc/self/ns/time").stat()
    if len(boot) != 36 or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise RuntimeWorkError("actual original kernel time domain unavailable")
    return {"boot": boot, "namespace": [before.st_dev, before.st_ino], "elapsed_ns": elapsed}


def encode(document: dict[str, Any]) -> str:
    """Keep complete bounded canonical JSON without headers, credentials or lossy truncation."""
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(text.encode()) > MAX_DOCUMENT_BYTES:
        raise RuntimeWorkError("original MI document exceeds its byte bound")
    return text


class RuntimeWorkStore:
    """Own actual SQLite inode/path leases and immutable work/effect rows."""

    def __init__(self, path: Path) -> None:
        """Lock before opening SQLite; aliases cannot acquire another worker authority."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path.absolute()
        self._descriptors: list[int] = []
        try:
            for target in (path.with_name(path.name + ".worker.lock"), path):
                descriptor = os.open(target, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                self._descriptors.append(descriptor)
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            metadata = os.fstat(self._descriptors[1])
            self._inode = (metadata.st_dev, metadata.st_ino)
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS format (
                    schema TEXT NOT NULL,instance_id TEXT NOT NULL)"""
                )
                rows = connection.execute("SELECT schema,instance_id FROM format").fetchall()
                if rows and (len(rows) != 1 or rows[0][0] != SCHEMA):
                    raise RuntimeWorkError("runtime MI writer schema differs")
                if not rows:
                    self.instance_id = uuid.uuid4().hex
                    connection.execute(
                        "INSERT INTO format VALUES (?,?)", (SCHEMA, self.instance_id)
                    )
                else:
                    self.instance_id = str(rows[0][1])
                    if len(self.instance_id) != 32 or any(
                        c not in "0123456789abcdef" for c in self.instance_id
                    ):
                        raise RuntimeWorkError("original MI SQL instance identity invalid")
                connection.execute("""CREATE TABLE IF NOT EXISTS work (
                    work_id TEXT PRIMARY KEY, input_digest TEXT NOT NULL,
                    document_json TEXT NOT NULL, identity_json TEXT NOT NULL,
                    kernel_json TEXT NOT NULL, duration_ms INTEGER NOT NULL)""")
                connection.execute("""CREATE TABLE IF NOT EXISTS effects (
                    work_id TEXT NOT NULL, phase TEXT NOT NULL, request_json TEXT NOT NULL,
                    request_digest TEXT NOT NULL, status TEXT NOT NULL,
                    response_json TEXT, error_type TEXT, kernel_json TEXT NOT NULL,
                    finished_kernel_json TEXT, finished_at_ms INTEGER, nonce TEXT NOT NULL,
                    PRIMARY KEY(work_id,phase), FOREIGN KEY(work_id) REFERENCES work(work_id))""")
                connection.execute("""CREATE TABLE IF NOT EXISTS observations (
                    work_id TEXT NOT NULL, phase TEXT NOT NULL, request_digest TEXT NOT NULL,
                    proof_json TEXT NOT NULL, result_json TEXT NOT NULL,
                    PRIMARY KEY(work_id,phase), FOREIGN KEY(work_id,phase)
                    REFERENCES effects(work_id,phase))""")
        except BaseException:
            self.close()
            raise

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Use short durable transactions with the original inode lease."""
        metadata = self.path.stat()
        if (metadata.st_dev, metadata.st_ino) != self._inode:
            raise RuntimeWorkError("original runtime MI database inode changed")
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            with connection:
                yield connection
        finally:
            connection.close()

    def close(self) -> None:
        """Release explicitly at the owner, independently of inherited nonwriter descriptors."""
        for descriptor in reversed(self._descriptors):
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        self._descriptors.clear()

    def _capacity(self, connection: sqlite3.Connection, additional: int) -> None:
        """Fail closed before a side effect when immutable evidence storage is exhausted."""
        row = connection.execute("""SELECT
            COALESCE((SELECT SUM(length(CAST(document_json AS BLOB))+
              length(CAST(identity_json AS BLOB))) FROM work),0)+
            COALESCE((SELECT SUM(length(CAST(request_json AS BLOB))+
              COALESCE(length(CAST(response_json AS BLOB)),0)) FROM effects),0)+
            COALESCE((SELECT SUM(length(CAST(proof_json AS BLOB))+
              length(CAST(result_json AS BLOB))) FROM observations),0)""").fetchone()
        if row is None or int(row[0]) + additional > MAX_STORE_BYTES:
            raise RuntimeWorkError("runtime MI evidence storage capacity exhausted")

    def admit(
        self,
        work_id: str,
        document: dict[str, Any],
        identity: dict[str, Any],
        remaining_ms: int,
        issued_kernel: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist original input, configuration and budget before model work."""
        if not work_id or len(work_id) > 256 or type(remaining_ms) is not int:
            raise RuntimeWorkError("invalid original work identity")
        encoded, configured = encode(document), encode(identity)
        # Capture before SQLite waiting. A remote-source caller supplies its actual pre-read anchor.
        issued = kernel_time() if issued_kernel is None else issued_kernel
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT input_digest,identity_json,document_json FROM work WHERE work_id=?",
                (work_id,),
            ).fetchone()
            if row is not None:
                if row[0] != canonical_plan_digest(document) or row[1] != configured:
                    raise RuntimeWorkError("original runtime work source/configuration changed")
                return dict(json.loads(row[2]))
            if not 1 <= remaining_ms <= 300_000:
                raise RuntimeWorkError("original runtime work budget absent or expired")
            if connection.execute("SELECT COUNT(*) FROM work").fetchone()[0] >= MAX_WORKS:
                raise RuntimeWorkError("runtime MI retained work capacity exhausted")
            self._capacity(connection, len(encoded.encode()) + len(configured.encode()))
            connection.execute(
                "INSERT INTO work VALUES (?,?,?,?,?,?)",
                (
                    work_id,
                    canonical_plan_digest(document),
                    encoded,
                    configured,
                    encode(issued),
                    remaining_ms,
                ),
            )
        return document

    def original(self, work_id: str, identity: dict[str, Any]) -> dict[str, Any] | None:
        """Read retained original inputs so restart never replaces them with later observations."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT document_json,identity_json FROM work WHERE work_id=?", (work_id,)
            ).fetchone()
        if row is None:
            return None
        if row[1] != encode(identity):
            raise RuntimeWorkError("runtime MI implementation/configuration drift")
        return dict(json.loads(row[0]))

    def remaining(self, work_id: str) -> int:
        """Never renew on restart, another boot/namespace, regressed time or delayed HTTP."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT kernel_json,duration_ms FROM work WHERE work_id=?", (work_id,)
            ).fetchone()
        if row is None:
            raise RuntimeWorkError("original runtime MI work absent")
        original, current = json.loads(row[0]), kernel_time()
        elapsed = int(current["elapsed_ns"]) - int(original["elapsed_ns"])
        if (
            original["boot"] != current["boot"]
            or original["namespace"] != current["namespace"]
            or elapsed < 0
        ):
            return 0
        return max(0, int(row[1]) - (elapsed + 999_999) // 1_000_000)

    def perform(
        self,
        work_id: str,
        phase: str,
        request: dict[str, Any],
        operation: Callable[[float], dict[str, Any]],
    ) -> dict[str, Any]:
        """Commit before I/O; replay completed bytes and retain uncertain effects."""
        encoded = encode(request)
        remaining = self.remaining(work_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT request_digest,status,response_json FROM effects "
                "WHERE work_id=? AND phase=?",
                (work_id, phase),
            ).fetchone()
            if row is not None:
                if row[0] != canonical_plan_digest(request):
                    raise RuntimeWorkError("original MI effect input changed")
                if row[1] != "completed" or row[2] is None:
                    observed = connection.execute(
                        "SELECT request_digest,result_json FROM observations "
                        "WHERE work_id=? AND phase=?",
                        (work_id, phase),
                    ).fetchone()
                    if observed is not None and observed[0] == row[0]:
                        return dict(json.loads(observed[1]))
                    raise RuntimeWorkError(
                        "original MI effect is uncertain; read-only reconciliation required"
                    )
                return dict(json.loads(row[2]))
            if remaining <= 0:
                raise RuntimeWorkError("original MI effect budget expired")
            if (
                connection.execute(
                    "SELECT COUNT(*) FROM effects WHERE work_id=?", (work_id,)
                ).fetchone()[0]
                >= MAX_EFFECTS
            ):
                raise RuntimeWorkError("original MI effect count exhausted")
            self._capacity(connection, len(encoded.encode()) + MAX_DOCUMENT_BYTES)
            connection.execute(
                "INSERT INTO effects VALUES (?,?,?,?,?,NULL,NULL,?,NULL,NULL,?)",
                (
                    work_id,
                    phase,
                    encoded,
                    canonical_plan_digest(request),
                    "pending",
                    encode(kernel_time()),
                    uuid.uuid4().hex,
                ),
            )
        try:
            # The original budget can expire while SQLite waits; check again before actual I/O.
            remaining = self.remaining(work_id)
            if remaining <= 0:
                raise RuntimeWorkError("original MI effect expired before I/O")
            response = operation(remaining / 1000)
            finished_kernel, finished_wall = kernel_time(), time.time_ns() // 1_000_000
            serialized = encode(response)
            with self._connect() as connection:
                self._capacity(connection, len(serialized.encode()))
                connection.execute(
                    "UPDATE effects SET status='completed',response_json=?,"
                    "finished_kernel_json=?,finished_at_ms=? "
                    "WHERE work_id=? AND phase=?",
                    (serialized, encode(finished_kernel), finished_wall, work_id, phase),
                )
            return response
        except BaseException as error:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE effects SET status='uncertain',error_type=? "
                    "WHERE work_id=? AND phase=?",
                    (type(error).__name__, work_id, phase),
                )
            raise

    def retain_observation(
        self,
        work_id: str,
        phase: str,
        request: dict[str, Any],
        proof: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        """Keep read evidence separately from the original uncertain outcome."""
        proof_bytes, result_bytes = encode(proof), encode(result)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT request_digest,result_json FROM observations WHERE work_id=? AND phase=?",
                (work_id, phase),
            ).fetchone()
            if previous is not None:
                if previous != (canonical_plan_digest(request), result_bytes):
                    raise RuntimeWorkError("original read-only MI observation changed")
                return
            row = connection.execute(
                "SELECT request_digest,status FROM effects WHERE work_id=? AND phase=?",
                (work_id, phase),
            ).fetchone()
            if row is None or row[0] != canonical_plan_digest(request) or row[1] == "completed":
                raise RuntimeWorkError("observation does not match an original uncertain effect")
            self._capacity(connection, len(proof_bytes.encode()) + len(result_bytes.encode()))
            connection.execute(
                "INSERT INTO observations VALUES (?,?,?,?,?)",
                (work_id, phase, row[0], proof_bytes, result_bytes),
            )

    def observed(self, work_id: str, phase: str) -> bool:
        """Identify a retained original observation without renewing its budget or query count."""
        with self._connect() as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM observations WHERE work_id=? AND phase=?", (work_id, phase)
                ).fetchone()
                is not None
            )

    def effect_nonce(self, work_id: str, phase: str) -> str:
        """Read the nonce committed before this effect's original network permission request."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT nonce FROM effects WHERE work_id=? AND phase=?", (work_id, phase)
            ).fetchone()
        if row is None:
            raise RuntimeWorkError("original SQL effect nonce absent")
        return str(row[0])

    def effects(self, work_id: str) -> list[dict[str, Any]]:
        """Return bounded original call/command evidence without changing its interpretation."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT phase,request_json,status,response_json,error_type,kernel_json,"
                "finished_kernel_json,finished_at_ms,nonce FROM effects "
                "WHERE work_id=? ORDER BY phase LIMIT ?",
                (work_id, MAX_EFFECTS),
            ).fetchall()
        return [
            {
                "phase": phase,
                "request": json.loads(request),
                "status": status,
                "response": json.loads(response) if response else None,
                "error_type": error,
                "original_kernel": json.loads(kernel),
                "finished_kernel": json.loads(finished) if finished else None,
                "finished_at_ms": wall,
                "nonce": nonce,
            }
            for phase, request, status, response, error, kernel, finished, wall, nonce in rows
        ]
