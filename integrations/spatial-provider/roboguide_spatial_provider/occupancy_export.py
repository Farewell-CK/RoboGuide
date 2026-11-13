"""Export verified sparse occupancy tiles as a bounded Nav2 trinary map."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .contracts import canonical, digest, number
from .occupancy import bundle_cells
from .occupancy_contracts import OccupancyPolicy
from .occupancy_planning import CoveragePolicy, TargetExtent, plan_free_coverage

MAX_BUNDLE_BYTES = 64 * 1024**2
MAX_GRID_CELLS = 1_000_000


@dataclass(frozen=True)
class NavigationMap:
    """Carry deterministic image, configuration and provenance bytes for one export."""

    pgm: bytes
    yaml: bytes
    receipt: bytes


def export_navigation_map(
    content: bytes,
    *,
    content_digest: str,
    frame_id: str,
    revision_id: str,
    resolution_meters: float,
    planning_inputs: tuple[bytes, ...] | None = None,
) -> NavigationMap:
    """Export tiles with optional complete free coverage from each exact original source."""
    if len(content) > MAX_BUNDLE_BYTES or digest(content) != content_digest:
        raise ValueError("occupancy export source exceeds its bound or differs from its digest")
    resolution = number(resolution_meters, "resolution_meters", 0.001, 10)
    manifest, cells = bundle_cells(
        content,
        frame_id=frame_id,
        revision_id=revision_id,
        resolution=resolution,
        max_tiles=512,
        max_cells=MAX_GRID_CELLS,
    )
    min_x = min(x for x, _ in cells)
    min_y = min(y for _, y in cells)
    max_x = max(x for x, _ in cells)
    max_y = max(y for _, y in cells)
    width, height = max_x - min_x + 1, max_y - min_y + 1
    if width > 4096 or height > 4096 or width * height > MAX_GRID_CELLS:
        raise ValueError("occupancy export rectangle exceeds its grid bounds")
    origin = [min_x * resolution, min_y * resolution, 0.0]
    planning = None
    if planning_inputs is not None:
        planning = _add_verified_free_coverage(
            cells,
            manifest,
            planning_inputs,
            TargetExtent(origin[0], origin[1], width * resolution, height * resolution, resolution),
            min_x,
            min_y,
        )
    pixels = bytearray([128]) * (width * height)
    counts = {1: 0, 2: 0, 3: 0}
    for (x, y), mask in cells.items():
        pixels[(max_y - y) * width + x - min_x] = 254 if mask == 1 else 0
        counts[mask] += 1
    pgm = f"P5\n{width} {height}\n255\n".encode("ascii") + pixels
    yaml = (
        f"image: map.pgm\nmode: trinary\nresolution: {resolution!r}\n"
        f"origin: {origin!r}\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.25\n"
    ).encode("ascii")
    grid: dict[str, Any] = {
        "schema_version": "roboguide.occupancy-grid/v0.1",
        "width": width,
        "height": height,
        "resolution": resolution,
        "origin": origin,
        "cells": [
            0 if pixel == 254 else 100 if pixel == 0 else -1
            for row in range(height - 1, -1, -1)
            for pixel in pixels[row * width : (row + 1) * width]
        ],
    }
    receipt = canonical(
        {
            "schema_version": "roboguide.occupancy-navigation-export/v0.1",
            "source_digest": content_digest,
            "frame_id": frame_id,
            "revision_id": revision_id,
            "width": width,
            "height": height,
            "origin_m": origin,
            "resolution_meters": resolution,
            "observed_cell_count": manifest["observed_cell_count"],
            "planning_coverage": planning,
            "free_cells": counts[1],
            "occupied_cells": counts[2],
            "conflict_cells": counts[3],
            "unknown_cells": width * height - len(cells),
            "pgm_digest": digest(pgm),
            "yaml_digest": digest(yaml),
            "ros_cell_data_digest": digest(bytes(cell % 256 for cell in grid["cells"])),
            "expected_grid_digest": digest(canonical(grid)),
        }
    )
    return NavigationMap(pgm, yaml, receipt)


def _add_verified_free_coverage(
    cells: dict[tuple[int, int], int],
    manifest: dict[str, Any],
    inputs: tuple[bytes, ...],
    extent: TargetExtent,
    min_x: int,
    min_y: int,
) -> dict[str, Any]:
    """Extend the planning raster using complete coverage by each source separately."""
    sources = manifest["sources"]
    transforms = manifest["transformations_to_anchor"]
    if (
        manifest["parent"] is not None
        or len(sources) not in (1, 2)
        or len(transforms) != len(sources)
        or len(inputs) != len(sources)
    ):
        raise ValueError(
            "planning coverage requires complete original single- or two-source inputs"
        )
    original = OccupancyPolicy.parse(manifest["algorithm"]["policy"])
    if original.free_coverage_margin_m != 0:
        raise ValueError("planning coverage requires an explicit zero source-cell margin")
    coverage = CoveragePolicy(
        free_max=original.free_max,
        occupied_min=original.occupied_min,
        max_source_cells=original.max_cells,
        max_target_cells=MAX_GRID_CELLS,
        max_cells_per_target=4096,
        max_work_cells=8_000_000,
        max_output_cells=MAX_GRID_CELLS,
    )
    evidence = []
    for content, source, transform in zip(inputs, sources, transforms, strict=True):
        if (
            len(content) > original.max_input_bytes
            or len(content) != source["byte_size"]
            or digest(content) != source["content_digest"]
            or source["units"] != "m"
            or source["format"] != "occupancy-json"
        ):
            raise ValueError("planning coverage input differs from the original metric source")
        projected = plan_free_coverage(
            json.loads(content),
            target_from_source=transform,
            target_extent=extent,
            policy=coverage,
        )
        for x, y in projected.eligible_cells:
            key = (min_x + x, min_y + y)
            cells[key] = cells.get(key, 0) | 1
        evidence.append(
            {
                "source_digest": source["content_digest"],
                "free_cells": len(projected.eligible_cells),
                "evaluated_source_cell_visits": projected.evaluated_source_cell_visits,
            }
        )
    return {"method": "same-source-complete-bounding-cell-coverage/v0.1", "sources": evidence}
