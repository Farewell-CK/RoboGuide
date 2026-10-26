"""Bounded comparisons of already returned detection and semantic image evidence.

No camera, sensor, predicate, simulator step or model is invoked. Pixel counts
and camera projections explain the current cache; they never authorize local
completion, manufacture visibility, or replace Habitat's official metric.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .diagnostics import BufferedJsonlWriter
from .model import CanonicalObservationAcquisitionInvocation, CanonicalObservationInvocation
from .perception import observe_detection

_LOG = logging.getLogger(__name__)
PERCEPTION_DIAGNOSTICS_SCHEMA = "roboguide.cached-perception-diagnostics/v0.1"
SAMPLE_PERIOD = 16
MAX_CAMERAS = 8
MAX_PIXELS = 4_194_304
MAX_RECORDS = 4096


def _unavailable(error: Exception) -> dict[str, str]:
    """Retain the failure type without serializing arbitrary simulator objects."""
    return {"status": "unavailable", "reason": type(error).__name__}


def _projection(environment: Any, camera: str, target: Any) -> dict[str, Any]:
    """Read existing camera matrices; geometric inclusion does not establish visibility."""
    try:
        sensor = environment.sim._sensors[camera]._sensor_object
        renderer = sensor.render_camera
        point = [float(value) for value in renderer.camera_matrix.transform_point(target)]
        hfov = float(sensor.hfov)
        viewport = renderer.viewport
        width, height = int(viewport[0]), int(viewport[1])
        if (
            len(point) != 3
            or not all(math.isfinite(value) for value in point)
            or not 0 < hfov < 180
            or min(width, height) <= 0
        ):
            raise ValueError("invalid camera geometry")
        half_horizontal = math.radians(hfov) / 2
        half_vertical = math.atan(math.tan(half_horizontal) * height / width)
        return {
            "status": "observed-geometry",
            "target_camera_position": point,
            "camera_forward_axis": "-z",
            "hfov_degrees": hfov,
            "viewport": [width, height],
            "target_center_inside_frustum": point[2] < 0
            and abs(math.atan2(point[0], -point[2])) <= half_horizontal
            and abs(math.atan2(point[1], -point[2])) <= half_vertical,
            "occlusion": "unavailable",
            "frustum_basis": "derived-from-read-only-camera-matrices; not visibility proof",
        }
    except Exception as error:  # noqa: BLE001 - optional reads never affect execution
        return _unavailable(error)


def _pixels(image: Any, object_id: int, remaining: int) -> tuple[int, int]:
    """Count one exact ID in a bounded integer frame without copying the image to JSON."""
    shape = tuple(image.shape)
    count = math.prod(shape)
    if (
        len(shape) not in (2, 3)
        or (len(shape) == 3 and shape[-1] != 1)
        or not all(type(size) is int and size > 0 for size in shape)
        or count > remaining
        or image.dtype.kind not in {"i", "u"}
    ):
        raise ValueError("semantic image is invalid or exceeds the read budget")
    result = int((image == object_id).sum())
    if not 0 <= result <= count:
        raise ValueError("invalid semantic pixel count")
    return result, count


def cached_detection_snapshot(
    invocation: CanonicalObservationInvocation,
    environment: Any,
    observations: Mapping[str, Any],
    sensors: Mapping[int, str],
    step: int,
    semantic_digest: str,
) -> dict[str, Any]:
    """Compare exact-agent caches, raw images and the current union from the same Gym result."""
    problem = environment.task.pddl_problem
    entity = next(
        item for item in problem.get_ordered_entities_list() if item.name == invocation.object_ref
    )
    target = problem.sim_info.get_entity_pos(entity)
    detectors = environment.task.sensor_suite.sensors
    if not 1 <= len(sensors) <= 4:
        raise ValueError("cached detector coverage exceeds the diagnostic bound")
    records: dict[str, Any] = {}
    values: list[bool | None] = []
    remaining = MAX_PIXELS
    for agent, key in sorted(sensors.items()):
        receipt = observe_detection(
            invocation, environment, observations, agent, key, step, semantic_digest
        )
        observed = receipt.get("detection_condition_holds", receipt["condition_holds"])
        values.append(observed)
        entry = {
            field: receipt.get(field)
            for field in (
                "sensor_uuid",
                "sensor_scope",
                "condition_holds",
                "status",
                "simulator_semantic_object_id",
                "unavailable_reason",
            )
        }
        records[str(agent)] = entry
        if (
            isinstance(invocation, CanonicalObservationAcquisitionInvocation)
            and invocation.requires_arrival
        ):
            entry.update(
                detection_condition_holds=observed,
                arrival_condition_holds=receipt.get("arrival_condition_holds"),
            )
        cameras: dict[str, Any] = {}
        try:
            detector = detectors[key]
            owner = detector.agent_id
            if owner is not None and (type(owner) is not int or owner != agent):
                raise ValueError("detector owner differs from its declared endpoint")
            threshold = detector.pixel_threshold
            if isinstance(threshold, bool) or not math.isfinite(threshold) or threshold < 0:
                raise ValueError("invalid original pixel threshold")
            keys = [
                name
                for name in observations
                if "semantic" in name and (owner is None or f"agent_{owner}" in name)
            ]
            if not 1 <= len(keys) <= MAX_CAMERAS or any(len(name) > 256 for name in keys):
                raise ValueError("semantic camera coverage is unavailable or oversized")
            for name in keys:
                pixels, consumed = _pixels(
                    observations[name], receipt["simulator_semantic_object_id"], remaining
                )
                remaining -= consumed
                cameras[name] = {
                    "target_pixels": pixels,
                    "pixel_threshold": threshold,
                    "comparison": ">",
                    "detected": pixels > threshold,
                    "geometry": _projection(environment, name, target),
                }
            from_frames = any(row["detected"] for row in cameras.values())
            entry.update(
                original_detector_agent_id=owner,
                semantic_frames=cameras,
                detection_from_current_frames=from_frames,
                cache_matches_current_frames=(observed == from_frames)
                if observed is not None
                else None,
            )
        except Exception as error:  # noqa: BLE001 - incomplete evidence is not a false reading
            entry["semantic_frames"] = cameras
            entry["frame_comparison"] = _unavailable(error)
    return {
        "schema_version": "roboguide.cached-perception-diagnostics/v0.2"
        if isinstance(invocation, CanonicalObservationAcquisitionInvocation)
        and invocation.requires_arrival
        else PERCEPTION_DIAGNOSTICS_SCHEMA,
        "invocation_digest": "sha256:" + invocation.request_key(),
        "attempt_id": invocation.attempt_id,
        "authoritative_semantic_evidence_digest": semantic_digest,
        "condition": invocation.parameters["expected"],
        "simulator_step": step,
        "source": "same-existing-gym-observation",
        "detectors": records,
        "cached_world_union": True
        if True in values
        else False
        if values and all(value is False for value in values)
        else None,
        "union_basis": "derived-current-cache-only; not an official predicate evaluation",
        "historical_samples_latched": False,
        "official_goal_truth": "unavailable",
        "semantic_pixels_examined": MAX_PIXELS - remaining,
    }


class PerceptionDiagnostics:
    """Sample cached acquisition evidence with bounded memory, disk and capture work."""

    def __init__(self, directory: Path, *, enabled: bool) -> None:
        """Allocate scalar accounting and a bounded writer without opening a file."""
        self.enabled = enabled
        self._writer = BufferedJsonlWriter(directory / "perception-diagnostics.jsonl")
        self._records = 0
        self._dropped = 0
        self._seconds = 0.0
        self._last: dict[str, int] = {}

    def record(
        self,
        invocation: CanonicalObservationInvocation,
        environment: Any,
        observations: Any,
        sensors: Mapping[int, str],
        step: int,
        semantic_digest: str,
        *,
        force: bool = False,
    ) -> None:
        """Read existing data only on a bounded cadence; all diagnostic faults stay separate."""
        if not self.enabled:
            return
        started = time.perf_counter()
        try:
            key = invocation.request_key()
            previous = self._last.get(key)
            if previous == step or (
                not force and previous is not None and step - previous < SAMPLE_PERIOD
            ):
                return
            if self._records + self._dropped >= MAX_RECORDS or (
                len(self._last) >= 32 and key not in self._last
            ):
                self._dropped += 1
                return
            self._last[key] = step
            document = cached_detection_snapshot(
                invocation, environment, observations, sensors, step, semantic_digest
            )
            self._writer.append(document)
            self._records += 1
        except Exception:  # noqa: BLE001 - diagnostics never change physical or local outcomes
            self._dropped += 1
            _LOG.exception("cached perception diagnostic unavailable")
        finally:
            self._seconds += time.perf_counter() - started

    def close(self) -> dict[str, Any]:
        """Flush failures remain explicit loss evidence, never execution failures."""
        self._writer.flush()
        return {
            "schema_version": PERCEPTION_DIAGNOSTICS_SCHEMA,
            "enabled": self.enabled,
            "sampling_period_simulator_steps": SAMPLE_PERIOD,
            "max_records": MAX_RECORDS,
            "max_pixels_per_record": MAX_PIXELS,
            "records_accepted": self._records,
            "records_dropped": self._dropped,
            "capture_seconds": self._seconds,
            "writer": self._writer.stats(),
        }
