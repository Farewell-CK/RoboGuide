"""Reclaim one original provider's bytes from actual local references and durable revokes."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from contextlib import closing
from typing import Any

from .active_map import ActiveMapLedger
from .content_references import MAX_REFERENCES
from .jobs import FileJobs
from .store import DIGEST, MAX_METADATA_BYTES, MemoryStore, canonical, content_digest
from .use_leases import UseLeases

MAX_COMMANDS = 4096
MAX_IMPORTS = 4096


class ContentReclamation:
    """Own local copy removal; no receipt claims global deletion or physical stopping."""

    def __init__(
        self,
        store: MemoryStore,
        leases: UseLeases,
        jobs: FileJobs,
        active_maps: ActiveMapLedger | None,
    ) -> None:
        """Require the same actual backend across all local reference authorities."""
        if leases.store is not store or jobs.store is not store:
            raise ValueError("content reclamation requires the exact original backend owners")
        if active_maps is not None and active_maps.store is not store:
            raise ValueError("active-map reclamation uses a different backend")
        if active_maps is None:
            path = store.root / "active-map/active-map.sqlite3"
            if path.exists():
                with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
                    authority = connection.execute(
                        "SELECT 1 FROM sqlite_schema WHERE type='table' LIMIT 1"
                    ).fetchone()
                if authority is not None:
                    raise ValueError("existing active-map reference authority must be opened")
        self.store, self.leases, self.jobs, self.active_maps = store, leases, jobs, active_maps

    def _request(self, request: dict[str, Any]) -> bytes:
        """Validate a closed, source-bound command without accepting caller reference claims."""
        if (
            set(request)
            != {
                "schema",
                "command_id",
                "provider_id",
                "provider_instance_id",
                "content_digest",
                "byte_size",
            }
            or request["schema"] != "roboguide.content-reclamation-command/v0.1"
        ):
            raise ValueError("content reclamation command is not closed or versioned")
        command = request["command_id"]
        if (
            not isinstance(command, str)
            or not command
            or len(command.encode()) > 256
            or command != command.strip()
            or any(character in command for character in "\x00\r\n")
        ):
            raise ValueError("content reclamation command identity is invalid")
        if (
            request["provider_id"] != self.store.provider_id
            or request["provider_instance_id"] != self.store.instance_id
            or not isinstance(request["content_digest"], str)
            or DIGEST.fullmatch(request["content_digest"]) is None
            or type(request["byte_size"]) is not int
            or not 0 <= request["byte_size"] <= self.store.max_bytes
        ):
            raise ValueError("content reclamation original backend/content differs")
        return canonical(request)

    def _imports(self, digest: str) -> list[dict[str, Any]]:
        """Read every original local import for this digest, refusing incomplete coverage."""
        total = self.store._db.execute(
            "SELECT COALESCE(SUM(length(original_json)),0) FROM imports WHERE content_digest=?",
            (digest,),
        ).fetchone()[0]
        if total > 16 * MAX_METADATA_BYTES:
            raise ValueError("complete original import coverage exceeds its byte bound")
        rows = self.store._db.execute(
            "SELECT original_json FROM imports WHERE content_digest=? ORDER BY import_id LIMIT ?",
            (digest, MAX_IMPORTS + 1),
        ).fetchall()
        if not rows or len(rows) > MAX_IMPORTS:
            raise ValueError("complete bounded original import coverage is unavailable")
        originals = [dict(json.loads(row[0])) for row in rows]
        if any(len(canonical(original)) > MAX_METADATA_BYTES for original in originals):
            raise ValueError("original import metadata exceeds its admitted bound")
        return originals

    def _references(
        self, digest: str, originals: list[dict[str, Any]]
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """Inspect actual exports, workers, retained copies and original revocations."""
        blockers = []
        evidence = []
        for binding in self.store._exports:
            if self.store._artifact(binding["manifest"])[0] == digest:
                blockers.append("configured_source_export")
        references = self.store._db.execute(
            "SELECT reference_id,kind,owner FROM content_references "
            "WHERE content_digest=? AND active=1 ORDER BY reference_id LIMIT ?",
            (digest, MAX_REFERENCES + 1),
        ).fetchall()
        if len(references) > MAX_REFERENCES:
            blockers.append("provider_reference_set_exceeds_bound")
        else:
            blockers.extend(f"{kind}:{owner}:{reference}" for reference, kind, owner in references)
        blockers.extend(self.jobs.reclamation_blockers(digest))
        blockers.extend(self.leases.content_retention_blockers(digest))
        for original in originals:
            try:
                evidence.append(self.leases.retired_import_evidence(original))
            except (ValueError, KeyError, TypeError) as error:
                blockers.append(f"original_import:{original['import_id']}:{error}")
        if self.active_maps is not None:
            blockers.extend(self.active_maps.reclamation_blockers(digest))
        return sorted(set(blockers)), evidence

    def _fingerprint(self, request: dict[str, Any]) -> dict[str, int]:
        """Hash an actual no-follow regular file and freeze its stable original inode identity."""
        directory = os.open(self.store._content, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            descriptor = os.open(
                request["content_digest"][7:], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory
            )
        finally:
            os.close(directory)
        with os.fdopen(descriptor, "rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size != request["byte_size"]:
                raise ValueError("original local content size/type differs")
            digest = "sha256:" + hashlib.file_digest(source, "sha256").hexdigest()
            after = os.fstat(source.fileno())
            if digest != request["content_digest"] or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("original local content changed during reclamation verification")
            return {
                "device": after.st_dev,
                "inode": after.st_ino,
                "size": after.st_size,
                "mtime_ns": after.st_mtime_ns,
                "ctime_ns": after.st_ctime_ns,
            }

    def _result(self, command: str, state: str, **fields: object) -> dict[str, Any]:
        """Describe exact local progress without implying remote deletion or physical safety."""
        return {
            "schema": "roboguide.content-reclamation-receipt/v0.1",
            "command_id": command,
            "provider_id": self.store.provider_id,
            "provider_instance_id": self.store.instance_id,
            "state": state,
            "local_copy_only": True,
            **fields,
        }

    def tombstone(self, request: dict[str, Any]) -> dict[str, Any]:
        """Persist the exact immutable removal intent only after all actual original revokes."""
        encoded = self._request(request)
        command, digest = request["command_id"], request["content_digest"]
        with self.store._mutex:
            original = self.store._db.execute(
                "SELECT request_json,imports_json,state,receipt_json FROM reclamation_commands "
                "WHERE command_id=?",
                (command,),
            ).fetchone()
            if original is not None and original[0] != encoded:
                raise ValueError("original reclamation command identity conflicts")
            if original is not None and original[2] in {"tombstoned", "deleted"}:
                return dict(json.loads(original[3]))
            originals = self._imports(digest)
            frozen = canonical(originals)
            if original is not None and original[1] != frozen:
                raise ValueError("original reclamation import coverage changed")
            if original is None:
                if (
                    self.store._db.execute("SELECT COUNT(*) FROM reclamation_commands").fetchone()[
                        0
                    ]
                    >= MAX_COMMANDS
                ):
                    raise ValueError("content reclamation retained commands are full")
                self.store.require_content_live(digest)
                with self.store._db:
                    self.store._db.execute(
                        "INSERT INTO reclamation_commands VALUES (?,?,?,'requested',NULL,NULL)",
                        (command, encoded, frozen),
                    )
            blockers, evidence = self._references(digest, originals)
            # Only independently confirmed revokes permit retiring local activation evidence.
            if self.active_maps is not None and len(evidence) == len(originals):
                self.active_maps.retire_content(digest)
                blockers, evidence = self._references(digest, originals)
            if blockers:
                return self._result(command, "blocked", reasons=blockers)
            fingerprint = self._fingerprint(request)
            completions = [(value["import_id"], self.store.query(value)) for value in originals]
            if any(value["outcome"] != "completed" for _, value in completions):
                raise ValueError("original import completion remains unknown")
            receipt = self._result(
                command,
                "tombstoned",
                content_digest=digest,
                byte_size=request["byte_size"],
                reference_digest=content_digest(evidence),
                original_imports=[value["import_id"] for value in originals],
            )
            with self.store._db:
                self.store._db.execute(
                    "INSERT INTO content_tombstones VALUES (?,?,'tombstoned')", (digest, command)
                )
                for import_id, completion in completions:
                    self.store._db.execute(
                        "INSERT INTO retired_imports VALUES (?,?,?)",
                        (import_id, command, canonical(completion)),
                    )
                self.store._db.execute(
                    "UPDATE reclamation_commands SET state='tombstoned',file_json=?,receipt_json=? "
                    "WHERE command_id=?",
                    (canonical(fingerprint), canonical(receipt), command),
                )
            return receipt

    def collect(self, request: dict[str, Any]) -> dict[str, Any]:
        """Finish only the original durable unlink and reconcile an interrupted final receipt."""
        encoded = self._request(request)
        command, digest = request["command_id"], request["content_digest"]
        with self.store._mutex:
            original = self.store._db.execute(
                "SELECT request_json,imports_json,state,file_json,receipt_json "
                "FROM reclamation_commands WHERE command_id=?",
                (command,),
            ).fetchone()
            if original is None or original[0] != encoded:
                raise ValueError("original durable reclamation command is unavailable")
            if original[2] == "deleted":
                return dict(json.loads(original[4]))
            if original[2] != "tombstoned":
                raise ValueError("content reclamation requires its durable original tombstone")
            if canonical(self._imports(digest)) != original[1]:
                raise ValueError("original tombstone import coverage changed")
            blockers, _ = self._references(digest, json.loads(original[1]))
            if blockers:
                return self._result(command, "blocked", reasons=blockers)
            absent_before_retry = False
            try:
                fingerprint = self._fingerprint(request)
                if canonical(fingerprint) != original[3]:
                    raise ValueError("tombstoned original content inode was replaced or changed")
            except FileNotFoundError:
                absent_before_retry = True
            directory = os.open(self.store._content, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                if not absent_before_retry:
                    current = os.stat(digest[7:], dir_fd=directory, follow_symlinks=False)
                    frozen = json.loads(original[3])
                    if not stat.S_ISREG(current.st_mode) or (
                        current.st_dev,
                        current.st_ino,
                        current.st_size,
                        current.st_mtime_ns,
                        current.st_ctime_ns,
                    ) != (
                        frozen["device"],
                        frozen["inode"],
                        frozen["size"],
                        frozen["mtime_ns"],
                        frozen["ctime_ns"],
                    ):
                        raise ValueError("original content changed before physical unlink")
                    os.unlink(digest[7:], dir_fd=directory)
                os.fsync(directory)
            finally:
                os.close(directory)
            receipt = self._result(
                command,
                "deleted",
                content_digest=digest,
                byte_size=request["byte_size"],
                original_tombstone=json.loads(original[4]),
                reconciled_missing_after_tombstone=absent_before_retry,
            )
            with self.store._db:
                self.store._db.execute(
                    "UPDATE content_tombstones SET state='deleted' WHERE command_id=?", (command,)
                )
                self.store._db.execute(
                    "UPDATE reclamation_commands SET state='deleted',receipt_json=? "
                    "WHERE command_id=?",
                    (canonical(receipt), command),
                )
            return receipt

    def observe(self, command_id: str) -> dict[str, Any]:
        """Read original progress without unlink, fresh authorization, or fabricated completion."""
        if not isinstance(command_id, str) or not 0 < len(command_id.encode()) <= 256:
            raise ValueError("content reclamation query identity is invalid")
        with self.store._mutex:
            row = self.store._db.execute(
                "SELECT state,receipt_json FROM reclamation_commands WHERE command_id=?",
                (command_id,),
            ).fetchone()
            if row is None:
                return self._result(command_id, "unavailable")
            if row[1] is None:
                return self._result(command_id, str(row[0]))
            return dict(json.loads(row[1]))
