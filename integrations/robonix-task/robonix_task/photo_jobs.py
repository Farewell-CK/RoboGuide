"""Persist no-motion photo inspection admission before resolving its producer evidence."""

from __future__ import annotations

import hashlib
import json
import queue
import re
import sqlite3
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from robonix_task.image_inspection import ImageInspector, _no_links
from robonix_task.journal import Conflict, canonical

_TERMINAL = frozenset({"SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN"})


def _request(body: dict[str, Any]) -> tuple[str, str]:
    """Validate the original attempt and Role identity without adding execution authority."""
    if set(body) != {"attempt_id", "invocation"}:
        raise ValueError("exact_photo_job_fields_required")
    encoded = canonical(body)
    if len(encoded.encode()) > 16384:
        raise ValueError("photo_job_request_limit_exceeded")
    attempt, invocation = body["attempt_id"], body["invocation"]
    if not isinstance(attempt, str) or not 0 < len(attempt) <= 1024 or not attempt.strip():
        raise ValueError("original_attempt_required")
    if not isinstance(invocation, dict):
        raise ValueError("original_invocation_required")
    for field in ("mission_id", "group_id", "task_id", "role_id"):
        value = invocation.get(field)
        if not isinstance(value, str) or not 0 < len(value) <= 1024 or not value.strip():
            raise ValueError("complete_photo_job_role_identity_required")
    if "execution_id" in invocation and invocation["execution_id"] != attempt:
        raise ValueError("photo_job_execution_identity_mismatch")
    intent = invocation.get("intent", invocation)
    if not isinstance(intent, dict) or intent.get("operation") not in (
        "image.inspect@v1",
        {"namespace": "image", "name": "inspect", "version": "v1"},
    ):
        raise ValueError("photo_job_operation_required")
    parameters = intent.get("parameters")
    if (
        not isinstance(parameters, dict)
        or set(parameters) != {"producer_task_id", "producer_role_id", "producer_node_id"}
        or any(
            not isinstance(value, str) or not 0 < len(value) <= 1024 or not value.strip()
            for value in parameters.values()
        )
    ):
        raise ValueError("exact_photo_job_producer_binding_required")
    return attempt, encoded


class PhotoInspectionJobs:
    """Own one bounded serial worker and immutable attempts; process loss never replays fetches."""

    def __init__(
        self,
        root: Path,
        inspector: ImageInspector,
        resolver: Callable[[dict[str, Any], str], dict[str, Any]],
        *,
        max_records: int = 1000,
        max_pending: int = 4,
    ) -> None:
        """Open a versioned single-owner journal and fence all prior in-flight work as UNKNOWN."""
        if max_records <= 0 or max_pending <= 0:
            raise ValueError("positive_photo_job_bounds_required")
        self.root = root.absolute()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        _no_links(self.root)
        self.inspector = inspector
        self.resolver = resolver
        self.max_records, self.max_pending = max_records, max_pending
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.pending: queue.Queue[str] = queue.Queue(maxsize=max_pending)
        self.closed = False
        self.initialized = False
        self.db: sqlite3.Connection | None = None
        self.worker: threading.Thread | None = None
        self.owner = (self.root / "owner.lock").open("a+b")
        try:
            if sys.platform == "win32":
                import msvcrt

                self.owner.seek(0)
                if not self.owner.read(1):
                    self.owner.write(b"0")
                    self.owner.flush()
                self.owner.seek(0)
                msvcrt.locking(self.owner.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            db_path = self.root / "jobs.sqlite3"
            if db_path.is_symlink():
                raise Conflict("photo_job_journal_symlink_forbidden")
            self.db = sqlite3.connect(db_path, check_same_thread=False)
            self.db.row_factory = sqlite3.Row
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            tables = {
                row[0]
                for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if version not in {0, 1} or (version == 0 and tables):
                raise Conflict("unsupported_photo_job_journal_generation")
            if version == 1 and tables != {"meta", "jobs"}:
                raise Conflict("incomplete_photo_job_journal")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            profile = canonical(
                {
                    "schema": "roboguide.photo-inspection-jobs/v0.1",
                    "node_id": inspector.node_id,
                    "inspector_root": str(inspector.root),
                    "input_root": str(inspector.input_root),
                    "max_records": max_records,
                    "max_pending": max_pending,
                }
            )
            if version == 0:
                with self.db:
                    self.db.execute("BEGIN IMMEDIATE")
                    self.db.execute("CREATE TABLE meta (profile TEXT NOT NULL)")
                    self.db.execute(
                        "CREATE TABLE jobs (handle TEXT PRIMARY KEY, attempt TEXT UNIQUE NOT NULL, "
                        "request TEXT NOT NULL, digest TEXT NOT NULL, state TEXT NOT NULL, "
                        "cancel_requested INTEGER NOT NULL, result TEXT NOT NULL, "
                        "detail TEXT NOT NULL, "
                        "sequence INTEGER NOT NULL)"
                    )
                    self.db.execute("INSERT INTO meta VALUES (?)", (profile,))
                    self.db.execute("PRAGMA user_version=1")
            elif [row[0] for row in self.db.execute("SELECT profile FROM meta")] != [profile]:
                raise Conflict("photo_job_store_identity_changed")
            rows = self.db.execute("SELECT * FROM jobs").fetchall()
            if len(rows) > max_records:
                raise Conflict("photo_job_record_bound_exceeded")
            for row in rows:
                self._view(row)
            self.initialized = True
            with self.db:
                self.db.execute(
                    "UPDATE jobs SET state='UNKNOWN',detail='process_lost_before_original_result',"
                    "sequence=sequence+1 WHERE state IN ('PENDING','RUNNING')"
                )
            self.worker = threading.Thread(target=self._run, name="rg-photo-inspection")
            self.worker.start()
        except BaseException:
            self.close()
            raise

    def _connection(self) -> sqlite3.Connection:
        """Refuse journal operations after the owning service has closed."""
        if self.db is None:
            raise Conflict("photo_job_store_closed")
        return self.db

    def _view(self, row: sqlite3.Row) -> dict[str, Any]:
        """Rebuild a bounded receipt from exact retained request and durable result fields."""
        request = json.loads(row["request"])
        if not isinstance(request, dict):
            raise Conflict("corrupt_photo_job_request")
        attempt, encoded = _request(request)
        if (
            attempt != row["attempt"]
            or encoded != row["request"]
            or hashlib.sha256(encoded.encode()).hexdigest() != row["digest"]
            or hashlib.sha256(attempt.encode()).hexdigest() != row["handle"]
            or row["state"] not in _TERMINAL | {"PENDING", "RUNNING"}
            or row["cancel_requested"] not in {0, 1}
            or type(row["sequence"]) is not int
            or row["sequence"] < 1
        ):
            raise Conflict("corrupt_photo_job_identity_or_lifecycle")
        result = json.loads(row["result"])
        if result is not None and not isinstance(result, dict):
            raise Conflict("corrupt_photo_job_result")
        canonical(result)
        if row["state"] == "SUCCEEDED" and (
            not isinstance(result, dict) or result.get("state") != "SUCCEEDED"
        ):
            raise Conflict("successful_photo_job_missing_original_result")
        if result is not None and (
            result.get("attempt_id") != attempt
            or result.get("invocation") != request["invocation"]
            or result.get("node_id") != self.inspector.node_id
            or result != self.inspector.status(row["handle"])
        ):
            raise Conflict("photo_job_result_differs_from_original_inspection")
        return {
            "schema_version": "roboguide.photo-inspection-job/v0.1",
            "node_id": self.inspector.node_id,
            "attempt_id": attempt,
            "invocation": request["invocation"],
            "request_digest": "sha256:" + row["digest"],
            "handle": row["handle"],
            "state": row["state"],
            "cancel_requested": bool(row["cancel_requested"]),
            "sequence": row["sequence"],
            "result": result,
            "detail": row["detail"],
            "motion_authorized": False,
        }

    def submit(self, body: dict[str, Any]) -> dict[str, Any]:
        """Persist admission before enqueue; repeated original identities never fetch again."""
        attempt, encoded = _request(body)
        handle = hashlib.sha256(attempt.encode()).hexdigest()
        with self.lock:
            if self.closed:
                raise Conflict("photo_job_store_closed")
            db = self._connection()
            row = db.execute("SELECT * FROM jobs WHERE attempt=?", (attempt,)).fetchone()
            if row is not None:
                if row["request"] != encoded:
                    raise Conflict("photo_job_attempt_request_changed")
                return self._view(row)
            if db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] >= self.max_records:
                raise Conflict("photo_job_record_bound_exceeded")
            if (
                db.execute(
                    "SELECT COUNT(*) FROM jobs WHERE state IN ('PENDING','RUNNING')"
                ).fetchone()[0]
                >= self.max_pending
                or self.pending.full()
            ):
                raise Conflict("photo_job_pending_bound_exceeded")
            with db:
                db.execute(
                    "INSERT INTO jobs VALUES (?,?,?,?,?,0,'null',?,1)",
                    (
                        handle,
                        attempt,
                        encoded,
                        hashlib.sha256(encoded.encode()).hexdigest(),
                        "PENDING",
                        "durably_admitted_before_producer_resolution",
                    ),
                )
            self.pending.put_nowait(handle)
            return self.status(handle)

    def status(self, handle: str) -> dict[str, Any]:
        """Return the current original receipt without invoking any resolver or inspector."""
        if not isinstance(handle, str) or re.fullmatch(r"[a-f0-9]{64}", handle) is None:
            raise ValueError("photo_job_handle_required")
        with self.lock:
            row = (
                self._connection()
                .execute("SELECT * FROM jobs WHERE handle=?", (handle,))
                .fetchone()
            )
            if row is None:
                raise ValueError("unknown_photo_job_handle")
            return self._view(row)

    def cancel(self, handle: str) -> dict[str, Any]:
        """Latch running cancellation and report CANCELED only after its reads have exited."""
        with self.lock:
            current = self.status(handle)
            if current["state"] in _TERMINAL or current["cancel_requested"]:
                return current
            state = "CANCELED" if current["state"] == "PENDING" else current["state"]
            detail = (
                "canceled_before_resolution"
                if state == "CANCELED"
                else "cancel_waits_for_read_exit"
            )
            db = self._connection()
            with db:
                db.execute(
                    "UPDATE jobs SET state=?,cancel_requested=1,detail=?,"
                    "sequence=sequence+1 WHERE handle=?",
                    (state, detail, handle),
                )
            return self.status(handle)

    def latest(self) -> dict[str, Any]:
        """Expose the newest admitted job for this Connector's selective State export."""
        with self.lock:
            row = (
                self._connection()
                .execute("SELECT * FROM jobs ORDER BY rowid DESC LIMIT 1")
                .fetchone()
            )
            if row is None:
                return {"inspection": None}
            view = self._view(row)
            result = view["result"]
            projected = {
                key: value for key, value in view.items() if key not in {"invocation", "result"}
            }
            projected["result"] = (
                {
                    key: value
                    for key, value in result.items()
                    if key not in {"invocation", "producer_receipt"}
                }
                if isinstance(result, dict)
                else None
            )
            producer = result.get("producer_receipt") if isinstance(result, dict) else None
            if isinstance(producer, dict):
                projected["producer_receipt"] = self._producer_projection(producer)
                projected["producer_receipt_digest"] = (
                    "sha256:" + hashlib.sha256(canonical(producer).encode()).hexdigest()
                )
            if len(canonical(projected).encode()) > 49152:
                raise Conflict("photo_state_projection_exceeds_bound")
            return {"inspection": projected}

    def _producer_projection(self, producer: dict[str, Any]) -> dict[str, Any]:
        """Keep exact transfer identities in State while full source evidence stays in status."""
        keys = {
            "schema",
            "producer_attempt",
            "consumer_attempt_id",
            "consumer_attempt",
            "consumer_node_id",
            "capture_id",
            "content_digest",
            "byte_size",
            "state_source",
            "state_channel_id",
            "state_source_epoch",
            "state_sequence",
            "controller_received_at_ms",
            "controller_observed_at_ms",
            "valid_for_ms",
            "motion_authorized",
            "memory_manifest",
        }
        value = {key: item for key, item in producer.items() if key in keys}
        if len(canonical(value).encode()) > 32768:
            return {
                "evidence_projection": "oversized_details_retained_in_original_status",
                "consumer_attempt_id": producer.get("consumer_attempt_id"),
                "content_digest": producer.get("content_digest"),
            }
        return value

    def health(self) -> dict[str, Any]:
        """Report the bounded local worker separately from camera, chassis and source readiness."""
        with self.lock:
            alive = not self.closed and self.worker is not None and self.worker.is_alive()
            pending = (
                self._connection()
                .execute("SELECT COUNT(*) FROM jobs WHERE state IN ('PENDING','RUNNING')")
                .fetchone()[0]
                if alive
                else self.max_pending
            )
            return {
                "state": "ONLINE" if alive else "OFFLINE",
                "readiness_state": "READY"
                if alive and pending < self.max_pending
                else "UNAVAILABLE",
                "detail": "bounded_local_photo_worker; source_evidence_checked_per_attempt",
            }

    def _run(self) -> None:
        """Resolve and inspect one admitted job at a time without a replay-on-recovery path."""
        while not self.stopping.is_set():
            try:
                handle = self.pending.get(timeout=0.1)
            except queue.Empty:
                continue
            with self.lock:
                if self.stopping.is_set():
                    return
                current = self.status(handle)
                if current["state"] != "PENDING":
                    continue
                db = self._connection()
                with db:
                    db.execute(
                        "UPDATE jobs SET state='RUNNING',detail='resolving_original_producer',"
                        "sequence=sequence+1 WHERE handle=?",
                        (handle,),
                    )
                row = db.execute("SELECT request FROM jobs WHERE handle=?", (handle,)).fetchone()
                body = cast(dict[str, Any], json.loads(row[0]))
            result: dict[str, Any] | None = None
            state, detail = "FAILED", "producer_resolution_failed"
            try:
                resolved = self.resolver(
                    json.loads(canonical(body["invocation"])), body["attempt_id"]
                )
                if not isinstance(resolved, dict) or set(resolved) != {
                    "artifact_path",
                    "content_digest",
                    "producer_receipt",
                }:
                    raise ValueError("exact_resolved_photo_fields_required")
                producer = resolved["producer_receipt"]
                if (
                    not isinstance(producer, dict)
                    or producer.get("consumer_attempt_id") != body["attempt_id"]
                ):
                    raise ValueError("resolved_consumer_attempt_mismatch")
                with self.lock:
                    canceled = self.status(handle)["cancel_requested"]
                if canceled:
                    state, detail = "CANCELED", "read_exited_after_cancel"
                else:
                    result = self.inspector.inspect({**body, **resolved})
                    state, detail = result["state"], result["detail"]
            except Exception as error:
                detail = f"{type(error).__name__}: {str(error)[:2048]}"
            with self.lock:
                current = self.status(handle)
                if current["cancel_requested"]:
                    state, detail = "CANCELED", "all_original_reads_exited_after_cancel"
                db = self._connection()
                with db:
                    db.execute(
                        "UPDATE jobs SET state=?,result=?,detail=?,"
                        "sequence=sequence+1 WHERE handle=?",
                        (state, canonical(result), detail, handle),
                    )

    def close(self) -> None:
        """Join this worker before journal close; the caller owns inspector cleanup."""
        self.stopping.set()
        with self.lock:
            self.closed = True
        if self.worker is not None and self.worker.ident is not None:
            self.worker.join()
        with self.lock:
            if self.db is not None:
                if self.initialized:
                    with self.db:
                        self.db.execute(
                            "UPDATE jobs SET state='UNKNOWN',"
                            "detail='service_closed_before_original_result',"
                            "sequence=sequence+1 WHERE state IN ('PENDING','RUNNING')"
                        )
                self.db.close()
                self.db = None
            self.owner.close()
