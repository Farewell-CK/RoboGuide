"""Pure occupancy input normalization, exact byte identity and bounded decoding."""

from __future__ import annotations

import importlib.util
import json
from typing import Any, Literal

import pytest
from roboguide_spatial_provider.contracts import canonical, digest
from roboguide_spatial_provider.occupancy_input import normalize_occupancy_json, normalize_ros_map


def occupancy_json(
    *,
    units_resolution: float = 0.2,
    units_origin: list[float] | None = None,
    cells: list[int] | None = None,
) -> bytes:
    """Return one ordinary occupancy-grid document without hidden device semantics."""
    value = {
        "schema_version": "roboguide.occupancy-grid/v0.1",
        "width": 2,
        "height": 2,
        "resolution": units_resolution,
        "origin": units_origin if units_origin is not None else [0.1, -0.2, 0.5],
        "cells": cells if cells is not None else [-1, 20, 21, 80],
    }
    return json.dumps(value, separators=(",", ":")).encode()


def normalize_json(content: bytes, units: Literal["m", "cm", "mm"] = "m") -> Any:
    """Invoke the JSON adapter with complete explicit bounds and thresholds."""
    return normalize_occupancy_json(
        content,
        expected_digest=digest(content),
        units=units,
        frame_id="source-frame",
        free_max=20,
        occupied_min=80,
        max_bytes=64 * 1024,
        max_cells=16,
    )


def test_occupancy_json_normalizes_each_declared_unit_to_metres() -> None:
    """Metre, centimetre and millimetre inputs produce one metric grid representation."""
    metre = normalize_json(occupancy_json(units_resolution=0.2), "m")
    centimetre = normalize_json(
        occupancy_json(units_resolution=20, units_origin=[10, -20, 0.5]), "cm"
    )
    millimetre = normalize_json(
        occupancy_json(units_resolution=200, units_origin=[100, -200, 0.5]), "mm"
    )
    assert metre.grid == centimetre.grid == millimetre.grid
    assert metre.grid["resolution"] == pytest.approx(0.2)
    assert metre.grid["origin"] == pytest.approx([0.1, -0.2, 0.5])
    assert metre.grid["cells"] == [-1, 20, 21, 80]
    assert metre.receipt["classification"]["counts"] == {
        "free": 1,
        "occupied": 1,
        "unknown": 2,
    }
    assert metre.content == canonical(metre.grid)
    assert metre.content_digest == digest(metre.content)
    assert metre.receipt["output"]["content_digest"] == metre.content_digest
    assert metre.receipt["frame_id"] == "source-frame"


def test_occupancy_json_uses_inclusive_policy_without_embedding_frame() -> None:
    """Keep source cells and classify threshold equality like the occupancy worker."""
    result = normalize_json(occupancy_json(cells=[-1, 20, 21, 80]))
    assert result.receipt["classification"]["counts"] == {
        "free": 1,
        "occupied": 1,
        "unknown": 2,
    }
    assert "frame_id" not in result.grid
    assert result.grid["cells"][1:3] == [20, 21]


@pytest.mark.parametrize(
    "change",
    [
        {"expected_digest": "sha256:" + "0" * 64},
        {"units": "px"},
        {"free_max": 80, "occupied_min": 80},
    ],
)
def test_occupancy_json_rejects_identity_units_and_threshold_failures(
    change: dict[str, Any],
) -> None:
    """Raw identity and semantic policy fail before a normalized grid exists."""
    arguments: dict[str, Any] = {
        "content": occupancy_json(),
        "expected_digest": digest(occupancy_json()),
        "units": "m",
        "frame_id": "source-frame",
        "free_max": 20,
        "occupied_min": 80,
        "max_bytes": 64 * 1024,
        "max_cells": 16,
    }
    arguments.update(change)
    with pytest.raises(ValueError):
        normalize_occupancy_json(**arguments)


@pytest.mark.parametrize(
    "content",
    [
        b"not-json",
        b"[]",
        json.dumps(
            {
                "schema_version": "roboguide.occupancy-grid/v0.1",
                "width": 2,
                "height": 2,
                "resolution": 0.2,
                "origin": [0, 0, 0],
                "cells": [-1, 0, 100],
            }
        ).encode(),
    ],
)
def test_occupancy_json_rejects_bad_documents(content: bytes) -> None:
    """Malformed or dimensionally incomplete JSON never reaches native work."""
    with pytest.raises(ValueError):
        normalize_json(content)


def ros_map(
    *, negate: int = 0, free_thresh: float = 0.25, occupied_thresh: float = 0.65
) -> tuple[bytes, bytes]:
    """Return a two-by-two binary PGM and matching trinary YAML declaration."""
    yaml_content = (
        "image: floor.pgm\n"
        "resolution: 0.05\n"
        "origin: [-1.0, 2.0, 0.25]\n"
        f"negate: {negate}\n"
        f"occupied_thresh: {occupied_thresh}\n"
        f"free_thresh: {free_thresh}\n"
        "mode: trinary\n"
    ).encode()
    # PGM top row is [0, 255], bottom row is [180, 51].
    pgm_content = b"P5\n# bounded fixture\n2 2\n255\n" + bytes([0, 255, 180, 51])
    return yaml_content, pgm_content


def normalize_ros(
    yaml_content: bytes, pgm_content: bytes, *, max_bytes: int = 64 * 1024, max_cells: int = 16
) -> Any:
    """Invoke the ROS adapter with exact source byte identities."""
    return normalize_ros_map(
        yaml_content,
        pgm_content,
        yaml_digest=digest(yaml_content),
        pgm_digest=digest(pgm_content),
        frame_id="source-frame",
        max_bytes=max_bytes,
        max_cells=max_cells,
    )


@pytest.mark.skipif(
    importlib.util.find_spec("yaml") is None, reason="PyYAML dependency unavailable"
)
def test_ros_map_flips_rows_and_applies_strict_trinary_thresholds() -> None:
    """Normalized row zero comes from the PGM bottom row and trinary classes stay discrete."""
    yaml_content, pgm_content = ros_map()
    result = normalize_ros(yaml_content, pgm_content)
    assert result.grid["width"] == 2
    assert result.grid["height"] == 2
    assert result.grid["resolution"] == pytest.approx(0.05)
    assert result.grid["origin"] == pytest.approx([-1.0, 2.0, 0.25])
    assert result.grid["cells"] == [-1, 100, 100, 0]
    assert result.receipt["classification"]["counts"] == {
        "free": 1,
        "occupied": 2,
        "unknown": 1,
    }
    assert result.content == canonical(result.grid)
    assert result.receipt["output"]["content_digest"] == result.content_digest
    assert result.receipt["ros_map"]["row_order"].startswith("PGM top row")


@pytest.mark.skipif(
    importlib.util.find_spec("yaml") is None, reason="PyYAML dependency unavailable"
)
def test_ros_map_negate_and_threshold_equality_are_explicit() -> None:
    """Negate reverses occupancy score while exact threshold equality stays unknown."""
    free_score = 64 / 255
    occupied_score = 192 / 255
    yaml_content, _ = ros_map(negate=1, free_thresh=free_score, occupied_thresh=occupied_score)
    pgm_content = b"P5\n2 2\n255\n" + bytes([0, 255, 64, 192])
    result = normalize_ros(yaml_content, pgm_content)
    assert result.grid["cells"] == [-1, -1, 0, 100]


@pytest.mark.skipif(
    importlib.util.find_spec("yaml") is None, reason="PyYAML dependency unavailable"
)
@pytest.mark.parametrize(
    ("change", "value"),
    [
        ("mode", "scale"),
        ("image", "../escape.pgm"),
        ("resolution", "nan"),
        ("negate", True),
        ("free_thresh", 0.7),
    ],
)
def test_ros_map_rejects_bad_metadata(change: str, value: object) -> None:
    """Unsupported mode, path escape, non-finite scale and invalid thresholds reject."""
    yaml_content, pgm_content = ros_map()
    document = yaml_content.decode().replace("mode: trinary", f"mode: {value}")
    if change != "mode":
        field = {
            "image": "image",
            "resolution": "resolution",
            "negate": "negate",
            "free_thresh": "free_thresh",
        }[change]
        old = next(line for line in document.splitlines() if line.startswith(f"{field}:"))
        document = document.replace(old, f"{field}: {value}")
    with pytest.raises(ValueError):
        normalize_ros(document.encode(), pgm_content)


@pytest.mark.skipif(
    importlib.util.find_spec("yaml") is None, reason="PyYAML dependency unavailable"
)
@pytest.mark.parametrize(
    "pgm",
    [
        b"P2\n2 2\n255\n0 0 0 0",
        b"P5\n2 2\n65535\n" + bytes(8),
        b"P5\n2 2\n255\n" + bytes(3),
        b"P5\n4097 1\n255\n" + bytes(4097),
    ],
)
def test_ros_map_rejects_unsupported_or_bounded_bad_pgm(pgm: bytes) -> None:
    """Non-P5, non-8-bit, truncated and oversized rasters fail before conversion."""
    yaml_content, _ = ros_map()
    with pytest.raises(ValueError):
        normalize_ros(yaml_content, pgm, max_bytes=64 * 1024)


@pytest.mark.skipif(
    importlib.util.find_spec("yaml") is None, reason="PyYAML dependency unavailable"
)
def test_ros_map_rejects_digest_mismatch_and_aggregate_bounds() -> None:
    """Both exact identities precede conversion and aggregate raw bytes remain bounded."""
    yaml_content, pgm_content = ros_map()
    with pytest.raises(ValueError, match="digest"):
        normalize_ros_map(
            yaml_content,
            pgm_content + b"x",
            yaml_digest=digest(yaml_content),
            pgm_digest=digest(pgm_content),
            frame_id="source-frame",
            max_bytes=64 * 1024,
            max_cells=16,
        )
    with pytest.raises(ValueError, match="aggregate"):
        normalize_ros(yaml_content, pgm_content, max_bytes=len(yaml_content) + len(pgm_content) - 1)
    with pytest.raises(ValueError, match="cell"):
        normalize_ros(yaml_content, pgm_content, max_cells=3)
