"""Optional AKAZE registration of bounded occupancy grids."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .occupancy_contracts import OccupancyPolicy

OPENCV_PACKAGE = "opencv-python-headless"
OPENCV_VERSION = "4.13.0.92"


def feature_registration(
    np: Any, grids: list[dict[str, Any]], policy: OccupancyPolicy
) -> tuple[Callable[[int, int], Any | None], str, str]:
    """Compute features once per grid and estimate pairwise transforms in metres."""
    try:
        distribution = importlib.metadata.distribution(OPENCV_PACKAGE)
    except importlib.metadata.PackageNotFoundError as exc:
        raise ValueError(f"opencv-akaze requires {OPENCV_PACKAGE}=={OPENCV_VERSION}") from exc
    if distribution.version != OPENCV_VERSION:
        raise ValueError(f"opencv-akaze requires {OPENCV_PACKAGE}=={OPENCV_VERSION}")
    cv = importlib.import_module("cv2")
    # Resolve the native extension used by the Python package.
    native_path = Path(cv._native.__file__).resolve()
    if not any(
        distribution.locate_file(path).resolve() == native_path
        for path in distribution.files or ()
        if path.parts[0] == "cv2" and path.suffix in {".so", ".pyd"}
    ):
        raise ValueError("opencv-akaze did not load its declared OpenCV binary")
    with native_path.open("rb") as stream:
        binary_digest = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()

    cv.setNumThreads(policy.threads)
    detector = cv.AKAZE_create(max_points=policy.max_registration_points)
    features = []
    for grid in grids:
        cells = grid["cells"]
        image = np.full(cells.shape, 127, dtype=np.uint8)
        image[(0 <= cells) & (cells <= policy.free_max)] = 255
        image[cells >= policy.occupied_min] = 0
        keypoints, descriptors = detector.detectAndCompute(image, None)
        pixels = np.asarray([keypoint.pt for keypoint in keypoints]).reshape(-1, 2)
        local = (pixels + 0.5) * grid["resolution"]
        origin = grid["origin"]
        features.append((local @ origin[:2, :2].T + origin[:2, 2], descriptors))

    matcher = cv.BFMatcher(cv.NORM_HAMMING)

    def distinct_matches(source: Any, target: Any) -> dict[int, int]:
        return {
            pair[0].queryIdx: pair[0].trainIdx
            for pair in matcher.knnMatch(source, target, k=2)
            if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance
        }

    def estimate(first: int, second: int) -> Any | None:
        source_points, source_descriptors = features[first]
        target_points, target_descriptors = features[second]
        if source_descriptors is None or target_descriptors is None:
            return None
        forward = distinct_matches(source_descriptors, target_descriptors)
        reverse = distinct_matches(target_descriptors, source_descriptors)
        pairs = [(i, j) for i, j in forward.items() if reverse.get(j) == i]
        if len(pairs) < 6:
            return None
        source_indices, target_indices = zip(*pairs, strict=True)
        source, target = source_points[list(source_indices)], target_points[list(target_indices)]
        source_origin, target_origin = source.mean(axis=0), target.mean(axis=0)
        affine, inliers = cv.estimateAffinePartial2D(
            source - source_origin,
            target - target_origin,
            method=cv.RANSAC,
            ransacReprojThreshold=policy.max_pair_distance_m,
            maxIters=min(2000, policy.max_hypotheses),
        )
        if affine is None or inliers is None or not np.isfinite(affine).all():
            return None
        selected = inliers.ravel().astype(bool)
        if selected.sum() < 6 or selected.mean() <= 0.5:
            return None
        scale = float(np.linalg.norm(affine[:, 0]))
        if abs(scale - 1.0) > 0.02:
            return None
        rotation = affine[:, :2] / scale
        yaw = math.atan2(rotation[1, 0], rotation[0, 0])
        if not policy.yaw_min <= yaw <= policy.yaw_max:
            return None
        delta = target[selected] - source[selected] @ rotation.T
        translation = delta.mean(axis=0)
        if np.sqrt(np.mean(np.sum((delta - translation) ** 2, axis=1))) > policy.max_rmse_m:
            return None
        pose = np.eye(3)
        pose[:2, :2] = rotation
        pose[:2, 2] = translation
        return pose

    return estimate, distribution.version, binary_digest
