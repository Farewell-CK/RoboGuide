"""Persist generation-scoped local references that fence content reclamation."""

from __future__ import annotations

from typing import Any

from .store import DIGEST, MemoryStore

MAX_REFERENCES = 16_384
KINDS = frozenset({"execution", "lineage", "replica", "snapshot", "archive"})


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
            or kind not in KINDS
            or type(generation) is not int
            or not 0 < generation <= 2**63 - 1
        ):
            raise ValueError("content reference digest, kind, or generation is invalid")
        if operation == "acquire" and (
            type(request["byte_size"]) is not int
            or not 0 <= request["byte_size"] <= self.store.max_bytes
        ):
            raise ValueError("content reference byte size is invalid")
        return reference, digest, kind, owner, generation

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
