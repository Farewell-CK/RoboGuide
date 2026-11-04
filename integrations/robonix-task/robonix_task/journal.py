"""Single-owner durable attempt identity and complete bounded event storage."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELED"}


class Conflict(ValueError):
    """A fixed identity, input or retained execution owner conflicts."""


def canonical(value: Any) -> str:
    """Encode immutable finite JSON identically on every request and process."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


class Journal:
    """Persist before send and retain unknown work across process restart without replay."""

    def __init__(
        self,
        directory: str | Path,
        profile: dict[str, Any],
        *,
        max_records: int = 1000,
        max_events: int = 2048,
        max_bytes: int = 8388608,
    ) -> None:
        """Acquire one OS lock, validate profile continuity and fence interrupted records."""
        self.root = Path(directory).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.effect_cache: dict[str, tuple[int, dict[str, Any]]] = {}
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
        except OSError:
            self.owner.close()
            raise Conflict("another process owns this journal") from None
        self._db: sqlite3.Connection | None = None
        try:
            self._db = sqlite3.connect(self.root / "pilot.sqlite3", check_same_thread=False)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            self.db.execute("""CREATE TABLE IF NOT EXISTS runs (
                handle TEXT PRIMARY KEY, attempt TEXT UNIQUE NOT NULL, request TEXT NOT NULL,
                digest TEXT NOT NULL, state TEXT NOT NULL, detail TEXT NOT NULL,
                session_id TEXT NOT NULL, task_id TEXT NOT NULL, abort_id TEXT NOT NULL,
                created_ns INTEGER NOT NULL, dispatched INTEGER NOT NULL DEFAULT 0,
                cancel_requested INTEGER NOT NULL DEFAULT 0, abort_sent INTEGER NOT NULL DEFAULT 0,
                sequence INTEGER NOT NULL DEFAULT 1, progress TEXT NOT NULL DEFAULT '{}')""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS events (
                handle TEXT NOT NULL, seq INTEGER NOT NULL, source TEXT NOT NULL,
                received_ns INTEGER NOT NULL, raw BLOB NOT NULL, decoded TEXT NOT NULL,
                provider TEXT NOT NULL, PRIMARY KEY(handle,seq))""")
            encoded_profile = canonical(
                {
                    "schema": "lab-pilot-journal/1",
                    "profile": profile,
                    "limits": [max_records, max_events, max_bytes],
                }
            )
            previous = self.db.execute("SELECT value FROM meta WHERE key='profile'").fetchone()
            if previous and previous[0] != encoded_profile:
                raise Conflict("journal belongs to a different immutable deployment profile")
            self.db.execute("INSERT OR IGNORE INTO meta VALUES('profile',?)", (encoded_profile,))
            self.db.execute(
                """UPDATE runs SET state='UNKNOWN',detail=?,sequence=sequence+1
                WHERE state NOT IN ('SUCCEEDED','FAILED','CANCELED','UNKNOWN')""",
                ("process restarted; no task or abort replay",),
            )
            self.db.commit()
            self.max_records, self.max_events, self.max_bytes = (
                max_records,
                max_events,
                max_bytes,
            )
        except Exception:
            self.close()
            raise

    @property
    def db(self) -> sqlite3.Connection:
        """Return the owned live connection or reject use after closing."""
        if self._db is None:
            raise RuntimeError("journal is closed")
        return self._db

    def close(self) -> None:
        """Release SQLite and the OS lock after all workers have detached."""
        if self._db is not None:
            self._db.close()
            self._db = None
        self.owner.close()

    def decode(self, row: sqlite3.Row | None) -> dict[str, Any]:
        """Return an immutable snapshot including persisted request and event progress."""
        if row is None:
            raise KeyError("execution not found")
        result = dict(row)
        result["request"] = json.loads(result["request"])
        result["progress"] = json.loads(result["progress"])
        return result

    def get(self, handle: str) -> dict[str, Any]:
        """Read only the explicit handle; absence never selects a latest run."""
        with self.lock:
            return self.decode(
                self.db.execute("SELECT * FROM runs WHERE handle=?", (handle,)).fetchone()
            )

    def find(self, attempt: str) -> dict[str, Any]:
        """Recover an original external attempt without creating or dispatching work."""
        with self.lock:
            return self.decode(
                self.db.execute("SELECT * FROM runs WHERE attempt=?", (attempt,)).fetchone()
            )

    def admit(
        self, body: dict[str, Any], validate: Callable[[dict[str, Any]], None]
    ) -> tuple[dict[str, Any], bool]:
        """Atomically return an old identity or retain a new slot before any native call."""
        encoded = canonical(body)
        if len(encoded.encode()) > 65536:
            raise ValueError("request exceeds 64 KiB")
        attempt = body.get("attempt_id")
        if not isinstance(attempt, str) or not 1 <= len(attempt) <= 512:
            raise ValueError("explicit bounded attempt_id is required")
        with self.lock, self.db:
            old = self.db.execute("SELECT * FROM runs WHERE attempt=?", (attempt,)).fetchone()
            if old is not None:
                if old["request"] != encoded:
                    raise Conflict("attempt input changed")
                return self.decode(old), False
            if self.db.execute("SELECT count(*) FROM runs").fetchone()[0] >= self.max_records:
                raise Conflict("journal capacity reached; no destructive GC")
            occupied = self.db.execute(
                "SELECT handle FROM runs WHERE state NOT IN ('SUCCEEDED','FAILED','CANCELED')"
            ).fetchone()
            if occupied:
                raise Conflict("slot retained by " + occupied[0])
            validate(body)
            handle, session, task_id, abort_id = (str(uuid.uuid4()) for _ in range(4))
            self.db.execute(
                """INSERT INTO runs(handle,attempt,request,digest,state,detail,
                session_id,task_id,abort_id,created_ns) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    handle,
                    attempt,
                    encoded,
                    hashlib.sha256(encoded.encode()).hexdigest(),
                    "PENDING",
                    "durable local admission; Pilot acceptance not established",
                    session,
                    task_id,
                    abort_id,
                    time.time_ns(),
                ),
            )
            return self.get(handle), True

    def begin(self, handle: str) -> bool:
        """Commit the uncertain send boundary once, serialized against pre-send cancel."""
        with self.lock, self.db:
            row = self.get(handle)
            if row["state"] != "PENDING" or row["cancel_requested"]:
                return False
            self.db.execute(
                "UPDATE runs SET dispatched=1,state='DISPATCHING',detail=?,"
                "sequence=sequence+1 WHERE handle=?",
                ("native request may be sent; never replay on ambiguity", handle),
            )
            return True

    def mark_unknown(self, handle: str, reason: str) -> None:
        """Retain the slot and original identity without overwriting a proven no-effect terminal."""
        with self.lock, self.db:
            self.db.execute(
                """UPDATE runs SET state='UNKNOWN',detail=?,sequence=sequence+1
                WHERE handle=? AND state NOT IN ('SUCCEEDED','FAILED','CANCELED')""",
                (reason, handle),
            )

    def record_planning_error(self, handle: str, evidence: dict[str, Any]) -> None:
        """Retain an attributed native planning error without inferring that work has stopped."""
        if evidence.get("error") != "truncated JSON response" or evidence.get("source") not in {
            "native-rpc",
            "native-pilot-log",
        }:
            raise ValueError("unsupported planning failure evidence")
        with self.lock, self.db:
            row = self.get(handle)
            if evidence.get("session_id") != row["session_id"]:
                raise ValueError("planning error belongs to another session")
            progress = row["progress"]
            progress["planning_error"] = evidence
            self.db.execute(
                "UPDATE runs SET progress=?,sequence=sequence+1 WHERE handle=?",
                (canonical(progress), handle),
            )

    def finish_idle_planning_failure(self, handle: str) -> bool:
        """Close a parser failure after successful prior plans and scoped native idle evidence."""
        with self.lock, self.db:
            row = self.get(handle)
            progress = row["progress"]
            error = progress.get("planning_error")
            if row["state"] != "UNKNOWN" or not error:
                return False
            if progress.get("turn_id") != row["task_id"]:
                return False
            events = self.events(handle)
            idle = False
            roots: dict[str, int] = {}
            states: dict[tuple[str, int], int] = {}
            for record in events:
                event = json.loads(record["decoded"])
                if event.get("session_id") != row["session_id"] or (
                    json.loads(record["provider"]) != progress.get("provider")
                ):
                    return False
                if record["source"] == "task":
                    if event["kind"] == "final_text":
                        return False
                    plan = event.get("plan", {})
                    if plan.get("plan_id"):
                        roots[plan["plan_id"]] = plan.get("root_index", 0)
                    observations = [event.get("node_state", {})]
                    observations.extend(event.get("batch_result", {}).get("results", []))
                    for observation in observations:
                        if observation.get("plan_id"):
                            states[(observation["plan_id"], observation.get("node_index", 0))] = (
                                observation.get("state", 0)
                            )
                status = event.get("status", {})
                if record["source"] == "abort":
                    idle = (
                        status.get("session_id") == row["session_id"]
                        and status.get("state") == 1
                        and status.get("message")
                        == "no active turn or running plan exists for this session"
                    )
            if (
                not idle
                or set(roots) != set(progress.get("plan_ids", []))
                or any(states.get((plan_id, index)) != 2 for plan_id, index in roots.items())
            ):
                return False
            detail = canonical(
                {
                    "basis": "robonix-preplan-failure-and-idle/v1",
                    "error": error,
                    "previous_detail": row["detail"],
                    "native_idle_confirmed": True,
                    "completed_plan_ids": sorted(roots),
                    "session_id": row["session_id"],
                    "request_task_id": row["task_id"],
                }
            )
            self.db.execute(
                "UPDATE runs SET state='FAILED',detail=?,sequence=sequence+1 WHERE handle=?",
                (detail, handle),
            )
            return True

    def finish_unscheduled_cancel(self, handle: str) -> bool:
        """Close an interrupted original Pilot turn that emitted no execution plans."""
        with self.lock, self.db:
            row = self.get(handle)
            progress = row["progress"]
            if row["state"] in TERMINAL or not row["cancel_requested"] or progress.get("plan_ids"):
                return False
            if progress.get("turn_id") != row["task_id"]:
                return False
            interrupted = False
            for record in self.events(handle):
                event = json.loads(record["decoded"])
                if event.get("session_id") != row["session_id"] or json.loads(
                    record["provider"]
                ) != progress.get("provider"):
                    return False
                if record["source"] != "task":
                    continue
                if event["kind"] in {"plan", "batch_result", "node_state", "final_text"}:
                    return False
                status = event.get("status", {})
                if (
                    status.get("session_id") == row["session_id"]
                    and status.get("state") == 2
                    and status.get("message") == "interrupted"
                ):
                    interrupted = True
            if not interrupted:
                return False
            self.db.execute(
                "UPDATE runs SET state='CANCELED',detail=?,sequence=sequence+1 WHERE handle=?",
                (
                    canonical(
                        {
                            "basis": "native-original-turn-interrupted-before-plan/v1",
                            "session_id": row["session_id"],
                            "request_task_id": row["task_id"],
                        }
                    ),
                    handle,
                ),
            )
            return True

    def cancel(self, handle: str) -> bool:
        """Persist cancellation first; only definitely unsent work is terminal here."""
        with self.lock, self.db:
            row = self.get(handle)
            if row["state"] in TERMINAL:
                return False
            self.db.execute(
                "UPDATE runs SET cancel_requested=1,sequence=sequence+1 WHERE handle=?",
                (handle,),
            )
            if not row["dispatched"]:
                self.db.execute(
                    "UPDATE runs SET state='CANCELED',detail=? WHERE handle=?",
                    (
                        "canceled before native send boundary; no task dispatched",
                        handle,
                    ),
                )
                return False
            return not bool(row["abort_sent"])

    def begin_abort(self, handle: str) -> bool:
        """Persist one scoped abort attempt, including its uncertain response window."""
        with self.lock, self.db:
            row = self.get(handle)
            if (
                row["state"] in TERMINAL
                or not row["cancel_requested"]
                or row["abort_sent"]
                or not row["dispatched"]
            ):
                return False
            self.db.execute(
                "UPDATE runs SET abort_sent=1,sequence=sequence+1 WHERE handle=?",
                (handle,),
            )
            return True

    def record_withheld_cancel(self, handle: str) -> bool:
        """Record positive suppression before original RPC initiation, retaining history."""
        with self.lock, self.db:
            row = self.get(handle)
            if (
                row["state"] != "DISPATCHING"
                or not row["cancel_requested"]
                or row["abort_sent"]
                or self.events(handle)
            ):
                return False
            progress = row["progress"]
            progress["native_send_withheld"] = True
            self.db.execute(
                "UPDATE runs SET state='CANCELED',detail=?,progress=?,"
                "sequence=sequence+1 WHERE handle=?",
                (
                    "original RPC withheld by final pre-send cancellation guard",
                    canonical(progress),
                    handle,
                ),
            )
            return True

    def append(
        self, handle: str, source: str, raw: bytes, event: dict[str, Any], provider: dict[str, Any]
    ) -> None:
        """Store exact wire bytes plus full fields and source-local sequence before reducing."""
        with self.lock, self.db:
            row = self.get(handle)
            size = len(raw) + len(canonical(event).encode()) + len(canonical(provider).encode())
            counts = self.db.execute(
                "SELECT count(*),coalesce(sum(length(raw)+length(cast(decoded as blob))"
                "+length(cast(provider as blob))),0) FROM events WHERE handle=?",
                (handle,),
            ).fetchone()
            if (
                len(raw) > 262144
                or counts[0] >= self.max_events
                or counts[1] + size > self.max_bytes
            ):
                raise ValueError("event storage bound reached")
            seq = counts[0] + 1
            self.db.execute(
                "INSERT INTO events VALUES(?,?,?,?,?,?,?)",
                (
                    handle,
                    seq,
                    source,
                    time.time_ns(),
                    raw,
                    canonical(event),
                    canonical(provider),
                ),
            )
            if event["session_id"] != row["session_id"]:
                raise ValueError("foreign session event")
            progress = row["progress"]
            if source == "task":
                progress["pilot_event_observed"] = True
                plans = set(progress.get("plan_ids", []))
                for field in ("plan", "batch_result", "node_state"):
                    if event.get(field, {}).get("plan_id"):
                        plans.add(event[field]["plan_id"])
                if "task_state" in event:
                    progress["task_state"] = event["task_state"]
                    progress["task_state_sequence"] = seq
                    for item in event["task_state"].get("tasks", []):
                        plans.update(item.get("plan_ids", []))
                if "status" in event and event["status"].get("message", "").startswith("turn_id="):
                    progress["turn_id"] = event["status"]["message"][8:]
                if "status" in event:
                    progress["session_status"] = event["status"]
                if event.get("final_text"):
                    progress["final_text"] = event["final_text"]
                if event.get("plan", {}).get("plan_id"):
                    progress["last_plan_sequence"] = seq
                progress["plan_ids"] = sorted(plans)
                progress["last_kind"] = event["kind"]
            progress["event_count"] = seq
            # A later observation cannot heal a fenced Unknown or prove task completion.
            state = (
                "RUNNING" if source == "task" and row["state"] == "DISPATCHING" else row["state"]
            )
            self.db.execute(
                "UPDATE runs SET progress=?,state=?,sequence=sequence+1 WHERE handle=?",
                (canonical(progress), state, handle),
            )

    def finish_task(self, handle: str, *, reconcile: bool = False) -> bool:
        """Reduce retained task evidence, optionally reconciling an earlier parser fence."""
        with self.lock, self.db:
            row = self.get(handle)
            allowed_states = {"RUNNING", "UNKNOWN"} if reconcile else {"RUNNING"}
            if row["state"] not in allowed_states:
                return False
            progress = row["progress"]
            records = self.events(handle)
            task_events = []
            idle_abort = False
            for record in records:
                event = json.loads(record["decoded"])
                if event.get("session_id") != row["session_id"] or json.loads(
                    record["provider"]
                ) != progress.get("provider"):
                    return False
                if record["source"] == "task":
                    task_events.append(event)
                elif record["source"] == "abort":
                    status = event.get("status", {})
                    idle_abort = (
                        status.get("session_id") == row["session_id"]
                        and status.get("state") == 1
                        and status.get("message")
                        == "no active turn or running plan exists for this session"
                    )
            # Native Pilot closes SubmitTask immediately after FinalText, before its later
            # SessionStatus::Completed can reach this subscriber. TaskState carries the result.
            if not task_events or task_events[-1].get("kind") != "final_text":
                return False
            if progress.get("turn_id") != row["task_id"]:
                return False
            if row["cancel_requested"] and not (reconcile and idle_abort):
                return False
            roots = {}
            states = {}
            for event in task_events:
                plan = event.get("plan", {})
                if plan.get("plan_id"):
                    roots[plan["plan_id"]] = plan.get("root_index", 0)
                observations = [event.get("node_state", {})]
                observations.extend(event.get("batch_result", {}).get("results", []))
                for observation in observations:
                    if observation.get("plan_id"):
                        states[(observation["plan_id"], observation.get("node_index", 0))] = (
                            observation.get("state", 0)
                        )
            if any(
                states.get((plan_id, root)) not in {2, 3, 4, 5} for plan_id, root in roots.items()
            ):
                return False
            snapshot = progress.get("task_state", {})
            tasks = snapshot.get("tasks", [])
            owned_plans = {plan_id for item in tasks for plan_id in item.get("plan_ids", [])}
            if (
                progress.get("task_state_sequence", 0) < progress.get("last_plan_sequence", 0)
                or owned_plans != set(roots)
                or not set(progress.get("plan_ids", [])).issubset(owned_plans)
            ):
                return False
            # Each attempt owns a new session and exactly one submitted user message.
            owners = [item for item in tasks if "m1" in item.get("message_ids", [])]
            if len(owners) != 1 or any(
                item.get("status") not in {"done", "failed", "superseded"}
                or any(mid != "m1" for mid in item.get("message_ids", []))
                for item in tasks
            ):
                return False
            original = owners[0]
            if (
                not original.get("task_id")
                or snapshot.get("task_id") != original["task_id"]
                or snapshot.get("status") != original.get("status")
                or original.get("status") not in {"done", "failed"}
            ):
                return False
            detail = canonical(
                {
                    "basis": "robonix-pilot-task-result/v1",
                    "session_id": row["session_id"],
                    "request_task_id": row["task_id"],
                    "pilot_task_id": original["task_id"],
                    "native_status": original["status"],
                    "reason": original.get("status_reason", ""),
                    "plan_ids": original.get("plan_ids", []),
                    "result_text": progress.get("final_text", ""),
                    "reconciled": reconcile,
                    "previous_state": row["state"],
                    "previous_detail": row["detail"],
                    "evidence_event_count": len(records),
                }
            )
            state = "SUCCEEDED" if original["status"] == "done" else "FAILED"
            self.db.execute(
                "UPDATE runs SET state=?,detail=?,sequence=sequence+1 WHERE handle=?",
                (state, detail, handle),
            )
            return True

    def bind_provider(self, handle: str, provider: dict[str, Any]) -> None:
        """Persist the discovered registration before sending the native Task."""
        with self.lock, self.db:
            row = self.get(handle)
            progress = row["progress"]
            old = progress.get("provider")
            if old is not None and old != provider:
                raise Conflict("native provider identity changed")
            progress["provider"] = provider
            self.db.execute(
                "UPDATE runs SET progress=?,sequence=sequence+1 WHERE handle=?",
                (canonical(progress), handle),
            )

    def events(self, handle: str) -> list[dict[str, Any]]:
        """Read the retained ordered audit stream; raw bytes remain available in SQLite."""
        with self.lock:
            return [
                dict(row)
                for row in self.db.execute(
                    "SELECT * FROM events WHERE handle=? ORDER BY seq", (handle,)
                )
            ]

    def no_effects_observations(self) -> dict[str, Any]:
        """Export original canceled attempts with positive no-dispatch evidence."""
        observations = []
        encoded_bytes = 32
        with self.lock:
            records = self.db.execute(
                "SELECT * FROM runs WHERE state='CANCELED' ORDER BY created_ns DESC LIMIT 256"
            ).fetchall()
            for record in records:
                row = self.decode(record)
                invocation = row["request"].get("invocation", {})
                generation = invocation.get("execution_generation")
                if (
                    isinstance(generation, bool)
                    or not isinstance(generation, int)
                    or generation <= 0
                ):
                    continue
                cached = self.effect_cache.get(row["handle"])
                if cached and cached[0] == row["sequence"]:
                    addition = len(canonical(cached[1]).encode()) + 1
                    if encoded_bytes + addition > 32768:
                        break
                    encoded_bytes += addition
                    observations.append(cached[1])
                    continue
                if not row["cancel_requested"] or row["progress"].get("plan_ids"):
                    continue
                events = self.events(row["handle"])
                if row["dispatched"] and not row["progress"].get("native_send_withheld"):
                    if row["progress"].get("turn_id") != row["task_id"]:
                        continue
                    interrupted = False
                    valid = True
                    for event_row in events:
                        event = json.loads(event_row["decoded"])
                        if event.get("session_id") != row["session_id"] or json.loads(
                            event_row["provider"]
                        ) != row["progress"].get("provider"):
                            valid = False
                            break
                        if event_row["source"] != "task":
                            continue
                        if event["kind"] in {"plan", "batch_result", "node_state", "final_text"}:
                            valid = False
                            break
                        status = event.get("status", {})
                        interrupted |= (
                            status.get("session_id") == row["session_id"]
                            and status.get("state") == 2
                            and status.get("message") == "interrupted"
                        )
                    if not valid or not interrupted:
                        continue
                elif events:
                    continue
                evidence = {
                    "request_digest": row["digest"],
                    "handle": row["handle"],
                    "dispatched": row["dispatched"],
                    "sequence": row["sequence"],
                    "events": [
                        {
                            "source": event["source"],
                            "raw_sha256": hashlib.sha256(event["raw"]).hexdigest(),
                            "decoded": event["decoded"],
                            "provider": event["provider"],
                        }
                        for event in events
                    ],
                }
                observation = {
                    "execution_id": row["attempt"],
                    "execution_generation": generation,
                    "command_id": "cancel-" + row["attempt"],
                    "terminal_state": "CANCELLED",
                    "safe_state": True,
                    "assurance": "no-effects",
                    "task_effects": {
                        "native_session_id": row["session_id"],
                        "native_task_id": row["task_id"],
                        "disposition": "no-effects",
                        "evidence_digest": "sha256:"
                        + hashlib.sha256(canonical(evidence).encode()).hexdigest(),
                    },
                }
                self.effect_cache[row["handle"]] = (row["sequence"], observation)
                addition = len(canonical(observation).encode()) + 1
                if encoded_bytes + addition > 32768:
                    break
                encoded_bytes += addition
                observations.append(observation)
        return {"observations": observations}
