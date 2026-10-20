"""Own original file-computation children and durable independent OS exit evidence."""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO

from .jobs import FileJobs
from .store import MemoryStore, canonical, content_digest
from .use_leases import UseLeases, kernel_clock

SCHEMA = "roboguide.file-process-jobs/v0.1"


class ProcessFileJobs(FileJobs):
    """Keep compute authority in the parent and reap only the exact original owned child."""

    _table = "process_jobs"

    def __init__(
        self,
        store: MemoryStore,
        leases: UseLeases | None = None,
        max_job_seconds: int = 5,
        max_job_rounds: int = 256,
    ) -> None:
        """Fence old writers and retain missing historical process evidence as unavailable."""
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._admitting_clock: tuple[str, str, int, int] | None = None
        self._proof_cursor = 0
        super().__init__(store, leases, max_job_seconds, max_job_rounds)
        try:
            self._db.execute("BEGIN IMMEDIATE")
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                legacy = self._db.execute(
                    "SELECT type FROM sqlite_schema WHERE name='jobs'"
                ).fetchone()
                if legacy == ("table",):
                    self._db.execute("INSERT INTO process_jobs SELECT * FROM jobs")
                    self._db.execute("DROP TABLE jobs")
                elif legacy is not None:
                    raise ValueError("legacy file jobs authority is ambiguous")
                self._db.execute("CREATE VIEW jobs AS SELECT * FROM process_jobs")
                self._db.execute("CREATE TABLE file_job_format(schema TEXT PRIMARY KEY)")
                self._db.execute("INSERT INTO file_job_format VALUES (?)", (SCHEMA,))
                self._db.execute(
                    "CREATE TABLE original_processes (execution_id TEXT PRIMARY KEY, "
                    "instance_id TEXT NOT NULL UNIQUE, backend_instance_id TEXT NOT NULL, "
                    "invocation_digest TEXT NOT NULL, budget_json BLOB NOT NULL, "
                    "pid INTEGER, exit_json BLOB)"
                )
                self._db.execute("PRAGMA user_version=1")
            else:
                self._db.execute("SELECT exit_json FROM original_processes LIMIT 1")
            self._db.execute(
                "UPDATE process_jobs SET state='UNKNOWN',"
                "reason='original computation was interrupted' "
                "WHERE state='RUNNING'"
            )
            self._db.commit()
        except BaseException:
            self._db.rollback()
            self._db.close()
            raise

    def _admit(
        self, invocation: dict[str, Any]
    ) -> tuple[threading.Event, str | None, int, int] | None:
        """Sample before original SQL waiting and commit process intent with the original job."""
        observed = kernel_clock()
        with self._mutex:
            uncertain = self._db.execute(
                "SELECT count(*) FROM original_processes p JOIN process_jobs j USING(execution_id) "
                "WHERE p.exit_json IS NULL AND j.state='UNKNOWN'"
            ).fetchone()[0]
            existing = self._db.execute(
                "SELECT 1 FROM process_jobs WHERE execution_id=?", (invocation.get("execution_id"),)
            ).fetchone()
            if existing is None and uncertain + len(self._active) >= 4:
                raise ValueError("original unverified file workers retain local capacity")
            self._admitting_clock = observed
            try:
                return super()._admit(invocation)
            finally:
                self._admitting_clock = None

    def health(self) -> dict[str, object]:
        """Keep unreaped original processes explicitly uncertain after a parent restart."""
        with self._mutex:
            result = super().health()
            unresolved = self._db.execute(
                "SELECT count(*) FROM original_processes WHERE exit_json IS NULL"
            ).fetchone()[0]
            result["safe"] = result["safe"] is True and unresolved == 0
            result["unverified_original_processes"] = unresolved
            result["assurance"] = "software-process"
            if unresolved >= 4:
                result["readiness"] = "UNAVAILABLE"
            return result

    def reclamation_blockers(self) -> list[str]:
        """Retain unknown original readers even when today's parent owns no live Popen handle."""
        with self._mutex:
            return super().reclamation_blockers() + [
                "unverified_original_process:" + row[0]
                for row in self._db.execute(
                    "SELECT execution_id FROM original_processes WHERE exit_json IS NULL "
                    "ORDER BY execution_id"
                )
            ]

    def _save_budget(self, invocation: dict[str, Any], admitted_ns: int, budget_ms: int) -> None:
        """Persist the original instance and kernel budget before process or file effects."""
        if self._admitting_clock is None:
            raise ValueError("original process admission clock unavailable")
        if self._db.execute("SELECT count(*) FROM original_processes").fetchone()[0] >= 4096:
            raise ValueError("original file process evidence retention bound reached")
        super()._save_budget(invocation, admitted_ns, budget_ms)
        boot, namespace, lower, _ = self._admitting_clock
        budget = {
            "boot_id": boot,
            "time_namespace": namespace,
            "received_lower_ms": lower,
            "deadline_lower_ms": lower + budget_ms,
        }
        self._db.execute(
            "INSERT INTO original_processes VALUES (?,?,?,?,?,NULL,NULL)",
            (
                invocation["execution_id"],
                uuid.uuid4().hex,
                self.store.instance_id,
                content_digest(invocation),
                canonical(budget),
            ),
        )

    def _retire(self, execution: str) -> None:
        """Keep local content references if an original process could not actually be reaped."""
        original = self._processes.get(execution)
        if original is None or original.returncode is not None:
            super()._retire(execution)

    def _record_exit(self, execution: str, process: subprocess.Popen[bytes], code: int) -> None:
        """Persist only an actual original wait result, retaining the immutable instance and PID."""
        with self._mutex:
            row = self._db.execute(
                "SELECT instance_id,pid,exit_json FROM original_processes WHERE execution_id=?",
                (execution,),
            ).fetchone()
            if row is None or row[1] != process.pid or process.returncode != code:
                raise ValueError("actual original file process identity differs")
            proof = canonical({"instance_id": row[0], "pid": process.pid, "exit_code": code})
            if row[2] is not None and row[2] != proof:
                raise ValueError("original OS exit evidence cannot change")
            with self._db:
                self._db.execute(
                    "UPDATE original_processes SET exit_json=? WHERE execution_id=? AND pid=?",
                    (proof, execution, process.pid),
                )

    def _hash_inputs(
        self,
        invocation: dict[str, Any],
        inputs: list[tuple[dict[str, Any], Path]],
        opened: list[BinaryIO],
        current_permission: Callable[[], None],
        event: threading.Event,
        admitted_ns: int,
        budget_ms: int,
    ) -> list[str]:
        """Grant bounded reads to an owned child only under the original kernel/use budget."""
        execution = invocation["execution_id"]
        with self._mutex:
            row = self._db.execute(
                "SELECT budget_json,invocation_digest FROM original_processes WHERE execution_id=?",
                (execution,),
            ).fetchone()
        if row is None or row[1] != content_digest(invocation):
            raise ValueError("original file process invocation unavailable")
        budget = json.loads(row[0])

        def guard() -> dict[str, Any]:
            """Recheck after use-lease/SQL waiting, immediately before each child permission."""
            current_permission()
            deadline = budget["deadline_lower_ms"]
            if self.leases is not None:
                lease_budget = self.leases.compute_budget(
                    invocation["memory_preparation"]["use_lease_id"], execution
                )
                if any(lease_budget[key] != budget[key] for key in ("boot_id", "time_namespace")):
                    raise InterruptedError("original child and use budget time domains differ")
                deadline = min(deadline, lease_budget["deadline_lower_ms"])
            boot, namespace, lower, quantum = kernel_clock()
            if (
                boot != budget["boot_id"]
                or namespace != budget["time_namespace"]
                or lower < budget["received_lower_ms"]
                or lower + quantum >= deadline
            ):
                raise TimeoutError("original file process kernel budget ended")
            if event.is_set():
                raise InterruptedError("original file process cancellation observed")
            return {
                "boot_id": boot,
                "time_namespace": namespace,
                "received_lower_ms": lower,
                "deadline_lower_ms": min(deadline, lower + 100),
            }

        guard()
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).with_name("file_worker.py"))],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            pass_fds=tuple(stream.fileno() for stream in opened),
            close_fds=True,
            env={"PATH": os.defpath, "LANG": "C.UTF-8"},
        )
        self._processes[execution] = process
        assert process.stdin is not None and process.stdout is not None
        digests = None
        failure = None
        try:
            with self._mutex, self._db:
                changed = self._db.execute(
                    "UPDATE original_processes SET pid=? WHERE execution_id=? AND pid IS NULL",
                    (process.pid, execution),
                ).rowcount
                if changed != 1:
                    raise ValueError("original file process was already started")
            guard()
            command = {
                "schema": "roboguide.file-process-start/v0.1",
                "rounds": invocation.get("parameters", {}).get("rounds", 1),
                "inputs": [
                    {
                        "fd": stream.fileno(),
                        "digest": manifest["artifact"]["content_digest"],
                        "size": manifest["artifact"]["byte_size"],
                    }
                    for (manifest, _), stream in zip(inputs, opened, strict=True)
                ],
            }
            process.stdin.write(canonical(command) + b"\n")
            process.stdin.flush()
            pending = b""
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    guard()
                    ready = selector.select(0.02)
                    if not ready:
                        if process.poll() is not None:
                            break
                        continue
                    chunk = os.read(process.stdout.fileno(), 4096)
                    if not chunk:
                        break
                    pending += chunk
                    if len(pending) > 16384:
                        raise ValueError("original file worker control response exceeds its bound")
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        message = json.loads(line)
                        if message == {"kind": "permit"} and digests is None:
                            process.stdin.write(canonical(guard()) + b"\n")
                            process.stdin.flush()
                        elif (
                            isinstance(message, dict)
                            and set(message) == {"kind", "digests"}
                            and message["kind"] == "result"
                            and message["digests"]
                            == [manifest["artifact"]["content_digest"] for manifest, _ in inputs]
                            and digests is None
                        ):
                            digests = list(message["digests"])
                        elif (
                            isinstance(message, dict)
                            and set(message) == {"kind", "reason"}
                            and message["kind"] == "error"
                            and isinstance(message["reason"], str)
                            and len(message["reason"]) <= 1024
                            and failure is None
                        ):
                            failure = message["reason"]
                        else:
                            raise ValueError(
                                "original file worker refused or changed its computation"
                            )
            code = process.wait(timeout=1)
            if code != 0 or digests is None or pending:
                raise ValueError(f"original file worker failed: {failure or code}")
            guard()
            return digests
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
            process.stdin.close()
            process.stdout.close()
            if process.returncode is not None:
                self._record_exit(execution, process, process.returncode)

    def safe_state(self) -> dict[str, object]:
        """Publish v0.3 from explicit original generation and durable actual OS wait evidence."""
        with self._mutex:
            rows = self._db.execute(
                "SELECT p.rowid,j.execution_id,j.invocation_json,j.state,"
                "p.exit_json,p.invocation_digest "
                "FROM process_jobs j JOIN original_processes p USING(execution_id) "
                "WHERE p.exit_json IS NOT NULL AND j.state IN ('COMPLETED','FAILED','CANCELLED') "
                "AND p.rowid>? ORDER BY p.rowid LIMIT 257",
                (self._proof_cursor,),
            ).fetchall()
            observations: list[dict[str, Any]] = []
            payload_size = len(canonical({"observations": []}))
            complete = True
            for index, (ordinal, execution, encoded, state, exited, invocation_digest) in enumerate(
                rows
            ):
                if index == 256:
                    complete = False
                    break
                invocation = json.loads(encoded)
                generation = invocation.get("execution_generation")
                if (
                    type(generation) is not int
                    or generation != self._original_generation(invocation)
                    or content_digest(invocation) != invocation_digest
                ):
                    self._proof_cursor = ordinal
                    continue
                proof = json.loads(exited)
                proof.update(
                    group_id=invocation["group_id"],
                    attempts=[{"execution_id": execution, "execution_generation": generation}],
                )
                observation = {
                    "execution_id": execution,
                    "execution_generation": generation,
                    "command_id": None,
                    "safe_state": True,
                    "assurance": "software-process",
                    "terminal_state": state,
                    "process_exit": proof,
                }
                additional = len(canonical(observation)) + (1 if observations else 0)
                if payload_size + additional > 60 * 1024:
                    complete = False
                    break
                payload_size += additional
                observations.append(observation)
                self._proof_cursor = ordinal
            if complete:
                self._proof_cursor = 0
            return {"observations": observations}
