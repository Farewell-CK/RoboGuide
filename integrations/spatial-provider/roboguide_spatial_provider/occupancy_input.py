"""Bounded, pure normalization of digest-bound planar occupancy inputs."""

from __future__ import annotations

import importlib
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal

from .contracts import canonical, digest, exact, integer, number
from .occupancy_contracts import grid

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_UNIT_SCALE = {"m": 1.0, "cm": 0.01, "mm": 0.001}
_MAX_MAP_METADATA_BYTES = 16 * 1024


@dataclass(frozen=True)
class NormalizedOccupancy:
    """Validated occupancy grid, its canonical bytes and conversion receipt.

    The grid uses the existing ``roboguide.occupancy-grid/v0.1`` contract.  The
    caller owns the external frame identity; it is recorded in the receipt but
    is not inferred or embedded in the grid.
    """

    grid: dict[str, Any]
    content: bytes
    receipt: dict[str, Any]

    @property
    def content_digest(self) -> str:
        """Return the SHA-256 identity of the canonical normalized grid bytes."""
        return digest(self.content)


def normalize_occupancy_json(
    content: bytes,
    *,
    expected_digest: str,
    units: Literal["m", "cm", "mm"],
    frame_id: str,
    free_max: int,
    occupied_min: int,
    max_bytes: int,
    max_cells: int,
) -> NormalizedOccupancy:
    """Normalize one digest-bound ``occupancy-json`` grid to metre geometry.

    Args:
        content: Exact original JSON bytes.
        expected_digest: SHA-256 declared for those exact bytes.
        units: Unit of the original ``resolution`` and ``origin`` x/y values.
        frame_id: Caller-owned frame identity retained in the receipt.
        free_max: Highest original cell value classified as free.
        occupied_min: Lowest original cell value classified as occupied.
        max_bytes: Aggregate raw-input byte bound.
        max_cells: Validated width times height bound.

    Returns:
        A validated metre grid, canonical JSON bytes and provenance receipt.

    Raises:
        ValueError: If the bytes, contract, units, thresholds or bounds fail.
    """
    max_bytes = integer(max_bytes, "max_bytes", 1, 256 * 1024**2)
    max_cells = integer(max_cells, "max_cells", 1, 1_000_000)
    _verify_digest(content, expected_digest, "occupancy JSON", max_bytes)
    if not isinstance(units, str) or units not in _UNIT_SCALE:
        raise ValueError("occupancy JSON units must be m, cm or mm")
    frame = exact(frame_id, "frame_id")
    lower, upper = _thresholds(free_max, occupied_min)

    try:
        decoded = content.decode("utf-8")
        raw = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("occupancy JSON is not bounded UTF-8 JSON") from error
    original = grid(raw, max_cells)

    scale = _UNIT_SCALE[units]
    normalized = {
        "schema_version": "roboguide.occupancy-grid/v0.1",
        "width": original["width"],
        "height": original["height"],
        "resolution": float(original["resolution"]) * scale,
        "origin": [
            float(original["origin"][0]) * scale,
            float(original["origin"][1]) * scale,
            float(original["origin"][2]),
        ],
        "cells": list(original["cells"]),
    }
    grid(normalized, max_cells)
    encoded = canonical(normalized)
    counts = _classify(original["cells"], lower, upper)
    receipt = {
        "schema_version": "roboguide.occupancy-normalization/v0.1",
        "format": "occupancy-json",
        "frame_id": frame,
        "original_units": units,
        "sources": [
            {
                "role": "occupancy",
                "content_digest": expected_digest,
                "byte_size": len(content),
            }
        ],
        "output": {
            "content_digest": digest(encoded),
            "byte_size": len(encoded),
            "units": "m",
        },
        "classification": {
            "free_max": lower,
            "occupied_min": upper,
            "free_rule": "0 <= value <= free_max",
            "occupied_rule": "value >= occupied_min",
            "counts": counts,
        },
    }
    return NormalizedOccupancy(normalized, encoded, receipt)


def normalize_occupancy_bundle(
    content: bytes,
    *,
    expected_digest: str,
    frame_id: str,
    revision_id: str,
    resolution_meters: float,
    max_bytes: int,
    max_tiles: int,
    max_cells: int,
) -> NormalizedOccupancy:
    """Recover the observed grid from a prepared, single-source planar Artifact.

    The caller establishes current permission separately. Unobserved cells inside
    the rectangle remain unknown. Parent and fused histories require a consumer
    that can preserve per-source conflicts and are outside this input adapter.
    """
    from .occupancy import bundle_cells

    max_bytes = integer(max_bytes, "max_bytes", 1, 256 * 1024**2)
    max_tiles = integer(max_tiles, "max_tiles", 1, 4096)
    max_cells = integer(max_cells, "max_cells", 1, 1_000_000)
    _verify_digest(content, expected_digest, "occupancy bundle", max_bytes)
    frame = exact(frame_id, "frame_id")
    revision = exact(revision_id, "revision_id")
    resolution = number(resolution_meters, "resolution_meters", 0.001, 10.0)
    try:
        manifest, observed = bundle_cells(
            content,
            frame_id=frame,
            revision_id=revision,
            resolution=resolution,
            max_tiles=max_tiles,
            max_cells=max_cells,
        )
    except (KeyError, TypeError, zipfile.BadZipFile) as error:
        raise ValueError("occupancy bundle has an invalid complete tile index") from error
    sources = manifest.get("sources")
    if (
        manifest.get("geometry") != "SE(2)"
        or manifest.get("unobserved_space") != "unknown"
        or manifest.get("parent") is not None
        or not isinstance(sources, list)
        or len(sources) != 1
        or manifest.get("registration") != []
        or not observed
        or 3 in observed.values()
    ):
        raise ValueError("occupancy bundle requires a single conflict-free original planar map")
    min_x, max_x = min(x for x, _ in observed), max(x for x, _ in observed)
    min_y, max_y = min(y for _, y in observed), max(y for _, y in observed)
    width, height = max_x - min_x + 1, max_y - min_y + 1
    if width * height > max_cells:
        raise ValueError("occupancy bundle rectangle exceeds its cell bound")
    cells = [-1] * (width * height)
    for (x, y), mask in observed.items():
        cells[(y - min_y) * width + x - min_x] = 0 if mask == 1 else 100
    normalized = {
        "schema_version": "roboguide.occupancy-grid/v0.1",
        "width": width,
        "height": height,
        "resolution": resolution,
        "origin": [min_x * resolution, min_y * resolution, 0.0],
        "cells": cells,
    }
    grid(normalized, max_cells)
    encoded = canonical(normalized)
    return NormalizedOccupancy(
        normalized,
        encoded,
        {
            "schema_version": "roboguide.occupancy-normalization/v0.1",
            "format": "spatial-occupancy-bundle",
            "frame_id": frame,
            "original_units": "m",
            "sources": [
                {"role": "bundle", "content_digest": expected_digest, "byte_size": len(content)}
            ],
            "output": {"content_digest": digest(encoded), "byte_size": len(encoded), "units": "m"},
            "classification": {
                "free_rule": "original mask 1",
                "occupied_rule": "original mask 2",
                "counts": _classify(cells, 0, 100),
            },
        },
    )


def normalize_ros_map(
    yaml_content: bytes,
    pgm_content: bytes,
    *,
    yaml_digest: str,
    pgm_digest: str,
    frame_id: str,
    max_bytes: int,
    max_cells: int,
) -> NormalizedOccupancy:
    """Normalize a digest-bound ROS map YAML and binary 8-bit PGM pair.

    Only YAML ``mode: trinary`` maps with a binary ``P5`` PGM and ``maxval``
    255 are admitted.  YAML is parsed with the repository-declared PyYAML
    dependency through ``safe_load``.  PGM rows are flipped to the ROS
    bottom-left origin, and threshold equality is conservatively unknown.

    Args:
        yaml_content: Exact original map YAML bytes.
        pgm_content: Exact original binary PGM bytes.
        yaml_digest: SHA-256 declared for the exact YAML bytes.
        pgm_digest: SHA-256 declared for the exact PGM bytes.
        frame_id: Caller-owned frame identity retained in the receipt.
        max_bytes: Aggregate raw YAML plus PGM byte bound.
        max_cells: Validated width times height bound.

    Returns:
        A validated grid, canonical JSON bytes and provenance receipt.

    Raises:
        RuntimeError: If PyYAML is unavailable in the execution environment.
        ValueError: If bytes, metadata, PGM encoding, thresholds or bounds fail.
    """
    max_bytes = integer(max_bytes, "max_bytes", 1, 256 * 1024**2)
    max_cells = integer(max_cells, "max_cells", 1, 1_000_000)
    if not isinstance(yaml_content, bytes) or not isinstance(pgm_content, bytes):
        raise ValueError("ROS map inputs must be exact raw byte strings")
    if len(yaml_content) + len(pgm_content) > max_bytes:
        raise ValueError("ROS map inputs exceed their aggregate raw byte bound")
    _verify_digest(yaml_content, yaml_digest, "ROS map YAML", max_bytes)
    _verify_digest(pgm_content, pgm_digest, "ROS map PGM", max_bytes)
    frame = exact(frame_id, "frame_id")
    metadata = _ros_metadata(yaml_content)
    width, height, cells, counts = _trinary_grid(pgm_content, metadata, max_cells)
    normalized = {
        "schema_version": "roboguide.occupancy-grid/v0.1",
        "width": width,
        "height": height,
        "resolution": metadata["resolution"],
        "origin": [*metadata["origin"][:2], metadata["origin"][2]],
        "cells": cells,
    }
    grid(normalized, max_cells)
    encoded = canonical(normalized)
    receipt = {
        "schema_version": "roboguide.occupancy-normalization/v0.1",
        "format": "ros-map-pgm-trinary",
        "frame_id": frame,
        "original_units": "m",
        "sources": [
            {"role": "map-yaml", "content_digest": yaml_digest, "byte_size": len(yaml_content)},
            {"role": "map-pgm", "content_digest": pgm_digest, "byte_size": len(pgm_content)},
        ],
        "output": {
            "content_digest": digest(encoded),
            "byte_size": len(encoded),
            "units": "m",
        },
        "ros_map": {
            "image": metadata["image"],
            "mode": "trinary",
            "negate": metadata["negate"],
            "free_thresh": metadata["free_thresh"],
            "occupied_thresh": metadata["occupied_thresh"],
            "row_order": "PGM top row maps to normalized row height-1",
        },
        "classification": {
            "free_rule": "adjusted occupancy < free_thresh",
            "occupied_rule": "adjusted occupancy > occupied_thresh",
            "equality": "unknown",
            "counts": counts,
        },
    }
    return NormalizedOccupancy(normalized, encoded, receipt)


def _verify_digest(content: bytes, expected: str, name: str, maximum: int) -> None:
    """Check a raw byte bound and exact declared digest before any decoding."""
    if not isinstance(content, bytes) or len(content) > maximum:
        raise ValueError(f"{name} is not a bounded byte string")
    if not isinstance(expected, str) or _DIGEST.fullmatch(expected) is None:
        raise ValueError(f"{name} requires an exact lowercase SHA-256")
    if digest(content) != expected:
        raise ValueError(f"{name} bytes differ from their declared digest")


def _thresholds(free_max: Any, occupied_min: Any) -> tuple[int, int]:
    """Validate explicit classifications without normalizing intermediate JSON values."""
    lower = integer(free_max, "free_max", 0, 99)
    upper = integer(occupied_min, "occupied_min", 1, 100)
    if lower >= upper:
        raise ValueError("free_max must be lower than occupied_min")
    return lower, upper


def _classify(cells: list[int], free_max: int, occupied_min: int) -> dict[str, int]:
    """Count complete free/occupied/unknown classes without changing source cells."""
    counts = {"free": 0, "occupied": 0, "unknown": 0}
    for cell in cells:
        if cell >= occupied_min:
            counts["occupied"] += 1
        elif 0 <= cell <= free_max:
            counts["free"] += 1
        else:
            counts["unknown"] += 1
    return counts


def _ros_metadata(content: bytes) -> dict[str, Any]:
    """Read the closed Nav2 map metadata subset with safe YAML construction."""
    if len(content) > _MAX_MAP_METADATA_BYTES:
        raise ValueError("ROS map YAML exceeds its metadata byte bound")
    try:
        yaml = importlib.import_module("yaml")
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "normalize_ros_map requires PyYAML; use the repository dependency pyyaml>=6.0.2"
        ) from error
    try:
        source = content.decode("utf-8")
        parsed = yaml.compose(source, Loader=yaml.SafeLoader)
        if not isinstance(parsed, yaml.MappingNode):
            raise ValueError("ROS map YAML must contain one mapping")
        keys = [key.value for key, _ in parsed.value if isinstance(key, yaml.ScalarNode)]
        if len(set(keys)) != len(parsed.value):
            raise ValueError("ROS map YAML requires unique scalar keys")
        value = yaml.safe_load(source)
    except (UnicodeDecodeError, RecursionError, yaml.YAMLError) as error:
        raise ValueError("ROS map YAML is not valid bounded UTF-8 YAML") from error
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("ROS map YAML must contain one mapping")
    required = {"image", "resolution", "origin", "negate", "occupied_thresh", "free_thresh"}
    if not required <= value.keys() or value.keys() - required - {"mode"}:
        raise ValueError("ROS map YAML has missing or unknown fields")

    image = value["image"]
    if not isinstance(image, str) or not image.lower().endswith(".pgm"):
        raise ValueError("ROS map image must name a PGM file")
    exact(image, "ROS map image", 1024)
    path = PurePosixPath(image)
    if (
        path.is_absolute()
        or "\\" in image
        or any(part in {"", ".", ".."} for part in image.split("/"))
    ):
        raise ValueError("ROS map image must be a safe relative path")

    mode = value.get("mode", "trinary")
    if mode != "trinary":
        raise ValueError("ROS map mode must be trinary")
    negate = value["negate"]
    if isinstance(negate, bool) or not isinstance(negate, int) or negate not in {0, 1}:
        raise ValueError("ROS map negate must be integer zero or one")
    resolution = number(value["resolution"], "ROS map resolution", 0.0001, 1000)
    free_thresh = number(value["free_thresh"], "ROS map free_thresh", 0, 1)
    occupied_thresh = number(value["occupied_thresh"], "ROS map occupied_thresh", 0, 1)
    if not free_thresh < occupied_thresh:
        raise ValueError("ROS map free_thresh must be lower than occupied_thresh")

    origin = value["origin"]
    if not isinstance(origin, list) or len(origin) != 3:
        raise ValueError("ROS map origin must be explicit x/y/yaw")
    anchor = [
        number(item, f"ROS map origin[{index}]", -1_000_000, 1_000_000)
        for index, item in enumerate(origin)
    ]
    return {
        "image": image,
        "resolution": resolution,
        "origin": anchor,
        "negate": negate,
        "free_thresh": free_thresh,
        "occupied_thresh": occupied_thresh,
    }


def _trinary_grid(
    content: bytes, metadata: dict[str, Any], max_cells: int
) -> tuple[int, int, list[int], dict[str, int]]:
    """Decode exact P5 raster bytes and apply Nav2 trinary threshold semantics."""
    tokens: list[bytes] = []
    position = 0
    for _ in range(4):
        token, position = _pgm_token(content, position)
        tokens.append(token)
    if tokens[0] != b"P5":
        raise ValueError("ROS map PGM must use binary P5 encoding")
    if not tokens[1].isdigit() or not tokens[2].isdigit() or not tokens[3].isdigit():
        raise ValueError("ROS map PGM dimensions and maxval must be decimal integers")
    width, height, maximum = (int(token) for token in tokens[1:])
    if not 1 <= width <= 4096 or not 1 <= height <= 4096:
        raise ValueError("ROS map PGM dimensions exceed their exact bound")
    if width * height > max_cells:
        raise ValueError("ROS map PGM exceeds the aggregate cell bound")
    if maximum != 255:
        raise ValueError("ROS map PGM must declare an 8-bit maxval of 255")
    if position >= len(content) or content[position] not in b" \t\r\n\v\f":
        raise ValueError("ROS map PGM header lacks its raster delimiter")
    position += 2 if content[position : position + 2] == b"\r\n" else 1
    raster = content[position:]
    if len(raster) != width * height:
        raise ValueError("ROS map PGM raster length differs from its dimensions")

    negate = metadata["negate"]
    free_thresh = metadata["free_thresh"]
    occupied_thresh = metadata["occupied_thresh"]
    cells: list[int] = []
    counts = {"free": 0, "occupied": 0, "unknown": 0}
    for row in range(height):
        source_row = height - row - 1
        for column in range(width):
            sample = raster[source_row * width + column] / 255.0
            occupancy = 1.0 - sample if negate == 0 else sample
            if occupancy > occupied_thresh:
                cells.append(100)
                counts["occupied"] += 1
            elif occupancy < free_thresh:
                cells.append(0)
                counts["free"] += 1
            else:
                cells.append(-1)
                counts["unknown"] += 1
    return width, height, cells, counts


def _pgm_token(content: bytes, position: int) -> tuple[bytes, int]:
    """Read one bounded PGM header token with whitespace and comment handling."""
    whitespace = b" \t\r\n\v\f"
    while position < len(content):
        if content[position] in whitespace:
            position += 1
            continue
        if content[position : position + 1] == b"#":
            end = content.find(b"\n", position + 1)
            position = len(content) if end < 0 else end + 1
            continue
        break
    start = position
    while position < len(content) and content[position] not in whitespace:
        position += 1
    if start == position:
        raise ValueError("ROS map PGM header is incomplete")
    return content[start:position], position
