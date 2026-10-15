"""Adversarial framing, file ownership and interrupted-job checks without device substitutes."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from roboguide_spatial_provider.__main__ import run
from roboguide_spatial_provider.contracts import Job, canonical, digest
from roboguide_spatial_provider.geometry import code_digest
from roboguide_spatial_provider.storage import point_count, read_source, save_json


def policy() -> dict[str, Any]:
    """Declare explicit small software-test budgets; these are not device performance evidence."""
    return {
        "voxel_size": 0.05,
        "normal_radius": 0.1,
        "feature_radius": 0.25,
        "ransac_distance": 0.1,
        "icp_distance": 0.05,
        "min_fitness": 0.5,
        "max_rmse": 0.04,
        "min_shape_ratio": 0.00001,
        "max_cycle_translation": 0.1,
        "max_cycle_angle": 0.1,
        "tile_size": 1.0,
        "ransac_trials": 1000,
        "ransac_repeats": 2,
        "icp_iterations": 20,
        "seed": 7,
        "max_input_bytes": 1048576,
        "max_points": 10000,
        "max_total_points": 20000,
        "max_downsample_points": 10000,
        "max_output_bytes": 1048576,
        "max_tiles": 64,
        "timeout_s": 10,
        "memory_mb": 4096,
        "threads": 2,
    }


def request() -> dict[str, Any]:
    """Provide framing metadata; actual geometry uses the separate native runner."""
    return {
        "schema_version": "roboguide.spatial-file-job/v0.1",
        "job_id": "original",
        "frame_id": "input-frame",
        "inputs": [
            {
                "source_id": "file",
                "frame_id": "input-frame",
                "path": "source.pcd",
                "content_digest": "sha256:" + "a" * 64,
                "units": "m",
                "format": "pcd",
            }
        ],
        "policy": policy(),
    }


def test_duplicate_source_frames_cannot_be_counted_as_independent_geometry() -> None:
    """An alias cannot create another input or an unstated output coordinate frame."""
    value = request()
    alias = copy.deepcopy(value["inputs"][0])
    alias["source_id"] = "another-name"
    value["inputs"].append(alias)
    with pytest.raises(ValueError, match="duplicate"):
        Job.parse(value)
    value = request()
    value["frame_id"] = "invented-global-frame"
    with pytest.raises(ValueError, match="anchor"):
        Job.parse(value)
    value = request()
    value["policy"]["max_points"] = True
    with pytest.raises(ValueError, match="integer"):
        Job.parse(value)


def test_pcd_allocation_envelope_and_source_symlinks_fail_before_native_decode(
    tmp_path: Path,
) -> None:
    """Small malicious headers cannot allocate huge arrays or escape source roots."""
    header = (
        b"FIELDS x y z\nSIZE 4 4 4\nTYPE F F F\nCOUNT 1 1 1\n"
        b"WIDTH 1000000000\nHEIGHT 1000000000\nPOINTS 3\nDATA ascii\n"
        b"0 0 0\n1 0 0\n0 1 0\n"
    )
    with pytest.raises(ValueError, match="width/height"):
        point_count(header, "pcd", 1000)
    (tmp_path / "source").write_bytes(b"actual private file")
    (tmp_path / "alias").symlink_to(tmp_path / "source")
    with pytest.raises(OSError):
        read_source(tmp_path, "alias", 100)
    (tmp_path / "directory-alias").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OSError):
        read_source(tmp_path, "directory-alias/source", 100)
    with pytest.raises(ValueError):
        read_source(tmp_path, "../source", 100)


@pytest.mark.parametrize("field,value", [("units", []), ("format", {}), ("units", None)])
def test_input_enum_shapes_fail_before_files_are_opened(field: str, value: Any) -> None:
    """Malformed JSON cannot bypass the typed unit/format boundary through an unhashable value."""
    original = request()
    original["inputs"][0][field] = value
    with pytest.raises(ValueError, match="units/format"):
        Job.parse(original)


def test_interrupted_identity_returns_original_unknown_without_new_computation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No source bytes, newer code or explicit retry renews an original interrupted job deadline."""
    value = request()
    directory = tmp_path / "store/original"
    directory.mkdir(parents=True)
    save_json(directory / "request.json", value)
    save_json(
        directory / "intent.json",
        {
            "job_id": "original",
            "request_digest": digest(canonical(value)),
            "code_digest": code_digest(),
            "admitted_at_ms": 1,
            "deadline_ms": 10001,
        },
    )

    def forbidden(*args: Any, **kwargs: Any) -> None:
        """Fail if retry attempts native work after the interrupted original intent."""
        raise AssertionError("interrupted job launched another worker")

    monkeypatch.setattr("roboguide_spatial_provider.__main__.subprocess.Popen", forbidden)
    observed = run(value, tmp_path, tmp_path / "store")
    assert observed["status"] == "unknown" and observed["deadline_ms"] == 10001
    assert observed["admitted_at_ms"] == 1
    changed = copy.deepcopy(value)
    changed["policy"]["timeout_s"] = 20
    with pytest.raises(ValueError, match="conflicts"):
        run(changed, tmp_path, tmp_path / "store")
    assert not (directory / "started.json").exists()


def test_source_digest_failure_is_durable_and_never_promoted_by_retry(tmp_path: Path) -> None:
    """Missing immutable source proof rejects before native work without a retry reset."""
    value = request()
    (tmp_path / "source.pcd").write_bytes(b"actual bytes with another digest")
    first = run(value, tmp_path, tmp_path / "store")
    assert first["status"] == "rejected_before_compute"
    assert "digest" in first["reason"]
    assert run(value, tmp_path, tmp_path / "store") == first
    assert not (tmp_path / "store/original/started.json").exists()
