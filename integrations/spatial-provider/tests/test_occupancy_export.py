"""Check deterministic navigation export and refusal of unverified occupancy bytes."""

import io
import json
import zipfile
from typing import Any

import pytest
from roboguide_spatial_provider.contracts import canonical, digest
from roboguide_spatial_provider.occupancy_export import export_navigation_map
from roboguide_spatial_provider.occupancy_input import normalize_occupancy_bundle


def bundle(cells: list[list[int]]) -> bytes:
    """Build a minimal stored bundle with immutable tile identities."""
    grouped: dict[tuple[int, int], list[list[int]]] = {}
    for x, y, mask in cells:
        grouped.setdefault((x // 4, y // 4), []).append([x, y, mask])
    output = io.BytesIO()
    tiles = []
    with zipfile.ZipFile(output, "w") as archive:
        for key, points in grouped.items():
            payload = canonical({"tile_key": list(key), "cells": points})
            identity = digest(payload)
            path = f"tiles/{identity.removeprefix('sha256:')}.json"
            tiles.append({"path": path, "content_digest": identity, "cell_count": len(points)})
            archive.writestr(path, payload)
        archive.writestr(
            "manifest.json",
            canonical(
                {
                    "schema_version": "roboguide.spatial-occupancy-map/v0.1",
                    "frame_id": "local-map",
                    "revision_id": "fused-r1",
                    "resolution_meters": 0.05,
                    "units": "m",
                    "tile_cells": 4,
                    "observed_cell_count": len(cells),
                    "tiles": tiles,
                    "geometry": "SE(2)",
                    "unobserved_space": "unknown",
                    "parent": None,
                    "registration": [],
                    "sources": [{"source_id": "fixture"}],
                }
            ),
        )
    return output.getvalue()


def test_bundle_input_binds_exact_bytes_and_keeps_unobserved_cells() -> None:
    """Keep the prepared bundle identity and refuse rasterizing unresolved conflicts."""
    settings: dict[str, Any] = {
        "frame_id": "local-map",
        "revision_id": "fused-r1",
        "resolution_meters": 0.05,
        "max_bytes": 2048,
        "max_tiles": 4,
        "max_cells": 6,
    }
    content = bundle([[-1, -1, 1], [1, -1, 2], [-1, 0, 1]])
    result = normalize_occupancy_bundle(content, expected_digest=digest(content), **settings)
    assert result.grid["origin"] == [-0.05, -0.05, 0.0]
    assert result.grid["cells"] == [0, -1, 100, 0, -1, -1]
    assert result.receipt["sources"] == [
        {"role": "bundle", "content_digest": digest(content), "byte_size": len(content)}
    ]
    with pytest.raises(ValueError, match="digest"):
        normalize_occupancy_bundle(content, expected_digest=digest(b"other"), **settings)
    conflict = bundle([[0, 0, 3]])
    with pytest.raises(ValueError, match="conflict-free"):
        normalize_occupancy_bundle(conflict, expected_digest=digest(conflict), **settings)
    large = bundle([[0, 0, 1], [4096, 0, 2]])
    with pytest.raises(ValueError, match="rectangle"):
        normalize_occupancy_bundle(large, expected_digest=digest(large), **settings)


def test_navigation_export_retains_orientation_unknown_and_conflict() -> None:
    """Bind the complete conservative ROS raster to the source and exported byte hashes."""
    content = bundle([[-1, -1, 1], [1, -1, 3], [-1, 0, 2]])
    result = export_navigation_map(
        content,
        content_digest=digest(content),
        frame_id="local-map",
        revision_id="fused-r1",
        resolution_meters=0.05,
    )
    assert result.pgm == b"P5\n3 2\n255\n" + bytes([0, 128, 128, 254, 128, 0])
    assert b"origin: [-0.05, -0.05, 0.0]\n" in result.yaml
    receipt = json.loads(result.receipt)
    assert receipt["pgm_digest"] == digest(result.pgm)
    assert receipt["yaml_digest"] == digest(result.yaml)
    assert receipt["source_digest"] == digest(content)
    assert [receipt[key] for key in ("free_cells", "occupied_cells", "conflict_cells")] == [1, 1, 1]
    assert receipt["unknown_cells"] == 3
    assert receipt["expected_grid_digest"] == digest(
        canonical(
            {
                "schema_version": "roboguide.occupancy-grid/v0.1",
                "width": 3,
                "height": 2,
                "resolution": 0.05,
                "origin": [-0.05, -0.05, 0.0],
                "cells": [0, -1, 100, 100, -1, -1],
            }
        )
    )


@pytest.mark.parametrize("case", ["changed-digest", "wrong-frame", "too-wide", "duplicate"])
def test_navigation_export_refuses_invalid_or_unbounded_bundle(case: str) -> None:
    """Require exact input identity, consistent tiles and a bounded dense allocation."""
    samples = {
        "too-wide": [[0, 0, 1], [4096, 0, 2]],
        "duplicate": [[0, 0, 1], [0, 0, 2]],
    }
    content = bundle(samples.get(case, [[0, 0, 1]]))
    with pytest.raises(ValueError):
        export_navigation_map(
            content,
            content_digest=digest(b"other") if case == "changed-digest" else digest(content),
            frame_id="other" if case == "wrong-frame" else "local-map",
            revision_id="fused-r1",
            resolution_meters=0.05,
        )
