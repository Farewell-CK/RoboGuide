"""Exercise selectable strategies through the actual bounded occupancy worker."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema
import pytest
from roboguide_spatial_provider.__main__ import run
from roboguide_spatial_provider.contracts import canonical, digest
from test_occupancy_contracts import policy


@pytest.mark.skipif(
    sys.platform != "linux" or sys.version_info[:2] != (3, 11),
    reason="requires the Linux Python 3.11 Spatial worker",
)
def test_selectable_strategies_fuse_to_the_same_metric_anchor(tmp_path: Path) -> None:
    """Verify both output contracts and refuse a map without valid alignment."""
    np = pytest.importorskip("numpy")
    cv = pytest.importorskip("cv2")
    rng = np.random.default_rng(2026)
    anchor = np.zeros((256, 256), dtype=np.uint8)
    for x, y, radius in rng.integers([32, 32, 2], [216, 216, 8], size=(85, 3)).tolist():
        cv.circle(anchor, (x, y), radius, 100, -1)
    other = np.roll(anchor, (4, -6), axis=(0, 1))
    inputs = []
    for name, cells, origin in (
        ("n", anchor, [0, 0, 0]),
        ("m", other, [1, -2, 0]),
        ("distant", cv.flip(anchor, 1), [0, 0, 0]),
    ):
        content = canonical(
            {
                "schema_version": "roboguide.occupancy-grid/v0.1",
                "width": 256,
                "height": 256,
                "resolution": 0.05,
                "origin": origin,
                "cells": cells.ravel().tolist(),
            }
        )
        path = f"{name}.json"
        (tmp_path / path).write_bytes(content)
        inputs.append(
            {
                "source_id": name,
                "path": path,
                "content_digest": digest(content),
                "frame_id": name,
                "units": "m",
                "format": "occupancy-json",
            }
        )
    original_policy = {
        **policy(),
        "resolution_meters": 0.05,
        "yaw_min": -0.01,
        "yaw_max": 0.01,
        "translation_bound_m": 0,
        "max_pair_distance_m": 0.1,
        "min_overlap": 0.9,
        "max_rmse_m": 0.03,
        "tile_cells": 32,
        "memory_mb": 16384,
        "timeout_s": 90,
    }
    schemas = Path(__file__).resolve().parents[3] / "contracts/spatial/file-provider-v0.1"
    manifest_schema = json.loads((schemas / "occupancy-map.schema.json").read_text())
    request_schema = json.loads((schemas / "occupancy-job.schema.json").read_text())
    for strategy, prefix in (("correlative-icp", "bounded-se2"), ("opencv-akaze", "opencv-akaze")):
        request = {
            "schema_version": "roboguide.spatial-occupancy-job/v0.1",
            "job_id": strategy,
            "frame_id": "n",
            "inputs": inputs[:2],
            "policy": {**original_policy, "strategy": strategy},
        }
        jsonschema.validate(request, request_schema)
        result = run(request, tmp_path, tmp_path / "jobs")
        assert result["status"] == "completed", result.get("reason")
        manifest = result["manifest"]
        jsonschema.validate(manifest, manifest_schema)
        assert manifest["algorithm"]["name"].startswith(prefix)
        assert manifest["frame_id"] == "n"
        np.testing.assert_array_equal(manifest["transformations_to_anchor"][0], np.eye(3))
        np.testing.assert_allclose(
            manifest["transformations_to_anchor"][1],
            [[1, 0, -0.7], [0, 1, 1.8], [0, 0, 1]],
            atol=0.02,
        )
    unmatched = {**request, "job_id": "no-alignment", "inputs": [inputs[0], inputs[2]]}
    rejected = run(unmatched, tmp_path, tmp_path / "jobs")
    assert rejected["status"] == "unavailable"
    assert "disconnected or ambiguous" in rejected["reason"]
    assert not (tmp_path / "jobs/no-alignment/map.bundle").exists()
