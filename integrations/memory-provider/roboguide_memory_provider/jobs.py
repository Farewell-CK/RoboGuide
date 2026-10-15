"""Run actual bounded file hashing under durable once-only Local How identities."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO

from .store import MemoryStore, canonical, content_digest


class FileJobs:
    """Own software computations over declared files, without any robot or Task authority."""

    def __init__(self, store: MemoryStore) -> None:
        """Reopen original outcomes and fence interrupted computations without repeating them."""
        self.store = store
        self._mutex = threading.RLock()
        self._active: dict[str, threading.Event] = {}
        self._db = sqlite3.connect(store.root / "file-jobs.sqlite3", check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS jobs (execution_id TEXT PRIMARY KEY, "
            "invocation_json BLOB, state TEXT NOT NULL, result_json BLOB, reason TEXT NOT NULL)"
        )
        self._db.execute(
            "UPDATE jobs SET state='UNKNOWN',reason='original computation was interrupted' "
            "WHERE state='RUNNING'"
        )
        self._db.commit()

    def close(self) -> None:
        """Close only after the HTTP owner has joined every actual request worker."""
        with self._mutex:
            if self._active:
                raise RuntimeError("actual file computations are still active")
            self._db.close()

    def stop(self) -> None:
        """Request cancellation without asserting that active computation has stopped."""
        with self._mutex:
            for event in self._active.values():
                event.set()

    def health(self) -> dict[str, object]:
        """Observe the actual SQLite owner and current active computation set."""
        with self._mutex:
            ready = self._db.execute("SELECT 1").fetchone() == (1,)
            return {
                "ready": ready,
                "state": "ONLINE" if ready else "OFFLINE",
                "readiness": "READY" if ready and len(self._active) < 4 else "UNAVAILABLE",
                "detail": "actual file backend and bounded computation queue",
                "backend_instance_id": self.store.instance_id,
                "active_executions": sorted(self._active),
                "safe": not self._active,
                "assurance": "software-computation",
            }

    def status(self, execution_id: str) -> dict[str, object]:
        """Read the exact original outcome, with unavailable evidence left UNKNOWN."""
        with self._mutex:
            row = self._db.execute(
                "SELECT state,result_json,reason FROM jobs WHERE execution_id=?", (execution_id,)
            ).fetchone()
            return {
                "execution_id": execution_id,
                "state": row[0] if row else "UNKNOWN",
                "result": json.loads(row[1]) if row and row[1] is not None else None,
                "reason": row[2] if row else "original computation was not admitted",
                "backend_instance_id": self.store.instance_id,
            }

    def cancel(self, execution_id: str) -> dict[str, object]:
        """Fence a late start or request a current worker stop, without early CANCELLED status."""
        with self._mutex:
            event = self._active.get(execution_id)
            if event is not None:
                event.set()
            else:
                self._db.execute(
                    "INSERT OR IGNORE INTO jobs VALUES (?,NULL,'CANCELLED',NULL,"
                    "'durable cancellation preceded computation admission')",
                    (execution_id,),
                )
                self._db.commit()
            return {"accepted": True, **self.status(execution_id)}

    def _inputs(self, invocation: dict[str, Any]) -> list[tuple[dict[str, Any], Path]]:
        """Resolve only exact admitted imports or explicit startup exports, never caller paths."""
        prepared = invocation.get("memory_preparation", {}).get("inputs", [])
        if not isinstance(prepared, list) or len(prepared) > 8:
            raise ValueError("file computation requires bounded prepared inputs")
        inputs = []
        with self.store._mutex:
            for item in prepared:
                if item["consumer"]["provider_id"] != self.store.provider_id:
                    raise ValueError("computation cannot read another Local EAIOS provider")
                row = self.store._db.execute(
                    "SELECT original_json,manifest_json FROM imports WHERE import_id=?",
                    (item["import_id"],),
                ).fetchone()
                if row is None or json.loads(row[1]) != item["manifest"]:
                    raise ValueError("computation input differs from original imported metadata")
                original = json.loads(row[0])
                if self.store.availability(original)["available"] is not True:
                    raise ValueError("computation input is not currently available")
                manifest = item["manifest"]
                scope = manifest.get("scope", {})
                if scope.get("kind") == "execution_group" and scope.get(
                    "execution_group_id"
                ) != invocation.get("group_id"):
                    raise ValueError("computation input belongs to another semantic Group")
                digest, _ = self.store._artifact(manifest)
                inputs.append((manifest, self.store._content / digest[7:]))
            if not inputs:
                parameters = invocation.get("parameters", {})
                matches = [
                    binding
                    for binding in self.store._exports
                    if binding["manifest"].get("selector")
                    == {
                        "memory_id": parameters.get("input_memory_id"),
                        "revision_id": parameters.get("input_revision_id"),
                    }
                ]
                if len(matches) != 1:
                    raise ValueError("computation source is not a unique declared export")
                inputs.append((matches[0]["manifest"], self.store._export_path(matches[0])))
        return inputs

    def compute(self, invocation: dict[str, Any]) -> dict[str, object]:
        """Hash original file descriptors under once-only admission and report actual outcomes."""
        execution = invocation.get("execution_id")
        if not isinstance(execution, str) or not execution or len(execution) > 1024:
            raise ValueError("actual computation requires an exact bounded execution identity")
        parameters = invocation.get("parameters", {})
        rounds = parameters.get("rounds", 1)
        if isinstance(rounds, bool) or not isinstance(rounds, int) or not 1 <= rounds <= 256:
            raise ValueError("file hashing rounds must be an integer from one through 256")
        encoded = canonical(invocation)
        if len(encoded) > 512 * 1024:
            raise ValueError("actual computation metadata exceeds its bound")
        with self._mutex:
            original = self._db.execute(
                "SELECT invocation_json FROM jobs WHERE execution_id=?", (execution,)
            ).fetchone()
            if original is not None:
                if original[0] is not None and original[0] != encoded:
                    raise ValueError("computation identity conflicts with its original invocation")
                return self.status(execution)
            if len(self._active) >= 4:
                raise ValueError("bounded actual computation workers are busy")
            event = threading.Event()
            self._db.execute(
                "INSERT INTO jobs VALUES (?,?,'RUNNING',NULL,'')", (execution, encoded)
            )
            self._db.commit()
            self._active[execution] = event
        opened: list[BinaryIO] = []
        state, reason, result = "FAILED", "", None
        started = time.monotonic()
        try:
            inputs = self._inputs(invocation)
            for manifest, path in inputs:
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                stream: BinaryIO = os.fdopen(descriptor, "rb")
                opened.append(stream)
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise ValueError("actual computation input is not a regular file")
                if os.fstat(descriptor).st_size != manifest["artifact"]["byte_size"]:
                    raise ValueError("actual computation input size changed")
            digests: list[str] = []
            for _ in range(rounds):
                if event.is_set():
                    raise InterruptedError("actual computation cancellation observed")
                if time.monotonic() - started >= 5:
                    raise TimeoutError("original actual computation budget expired")
                digests = []
                for (manifest, _), stream in zip(inputs, opened, strict=True):
                    stream.seek(0)
                    hashed = hashlib.sha256()
                    while chunk := stream.read(1024 * 1024):
                        if event.is_set():
                            raise InterruptedError("actual computation cancellation observed")
                        if time.monotonic() - started >= 5:
                            raise TimeoutError("original actual computation budget expired")
                        hashed.update(chunk)
                    digest = "sha256:" + hashed.hexdigest()
                    if digest != manifest["artifact"]["content_digest"]:
                        raise ValueError("actual computation bytes differ from original content")
                    digests.append(digest)
            result = {
                "schema": "roboguide.file-hash-result/v0.1",
                "execution_id": execution,
                "backend_instance_id": self.store.instance_id,
                "input_digests": digests,
                "rounds": rounds,
                "output_digest": content_digest({"input_digests": digests, "rounds": rounds}),
            }
            state = "COMPLETED"
        except InterruptedError as error:
            state, reason = "CANCELLED", str(error)
        except (ValueError, KeyError, TypeError, OSError) as error:
            reason = str(error)[:1024]
        finally:
            for stream in opened:
                stream.close()
            with self._mutex:
                if event.is_set() and state == "COMPLETED":
                    state, reason, result = (
                        "CANCELLED",
                        "cancellation preceded outcome commit",
                        None,
                    )
                self._active.pop(execution, None)
                self._db.execute(
                    "UPDATE jobs SET state=?,result_json=?,reason=? WHERE execution_id=?",
                    (state, canonical(result) if result is not None else None, reason, execution),
                )
                self._db.commit()
        return self.status(execution)
