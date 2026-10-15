"""Planar dimensional, allocation and source-identity fences without native/device substitution."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from roboguide_spatial_provider.__main__ import parse_job
from roboguide_spatial_provider.occupancy_contracts import OccupancyJob, OccupancyPolicy, grid


def policy() -> dict[str, Any]:
    """Declare one complete neutral bounded test policy with no device identity or success facts."""
    return {
        "resolution_meters": 0.2,
        "yaw_min": -0.6,
        "yaw_max": 0.6,
        "yaw_step": 0.1,
        "translation_bound_m": 0.2,
        "translation_step_m": 0.1,
        "max_pair_distance_m": 0.015,
        "min_overlap": 0.95,
        "max_rmse_m": 0.0001,
        "min_shape_ratio": 0.00001,
        "ambiguity_translation_m": 0.05,
        "ambiguity_yaw_rad": 0.05,
        "score_margin": 0.0,
        "max_cycle_translation_m": 0.02,
        "max_cycle_yaw_rad": 0.02,
        "free_coverage_margin_m": 0.0,
        "free_max": 20,
        "occupied_min": 80,
        "max_hypotheses": 1000,
        "max_refinements": 32,
        "icp_iterations": 50,
        "pose_graph_evaluations": 100,
        "max_registration_points": 2048,
        "max_occupied_points": 10000,
        "max_input_bytes": 16 * 1024**2,
        "max_cells": 100000,
        "max_total_cells": 500000,
        "max_output_bytes": 16 * 1024**2,
        "tile_cells": 16,
        "max_tiles": 256,
        "timeout_s": 60,
        "memory_mb": 8192,
        "threads": 2,
    }


def request() -> dict[str, Any]:
    """Provide source declarations only; neither fixture frames nor metadata create observations."""
    return {
        "schema_version": "roboguide.spatial-occupancy-job/v0.1",
        "job_id": "original-grid",
        "frame_id": "source-frame",
        "policy": policy(),
        "inputs": [
            {
                "source_id": "actual-file",
                "path": "original.json",
                "content_digest": "sha256:" + "a" * 64,
                "frame_id": "source-frame",
                "format": "occupancy-json",
                "units": "m",
            }
        ],
    }


def test_planar_data_requires_exact_dimensions_values_and_no_invented_altitude() -> None:
    """Malformed cells/dimensions/altitude cannot reach a native allocation or SE(3) contract."""
    original = {
        "schema_version": "roboguide.occupancy-grid/v0.1",
        "width": 2,
        "height": 2,
        "resolution": 0.2,
        "origin": [0, 0, 0],
        "cells": [-1, 0, 100, 50],
    }
    assert grid(original, 4) == original
    for change in (
        {"cells": [True, 0, 100, -1]},
        {"width": 1_000_000_000},
        {"cells": [0, 0, 100, -2]},
        {"altitude": 0},
        {"origin": [0, 0, 0, 0]},
    ):
        with pytest.raises(ValueError):
            grid({**original, **change}, 4)
    assert isinstance(parse_job(request()), OccupancyJob)
    with pytest.raises(ValueError, match="unsupported spatial job schema"):
        parse_job({**request(), "schema_version": "roboguide.spatial-file-job/v0.2"})


def test_planar_search_and_occupancy_thresholds_are_bounded_before_native_work() -> None:
    """Unbounded hypothesis grids, Boolean budgets and contradictory value ranges reject."""
    for change in (
        {"max_hypotheses": 1},
        {"timeout_s": True},
        {"free_max": 90, "occupied_min": 80},
        {"yaw_min": 0.6, "yaw_max": -0.6},
    ):
        with pytest.raises(ValueError):
            OccupancyPolicy.parse({**policy(), **change})


def test_planar_aliases_and_unproven_output_frames_cannot_expand_source_evidence() -> None:
    """Renaming one file cannot add an observation or create a global/altitude frame."""
    original = request()
    alias = copy.deepcopy(original["inputs"][0])
    alias["source_id"] = "alias"
    with pytest.raises(ValueError, match="duplicate"):
        OccupancyJob.parse({**original, "inputs": [*original["inputs"], alias]})
    with pytest.raises(ValueError, match="anchor"):
        OccupancyJob.parse({**original, "frame_id": "invented-world-frame"})
