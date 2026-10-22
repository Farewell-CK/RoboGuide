"""Persist generation-scoped local references that fence content reclamation."""

from __future__ import annotations

import hashlib
from typing import Any

from .store import DIGEST, MemoryStore, canonical

MAX_REFERENCES = 16_384
KINDS = frozenset({"execution", "lineage", "replica", "snapshot", "archive", "use_lease"})
EXTERNAL_KINDS = KINDS - {"use_lease"}


def _text(value: object, name: str, maximum: int = 256) -> str:
    """Require one bounded, trimmed identifier without control characters."""
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value.encode()) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"content reference {name} is invalid")
    return value


class ContentReferences:
    """Own explicit provider-local reference registrations and monotonic release state."""

    def __init__(self, store: MemoryStore) -> None:
        """Bind the protocol to the exact original local Memory backend."""
        self.store = store

    def _identity(self, request: dict[str, Any]) -> tuple[str, str, str, str, int]:
        """Validate the complete backend/reference identity shared by acquire and release."""
        required = {
            "schema",
            "operation",
            "provider_id",
            "provider_instance_id",
            "reference_id",
            "content_digest",
            "kind",
            "owner",
            "generation",
        }
        operation = request.get("operation")
        expected = required | ({"byte_size"} if operation == "acquire" else set())
        if (
            set(request) != expected
            or request.get("schema") != "roboguide.content-reference-command/v0.1"
            or operation not in {"acquire", "release"}
        ):
            raise ValueError("content reference command is not closed or versioned")
        if (
            request["provider_id"] != self.store.provider_id
            or request["provider_instance_id"] != self.store.instance_id
        ):
            raise ValueError("content reference original provider or instance differs")
        reference = _text(request["reference_id"], "reference_id")
        digest = request["content_digest"]
        kind = _text(request["kind"], "kind", 64)
        owner = _text(request["owner"], "owner")
        generation = request["generation"]
        if (
            not isinstance(digest, str)
            or DIGEST.fullmatch(digest) is None
            or kind not in EXTERNAL_KINDS
            or type(generation) is not int
            or not 0 < generation <= 2**63 - 1
        ):
            raise ValueError("content reference digest, kind, or generation is invalid")
        if kind == "execution" and owner.startswith("file-job:"):
            raise ValueError("file-job execution references are owned by the job journal")
        if reference.startswith("replica:import:") or (
            kind == "replica" and owner.startswith("import:")
        ):
            raise ValueError("import Replica references are owned by the original import journal")
        if reference.startswith("snapshot-operation:") or owner.startswith("snapshot-operation:"):
            raise ValueError("snapshot references are owned by the original snapshot journal")
        if operation == "acquire" and (
            type(request["byte_size"]) is not int
            or not 0 <= request["byte_size"] <= self.store.max_bytes
        ):
            raise ValueError("content reference byte size is invalid")
        return reference, digest, kind, owner, generation

    @staticmethod
    def execution_owner(execution_id: str) -> str:
        """Map an immutable execution ID to the reserved bounded local job-reference owner."""
        execution_id = _text(execution_id, "execution_id", 1024)
        return "file-job:" + hashlib.sha256(execution_id.encode()).hexdigest()

    def synchronize_execution(
        self,
        execution_id: str,
        generation: int,
        manifests: list[dict[str, Any]],
        active: bool,
    ) -> None:
        """Tie exact file-job input CAS rows to the durable execution lifecycle."""
        owner = self.execution_owner(execution_id)
        if not active:
            with self.store._mutex, self.store._db:
                self.store._db.execute(
                    "DELETE FROM content_references WHERE kind='execution' AND owner=?", (owner,)
                )
            return
        if type(generation) is not int or not 0 <= generation <= 2**63 - 1 or len(manifests) > 8:
            raise ValueError("file-job execution reference coverage is unavailable")
        if not manifests:
            with self.store._mutex:
                existing = self.store._db.execute(
                    "SELECT 1 FROM content_references WHERE kind='execution' AND owner=? LIMIT 1",
                    (owner,),
                ).fetchone()
                if existing is not None:
                    raise ValueError("original file-job execution acquired different input content")
            return
        if generation == 0:
            raise ValueError("active file-job execution requires its exact positive generation")
        pending: list[tuple[str, str, int, str, int]] = []
        for manifest in manifests:
            digest, size = self.store._artifact(manifest)
            selector_digest = hashlib.sha256(canonical(manifest["selector"])).hexdigest()
            reference_id = f"file-job:{owner[9:]}:{generation}:{selector_digest}"
            pending.append((reference_id, digest, size, owner, generation))
        unique = {row[0]: row for row in pending}
        if any(unique[row[0]] != row for row in pending):
            raise ValueError("file-job execution reference identity conflicts")
        pending = list(unique.values())
        with self.store._mutex:
            for _, digest, _, _, _ in pending:
                self.store.require_content_live(digest)
            rows = self.store._db.execute(
                "SELECT reference_id,content_digest,generation,active FROM content_references "
                "WHERE kind='execution' AND owner=? ORDER BY reference_id",
                (owner,),
            ).fetchall()
            expected = sorted((ref, digest, current, 1) for ref, digest, _, _, current in pending)
            if rows:
                if rows != expected:
                    raise ValueError("original file-job execution reference identity conflicts")
                return
            for _, digest, size, _, _ in pending:
                if not self.store._verify_content(digest, size):
                    raise ValueError("file-job execution requires matching live provider bytes")
            with self.store._db:
                count = self.store._db.execute(
                    "SELECT COUNT(*) FROM content_references"
                ).fetchone()[0]
                if count + len(pending) > MAX_REFERENCES:
                    raise ValueError("provider content reference history is full")
                self.store._db.executemany(
                    "INSERT INTO content_references "
                    "(reference_id,content_digest,kind,owner,generation,active) "
                    "VALUES (?,?, 'execution', ?, ?, 1)",
                    [
                        (reference, digest, owner, current)
                        for reference, digest, _, owner, current in pending
                    ],
                )

    def prune_execution_projections(self, retained_owners: set[str]) -> None:
        """Remove only orphaned projections in the reserved file-job namespace."""
        with self.store._mutex:
            owners = {
                row[0]
                for row in self.store._db.execute(
                    "SELECT DISTINCT owner FROM content_references "
                    "WHERE kind='execution' AND owner GLOB 'file-job:*'"
                )
            }
            stale = owners - retained_owners
            if stale:
                with self.store._db:
                    self.store._db.executemany(
                        "DELETE FROM content_references WHERE kind='execution' AND owner=?",
                        [(owner,) for owner in sorted(stale)],
                    )

    def _receipt(
        self,
        reference: str,
        digest: str,
        kind: str,
        owner: str,
        generation: int,
        active: bool,
    ) -> dict[str, Any]:
        """Describe only the current local reference state and exact owner generation."""
        return {
            "schema": "roboguide.content-reference-receipt/v0.1",
            "provider_id": self.store.provider_id,
            "provider_instance_id": self.store.instance_id,
            "reference_id": reference,
            "content_digest": digest,
            "kind": kind,
            "owner": owner,
            "generation": generation,
            "state": "active" if active else "released",
            "local_provider_only": True,
        }

    def apply(self, request: dict[str, Any]) -> dict[str, Any]:
        """Apply an idempotent acquire or exact-generation release under the writer mutex."""
        reference, digest, kind, owner, generation = self._identity(request)
        operation = request["operation"]
        with self.store._mutex:
            existing = self.store._db.execute(
                "SELECT content_digest,kind,owner,generation,active "
                "FROM content_references WHERE reference_id=?",
                (reference,),
            ).fetchone()
            identity = (digest, kind, owner, generation)
            if operation == "acquire":
                if existing is not None:
                    if tuple(existing[:4]) != identity:
                        raise ValueError("original content reference identity conflicts")
                    return self._receipt(
                        reference, digest, kind, owner, generation, bool(existing[4])
                    )
                size = request["byte_size"]
                self.store.require_content_live(digest)
                if not self.store._verify_content(digest, size):
                    raise ValueError("content reference requires matching live provider bytes")
                if existing is None:
                    count = self.store._db.execute(
                        "SELECT COUNT(*) FROM content_references"
                    ).fetchone()[0]
                    if count >= MAX_REFERENCES:
                        raise ValueError("provider content reference history is full")
                    with self.store._db:
                        self.store._db.execute(
                            "INSERT INTO content_references "
                            "(reference_id,content_digest,kind,owner,generation,active) "
                            "VALUES (?,?,?,?,?,1)",
                            (reference, digest, kind, owner, generation),
                        )
                    return self._receipt(reference, digest, kind, owner, generation, True)

            if existing is None or tuple(existing[:4]) != identity:
                raise ValueError("exact original content reference generation is unavailable")
            if bool(existing[4]):
                with self.store._db:
                    cursor = self.store._db.execute(
                        "UPDATE content_references SET active=0 "
                        "WHERE reference_id=? AND generation=? AND active=1",
                        (reference, generation),
                    )
                if cursor.rowcount != 1:
                    raise ValueError("content reference generation changed during release")
            return self._receipt(reference, digest, kind, owner, generation, False)

    def observe(self, request: dict[str, Any]) -> dict[str, Any]:
        """Return the current exact local reference without refreshing or changing its state."""
        if (
            set(request)
            != {
                "schema",
                "provider_id",
                "provider_instance_id",
                "reference_id",
            }
            or request.get("schema") != "roboguide.content-reference-query/v0.1"
            or request.get("provider_id") != self.store.provider_id
            or request.get("provider_instance_id") != self.store.instance_id
        ):
            raise ValueError("content reference query is not closed or bound to this provider")
        reference = _text(request["reference_id"], "reference_id")
        with self.store._mutex:
            row = self.store._db.execute(
                "SELECT content_digest,kind,owner,generation,active "
                "FROM content_references WHERE reference_id=?",
                (reference,),
            ).fetchone()
        if row is None:
            raise ValueError("original provider content reference is unavailable")
        return self._receipt(reference, row[0], row[1], row[2], row[3], bool(row[4]))

    def synchronize_use_lease(
        self,
        lease_id: str,
        generation: int,
        originals: list[dict[str, Any]],
        active: bool,
        content_verified: bool = False,
    ) -> None:
        """Mirror a durable provider use-lease into exact per-import content references."""
        lease_id = _text(lease_id, "use lease owner")
        if type(generation) is not int or not 0 <= generation <= 2**63 - 1:
            raise ValueError("content reference use-lease generation is invalid")
        with self.store._mutex:
            if not active:
                # The durable lease/command journal is the historical authority. This table is
                # only its live GC projection, so prune it after a persistent revoke or restart
                # fence instead of retaining one dead row for every renewal forever.
                with self.store._db:
                    self.store._db.execute(
                        "DELETE FROM content_references WHERE kind='use_lease' AND owner=?",
                        (lease_id,),
                    )
                return
            if not 0 < generation <= 2**63 - 1 or not 1 <= len(originals) <= 8:
                raise ValueError("active Memory-use reference coverage is unavailable")
            pending: list[tuple[str, str, str, str, int]] = []
            for original in originals:
                manifest = self.store._validate_original(original)
                digest, size = self.store._artifact(manifest)
                self.store.require_content_live(digest)
                if not content_verified and not self.store._verify_content(digest, size):
                    raise ValueError("active Memory-use reference bytes are unavailable")
                import_id = _text(original["import_id"], "import_id")
                reference_id = f"use:{lease_id}:{generation}:{import_id}"
                if len(reference_id.encode()) > 256:
                    raise ValueError("Memory-use reference identity exceeds its bound")
                pending.append((reference_id, digest, "use_lease", lease_id, generation))
            if len({item[0] for item in pending}) != len(pending):
                raise ValueError("Memory-use repeats an original import reference")
            with self.store._db:
                # Historical use-lease receipts remain in the lease journal. Remove only older
                # projection generations in the same transaction that installs this complete
                # current generation; a failed insert rolls the pruning back and remains safe.
                self.store._db.execute(
                    "DELETE FROM content_references "
                    "WHERE kind='use_lease' AND owner=? AND generation<>?",
                    (lease_id, generation),
                )
                for reference, digest, kind, owner, current_generation in pending:
                    existing = self.store._db.execute(
                        "SELECT content_digest,kind,owner,generation,active "
                        "FROM content_references WHERE reference_id=?",
                        (reference,),
                    ).fetchone()
                    identity = (digest, kind, owner, current_generation)
                    if existing is not None:
                        if tuple(existing[:4]) != identity or not existing[4]:
                            raise ValueError("original Memory-use reference generation conflicts")
                        continue
                    count = self.store._db.execute(
                        "SELECT COUNT(*) FROM content_references"
                    ).fetchone()[0]
                    if count >= MAX_REFERENCES:
                        raise ValueError("provider content reference history is full")
                    self.store._db.execute(
                        "INSERT INTO content_references "
                        "(reference_id,content_digest,kind,owner,generation,active) "
                        "VALUES (?,?,?,?,?,1)",
                        (reference, digest, kind, owner, current_generation),
                    )
