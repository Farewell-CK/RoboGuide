"""Test actual ZIP/CAS active-map gates without devices, models or synthetic Node inventory."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from roboguide_memory_provider.active_map import ActiveMapError, ActiveMapLedger
from roboguide_memory_provider.store import MemoryStore


class NoopLease:
    """Accept the test's explicit software lease context without manufacturing device evidence."""

    def guard(self, lease_id: str, execution_id: str) -> None:
        """Validate nonempty software identities supplied by this metadata-only test."""
        if not lease_id or not execution_id:
            raise ValueError("lease context is empty")

    def guard_map(self, context: dict[str, Any]) -> None:
        """Keep this ZIP parser unit fixture explicitly separate from real lease admission tests."""
        self.guard(context["use_lease_id"], context["execution_id"])


def bundle() -> tuple[bytes, dict[str, Any]]:
    """Build one ZIP_STORED map bundle whose manifest and tile are independently digestable."""
    tile = b"ply\nformat ascii 1.0\nelement vertex 1\nend_header\n0 0 0\n"
    tile_digest = "sha256:" + hashlib.sha256(tile).hexdigest()
    manifest: dict[str, Any] = {
        "schema_version": "roboguide.spatial-file-map/v0.1",
        "revision_id": "r1",
        "frame_id": "map-frame",
        "units": "m",
        "voxel_size_m": 0.1,
        "unobserved_space": "unknown",
        "sources": [
            {
                "source_id": "raw-0",
                "content_digest": "sha256:" + "a" * 64,
                "byte_size": 3,
                "frame_id": "map-frame",
                "units": "m",
                "format": "pcd",
                "point_count": 3,
            }
        ],
        "tiles": [
            {
                "path": "tiles/tile.ply",
                "content_digest": tile_digest,
                "point_count": 1,
                "bounds": {"min": [-1.0, -1.0, -1.0], "max": [1.0, 1.0, 1.0]},
            }
        ],
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True).encode())
        archive.writestr("tiles/tile.ply", tile)
    return output.getvalue(), manifest


def request(digest: str, size: int, generation: int = 1) -> dict[str, Any]:
    """Return the exact closed selection context used by every active-map operation."""
    return {
        "schema": "roboguide.active-map/v0.1",
        "map_id": "map",
        "revision_id": "r1",
        "content_digest": digest,
        "byte_size": size,
        "frame_id": "map-frame",
        "spatial_anchor_id": "anchor",
        "attempt_id": "attempt-1",
        "execution_id": "attempt-1",
        "use_lease_id": "use-1",
        "generation": generation,
        "node_id": "node-1",
        "group_id": "group-1",
    }


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[tuple[ActiveMapLedger, MemoryStore, dict[str, Any], str]]:
    """Persist one actual CAS bundle and return its active-map ledger and immutable request."""
    source = tmp_path / "source"
    source.write_bytes(b"raw")
    store = MemoryStore(tmp_path / "backend", "files", (tmp_path,), 8 * 1024 * 1024)
    payload, _ = bundle()
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    (store._content / digest[7:]).write_bytes(payload)
    active = ActiveMapLedger(store.root / "active-map", store, NoopLease())
    try:
        yield active, store, request(digest, len(payload)), digest
    finally:
        active.close()
        store.close()


def test_select_query_release_and_restart_fence(
    ledger: tuple[ActiveMapLedger, MemoryStore, dict[str, Any], str],
) -> None:
    """Selection and query use actual bytes; release and restart never leave a current map."""
    active, store, selection, digest = ledger
    active.select(selection)
    query = {
        **selection,
        "schema": "roboguide.active-map/v0.1",
        "query_id": "q1",
        "x": 0.0,
        "y": 0.0,
        "z": 0.0,
        "radius": 0.1,
    }
    result = active.query(query)
    assert result["hits"][0]["tile_digest"].startswith("sha256:")
    assert result["localization"] is None
    assert active.release(selection)["state"] == "released"
    assert active.state()["heads"][0]["state"] == "released"
    with pytest.raises(ActiveMapError):
        active.query(query)
    assert store._content.joinpath(digest[7:]).is_file()


def test_legacy_gc_booleans_cannot_authorize_deletion(
    ledger: tuple[ActiveMapLedger, MemoryStore, dict[str, Any], str],
) -> None:
    """Even all eight caller assertions cannot authorize removal of actual local CAS bytes."""
    active, store, selection, digest = ledger
    active.select(selection)
    blocked = active.gc(
        {
            "schema": "roboguide.active-map-gc/v0.1",
            "content_digest": digest,
            "selector": "map:r1",
            "refs": {"execution": True},
        }
    )
    assert blocked["state"] == "blocked"
    active.release(selection)
    refs = {
        key: True
        for key in (
            "execution",
            "import",
            "use_lease",
            "active_map",
            "lineage",
            "replica",
            "snapshot",
            "archive",
        )
    }
    deleted = active.gc(
        {
            "schema": "roboguide.active-map-gc/v0.1",
            "content_digest": digest,
            "selector": "map:r1",
            "refs": refs,
        }
    )
    assert deleted["state"] == "blocked"
    assert deleted["reasons"] == ["caller_booleans_are_not_reference_authority"]
    assert store._content.joinpath(digest[7:]).is_file()


def test_gc_v2_reads_actual_active_map_and_provider_references(
    ledger: tuple[ActiveMapLedger, MemoryStore, dict[str, Any], str],
) -> None:
    """The versioned GC gate derives blockers from local SQL authorities, then delegates unlink."""
    active, store, selection, digest = ledger
    active.select(selection)
    blocked = active.gc(
        {
            "schema": "roboguide.active-map-gc/v0.2",
            "content_digest": digest,
            "selector": "map:r1",
        }
    )
    assert blocked["state"] == "blocked"
    assert "active_map:map:r1" in blocked["reasons"]
    active.release(selection)
    eligible = active.gc(
        {
            "schema": "roboguide.active-map-gc/v0.2",
            "content_digest": digest,
            "selector": "map:r1",
        }
    )
    assert eligible["state"] == "eligible"
    assert eligible["delete_authority"] == "ContentReclamation.collect"
    assert store._content.joinpath(digest[7:]).is_file()


def test_non_stored_bundle_is_rejected(
    ledger: tuple[ActiveMapLedger, MemoryStore, dict[str, Any], str],
) -> None:
    """Compression or manifest changes cannot become active-map evidence."""
    active, store, selection, _ = ledger
    compressed = io.BytesIO()
    with zipfile.ZipFile(compressed, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps({}).encode())
    digest = "sha256:" + hashlib.sha256(compressed.getvalue()).hexdigest()
    (store._content / digest[7:]).write_bytes(compressed.getvalue())
    with pytest.raises(ActiveMapError):
        active.select(
            {**selection, "content_digest": digest, "byte_size": len(compressed.getvalue())}
        )


def test_handoff_preserves_source_generation_and_requires_software_stop(
    ledger: tuple[ActiveMapLedger, MemoryStore, dict[str, Any], str],
) -> None:
    """A handoff advances the same map selector only after a complete software stop barrier."""
    active, _, selection, _ = ledger
    active.select(selection)
    target = {
        **selection,
        "generation": 2,
        "attempt_id": "attempt-2",
        "execution_id": "attempt-2",
        "use_lease_id": "use-2",
        "node_id": "node-2",
    }
    with pytest.raises(ActiveMapError):
        active.prepare_handoff(
            {
                "schema": "roboguide.active-map-handoff/v0.1",
                "barrier_id": "barrier-1",
                "group_id": "group-1",
                "source": selection,
                "target": target,
                "stop_barrier": {
                    "schema": "roboguide.software-stop-barrier/v0.1",
                    "status": "software_stopped",
                    "attempts": ["attempt-1"],
                    "confirmed_attempts": [],
                },
            }
        )
    prepared = active.prepare_handoff(
        {
            "schema": "roboguide.active-map-handoff/v0.1",
            "barrier_id": "barrier-1",
            "group_id": "group-1",
            "source": selection,
            "target": target,
            "stop_barrier": {
                "schema": "roboguide.software-stop-barrier/v0.1",
                "status": "software_stopped",
                "attempts": ["attempt-1"],
                "confirmed_attempts": ["attempt-1"],
            },
        }
    )
    assert prepared["software_stop_only"] is True
    committed = active.commit_handoff(
        {
            "schema": "roboguide.active-map-handoff-commit/v0.1",
            "barrier_id": "barrier-1",
            "target": target,
        }
    )
    assert committed["state"] == "committed"
    state = active.state()
    assert state["heads"][0]["generation"] == 2
    assert state["heads"][0]["state"] == "active"
    assert any(
        item["generation"] == 1 and item["state"] == "handed_off" for item in state["history"]
    )


@pytest.mark.parametrize("changed", ["content_digest", "frame_id", "node_id", "use_lease_id"])
def test_handoff_commit_cannot_substitute_the_frozen_target(
    ledger: tuple[ActiveMapLedger, MemoryStore, dict[str, Any], str], changed: str
) -> None:
    """A prepared software map handoff cannot substitute another target field at Commit."""
    active, store, selection, digest = ledger
    active.select(selection)
    target = {**selection, "generation": 2}
    active.prepare_handoff(
        {
            "schema": "roboguide.active-map-handoff/v0.1",
            "barrier_id": "exact-target",
            "group_id": selection["group_id"],
            "source": selection,
            "target": target,
            "stop_barrier": {
                "schema": "roboguide.software-stop-barrier/v0.1",
                "status": "software_stopped",
                "attempts": [selection["execution_id"]],
                "confirmed_attempts": [selection["execution_id"]],
            },
        }
    )
    alternate = {
        **target,
        changed: "sha256:" + "f" * 64 if changed == "content_digest" else "other",
    }
    with pytest.raises(ActiveMapError, match="target differs"):
        active.commit_handoff(
            {
                "schema": "roboguide.active-map-handoff-commit/v0.1",
                "barrier_id": "exact-target",
                "target": alternate,
            }
        )
    assert active.state()["heads"][0]["generation"] == 1
    assert store._content.joinpath(digest[7:]).is_file()


def test_handoff_commit_rechecks_the_actual_source_head(
    ledger: tuple[ActiveMapLedger, MemoryStore, dict[str, Any], str],
) -> None:
    """A source released after prepare cannot be silently revived through handoff Commit."""
    active, _, selection, _ = ledger
    active.select(selection)
    target = {**selection, "generation": 2}
    active.prepare_handoff(
        {
            "schema": "roboguide.active-map-handoff/v0.1",
            "barrier_id": "source-race",
            "group_id": selection["group_id"],
            "source": selection,
            "target": target,
            "stop_barrier": {
                "schema": "roboguide.software-stop-barrier/v0.1",
                "status": "software_stopped",
                "attempts": [selection["execution_id"]],
                "confirmed_attempts": [selection["execution_id"]],
            },
        }
    )
    active.release(selection)
    with pytest.raises(ActiveMapError, match="source selection changed"):
        active.commit_handoff(
            {
                "schema": "roboguide.active-map-handoff-commit/v0.1",
                "barrier_id": "source-race",
                "target": target,
            }
        )
    assert active.state()["heads"][0]["state"] == "released"
