"""Actual CPU FPFH/RANSAC, ICP, pose-graph and observed-point tile fusion in a bounded worker."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import io
import json
import math
import re
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, cast

from . import timing
from .contracts import Job, canonical, digest
from .storage import immutable, point_count, read_json, read_source, save_json


def sdk() -> tuple[Any, Any, dict[str, str], dict[str, str]]:
    """Load only the pinned independent runtime; no tensor/GPU or simulator interface is used."""
    if sys.version_info[:2] != (3, 11):
        raise ValueError("spatial provider requires its independent Python 3.11 runtime")
    versions = {name: importlib.metadata.version(name) for name in ("open3d", "numpy", "scipy")}
    if versions != {"open3d": "0.19.0", "numpy": "2.4.6", "scipy": "1.17.1"}:
        raise ValueError("spatial dependency identity differs from the declared runtime")
    np = importlib.import_module("numpy")
    native = importlib.import_module("open3d")
    native.utility.set_verbosity_level(native.utility.VerbosityLevel.Error)
    binaries = {}
    for name in ("open3d.cpu.pybind", "numpy._core._multiarray_umath", "scipy.linalg._fblas"):
        module = importlib.import_module(name)
        binary = hashlib.sha256()
        with Path(module.__file__).open("rb") as stream:
            while part := stream.read(1024 * 1024):
                binary.update(part)
        binaries[name] = "sha256:" + binary.hexdigest()
    return np, native, versions, binaries


def rigid(np: Any, value: Any) -> Any:
    """Reject invalid transforms instead of projecting an arbitrary matrix into a physical pose."""
    matrix = np.asarray(value, dtype=float)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise ValueError("registration transform is not a finite 4x4 matrix")
    if (
        not np.allclose(matrix[3], [0, 0, 0, 1], atol=1e-8)
        or not np.allclose(matrix[:3, :3].T @ matrix[:3, :3], np.eye(3), atol=1e-5)
        or abs(float(np.linalg.det(matrix[:3, :3])) - 1) > 1e-5
    ):
        raise ValueError("registration transform is not rigid SE(3)")
    return matrix


def difference(np: Any, first: Any, second: Any) -> tuple[float, float]:
    """Measure actual transform disagreement in metres/radians without inventing covariance."""
    relative = np.linalg.inv(rigid(np, first)) @ rigid(np, second)
    angle = math.acos(max(-1.0, min(1.0, (float(np.trace(relative[:3, :3])) - 1) / 2)))
    return float(np.linalg.norm(relative[:3, 3])), angle


def cloud(
    np: Any, native: Any, path: Path, format_name: str, units: str, count: int, job: Job
) -> Any:
    """Decode the verified snapshot and check all original points before any geometry reduction."""
    value = native.io.read_point_cloud(
        str(path), format=format_name, remove_nan_points=False, remove_infinite_points=False
    )
    points = np.asarray(value.points)
    if points.shape != (count, 3) or not np.isfinite(points).all():
        raise ValueError("decoded point count/finiteness differs from the frozen source")
    factor = {"m": 1.0, "cm": 0.01, "mm": 0.001}[units]
    converted = points * factor
    if float(np.max(np.abs(converted))) > 10_000_000:
        raise ValueError("metric coordinates exceed the admitted numerical range")
    value.points = native.utility.Vector3dVector(converted)
    centered = converted - converted.mean(axis=0)
    eigenvalues = np.linalg.eigvalsh(centered.T @ centered / count)
    if (
        float(eigenvalues[-1]) <= 0
        or float(eigenvalues[0] / eigenvalues[-1]) < job.policy.min_shape_ratio
    ):
        raise ValueError("geometry is degenerate for the admitted full-SE(3) profile")
    if value.has_colors() and not np.isfinite(np.asarray(value.colors)).all():
        raise ValueError("point colors are non-finite")
    return value


def parent_cloud(
    np: Any, native: Any, directory: Path, job: Job, remaining_points: int
) -> tuple[Any, dict[str, Any]] | None:
    """Load only verified bounded immutable parent tiles, preserving its original anchor frame."""
    if job.parent is None:
        return None
    content = read_source(directory, "parent.bundle", job.policy.max_output_bytes)
    if digest(content) != job.parent["content_digest"]:
        raise ValueError("parent bundle digest differs")
    points = []
    total = 0
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or len(names) > job.policy.max_tiles + 1:
            raise ValueError("parent tile index is ambiguous or exceeds its bound")
        if any(
            i.compress_type != zipfile.ZIP_STORED or i.file_size > job.policy.max_output_bytes
            for i in archive.infolist()
        ):
            raise ValueError("parent archives must use bounded uncompressed immutable entries")
        if archive.getinfo("manifest.json").file_size > 1024 * 1024:
            raise ValueError("parent manifest exceeds its bound")
        manifest = json.loads(archive.read("manifest.json"))
        if (
            manifest.get("schema_version") != "roboguide.spatial-file-map/v0.1"
            or manifest.get("frame_id") != job.frame_id
            or manifest.get("revision_id") != job.parent["revision_id"]
            or manifest.get("units") != "m"
        ):
            raise ValueError("parent immutable revision/frame/unit contract differs")
        tiles = manifest.get("tiles")
        if not isinstance(tiles, list) or not 1 <= len(tiles) <= job.policy.max_tiles:
            raise ValueError("parent has no bounded complete tile index")
        for index, tile in enumerate(tiles):
            if not isinstance(tile, dict) or set(tile) != {
                "path",
                "content_digest",
                "point_count",
                "bounds",
            }:
                raise ValueError("parent tile metadata shape differs")
            path = tile["path"]
            content_digest = tile["content_digest"]
            if (
                not isinstance(path, str)
                or not isinstance(content_digest, str)
                or re.fullmatch("sha256:[0-9a-f]{64}", content_digest) is None
                or path != f"tiles/{content_digest.removeprefix('sha256:')}.ply"
            ):
                raise ValueError("parent tile name is not its immutable digest")
            payload = archive.read(path)
            if digest(payload) != tile["content_digest"]:
                raise ValueError("parent tile bytes differ")
            count = point_count(payload, "ply", remaining_points - total)
            if (
                not isinstance(tile["point_count"], int)
                or isinstance(tile["point_count"], bool)
                or count != tile["point_count"]
            ):
                raise ValueError("parent tile count differs")
            total += count
            if total > remaining_points:
                raise ValueError("parent geometry exceeds the aggregate point bound")
            local = directory / f"parent-tile-{index}.ply"
            immutable(local, payload)
            decoded = native.io.read_point_cloud(
                str(local), remove_nan_points=False, remove_infinite_points=False
            )
            array = np.asarray(decoded.points)
            if array.shape != (count, 3) or not np.isfinite(array).all():
                raise ValueError("parent tile decoded geometry differs")
            points.append(array.copy())
        if set(names) != {"manifest.json", *(tile["path"] for tile in tiles)}:
            raise ValueError("parent archive contains undeclared content")
        if (
            not isinstance(manifest.get("point_count"), int)
            or isinstance(manifest.get("point_count"), bool)
            or manifest["point_count"] != total
        ):
            raise ValueError("parent aggregate point count differs from its complete tile index")
    combined = native.geometry.PointCloud()
    combined.points = native.utility.Vector3dVector(np.concatenate(points))
    return combined, cast(dict[str, Any], manifest)


def preprocess(np: Any, native: Any, value: Any, job: Job) -> tuple[Any, Any]:
    """Compute bounded sampled normals/FPFH from actual points, never from a supplied transform."""
    sampled = value.voxel_down_sample(job.policy.voxel_size)
    if not 30 <= len(sampled.points) <= job.policy.max_downsample_points:
        raise ValueError("sampled geometry is insufficient or exceeds its feature budget")
    sampled.estimate_normals(
        native.geometry.KDTreeSearchParamHybrid(radius=job.policy.normal_radius, max_nn=30)
    )
    if not np.isfinite(np.asarray(sampled.normals)).all():
        raise ValueError("normal estimation is unavailable")
    feature = native.pipelines.registration.compute_fpfh_feature(
        sampled,
        native.geometry.KDTreeSearchParamHybrid(radius=job.policy.feature_radius, max_nn=100),
    )
    if not np.isfinite(np.asarray(feature.data)).all():
        raise ValueError("FPFH contains non-finite values")
    return sampled, feature


def register(
    np: Any, native: Any, first: tuple[Any, Any], second: tuple[Any, Any], job: Job, seed: int
) -> dict[str, Any] | None:
    """Run bounded global hypotheses and ICP, refusing unsupported competing poses."""
    registration = native.pipelines.registration
    source, source_feature = first
    target, target_feature = second
    candidates = []
    for repeat in range(job.policy.ransac_repeats):
        native.utility.random.seed((seed + repeat) % (2**31 - 1))
        global_result = registration.registration_ransac_based_on_feature_matching(
            source,
            target,
            source_feature,
            target_feature,
            True,
            job.policy.ransac_distance,
            registration.TransformationEstimationPointToPoint(False),
            3,
            [
                registration.CorrespondenceCheckerBasedOnEdgeLength(0.9),
                registration.CorrespondenceCheckerBasedOnDistance(job.policy.ransac_distance),
            ],
            registration.RANSACConvergenceCriteria(job.policy.ransac_trials, 0.999),
        )
        if not np.isfinite(global_result.transformation).all():
            continue
        refined = registration.registration_icp(
            source,
            target,
            job.policy.icp_distance,
            rigid(np, global_result.transformation),
            registration.TransformationEstimationPointToPlane(),
            registration.ICPConvergenceCriteria(max_iteration=job.policy.icp_iterations),
        )
        transform = rigid(np, refined.transformation)
        reverse = registration.evaluate_registration(
            target, source, job.policy.icp_distance, np.linalg.inv(transform)
        )
        fitness = min(float(refined.fitness), float(reverse.fitness))
        rmse = max(float(refined.inlier_rmse), float(reverse.inlier_rmse))
        if (
            not math.isfinite(fitness)
            or not math.isfinite(rmse)
            or fitness < job.policy.min_fitness
            or rmse > job.policy.max_rmse
        ):
            continue
        candidates.append({"transformation": transform, "fitness": fitness, "rmse": rmse})
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item["fitness"], item["rmse"]))
    best = candidates[0]
    for other in candidates[1:]:
        translation, angle = difference(np, best["transformation"], other["transformation"])
        if translation > job.policy.max_cycle_translation or angle > job.policy.max_cycle_angle:
            return None
    best["information"] = registration.get_information_matrix_from_point_clouds(
        source, target, job.policy.icp_distance, best["transformation"]
    )
    if not np.isfinite(best["information"]).all():
        return None
    return best


def poses(
    np: Any, native: Any, values: list[Any], job: Job
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Optimize a connected measured graph and check its original loop residuals."""
    prepared = [preprocess(np, native, value, job) for value in values]
    edges = []
    for first in range(len(values)):
        for second in range(first + 1, len(values)):
            pair_result = register(
                np,
                native,
                prepared[first],
                prepared[second],
                job,
                job.policy.seed + first * 101 + second * 13,
            )
            if pair_result is not None:
                edges.append({"source": first, "target": second, **pair_result})
    selected: list[Any | None] = [None] * len(values)
    selected[0] = np.eye(4)
    tree: set[tuple[int, int]] = set()
    for _ in range(len(values)):
        for edge in sorted(edges, key=lambda e: (-e["fitness"], e["source"], e["target"])):
            first, second = edge["source"], edge["target"]
            if selected[first] is not None and selected[second] is None:
                selected[second] = selected[first] @ np.linalg.inv(edge["transformation"])
                tree.add((first, second))
            elif selected[first] is None and selected[second] is not None:
                selected[first] = selected[second] @ edge["transformation"]
                tree.add((first, second))
    if any(pose is None for pose in selected):
        raise ValueError("registered geometry is disconnected, ambiguous or lacks measured overlap")
    registration = native.pipelines.registration
    graph = registration.PoseGraph()
    for pose in selected:
        graph.nodes.append(registration.PoseGraphNode(rigid(np, pose)))
    for edge in edges:
        graph.edges.append(
            registration.PoseGraphEdge(
                edge["source"],
                edge["target"],
                edge["transformation"],
                edge["information"],
                uncertain=(edge["source"], edge["target"]) not in tree,
            )
        )
    if len(values) > 1:
        registration.global_optimization(
            graph,
            registration.GlobalOptimizationLevenbergMarquardt(),
            registration.GlobalOptimizationConvergenceCriteria(),
            registration.GlobalOptimizationOption(
                max_correspondence_distance=job.policy.icp_distance,
                edge_prune_threshold=0.25,
                reference_node=0,
            ),
        )
    result = [rigid(np, node.pose) for node in graph.nodes]
    metrics = []
    for edge in edges:
        first, second = edge["source"], edge["target"]
        transform = np.linalg.inv(result[second]) @ result[first]
        translation, angle = difference(np, edge["transformation"], transform)
        if translation > job.policy.max_cycle_translation or angle > job.policy.max_cycle_angle:
            raise ValueError("pose-graph cycle conflicts with original measured registration")
        measured = registration.evaluate_registration(
            values[first], values[second], job.policy.icp_distance, transform
        )
        reverse = registration.evaluate_registration(
            values[second], values[first], job.policy.icp_distance, np.linalg.inv(transform)
        )
        fitness = min(float(measured.fitness), float(reverse.fitness))
        rmse = max(float(measured.inlier_rmse), float(reverse.inlier_rmse))
        if (
            not math.isfinite(fitness)
            or not math.isfinite(rmse)
            or fitness < job.policy.min_fitness
            or rmse > job.policy.max_rmse
        ):
            raise ValueError("full original geometry does not support the optimized graph")
        metrics.append(
            {
                "source": first,
                "target": second,
                "bidirectional_fitness": fitness,
                "max_direction_rmse_m": rmse,
                "cycle_translation_m": translation,
                "cycle_angle_rad": angle,
                "information_matrix": edge["information"].tolist(),
                "information_is_covariance": False,
            }
        )
    return result, metrics


def fuse(
    np: Any, native: Any, values: list[Any], transformations: list[Any], directory: Path, job: Job
) -> tuple[list[dict[str, Any]], int]:
    """Fuse observed transformed points into bounded sparse tiles."""
    points = []
    for value, transform in zip(values, transformations, strict=True):
        original = np.asarray(value.points)
        points.append(original @ transform[:3, :3].T + transform[:3, 3])
    combined = np.concatenate(points)
    if len(combined) > job.policy.max_total_points:
        raise ValueError("parent/new geometry exceeds the aggregate fusion budget")
    merged = native.geometry.PointCloud()
    merged.points = native.utility.Vector3dVector(combined)
    merged = merged.voxel_down_sample(job.policy.voxel_size)
    points = np.asarray(merged.points)
    keys = np.floor(points / job.policy.tile_size)
    if not np.isfinite(keys).all() or float(np.max(np.abs(keys))) > 2**52:
        raise ValueError("sparse tile coordinates exceed exact integer representation")
    keys = keys.astype(np.int64)
    unique = np.unique(keys, axis=0)
    if len(unique) > job.policy.max_tiles:
        raise ValueError("observed tiles exceed the declared output bound")
    result = []
    written = 0
    for index, key in enumerate(unique):
        selected = points[np.all(keys == key, axis=1)]
        order = np.lexsort((selected[:, 2], selected[:, 1], selected[:, 0]))
        tile = native.geometry.PointCloud()
        tile.points = native.utility.Vector3dVector(selected[order])
        path = directory / f"tile-{index}.ply"
        if not native.io.write_point_cloud(str(path), tile, write_ascii=False, compressed=False):
            raise ValueError("native tile serialization failed")
        payload = read_source(directory, path.name, job.policy.max_output_bytes)
        written += len(payload)
        if written > job.policy.max_output_bytes:
            raise ValueError("fused tile bytes exceed the aggregate output budget")
        content_digest = digest(payload)
        result.append(
            {
                "path": f"tiles/{content_digest.removeprefix('sha256:')}.ply",
                "content_digest": content_digest,
                "point_count": len(selected),
                "bounds": {
                    "min": selected.min(axis=0).tolist(),
                    "max": selected.max(axis=0).tolist(),
                },
                "local_file": path.name,
            }
        )
    return result, int(len(points))


def compute(directory: Path) -> dict[str, Any]:
    """Compute one immutable file revision without localization or Task authority."""
    request = read_json(directory / "request.json")
    job = Job.parse(request)
    frozen = read_json(directory / "sources.json")["inputs"]
    np, native, versions, binaries = sdk()
    for item in frozen:
        content = read_source(directory, item["snapshot"], job.policy.max_input_bytes)
        if digest(content) != item["content_digest"] or len(content) != item["byte_size"]:
            raise ValueError("frozen original source changed before native computation")
    new_count = sum(item["point_count"] for item in frozen)
    if new_count > job.policy.max_total_points:
        raise ValueError("original geometry exceeds its aggregate point budget")
    values = [
        cloud(
            np,
            native,
            directory / item["snapshot"],
            item["format"],
            item["units"],
            item["point_count"],
            job,
        )
        for item in frozen
    ]
    parent = parent_cloud(np, native, directory, job, job.policy.max_total_points - new_count)
    if parent is not None:
        values.insert(0, parent[0])
    transformations, metrics = poses(np, native, values, job)
    tiles, count = fuse(np, native, values, transformations, directory, job)
    manifest = {
        "schema_version": "roboguide.spatial-file-map/v0.1",
        "revision_id": job.job_id,
        "frame_id": job.frame_id,
        "units": "m",
        "voxel_size_m": job.policy.voxel_size,
        "sources": [
            {key: value for key, value in source.items() if key != "snapshot"} for source in frozen
        ],
        "parent": None
        if job.parent is None
        else {key: value for key, value in job.parent.items() if key != "path"},
        "transformations_to_anchor": [pose.tolist() for pose in transformations],
        "registration": metrics,
        "point_count": count,
        "tiles": [
            {key: value for key, value in tile.items() if key != "local_file"} for tile in tiles
        ],
        "unobserved_space": "unknown",
        "covariance": None,
        "current_node_localization": None,
        "algorithm": {
            "name": "fpfh-ransac-icp-posegraph-observed-voxel-tiles/v0.1",
            "versions": versions,
            "native_binary_digests": binaries,
            "policy": request["policy"],
            "code_digest": code_digest(),
        },
    }
    archive = directory / "map.bundle"
    with archive.open("xb") as stream:
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as output:
            for name, payload in [
                ("manifest.json", canonical(manifest)),
                *[
                    (
                        tile["path"],
                        read_source(directory, tile["local_file"], job.policy.max_output_bytes),
                    )
                    for tile in tiles
                ],
            ]:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.external_attr = 0o400 << 16
                output.writestr(info, payload)
        stream.flush()
        import os

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
            "content_digest": digest(payload),
            "byte_size": len(payload),
            "path": archive.name,
        },
        "manifest": manifest,
    }
    save_json(directory / "computed.json", result)
    return result


def code_digest() -> str:
    """Bind original code bytes independently of dependency version labels."""
    directory = Path(__file__).parent
    return digest(
        b"".join(
            path.name.encode() + b"\0" + path.read_bytes()
            for path in sorted(directory.glob("*.py"))
        )
    )
