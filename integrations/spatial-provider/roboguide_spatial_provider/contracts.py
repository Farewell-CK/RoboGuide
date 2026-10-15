"""Closed source, frame and computation budgets for immutable spatial file revisions."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any


def digest(value: bytes) -> str:
    """Return the explicit immutable SHA-256 identity without interpreting content as evidence."""
    return "sha256:" + hashlib.sha256(value).hexdigest()


def canonical(value: object) -> bytes:
    """Encode bounded validated metadata with stable keys and no non-finite JSON numbers."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def exact(value: Any, name: str, maximum: int = 128) -> str:
    """Reject blank, control-containing or ambiguous surrounding whitespace in source identities."""
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be a bounded exact string")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{name} contains control characters")
    return value


def number(value: Any, name: str, lower: float, upper: float) -> float:
    """Admit finite configured geometric values independently of confidence."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not lower <= result <= upper:
        raise ValueError(f"{name} is outside its configured safety bound")
    return result


def integer(value: Any, name: str, lower: int, upper: int) -> int:
    """Require exact integer work/storage bounds before starting native code."""
    if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
        raise ValueError(f"{name} must be an integer in [{lower}, {upper}]")
    return value


def fields(value: Any, required: set[str], optional: set[str] | None = None) -> dict[str, Any]:
    """Reject missing or unknown fields without supplying algorithm policy."""
    if not isinstance(value, dict) or not required <= value.keys():
        raise ValueError(f"missing required fields: {sorted(required)}")
    if value.keys() - required - (optional or set()):
        raise ValueError("unknown spatial contract field")
    return value


@dataclass(frozen=True)
class Input:
    """One actual immutable input file, with declared units and source-local coordinate frame."""

    source_id: str
    path: str
    content_digest: str
    frame_id: str
    units: str
    format: str

    @classmethod
    def parse(cls, value: Any) -> Input:
        """Require source identity and expected file hash before native decoding."""
        item = fields(value, {"source_id", "path", "content_digest", "frame_id", "units", "format"})
        identity = exact(item["content_digest"], "content_digest")
        if re.fullmatch("sha256:[0-9a-f]{64}", identity) is None:
            raise ValueError("input requires an exact lowercase SHA-256")
        path = exact(item["path"], "input path", 1024)
        if path.startswith("/") or any(part in {"", ".", ".."} for part in path.split("/")):
            raise ValueError("input paths must be exact source-root-relative components")
        if (
            not isinstance(item["units"], str)
            or item["units"] not in {"m", "cm", "mm"}
            or not isinstance(item["format"], str)
            or item["format"] not in {"pcd", "ply"}
        ):
            raise ValueError("input units/format is unsupported")
        return cls(
            exact(item["source_id"], "source_id"),
            path,
            identity,
            exact(item["frame_id"], "frame_id"),
            item["units"],
            item["format"],
        )


@dataclass(frozen=True)
class Policy:
    """Explicit registration, degeneracy, fusion and process budgets; no device-name branches."""

    voxel_size: float
    normal_radius: float
    feature_radius: float
    ransac_distance: float
    icp_distance: float
    min_fitness: float
    max_rmse: float
    min_shape_ratio: float
    max_cycle_translation: float
    max_cycle_angle: float
    tile_size: float
    ransac_trials: int
    ransac_repeats: int
    icp_iterations: int
    seed: int
    max_input_bytes: int
    max_points: int
    max_total_points: int
    max_downsample_points: int
    max_output_bytes: int
    max_tiles: int
    timeout_s: int
    memory_mb: int
    threads: int

    @classmethod
    def parse(cls, value: Any) -> Policy:
        """Validate all configured limits before any potentially expensive geometry allocation."""
        required = set(cls.__dataclass_fields__)
        raw = fields(value, required)
        floats = {
            key: number(raw[key], key, lower, upper)
            for key, lower, upper in (
                ("voxel_size", 0.0001, 10),
                ("normal_radius", 0.0002, 100),
                ("feature_radius", 0.0004, 100),
                ("ransac_distance", 0.0001, 100),
                ("icp_distance", 0.0001, 100),
                ("min_fitness", 0.01, 1),
                ("max_rmse", 0.000001, 100),
                ("min_shape_ratio", 0.00000001, 0.5),
                ("max_cycle_translation", 0.0001, 100),
                ("max_cycle_angle", 0.0001, math.pi),
                ("tile_size", 0.001, 1000),
            )
        }
        integers = {
            key: integer(raw[key], key, lower, upper)
            for key, lower, upper in (
                ("ransac_trials", 1, 1_000_000),
                ("ransac_repeats", 1, 4),
                ("icp_iterations", 1, 500),
                ("seed", 0, 2**31 - 1),
                ("max_input_bytes", 1024, 256 * 1024**2),
                ("max_points", 3, 2_000_000),
                ("max_total_points", 3, 4_000_000),
                ("max_downsample_points", 3, 200_000),
                ("max_output_bytes", 1024, 512 * 1024**2),
                ("max_tiles", 1, 512),
                ("timeout_s", 1, 300),
                ("memory_mb", 2048, 32_768),
                ("threads", 1, 16),
            )
        }
        if (
            floats["normal_radius"] <= floats["voxel_size"]
            or floats["feature_radius"] <= floats["normal_radius"]
        ):
            raise ValueError("normal/feature neighborhoods must exceed the sampling voxel")
        if (
            floats["tile_size"] < floats["voxel_size"]
            or integers["max_total_points"] < integers["max_points"]
        ):
            raise ValueError("tile/aggregate point budgets are inconsistent")
        return cls(**{**floats, **integers})  # type: ignore[arg-type]


@dataclass(frozen=True)
class Job:
    """Immutable job with an explicit first-file or verified-parent anchor frame."""

    schema_version: str
    job_id: str
    frame_id: str
    inputs: tuple[Input, ...]
    policy: Policy
    parent: dict[str, Any] | None

    @classmethod
    def parse(cls, value: Any) -> Job:
        """Validate source topology and parent identity without creating physical poses."""
        raw = fields(
            value, {"schema_version", "job_id", "frame_id", "inputs", "policy"}, {"parent"}
        )
        if raw["schema_version"] != "roboguide.spatial-file-job/v0.1":
            raise ValueError("unsupported spatial job schema")
        job_id = exact(raw["job_id"], "job_id", 64)
        if re.fullmatch("[A-Za-z0-9][A-Za-z0-9_-]*", job_id) is None:
            raise ValueError("job_id must be path-safe")
        if not isinstance(raw["inputs"], list) or not 1 <= len(raw["inputs"]) <= 8:
            raise ValueError("spatial job requires one to eight actual input files")
        inputs = tuple(Input.parse(item) for item in raw["inputs"])
        if len({item.source_id for item in inputs}) != len(inputs) or len(
            {item.content_digest for item in inputs}
        ) != len(inputs):
            raise ValueError("duplicate file identities cannot count as independent input geometry")
        frame = exact(raw["frame_id"], "frame_id")
        parent = raw.get("parent")
        if parent is not None:
            parent = fields(parent, {"path", "content_digest", "revision_id", "frame_id"})
            if (
                parent["frame_id"] != frame
                or re.fullmatch(
                    "sha256:[0-9a-f]{64}", exact(parent["content_digest"], "parent digest")
                )
                is None
            ):
                raise ValueError("parent frame/content identity differs")
            exact(parent["path"], "parent path", 1024)
            exact(parent["revision_id"], "parent revision")
        elif inputs[0].frame_id != frame:
            raise ValueError("output frame must be the actual declared anchor input frame")
        return cls(
            raw["schema_version"], job_id, frame, inputs, Policy.parse(raw["policy"]), parent
        )
