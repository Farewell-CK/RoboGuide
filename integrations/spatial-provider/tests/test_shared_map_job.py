"""Bounded assembly, exact identity binding and artifact-independent use evidence."""

from __future__ import annotations

import io
import json
import os
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from roboguide_spatial_provider import shared_map_job
from roboguide_spatial_provider.contracts import canonical, digest
from roboguide_spatial_provider.occupancy_input import NormalizedOccupancy
from roboguide_spatial_provider.shared_map_job import (
    SharedMapSource,
    build_shared_map_job,
    run_shared_map_job,
)


def policy() -> dict[str, Any]:
    """Return the smallest complete policy accepted by the existing planar job."""
    return {
        "resolution_meters": 0.05,
        "yaw_min": -0.1,
        "yaw_max": 0.1,
        "yaw_step": 0.1,
        "translation_bound_m": 0.1,
        "translation_step_m": 0.1,
        "max_pair_distance_m": 0.05,
        "min_overlap": 0.5,
        "max_rmse_m": 0.05,
        "min_shape_ratio": 0.01,
        "ambiguity_translation_m": 0.05,
        "ambiguity_yaw_rad": 0.05,
        "score_margin": 0.0,
        "max_cycle_translation_m": 0.05,
        "max_cycle_yaw_rad": 0.05,
        "free_coverage_margin_m": 0.0,
        "free_max": 20,
        "occupied_min": 80,
        "max_hypotheses": 32,
        "max_refinements": 4,
        "icp_iterations": 4,
        "pose_graph_evaluations": 16,
        "max_registration_points": 64,
        "max_occupied_points": 64,
        "max_input_bytes": 64 * 1024,
        "max_cells": 64,
        "max_total_cells": 128,
        "max_output_bytes": 64 * 1024,
        "tile_cells": 4,
        "max_tiles": 16,
        "timeout_s": 5,
        "memory_mb": 2048,
        "threads": 1,
    }


def normalized(role: str, frame: str) -> NormalizedOccupancy:
    """Create a canonical normalized grid with explicit conversion provenance."""
    value = {
        "schema_version": "roboguide.occupancy-grid/v0.1",
        "width": 2,
        "height": 2,
        "resolution": 0.05,
        "origin": [0.0, 0.0, 0.0],
        "cells": [-1, 0, 20, 100] if role == "n" else [-1, 20, 100, 100],
    }
    content = canonical(value)
    source_digest = digest((role + "-prepared-artifact").encode())
    receipt = {
        "schema_version": "roboguide.occupancy-normalization/v0.1",
        "format": "occupancy-json",
        "frame_id": frame,
        "original_units": "m",
        "sources": [{"role": "occupancy", "content_digest": source_digest, "byte_size": 20}],
        "output": {"content_digest": digest(content), "byte_size": len(content), "units": "m"},
        "classification": {
            "free_max": 20,
            "occupied_min": 80,
            "equality": "unknown",
            "counts": {"free": 1, "occupied": 1, "unknown": 2},
        },
    }
    return NormalizedOccupancy(value, content, receipt)


def source(role: str, frame: str) -> SharedMapSource:
    """Bind one upstream artifact identity to its exact normalized bytes."""
    grid_value = normalized(role, frame)
    return SharedMapSource(
        role=role,  # type: ignore[arg-type]
        artifact_id=f"artifact-{role}",
        artifact_version="roboguide.prepared-artifact/v0.1",
        artifact_digest=grid_value.receipt["sources"][0]["content_digest"],
        frame_id=frame,
        revision_id=f"revision-{role}",
        normalized=grid_value,
    )


@pytest.fixture(autouse=True)  # type: ignore[untyped-decorator]
def windows_storage_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the seam on Windows while production keeps the original no-follow storage."""
    if hasattr(os, "O_NOFOLLOW"):
        return

    def immutable(path: Path, content: bytes) -> None:
        if path.is_symlink():
            raise ValueError("test source must not be a symlink")
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if path.read_bytes() != content:
                raise ValueError("test source identity conflicts") from None
            return
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

    def read_source(root: Path, relative: str, maximum: int) -> bytes:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError("test source is not a regular file")
        content = path.read_bytes()
        if len(content) > maximum:
            raise ValueError("test source exceeds its bound")
        return content

    monkeypatch.setattr(shared_map_job, "immutable", immutable)
    monkeypatch.setattr(shared_map_job, "read_source", read_source)


def bundle(manifest: dict[str, Any]) -> bytes:
    """Build the exact stored occupancy bundle shape consumed by the exporter."""
    cells = [[0, 0, 1], [1, 0, 3], [0, 1, 2]]
    tile = canonical({"tile_key": [0, 0], "cells": cells})
    tile_path = f"tiles/{digest(tile).removeprefix('sha256:')}.json"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(tile_path, tile)
        archive.writestr(
            "manifest.json",
            canonical(
                {
                    **manifest,
                    "units": "m",
                    "tile_cells": 4,
                    "observed_cell_count": len(cells),
                    "tiles": [{"path": tile_path, "content_digest": digest(tile), "cell_count": 3}],
                }
            ),
        )
    return output.getvalue()


def test_builder_keeps_n_first_and_separates_grant_from_metadata() -> None:
    """Exact source metadata constructs the closed request without borrowing authority."""
    assembly = build_shared_map_job(
        job_id="shared-map-job", n=source("n", "n-map"), m=source("m", "m-map"), policy=policy()
    )
    assert assembly.request["frame_id"] == "n-map"
    assert [item["source_id"] for item in assembly.request["inputs"]] == ["n", "m"]
    assert "current_use_grant" not in assembly.request
    assert (
        assembly.source_identities()[1]["artifact_digest"] == source("m", "m-map").artifact_digest
    )
    assert assembly.source_identities()[1]["conversion"]["output_digest"] == digest(
        assembly.m.normalized.content
    )


def test_builder_rejects_hash_frame_and_role_substitution() -> None:
    """Supplied metadata cannot change exact normalized bytes or the N anchor."""
    n, m = source("n", "n-map"), source("m", "m-map")
    with pytest.raises(ValueError, match="roles"):
        build_shared_map_job(job_id="job", n=m, m=n, policy=policy())
    bad_receipt = dict(n.normalized.receipt)
    bad_receipt["output"] = {**bad_receipt["output"], "content_digest": digest(b"other")}
    with pytest.raises(ValueError, match="normalized bytes"):
        replace(
            n,
            normalized=NormalizedOccupancy(n.normalized.grid, n.normalized.content, bad_receipt),
        )


def test_stage_run_replay_and_export_preserve_unknown(tmp_path: Path) -> None:
    """A completed original job yields stable source, relationship and unknown export evidence."""
    assembly = build_shared_map_job(
        job_id="shared-map-job", n=source("n", "n-map"), m=source("m", "m-map"), policy=policy()
    )
    directory = tmp_path / "store" / assembly.job_id
    calls: list[Path] = []
    result: dict[str, Any] = {
        "schema_version": "roboguide.spatial-file-result/v0.1",
        "job_id": assembly.job_id,
        "request_digest": assembly.request_digest,
        "status": "completed",
        "manifest": {
            "schema_version": "roboguide.spatial-occupancy-map/v0.1",
            "frame_id": "n-map",
            "revision_id": assembly.job_id,
            "resolution_meters": 0.05,
            "unobserved_space": "unknown",
            "parent": None,
            "algorithm": {"policy": policy()},
            "sources": [
                {
                    "source_id": "n",
                    "frame_id": "n-map",
                    "content_digest": assembly.n.normalized.content_digest,
                    "units": "m",
                    "format": "occupancy-json",
                    "byte_size": len(assembly.n.normalized.content),
                    "cell_count": len(assembly.n.normalized.grid["cells"]),
                },
                {
                    "source_id": "m",
                    "frame_id": "m-map",
                    "content_digest": assembly.m.normalized.content_digest,
                    "units": "m",
                    "format": "occupancy-json",
                    "byte_size": len(assembly.m.normalized.content),
                    "cell_count": len(assembly.m.normalized.grid["cells"]),
                },
            ],
            "transformations_to_anchor": [
                [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                [[0.0, -1.0, 2.0], [1.0, 0.0, 3.0], [0.0, 0.0, 1.0]],
            ],
            "registration": [{"overlap": 0.8, "rmse_m": 0.01}],
            "observed_cell_count": 3,
        },
    }
    content = bundle(result["manifest"])
    result["artifact"] = {
        "path": "map.bundle",
        "byte_size": len(content),
        "content_digest": digest(content),
    }

    def run(request: dict[str, Any], source_root: Path, store: Path) -> dict[str, Any]:
        calls.append(source_root)
        assert [item["source_id"] for item in request["inputs"]] == ["n", "m"]
        assert (source_root / "n.occupancy.json").read_bytes() == assembly.n.normalized.content
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "map.bundle").write_bytes(content)
        return result

    def observe(path: Path) -> dict[str, Any]:
        assert path == directory
        return result

    first = run_shared_map_job(
        assembly,
        source_root=tmp_path / "sources",
        store=tmp_path / "store",
        run_occupancy=run,
        observe_occupancy=observe,
    )
    second = run_shared_map_job(
        assembly,
        source_root=tmp_path / "sources",
        store=tmp_path / "store",
        run_occupancy=run,
        observe_occupancy=observe,
    )
    assert first == second
    assert len(calls) == 2
    assert first.relationship is not None
    assert first.relationship["T_N_from_M"] == [
        [0.0, -1.0, 2.0],
        [1.0, 0.0, 3.0],
        [0.0, 0.0, 1.0],
    ]
    assert first.navigation_map is not None
    export = json.loads(first.navigation_map.receipt)
    assert export["unknown_cells"] == 1
    assert export["source_digest"] == digest(content)
    assert first.receipt()["authority"]["artifact_metadata_grants_use"] is False


def test_noncompleted_observation_does_not_export(tmp_path: Path) -> None:
    """Unknown or failed execution remains Unknown and never becomes navigation bytes."""
    assembly = build_shared_map_job(
        job_id="unknown-job", n=source("n", "n-map"), m=source("m", "m-map"), policy=policy()
    )
    result = {
        "job_id": assembly.job_id,
        "request_digest": assembly.request_digest,
        "status": "unknown",
        "reason": "original worker outcome is unavailable",
    }
    outcome = run_shared_map_job(
        assembly,
        source_root=tmp_path / "sources",
        store=tmp_path / "store",
        run_occupancy=lambda request, root, store: result,
        observe_occupancy=lambda directory: result,
    )
    assert outcome.occupancy_result == result
    assert outcome.relationship is None
    assert outcome.navigation_map is None
