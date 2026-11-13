"""Own original file-computation children and durable independent OS exit evidence."""

from __future__ import annotations

import json
import os
import selectors
import stat
import subprocess
import sys
import tempfile
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO

from .jobs import FileJobs
from .scoped_maps import validate_typed_input
from .store import MemoryStore, canonical, content_digest
from .use_leases import UseLeases, kernel_clock

SCHEMA = "roboguide.file-process-jobs/v0.2"
DEFAULT_ADDRESS_SPACE_BYTES = 256 * 1024 * 1024
PRIVATE_COPY_SCHEMA = "roboguide.file-private-copy-start/v0.1"
PRIVATE_COPY_RESULT_SCHEMA = "roboguide.file-private-copy-result/v0.1"
NAVIGATION_OPERATION = "mobility.navigate@v1"


class ProcessFileJobs(FileJobs):
    """Keep compute authority in the parent and reap only the exact original owned child."""

    _table = "process_jobs"

    def __init__(
        self,
        store: MemoryStore,
        leases: UseLeases | None = None,
        max_job_seconds: int = 5,
        max_job_rounds: int = 256,
        max_address_space_bytes: int = DEFAULT_ADDRESS_SPACE_BYTES,
        max_cpu_seconds: int | None = None,
    ) -> None:
        """Fence old writers and retain missing historical process evidence as unavailable."""
        if (
            type(max_address_space_bytes) is not int
            or not 64 * 1024 * 1024 <= max_address_space_bytes <= 8 * 1024**3
        ):
            raise ValueError("OS address-space quota is outside its deployment bound")
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._worker_cpu_seconds = max_job_seconds if max_cpu_seconds is None else max_cpu_seconds
        if type(self._worker_cpu_seconds) is not int or not 1 <= self._worker_cpu_seconds <= 300:
            raise ValueError("OS CPU quota is outside its deployment bound")
        self._worker_address_space_bytes = max_address_space_bytes
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
                    "pid INTEGER, exit_json BLOB, quota_json BLOB, quota_proof_json BLOB)"
                )
                self._db.execute("PRAGMA user_version=2")
            elif version == 1:
                self._db.execute("ALTER TABLE original_processes ADD COLUMN quota_json BLOB")
                self._db.execute("ALTER TABLE original_processes ADD COLUMN quota_proof_json BLOB")
                self._db.execute("UPDATE file_job_format SET schema=?", (SCHEMA,))
                self._db.execute("PRAGMA user_version=2")
            else:
                self._db.execute("SELECT exit_json FROM original_processes LIMIT 1")
            self._db.execute(
                "UPDATE process_jobs SET state='UNKNOWN',"
                "reason='original computation was interrupted' "
                "WHERE state='RUNNING'"
            )
            self._db.commit()
            self._reconcile_execution_references()
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
            unverified = self._db.execute(
                "SELECT execution_id FROM original_processes WHERE exit_json IS NULL"
            ).fetchall()
            unresolved = len(unverified)
            result["safe"] = result["safe"] is True and unresolved == 0
            result["unverified_original_processes"] = unresolved
            result["unowned_original_processes"] = sum(
                execution_id not in self._active for (execution_id,) in unverified
            )
            result["assurance"] = "software-process"
            if unresolved >= 4:
                result["readiness"] = "UNAVAILABLE"
            result["os_quota"] = {
                "schema": "roboguide.file-worker-os-quota/v0.1",
                "configured_enforcement": "posix-setrlimit",
                "scope": "new-admissions",
                "cpu_seconds": self._worker_cpu_seconds,
                "address_space_bytes": self._worker_address_space_bytes,
            }
            return result

    def reclamation_blockers(self, digest: str | None = None) -> list[str]:
        """Use exact durable execution references for active and unreaped original readers."""
        return super().reclamation_blockers(digest)

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
            "INSERT INTO original_processes (execution_id,instance_id,backend_instance_id,"
            "invocation_digest,budget_json,pid,exit_json,quota_json,quota_proof_json) "
            "VALUES (?,?,?,?,?,NULL,NULL,?,NULL)",
            (
                invocation["execution_id"],
                uuid.uuid4().hex,
                self.store.instance_id,
                content_digest(invocation),
                canonical(budget),
                canonical(
                    {
                        "schema": "roboguide.file-worker-os-quota/v0.1",
                        "cpu_seconds": self._worker_cpu_seconds,
                        "address_space_bytes": self._worker_address_space_bytes,
                    }
                ),
            ),
        )

    def quota_evidence(self, execution: str) -> dict[str, object]:
        """Observe exact original declaration/proof; old absence never receives current defaults."""
        with self._mutex:
            row = self._db.execute(
                "SELECT pid,quota_json,quota_proof_json FROM original_processes "
                "WHERE execution_id=?",
                (execution,),
            ).fetchone()
            if row is None:
                raise ValueError("original file process identity is unavailable")
            return {
                "execution_id": execution,
                "pid": row[0],
                "declaration": json.loads(row[1]) if row[1] is not None else None,
                "kernel_proof": json.loads(row[2]) if row[2] is not None else None,
            }

    def _record_quota_proof(
        self, execution: str, process: subprocess.Popen[bytes], proof: dict[str, Any]
    ) -> None:
        """Persist exact original child evidence before the first read permit is issued."""
        with self._mutex, self._db:
            row = self._db.execute(
                "SELECT pid,quota_proof_json FROM original_processes WHERE execution_id=?",
                (execution,),
            ).fetchone()
            encoded = canonical(proof)
            if row is None or row[0] != process.pid or (row[1] is not None and row[1] != encoded):
                raise ValueError("original file worker quota proof identity differs")
            self._db.execute(
                "UPDATE original_processes SET quota_proof_json=? WHERE execution_id=? AND pid=?",
                (encoded, execution, process.pid),
            )

    def _retire(self, execution: str) -> None:
        """Release only after the exact original OS wait has been durably recorded."""
        original = self._processes.get(execution)
        proof = self._db.execute(
            "SELECT exit_json FROM original_processes WHERE execution_id=?", (execution,)
        ).fetchone()
        if original is None or original.returncode is not None:
            self._active.pop(execution, None)
            if proof is not None and proof[0]:
                self._content_references.synchronize_execution(execution, 0, [], False)

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

    def _private_copy_invocation(self, invocation: dict[str, Any]) -> None:
        """Require the exact T08 navigation attempt and its single typed source."""
        if not isinstance(invocation, dict) or invocation.get("operation") != NAVIGATION_OPERATION:
            raise ValueError("private copy requires mobility.navigate@v1")
        generation = self._original_generation(invocation)
        if generation is None or invocation.get("execution_generation") != generation:
            raise ValueError("private copy requires the exact canonical attempt")
        if self.leases is None:
            raise ValueError("private copy requires current Memory use")
        memory = invocation.get("memory_preparation")
        if not isinstance(memory, dict) or set(memory) != {
            "preparation_id",
            "preparation_digest",
            "use_lease_id",
            "tenant_id",
            "inputs",
        }:
            raise ValueError("private copy requires its complete prepared context")
        inputs = memory["inputs"]
        if not isinstance(inputs, list) or len(inputs) != 1:
            raise ValueError("private copy requires exactly one typed M input")
        item = inputs[0]
        if not isinstance(item, dict) or not {
            "manifest",
            "typed_map",
            "consumer",
            "import_id",
        } <= set(item):
            raise ValueError("private copy typed M input is incomplete")
        manifest, typed = item["manifest"], item["typed_map"]
        if (
            not isinstance(manifest, dict)
            or not isinstance(typed, dict)
            or manifest.get("kind") != "spatial"
        ):
            raise ValueError("private copy requires an exact typed spatial M")
        validate_typed_input(
            typed,
            manifest,
            invocation.get("group_id"),
            self.store.scoped_typed_maps_enabled,
        )
        artifact = manifest.get("artifact")
        digest = artifact.get("content_digest") if isinstance(artifact, dict) else None
        size = artifact.get("byte_size") if isinstance(artifact, dict) else None
        if (
            not isinstance(digest, str)
            or len(digest) != 71
            or not digest.startswith("sha256:")
            or any(character not in "0123456789abcdef" for character in digest[7:])
            or type(size) is not int
            or not 0 < size <= self.store.max_bytes
            or typed.get("content_digest") != digest
            or typed.get("byte_size") != size
        ):
            raise ValueError("private copy typed M bytes are outside their exact bound")

    def private_copy(self, invocation: dict[str, Any]) -> tuple[int, dict[str, object]]:
        """Copy typed M to one owned anonymous descriptor without exposing its bytes."""
        self._private_copy_invocation(invocation)
        admission = self._admit(invocation)
        execution = invocation["execution_id"]
        if admission is None:
            raise ValueError("original private-copy descriptor cannot be replayed")
        event, use_lease, _, _ = admission
        leases = self.leases
        if leases is None:
            raise ValueError("private copy requires current Memory use")
        opened: list[BinaryIO] = []
        destination: BinaryIO | None = None
        descriptor: int | None = None
        receipt: dict[str, object] | None = None
        state, reason = "FAILED", ""

        def current_permission() -> None:
            """Fence admission, source opening, worker permits and outcome commit."""
            if use_lease is not None:
                try:
                    leases.guard(use_lease, execution)
                except ValueError as error:
                    raise InterruptedError(str(error)) from error

        try:
            current_permission()
            inputs = self._inputs(invocation)
            if len(inputs) != 1:
                raise ValueError("private copy resolved more than its exact typed M")
            manifest, path = inputs[0]
            current_permission()
            source_descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            source = os.fdopen(source_descriptor, "rb")
            opened.append(source)
            metadata = os.fstat(source_descriptor)
            artifact = manifest["artifact"]
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != artifact["byte_size"]:
                raise ValueError("private copy input is not the exact regular M")
            destination = tempfile.TemporaryFile()
            command = {
                "schema": PRIVATE_COPY_SCHEMA,
                "source": {
                    "fd": source_descriptor,
                    "digest": artifact["content_digest"],
                    "size": artifact["byte_size"],
                },
                "destination_fd": destination.fileno(),
                "max_bytes": artifact["byte_size"],
            }
            expected_ready = {
                "kind": "ready",
                "schema": PRIVATE_COPY_SCHEMA,
                "digest": artifact["content_digest"],
                "size": artifact["byte_size"],
            }

            def accepted_result(message: dict[str, Any]) -> bool:
                """Accept only the worker's exact typed-M copy receipt."""
                return message == {
                    "kind": "result",
                    "digest": artifact["content_digest"],
                    "size": artifact["byte_size"],
                }

            self._run_worker(
                invocation,
                opened,
                current_permission,
                event,
                command,
                expected_ready,
                accepted_result,
                (destination.fileno(),),
            )
            completed = os.fstat(destination.fileno())
            if (
                not stat.S_ISREG(completed.st_mode)
                or completed.st_nlink != 0
                or completed.st_size != artifact["byte_size"]
            ):
                raise ValueError("private copy did not produce its exact anonymous output")
            descriptor = os.dup(destination.fileno())
            os.set_inheritable(descriptor, False)
            os.lseek(descriptor, 0, os.SEEK_SET)
            receipt = {
                "schema": PRIVATE_COPY_RESULT_SCHEMA,
                "execution_id": execution,
                "backend_instance_id": self.store.instance_id,
                "content_digest": artifact["content_digest"],
                "byte_size": artifact["byte_size"],
            }
            state = "COMPLETED"
        except InterruptedError as error:
            state, reason = "CANCELLED", str(error)
        except (ValueError, KeyError, TypeError, OSError) as error:
            state, reason = "FAILED", str(error)[:1024]
        finally:
            for stream in opened:
                stream.close()
            try:
                with self._mutex:
                    if state == "COMPLETED":
                        try:
                            current_permission()
                        except InterruptedError as error:
                            state, reason, receipt = "CANCELLED", str(error), None
                    if event.is_set() and state == "COMPLETED":
                        state, reason, receipt = (
                            "CANCELLED",
                            "cancellation preceded private descriptor commit",
                            None,
                        )
                    self._db.execute(
                        f"UPDATE {self._table} SET state=?,result_json=?,reason=? "
                        "WHERE execution_id=?",
                        (
                            state,
                            canonical(receipt) if receipt is not None else None,
                            reason,
                            execution,
                        ),
                    )
                    self._db.commit()
                    self._retire(execution)
            except BaseException:
                if descriptor is not None:
                    os.close(descriptor)
                raise
            finally:
                if destination is not None:
                    destination.close()
                if state != "COMPLETED" and descriptor is not None:
                    os.close(descriptor)
                    descriptor = None
        if descriptor is None or receipt is None:
            raise ValueError(f"private copy {state.lower()}: {reason}")
        return descriptor, receipt

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
        expected_digests = [manifest["artifact"]["content_digest"] for manifest, _ in inputs]

        def accepted_result(message: dict[str, Any]) -> bool:
            """Accept only the existing closed hash-result response."""
            return message == {"kind": "result", "digests": expected_digests}

        result = self._run_worker(
            invocation,
            opened,
            current_permission,
            event,
            command,
            None,
            accepted_result,
        )
        return list(result["digests"])

    def _run_worker(
        self,
        invocation: dict[str, Any],
        opened: list[BinaryIO],
        current_permission: Callable[[], None],
        event: threading.Event,
        command: dict[str, Any],
        expected_ready: dict[str, Any] | None,
        accept_result: Callable[[dict[str, Any]], bool],
        extra_fds: tuple[int, ...] = (),
    ) -> dict[str, Any]:
        """Own one exact child from quota proof through permits and actual OS wait."""
        execution = invocation["execution_id"]
        with self._mutex:
            row = self._db.execute(
                "SELECT budget_json,invocation_digest,quota_json FROM original_processes "
                "WHERE execution_id=?",
                (execution,),
            ).fetchone()
        if row is None or row[1] != content_digest(invocation):
            raise ValueError("original file process invocation unavailable")
        budget = json.loads(row[0])
        quota = json.loads(row[2]) if row[2] is not None else None
        if (
            not isinstance(quota, dict)
            or set(quota) != {"schema", "cpu_seconds", "address_space_bytes"}
            or quota["schema"] != "roboguide.file-worker-os-quota/v0.1"
            or type(quota["cpu_seconds"]) is not int
            or not 1 <= quota["cpu_seconds"] <= 300
            or type(quota["address_space_bytes"]) is not int
            or not 64 * 1024 * 1024 <= quota["address_space_bytes"] <= 8 * 1024**3
        ):
            raise ValueError("original immutable file worker OS quota is unavailable")

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
            pass_fds=tuple(stream.fileno() for stream in opened) + extra_fds,
            close_fds=True,
            env={
                "PATH": os.defpath,
                "LANG": "C.UTF-8",
                "ROBOGUIDE_FILE_WORKER_CPU_SECONDS": str(quota["cpu_seconds"]),
                "ROBOGUIDE_FILE_WORKER_AS_BYTES": str(quota["address_space_bytes"]),
            },
        )
        self._processes[execution] = process
        assert process.stdin is not None and process.stdout is not None
        result: dict[str, Any] | None = None
        failure = None
        quota_observed = False
        ready_observed = expected_ready is None
        try:
            with self._mutex, self._db:
                changed = self._db.execute(
                    "UPDATE original_processes SET pid=? WHERE execution_id=? AND pid IS NULL",
                    (process.pid, execution),
                ).rowcount
                if changed != 1:
                    raise ValueError("original file process was already started")
            guard()
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
                        if (
                            isinstance(message, dict)
                            and message.get("kind") == "os-quota"
                            and not quota_observed
                        ):
                            expected = {
                                "kind": "os-quota",
                                "schema": quota["schema"],
                                "pid": process.pid,
                                "cpu_seconds": [quota["cpu_seconds"], quota["cpu_seconds"]],
                                "address_space_bytes": [
                                    quota["address_space_bytes"],
                                    quota["address_space_bytes"],
                                ],
                            }
                            if message != expected:
                                raise ValueError("actual original worker kernel quota differs")
                            self._record_quota_proof(execution, process, message)
                            quota_observed = True
                        elif (
                            expected_ready is not None
                            and message == expected_ready
                            and quota_observed
                            and not ready_observed
                            and result is None
                        ):
                            ready_observed = True
                        elif (
                            message == {"kind": "permit"}
                            and result is None
                            and quota_observed
                            and ready_observed
                        ):
                            process.stdin.write(canonical(guard()) + b"\n")
                            process.stdin.flush()
                        elif (
                            isinstance(message, dict)
                            and result is None
                            and quota_observed
                            and ready_observed
                            and accept_result(message)
                        ):
                            result = message
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
            if code != 0 or result is None or pending:
                raise ValueError(f"original file worker failed: {failure or code}")
            guard()
            return result
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
            try:
                # A buffered pipe can raise EPIPE again during close after an actual child exit.
                # Persist the independently reaped original result before closing those handles.
                if process.returncode is not None:
                    self._record_exit(execution, process, process.returncode)
            finally:
                for stream in (process.stdin, process.stdout):
                    try:
                        stream.close()
                    except BrokenPipeError:
                        pass

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
