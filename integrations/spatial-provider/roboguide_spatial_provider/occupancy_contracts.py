"""Closed planar occupancy inputs and explicit bounded SE(2) computation policies."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from .contracts import Input, exact, fields, integer, number


@dataclass(frozen=True)
class OccupancyPolicy:
    """Separate planar search/coverage policy, without unused three-dimensional tuning fields."""

    resolution_meters: float
    yaw_min: float
    yaw_max: float
    yaw_step: float
    translation_bound_m: float
    translation_step_m: float
    max_pair_distance_m: float
    min_overlap: float
    max_rmse_m: float
    min_shape_ratio: float
    ambiguity_translation_m: float
    ambiguity_yaw_rad: float
    score_margin: float
    max_cycle_translation_m: float
    max_cycle_yaw_rad: float
    free_coverage_margin_m: float
    free_max: int
    occupied_min: int
    max_hypotheses: int
    max_refinements: int
    icp_iterations: int
    pose_graph_evaluations: int
    max_registration_points: int
    max_occupied_points: int
    max_input_bytes: int
    max_cells: int
    max_total_cells: int
    max_output_bytes: int
    tile_cells: int
    max_tiles: int
    timeout_s: int
    memory_mb: int
    threads: int

    @classmethod
    def parse(cls, value: Any) -> OccupancyPolicy:
        """Bound actual search/allocation work before loading a grid or importing native code."""
        raw = fields(value, set(cls.__dataclass_fields__))
        floating = {
            key: number(raw[key], key, low, high)
            for key, low, high in (
                ("resolution_meters", 0.001, 10),
                ("yaw_min", -math.pi, math.pi),
                ("yaw_max", -math.pi, math.pi),
                ("yaw_step", 0.001, math.pi),
                ("translation_bound_m", 0, 100),
                ("translation_step_m", 0.001, 10),
                ("max_pair_distance_m", 0.001, 10),
                ("min_overlap", 0.01, 1),
                ("max_rmse_m", 0.000001, 10),
                ("min_shape_ratio", 0.00000001, 0.5),
                ("ambiguity_translation_m", 0.001, 10),
                ("ambiguity_yaw_rad", 0.001, math.pi),
                ("score_margin", 0, 1),
                ("max_cycle_translation_m", 0.001, 10),
                ("max_cycle_yaw_rad", 0.001, math.pi),
                ("free_coverage_margin_m", 0, 10),
            )
        }
        bounded = {
            key: integer(raw[key], key, low, high)
            for key, low, high in (
                ("free_max", 0, 99),
                ("occupied_min", 1, 100),
                ("max_hypotheses", 1, 300_000),
                ("max_refinements", 1, 32),
                ("icp_iterations", 1, 500),
                ("pose_graph_evaluations", 1, 1000),
                ("max_registration_points", 16, 2048),
                ("max_occupied_points", 16, 100_000),
                ("max_input_bytes", 1024, 256 * 1024**2),
                ("max_cells", 16, 1_000_000),
                ("max_total_cells", 16, 4_000_000),
                ("max_output_bytes", 1024, 512 * 1024**2),
                ("tile_cells", 4, 256),
                ("max_tiles", 1, 512),
                ("timeout_s", 1, 300),
                ("memory_mb", 2048, 32768),
                ("threads", 1, 16),
            )
        }
        yaw_count = (
            math.ceil((floating["yaw_max"] - floating["yaw_min"]) / floating["yaw_step"]) + 1
        )
        xy_count = (
            math.ceil(2 * floating["translation_bound_m"] / floating["translation_step_m"]) + 1
        )
        if (
            floating["yaw_min"] > floating["yaw_max"]
            or yaw_count * xy_count**2 > bounded["max_hypotheses"]
            or bounded["free_max"] >= bounded["occupied_min"]
            or bounded["max_total_cells"] < bounded["max_cells"]
            or bounded["max_registration_points"] > bounded["max_occupied_points"]
        ):
            raise ValueError("occupancy search/threshold/aggregate budgets are inconsistent")
        return cls(**{**floating, **bounded})  # type: ignore[arg-type]


@dataclass(frozen=True)
class OccupancyJob:
    """Immutable planar job anchored in an admitted first-grid or exact parent frame."""

    job_id: str
    frame_id: str
    inputs: tuple[Input, ...]
    policy: OccupancyPolicy
    parent: dict[str, Any] | None

    @classmethod
    def parse(cls, value: Any) -> OccupancyJob:
        """Admit explicit source bytes/units/frames without inventing altitude or physical pose."""
        raw = fields(
            value, {"schema_version", "job_id", "frame_id", "inputs", "policy"}, {"parent"}
        )
        if raw["schema_version"] != "roboguide.spatial-occupancy-job/v0.1":
            raise ValueError("unsupported occupancy job schema")
        identity = exact(raw["job_id"], "job_id", 64)
        if re.fullmatch("[A-Za-z0-9][A-Za-z0-9_-]*", identity) is None:
            raise ValueError("occupancy job_id must be path-safe")
        if not isinstance(raw["inputs"], list) or not 1 <= len(raw["inputs"]) <= 8:
            raise ValueError("occupancy jobs require one to eight actual grids")
        inputs = []
        for description in raw["inputs"]:
            item = fields(
                description, {"source_id", "path", "content_digest", "frame_id", "units", "format"}
            )
            if item["format"] != "occupancy-json":
                raise ValueError("planar input requires its closed occupancy-json contract")
            # Reuse the exact source/path/unit/hash boundary without admitting this format to SE(3).
            admitted = Input.parse({**item, "format": "ply"})
            inputs.append(
                Input(
                    admitted.source_id,
                    admitted.path,
                    admitted.content_digest,
                    admitted.frame_id,
                    admitted.units,
                    "occupancy-json",
                )
            )
        if len({item.source_id for item in inputs}) != len(inputs) or len(
            {item.content_digest for item in inputs}
        ) != len(inputs):
            raise ValueError("duplicate occupancy identities cannot add independent observations")
        frame = exact(raw["frame_id"], "frame_id")
        parent = raw.get("parent")
        if parent is not None:
            parent = fields(parent, {"path", "content_digest", "revision_id", "frame_id"})
            for key in parent:
                exact(parent[key], f"parent {key}", 1024 if key == "path" else 128)
            if re.fullmatch("sha256:[0-9a-f]{64}", parent["content_digest"]) is None:
                raise ValueError("parent bundle requires an immutable SHA-256")
        if frame != (parent["frame_id"] if parent else inputs[0].frame_id):
            raise ValueError("output frame must retain its declared planar anchor")
        return cls(identity, frame, tuple(inputs), OccupancyPolicy.parse(raw["policy"]), parent)


def grid(value: Any, maximum: int) -> dict[str, Any]:
    """Validate every ROS-like occupancy value before constructing any native array."""
    raw = fields(value, {"schema_version", "width", "height", "resolution", "origin", "cells"})
    if raw["schema_version"] != "roboguide.occupancy-grid/v0.1":
        raise ValueError("unsupported occupancy-grid schema")
    width = integer(raw["width"], "width", 1, 4096)
    height = integer(raw["height"], "height", 1, 4096)
    if (
        width * height > maximum
        or not isinstance(raw["cells"], list)
        or len(raw["cells"]) != width * height
    ):
        raise ValueError("occupancy dimensions exceed the actual bounded cell payload")
    number(raw["resolution"], "resolution", 0.0001, 1000)
    if not isinstance(raw["origin"], list) or len(raw["origin"]) != 3:
        raise ValueError("occupancy origin is an explicit planar x/y/yaw")
    for index, value in enumerate(raw["origin"]):
        number(value, f"origin[{index}]", -1_000_000, 1_000_000)
    for cell in raw["cells"]:
        integer(cell, "occupancy", -1, 100)
    return raw
