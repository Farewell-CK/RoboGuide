"""Project actual original import intents into conservative provider-local Replica pins."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from .store import MAX_METADATA_BYTES, canonical

if TYPE_CHECKING:
    from .store import MemoryStore
    from .use_leases import UseLeases

MAX_REPLICAS = 16_384


class ReplicaReferences:
    """Retain original imports without creating another import or revocation authority."""

    def __init__(self, store: MemoryStore) -> None:
        """Use the original backend journal, mutex and SQL transaction for every projection."""
        self.store = store

    @staticmethod
    def owner(original: dict[str, Any]) -> str:
        """Bind the reserved owner to all immutable original command and provider evidence."""
        return "import:" + hashlib.sha256(canonical(original)).hexdigest()

    def acquire(self, original: dict[str, Any]) -> None:
        """Project an already inserted original intent inside its caller's SQL transaction."""
        manifest = self.store._validate_original(original)
        digest, size = self.store._artifact(manifest)
        row = self.store._db.execute(
            "SELECT original_json,manifest_json,content_digest,byte_size FROM imports "
            "WHERE import_id=?",
            (original["import_id"],),
        ).fetchone()
        if row != (canonical(original), canonical(manifest), digest, size):
            raise ValueError("Replica reference requires its exact original import intent")
        owner = self.owner(original)
        reference = "replica:" + owner
        identity = (digest, "replica", owner, 1)
        existing = self.store._db.execute(
            "SELECT content_digest,kind,owner,generation,active FROM content_references "
            "WHERE reference_id=?",
            (reference,),
        ).fetchone()
        if existing is not None:
            if tuple(existing[:4]) != identity:
                raise ValueError("original Replica reference identity conflicts")
            # A late original command or a process restart cannot renew a retired import pin.
            return
        if self.store._db.execute("SELECT COUNT(*) FROM content_references").fetchone()[0] >= (
            MAX_REPLICAS
        ):
            raise ValueError("provider content reference history is full")
        retired = self.store._db.execute(
            "SELECT 1 FROM retired_imports WHERE import_id=?", (original["import_id"],)
        ).fetchone()
        tombstone = self.store._db.execute(
            "SELECT 1 FROM content_tombstones WHERE content_digest=?", (digest,)
        ).fetchone()
        if (retired is None) != (tombstone is None):
            raise ValueError("original Replica retirement and tombstone evidence conflict")
        self.store._db.execute(
            "INSERT INTO content_references "
            "(reference_id,content_digest,kind,owner,generation,active) VALUES (?,?,?,?,?,?)",
            (reference, *identity, int(retired is None)),
        )

    def restore(self) -> None:
        """Rebuild bounded missing projections while preserving every existing retired pin."""
        with self.store._mutex:
            total, count = self.store._db.execute(
                "SELECT COALESCE(SUM(length(original_json)),0),COUNT(*) FROM imports"
            ).fetchone()
            if count > MAX_REPLICAS or total > 16 * MAX_METADATA_BYTES:
                raise ValueError("original Replica reference coverage exceeds its bound")
            originals = self.store._db.execute(
                "SELECT original_json FROM imports ORDER BY import_id"
            ).fetchall()
            with self.store._db:
                for (encoded,) in originals:
                    self.acquire(dict(json.loads(encoded)))

    def retire(self, original: dict[str, Any], leases: UseLeases) -> None:
        """Retire only after verifying the exact original provider's irreversible revoke."""
        if leases.store is not self.store:
            raise ValueError("Replica retirement requires the original revocation authority")
        with self.store._mutex:
            leases.retired_import_evidence(original)
            with self.store._db:
                self.acquire(original)
                self.store._db.execute(
                    "UPDATE content_references SET active=0 "
                    "WHERE reference_id=? AND kind='replica' AND owner=? "
                    "AND generation=1 AND active=1",
                    ("replica:" + self.owner(original), self.owner(original)),
                )
