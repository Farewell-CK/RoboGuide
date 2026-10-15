"""Bounded measured SE(2) registration and conservative immutable occupancy tile fusion."""

from __future__ import annotations

import importlib
import io
import json
import math
import os
import time
import zipfile
from pathlib import Path
from typing import Any

from . import timing
from .contracts import canonical, digest, fields
from .geometry import code_digest, sdk
from .occupancy_contracts import OccupancyJob, OccupancyPolicy, grid
from .storage import immutable, read_json, read_source, save_json


def snapshots(job: OccupancyJob, root: Path, directory: Path) -> list[dict[str, Any]]:
    """Freeze original grid bytes under the complete declared cell budget before native work."""
    result = []
    total = 0
    for index, source in enumerate(job.inputs):
        content = read_source(root, source.path, job.policy.max_input_bytes)
        if digest(content) != source.content_digest:
            raise ValueError("occupancy source bytes differ from their admitted digest")
        value = grid(json.loads(content), job.policy.max_cells)
        count = value["width"] * value["height"]
        total += count
        if total > job.policy.max_total_cells:
            raise ValueError("occupancy inputs exceed their aggregate cell budget")
        name = f"input-{index}.occupancy.json"
        immutable(directory / name, content)
        result.append(
            {
                "source_id": source.source_id,
                "frame_id": source.frame_id,
                "content_digest": source.content_digest,
                "units": source.units,
                "format": source.format,
                "cell_count": count,
                "byte_size": len(content),
                "snapshot": name,
            }
        )
    return result


def transform(np: Any, x: float, y: float, yaw: float) -> Any:
    """Construct a planar rigid transform without fabricating an altitude or full-SE(3) pose."""
    cosine, sine = math.cos(yaw), math.sin(yaw)
    return np.asarray([[cosine, -sine, x], [sine, cosine, y], [0, 0, 1]], dtype=float)


def apply(points: Any, pose: Any) -> Any:
    """Transform actual planar cell centers using row-vector storage."""
    return points @ pose[:2, :2].T + pose[:2, 2]


def angle(value: float) -> float:
    """Retain the exact planar angle modulo one turn for geometric residuals only."""
    return math.atan2(math.sin(value), math.cos(value))


def error(np: Any, first: Any, second: Any) -> tuple[float, float]:
    """Measure planar transform disagreement without treating it as statistical uncertainty."""
    relative = np.linalg.inv(first) @ second
    return float(np.linalg.norm(relative[:2, 2])), abs(math.atan2(relative[1, 0], relative[0, 0]))


def occupied(np: Any, points: Any, policy: OccupancyPolicy) -> Any:
    """Require enough actual non-collinear occupied centers within the full geometry budget."""
    if not 16 <= len(points) <= policy.max_occupied_points or not np.isfinite(points).all():
        raise ValueError("occupied center count/finiteness exceeds the admitted planar profile")
    centered = points - points.mean(axis=0)
    eigenvalues = np.linalg.eigvalsh(centered.T @ centered / len(points))
    if eigenvalues[-1] <= 0 or eigenvalues[0] / eigenvalues[-1] < policy.min_shape_ratio:
        raise ValueError("occupied geometry is degenerate for full SE(2)")
    return points


def source_grid(
    np: Any, value: dict[str, Any], units: str, policy: OccupancyPolicy
) -> dict[str, Any]:
    """Interpret explicit occupancy values and metric cell geometry, retaining unknown values."""
    factor = {"m": 1.0, "cm": 0.01, "mm": 0.001}[units]
    resolution = float(value["resolution"]) * factor
    origin = transform(
        np, value["origin"][0] * factor, value["origin"][1] * factor, value["origin"][2]
    )
    cells = np.asarray(value["cells"], dtype=np.int16).reshape(value["height"], value["width"])
    rows, columns = np.where(cells >= policy.occupied_min)
    points = apply(np.column_stack((columns + 0.5, rows + 0.5)) * resolution, origin)
    if len(points) > policy.max_occupied_points or not np.isfinite(points).all():
        raise ValueError("occupied centers exceed the original planar allocation bound")
    return {
        "cells": cells,
        "resolution": resolution,
        "origin": origin,
        "points": points,
    }


def metric(
    np: Any, tree: Any, source_tree: Any, source: Any, target: Any, pose: Any, distance: float
) -> tuple[float, float]:
    """Measure bidirectional nearest-neighbor overlap and inlier residual on actual centers."""
    forward = tree.query(apply(source, pose), workers=1)[0]
    backward = source_tree.query(apply(target, np.linalg.inv(pose)), workers=1)[0]
    masks = (forward <= distance, backward <= distance)
    overlap = min(float(mask.mean()) for mask in masks)
    if any(not mask.any() for mask in masks):
        return 0.0, math.inf
    residual = max(
        float(np.sqrt(np.mean(values[mask] ** 2)))
        for values, mask in zip((forward, backward), masks, strict=True)
    )
    return overlap, residual


def refine(np: Any, tree: Any, source: Any, initial: Any, policy: OccupancyPolicy) -> Any:
    """Run bounded point-to-point ICP with a rigid two-dimensional SVD update."""
    pose = initial.copy()
    for _ in range(policy.icp_iterations):
        moved = apply(source, pose)
        distances, indices = tree.query(moved, workers=1)
        selected = distances <= policy.max_pair_distance_m
        if int(selected.sum()) < 16:
            raise ValueError("planar ICP lacks measured correspondences")
        left, right = moved[selected], tree.data[indices[selected]]
        left_center, right_center = left.mean(axis=0), right.mean(axis=0)
        u, _, vt = np.linalg.svd((left - left_center).T @ (right - right_center))
        rotation = vt.T @ u.T
        if np.linalg.det(rotation) < 0:
            vt[-1] *= -1
            rotation = vt.T @ u.T
        update = np.eye(3)
        update[:2, :2] = rotation
        update[:2, 2] = right_center - rotation @ left_center
        pose = update @ pose
        translation, yaw = error(np, np.eye(3), update)
        if translation < 1e-8 and yaw < 1e-8:
            break
    return pose


def register(np: Any, first: Any, second: Any, policy: OccupancyPolicy) -> dict[str, Any] | None:
    """Search declared SE(2) hypotheses, refine measured peaks and refuse unresolved ambiguity."""
    spatial = importlib.import_module("scipy.spatial")
    source = first[
        np.linspace(0, len(first) - 1, min(len(first), policy.max_registration_points)).astype(int)
    ]
    target = second[
        np.linspace(0, len(second) - 1, min(len(second), policy.max_registration_points)).astype(
            int
        )
    ]
    source_tree, target_tree = spatial.cKDTree(source), spatial.cKDTree(target)
    yaw_count = math.ceil((policy.yaw_max - policy.yaw_min) / policy.yaw_step) + 1
    xy_count = math.ceil(2 * policy.translation_bound_m / policy.translation_step_m) + 1
    hypotheses = []
    for yaw in np.linspace(policy.yaw_min, policy.yaw_max, yaw_count):
        rotation = transform(np, 0, 0, float(yaw))
        centered = target.mean(axis=0) - rotation[:2, :2] @ source.mean(axis=0)
        for dx in np.linspace(-policy.translation_bound_m, policy.translation_bound_m, xy_count):
            for dy in np.linspace(
                -policy.translation_bound_m, policy.translation_bound_m, xy_count
            ):
                pose = rotation.copy()
                pose[:2, 2] = centered + [dx, dy]
                overlap, residual = metric(
                    np, target_tree, source_tree, source, target, pose, policy.max_pair_distance_m
                )
                if overlap >= policy.min_overlap:
                    hypotheses.append(
                        (overlap, residual, float(yaw), float(pose[0, 2]), float(pose[1, 2]), pose)
                    )
    if not hypotheses:
        return None
    hypotheses.sort(key=lambda item: (-item[0], item[1], item[2], item[3], item[4]))
    peaks: list[Any] = []
    for candidate in hypotheses:
        if candidate[0] < hypotheses[0][0] - policy.score_margin:
            continue
        if any(
            error(np, candidate[-1], previous)[0] <= policy.ambiguity_translation_m
            and error(np, candidate[-1], previous)[1] <= policy.ambiguity_yaw_rad
            for previous in peaks
        ):
            continue
        if len(peaks) >= policy.max_refinements:
            raise ValueError(
                "planar search has unrefined competing peaks within its ambiguity margin"
            )
        peaks.append(candidate[-1])
    full_source, full_target = spatial.cKDTree(first), spatial.cKDTree(second)
    candidates = []
    for peak in peaks:
        try:
            pose = refine(np, full_target, first, peak, policy)
        except ValueError:
            continue
        overlap, residual = metric(
            np, full_target, full_source, first, second, pose, policy.max_pair_distance_m
        )
        if overlap >= policy.min_overlap and residual <= policy.max_rmse_m:
            candidates.append({"transform": pose, "overlap": overlap, "rmse_m": residual})
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item["overlap"], item["rmse_m"]))
    best = candidates[0]
    for other in candidates[1:]:
        translation, yaw = error(np, best["transform"], other["transform"])
        if other["overlap"] >= best["overlap"] - policy.score_margin and (
            translation > policy.ambiguity_translation_m or yaw > policy.ambiguity_yaw_rad
        ):
            return None
    return best


def poses(
    np: Any, points: list[Any], policy: OccupancyPolicy
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Optimize measured planar edges around the fixed anchor and reject inconsistent loops."""
    if len(points) > 1:
        for value in points:
            occupied(np, value, policy)
    edges = []
    for first in range(len(points)):
        for second in range(first + 1, len(points)):
            measured = register(np, points[first], points[second], policy)
            if measured is not None:
                edges.append({"source": first, "target": second, **measured})
    current = {0: np.eye(3)}
    while len(current) < len(points):
        ready = [
            edge for edge in edges if (edge["source"] in current) != (edge["target"] in current)
        ]
        if not ready:
            raise ValueError("planar registration graph is disconnected or ambiguous")
        edge = min(
            ready,
            key=lambda edge: (-edge["overlap"], edge["rmse_m"], edge["source"], edge["target"]),
        )
        first, second = edge["source"], edge["target"]
        if first in current:
            current[second] = current[first] @ np.linalg.inv(edge["transform"])
        else:
            current[first] = current[second] @ edge["transform"]
    result = [current[index] for index in range(len(points))]
    if len(result) > 1:
        initial = np.asarray(
            [[pose[0, 2], pose[1, 2], math.atan2(pose[1, 0], pose[0, 0])] for pose in result[1:]]
        ).reshape(-1)

        def decode(values: Any) -> list[Any]:
            """Retain the first immutable anchor while parameterizing only other planar poses."""
            return [
                np.eye(3),
                *[
                    transform(np, float(x), float(y), float(yaw))
                    for x, y, yaw in values.reshape(-1, 3)
                ],
            ]

        def residual(values: Any) -> Any:
            """Normalize deterministic geometric residuals by policy limits, never by confidence."""
            estimates = decode(values)
            components = []
            for edge in edges:
                predicted = np.linalg.inv(estimates[edge["target"]]) @ estimates[edge["source"]]
                delta = np.linalg.inv(edge["transform"]) @ predicted
                components.extend(
                    [
                        float(delta[0, 2]) / policy.max_cycle_translation_m,
                        float(delta[1, 2]) / policy.max_cycle_translation_m,
                        angle(math.atan2(delta[1, 0], delta[0, 0])) / policy.max_cycle_yaw_rad,
                    ]
                )
            return np.asarray(components)

        optimize = importlib.import_module("scipy.optimize")
        optimized = optimize.least_squares(
            residual, initial, method="trf", max_nfev=policy.pose_graph_evaluations
        )
        if not optimized.success or not np.isfinite(optimized.x).all():
            raise ValueError("planar pose graph did not converge within its original work budget")
        result = decode(optimized.x)
    evidence = []
    spatial = importlib.import_module("scipy.spatial")
    for edge in edges:
        first, second = edge["source"], edge["target"]
        predicted = np.linalg.inv(result[second]) @ result[first]
        translation, yaw = error(np, edge["transform"], predicted)
        overlap, rmse = metric(
            np,
            spatial.cKDTree(points[second]),
            spatial.cKDTree(points[first]),
            points[first],
            points[second],
            predicted,
            policy.max_pair_distance_m,
        )
        if (
            translation > policy.max_cycle_translation_m
            or yaw > policy.max_cycle_yaw_rad
            or overlap < policy.min_overlap
            or rmse > policy.max_rmse_m
        ):
            raise ValueError(
                "optimized planar graph contradicts original measured overlap or loop limits"
            )
        evidence.append(
            {
                "source": first,
                "target": second,
                "overlap": overlap,
                "rmse_m": rmse,
                "cycle_translation_m": translation,
                "cycle_yaw_rad": yaw,
            }
        )
    return result, evidence


def intersects(np: Any, first: Any, second: Any) -> bool:
    """Test positive-area overlap of convex metric cell footprints using separating axes."""
    for polygon in (first, second):
        for index in range(2):
            edge = polygon[(index + 1) % 4] - polygon[index]
            axis = np.asarray([-edge[1], edge[0]])
            left, right = first @ axis, second @ axis
            if float(min(left.max(), right.max()) - max(left.min(), right.min())) <= 1e-12:
                return False
    return True


def merge(
    np: Any,
    values: list[dict[str, Any]],
    transforms: list[Any],
    job: OccupancyJob,
    original: dict[tuple[int, int], int],
) -> dict[tuple[int, int], int]:
    """Preserve conflicts and require complete observed-cell coverage before marking free space."""
    result = dict(original)
    resolution = job.policy.resolution_meters
    square = np.asarray([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=float)
    for source, estimated in zip(values, transforms, strict=True):
        combined = estimated @ source["origin"]
        inverse = np.linalg.inv(combined)
        incoming = source["cells"]
        for row, column in np.argwhere(
            (incoming >= 0)
            & ((incoming <= job.policy.free_max) | (incoming >= job.policy.occupied_min))
        ):
            local = (square + [column, row]) * source["resolution"]
            footprint = apply(local, combined)
            minimum = np.floor(footprint.min(axis=0) / resolution).astype(int)
            maximum = np.ceil(footprint.max(axis=0) / resolution).astype(int)
            if max(abs(int(value)) for value in [*minimum, *maximum]) > 10_000_000:
                raise ValueError("planar grid coordinates exceed the admitted numerical range")
            if int(np.prod(maximum - minimum + 1)) > 4096:
                raise ValueError("one observed cell exceeds its bounded output coverage work")
            for x in range(int(minimum[0]), int(maximum[0]) + 1):
                for y in range(int(minimum[1]), int(maximum[1]) + 1):
                    target = (square + [x, y]) * resolution
                    mask = 0
                    if incoming[row, column] >= job.policy.occupied_min:
                        if intersects(np, footprint, target):
                            mask = 2
                    else:
                        corners = apply(target, inverse)
                        low = local.min(axis=0) + job.policy.free_coverage_margin_m
                        high = local.max(axis=0) - job.policy.free_coverage_margin_m
                        if np.all(corners >= low - 1e-9) and np.all(corners <= high + 1e-9):
                            mask = 1
                    if mask:
                        result[x, y] = result.get((x, y), 0) | mask
                        if len(result) > job.policy.max_total_cells:
                            raise ValueError("fused observed occupancy exceeds its cell budget")
    return result


def parent(
    np: Any, directory: Path, job: OccupancyJob, remaining: int
) -> tuple[dict[tuple[int, int], int], Any] | None:
    """Verify the complete immutable parent index before consuming any planar cell evidence."""
    if job.parent is None:
        return None
    content = read_source(directory, "parent.bundle", job.policy.max_output_bytes)
    if digest(content) != job.parent["content_digest"]:
        raise ValueError("occupancy parent bundle bytes differ")
    result: dict[tuple[int, int], int] = {}
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = archive.namelist()
        if (
            len(names) != len(set(names))
            or len(names) > job.policy.max_tiles + 1
            or any(item.compress_type != zipfile.ZIP_STORED for item in archive.infolist())
        ):
            raise ValueError("occupancy parent tile index is ambiguous or compressed")
        if archive.getinfo("manifest.json").file_size > 1024 * 1024:
            raise ValueError("occupancy parent manifest exceeds its metadata bound")
        manifest = json.loads(archive.read("manifest.json"))
        if (
            manifest.get("schema_version") != "roboguide.spatial-occupancy-map/v0.1"
            or manifest.get("frame_id") != job.frame_id
            or manifest.get("revision_id") != job.parent["revision_id"]
            or manifest.get("resolution_meters") != job.policy.resolution_meters
            or manifest.get("units") != "m"
        ):
            raise ValueError("occupancy parent frame/revision/unit/resolution differs")
        tiles = manifest.get("tiles")
        if not isinstance(tiles, list) or not 1 <= len(tiles) <= job.policy.max_tiles:
            raise ValueError("occupancy parent requires a bounded complete tile list")
        expected = {"manifest.json"}
        for tile in tiles:
            if not isinstance(tile, dict) or not isinstance(tile.get("path"), str):
                raise ValueError("occupancy parent tile metadata is invalid")
            payload = archive.read(tile["path"])
            identity = digest(payload)
            if (
                tile["path"] != f"tiles/{identity.removeprefix('sha256:')}.json"
                or tile.get("content_digest") != identity
            ):
                raise ValueError("occupancy parent tile bytes differ from their immutable index")
            value = fields(json.loads(payload), {"tile_key", "cells"})
            if (
                not isinstance(value, dict)
                or not isinstance(value.get("cells"), list)
                or not isinstance(tile.get("cell_count"), int)
                or isinstance(tile.get("cell_count"), bool)
                or not 1 <= tile["cell_count"] <= remaining
                or len(value["cells"]) != tile.get("cell_count")
                or len(value["cells"]) + len(result) > remaining
            ):
                raise ValueError("occupancy parent cell count exceeds its aggregate bound")
            tile_key = value["tile_key"]
            tile_cells = manifest.get("tile_cells")
            if (
                not isinstance(tile_key, list)
                or len(tile_key) != 2
                or any(isinstance(part, bool) or not isinstance(part, int) for part in tile_key)
                or not isinstance(tile_cells, int)
                or isinstance(tile_cells, bool)
                or not 4 <= tile_cells <= 256
            ):
                raise ValueError("occupancy parent tile dimensions/key are unavailable")
            for cell in value["cells"]:
                if (
                    not isinstance(cell, list)
                    or len(cell) != 3
                    or any(isinstance(item, bool) or not isinstance(item, int) for item in cell)
                    or cell[2] not in (1, 2, 3)
                    or max(abs(cell[0]), abs(cell[1])) > 10_000_000
                    or (cell[0], cell[1]) in result
                    or [cell[0] // tile_cells, cell[1] // tile_cells] != tile_key
                ):
                    raise ValueError("occupancy parent contains invalid or duplicate cells")
                result[cell[0], cell[1]] = cell[2]
            expected.add(tile["path"])
        if (
            expected != set(names)
            or not isinstance(manifest.get("observed_cell_count"), int)
            or isinstance(manifest.get("observed_cell_count"), bool)
            or manifest["observed_cell_count"] != len(result)
        ):
            raise ValueError("occupancy parent complete index/count differs")
    points = np.asarray(
        [
            [(x + 0.5) * job.policy.resolution_meters, (y + 0.5) * job.policy.resolution_meters]
            for (x, y), mask in sorted(result.items())
            if mask == 2
        ]
    )
    return result, points


def compute(directory: Path) -> dict[str, Any]:
    """Produce a measured planar map bundle with unknown space and immutable parent lineage."""
    request = read_json(directory / "request.json")
    job = OccupancyJob.parse(request)
    frozen = read_json(directory / "sources.json")["inputs"]
    np, _, versions, binaries = sdk()
    values = []
    total = 0
    for source in frozen:
        content = read_source(directory, source["snapshot"], job.policy.max_input_bytes)
        if digest(content) != source["content_digest"] or len(content) != source["byte_size"]:
            raise ValueError("frozen occupancy source bytes changed")
        raw = grid(json.loads(content), job.policy.max_cells)
        total += raw["width"] * raw["height"]
        if total > job.policy.max_total_cells:
            raise ValueError("frozen occupancy sources exceed their original aggregate cell budget")
        values.append(source_grid(np, raw, source["units"], job.policy))
    inherited = parent(np, directory, job, job.policy.max_total_cells - total)
    points = [value["points"] for value in values]
    if inherited is not None:
        points.insert(0, inherited[1])
    transforms, measurements = poses(np, points, job.policy)
    merged = merge(
        np,
        values,
        transforms if inherited is None else transforms[1:],
        job,
        {} if inherited is None else inherited[0],
    )
    if not merged:
        raise ValueError("occupancy fusion has no supported observed output")
    grouped: dict[tuple[int, int], list[list[int]]] = {}
    for (x, y), mask in sorted(merged.items()):
        grouped.setdefault((x // job.policy.tile_cells, y // job.policy.tile_cells), []).append(
            [x, y, mask]
        )
    if len(grouped) > job.policy.max_tiles:
        raise ValueError("occupancy tiles exceed the configured index budget")
    payloads: list[tuple[dict[str, Any], bytes]] = []
    for key, cells in sorted(grouped.items()):
        content = canonical({"tile_key": list(key), "cells": cells})
        identity = digest(content)
        payloads.append(
            (
                {
                    "path": f"tiles/{identity.removeprefix('sha256:')}.json",
                    "content_digest": identity,
                    "cell_count": len(cells),
                },
                content,
            )
        )
    manifest = {
        "schema_version": "roboguide.spatial-occupancy-map/v0.1",
        "revision_id": job.job_id,
        "frame_id": job.frame_id,
        "units": "m",
        "geometry": "SE(2)",
        "resolution_meters": job.policy.resolution_meters,
        "tile_cells": job.policy.tile_cells,
        "sources": [
            {key: value for key, value in source.items() if key != "snapshot"} for source in frozen
        ],
        "parent": None
        if job.parent is None
        else {key: value for key, value in job.parent.items() if key != "path"},
        "transformations_to_anchor": [pose.tolist() for pose in transforms],
        "registration": measurements,
        "observed_cell_count": len(merged),
        "tiles": [tile for tile, _ in payloads],
        "unobserved_space": "unknown",
        "cell_masks": {
            "1": "free-full-cell-coverage",
            "2": "occupied-footprint-overlap",
            "3": "conflicting-observations",
        },
        "free_coverage": "one-explicit-free-input-square-covers-complete-output-cell",
        "navigation_safety_assurance": None,
        "covariance": None,
        "current_node_localization": None,
        "algorithm": {
            "name": "bounded-se2-correlative-icp-posegraph-conservative-tiles/v0.1",
            "policy": request["policy"],
            "versions": versions,
            "native_binary_digests": binaries,
            "code_digest": code_digest(),
        },
    }
    archive = directory / "map.bundle"
    with archive.open("xb") as stream:
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as output:
            for name, payload in [
                ("manifest.json", canonical(manifest)),
                *[(tile["path"], content) for tile, content in payloads],
            ]:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.external_attr = 0o400 << 16
                output.writestr(info, payload)
        stream.flush()
        os.fsync(stream.fileno())
        os.fchmod(stream.fileno(), 0o400)
    payload = read_source(directory, archive.name, job.policy.max_output_bytes)
    result = {
        "schema_version": "roboguide.spatial-file-result/v0.1",
        "job_id": job.job_id,
        "request_digest": digest(canonical(request)),
        "status": "completed",
        "computed_at_ms": time.time_ns() // 1_000_000,
        **timing.completion(directory),
        "artifact": {
            "path": archive.name,
            "byte_size": len(payload),
            "content_digest": digest(payload),
        },
        "manifest": manifest,
    }
    save_json(directory / "computed.json", result)
    return result
