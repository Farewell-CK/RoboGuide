"""Reuse unchanged noise-free rendered images inside one original predicate read.

This is a shared backend optimization, not a predicate or policy replacement.
Every predicate and sensor transformation still executes. Scope exit discards
all images; uncertain geometry, unsupported sources and errors retain fresh reads.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import math
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)
_MISSING = object()
FLAG = "ROBOGUIDE_HABITAT_OBSERVATION_READ_CACHE"
MAX_BYTES = 64 * 1024 * 1024
MAX_OBJECTS = 512
_SOURCES = {
    "habitat.tasks.rearrange.multi_task.pddl_sensors": (
        "b1bcbea220d5daa65a3c834bb713c4c70569f5a92e842c107be8b1d88b0085bd"
    ),
    "habitat.tasks.rearrange.multi_task.pddl_sim_state": (
        "9f4b73bb5dd6fb724132a8a3cea156d9fc1797b9a0fe84e6b1b678529b0ff7ec"
    ),
}


def cache_declaration() -> dict[str, Any]:
    """Declare bounded implementation reuse separately from sensing and official authority."""
    return {
        "profile": "same-read-scope-render-cache/v0.1",
        "scope": "one-original-GlobalPredicatesSensor.get_observation",
        "max_bytes": MAX_BYTES,
        "max_world_objects": MAX_OBJECTS,
        "noise_policy": "exact-original-NoSensorNoiseModel-only",
        "world_policy": "same-clock-object-joints-and-camera-transforms",
        "on_unknown_or_change": "fresh-original-read",
        "cached_value": "owned-raw-image-copies-never-predicate-truth",
        "scope_exit": "discard-all-images",
        "predicate_sources_sha256": dict(_SOURCES),
        "official_goals_thresholds_and_budgets_changed": False,
    }


def _array_type() -> type[Any]:
    """Import the installed array implementation only in the actual worker."""
    import numpy as np  # type: ignore[import-not-found]

    return np.ndarray  # type: ignore[no-any-return]


def _noise_type() -> type[Any]:
    """Admit the actual deterministic original class, never a matching class name."""
    from habitat_sim.sensors.noise_models.no_noise_model import (  # type: ignore[import-not-found]
        NoSensorNoiseModel,
    )

    return NoSensorNoiseModel  # type: ignore[no-any-return]


def _vector(value: Any, limit: int = 256) -> tuple[float, ...]:
    """Read finite bounded geometry without sampling, updating or stepping the world."""
    if isinstance(value, (str, bytes, Mapping)) or len(value) > limit:
        raise ValueError("unsupported geometry")
    result = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in result):
        raise ValueError("nonfinite geometry")
    return result


def _matrix(value: Any) -> tuple[tuple[float, ...], ...]:
    """Copy the existing exact Matrix4 columns; unavailable transforms disable reuse."""
    columns = tuple(_vector(column, 4) for column in value)
    if len(columns) != 4 or any(len(column) != 4 for column in columns):
        raise ValueError("unsupported transformation")
    return columns


def world_witness(sim: Any) -> tuple[Any, ...]:
    """Cover actual rendered bodies, articulated joints, clock and fixed camera interfaces."""
    clock = float(sim.get_world_time())
    if not math.isfinite(clock):
        raise ValueError("world clock unavailable")
    bodies: list[Any] = []
    for manager, articulated in [
        (sim.get_rigid_object_manager(), False),
        (sim.get_articulated_object_manager(), True),
    ]:
        objects = manager.get_objects_by_handle_substring("")
        if not isinstance(objects, Mapping) or len(objects) + len(bodies) > MAX_OBJECTS:
            raise ValueError("world inventory exceeds cache witness")
        for name, obj in sorted(objects.items()):
            bodies.append(
                (
                    name,
                    obj.object_id,
                    _matrix(obj.transformation),
                    _vector(obj.joint_positions) if articulated else None,
                )
            )
    sensors = sim._sensors
    if not isinstance(sensors, Mapping) or not 1 <= len(sensors) <= 64:
        raise ValueError("raw camera inventory unavailable")
    cameras = []
    for name, sensor in sorted(sensors.items()):
        noise = sensor._noise_model
        spec = sensor._spec
        if type(noise) is not _noise_type() or spec.gpu2gpu_transfer:
            raise ValueError("sensor noise or device buffers cannot be reused")
        cameras.append(
            (
                name,
                id(sensor),
                id(noise),
                _matrix(sensor._sensor_object.node.absolute_transformation()),
                str(spec.sensor_type),
                str(spec.sensor_subtype),
                _vector(spec.resolution, 2),
                float(spec.hfov),
                float(spec.near),
                float(spec.far),
            )
        )
    return id(sim), clock, tuple(bodies), tuple(cameras)


def _copy_images(value: Any) -> Any:
    """Own every raw buffer; consumers cannot mutate the retained sample for later readers."""
    if not isinstance(value, Mapping) or not 1 <= len(value) <= 64:
        raise ValueError("raw image dictionary is unavailable")
    array = _array_type()
    total = 0
    result: dict[str, Any] = {}
    for name, image in value.items():
        if not isinstance(name, str):
            raise ValueError("raw sensor identity unavailable")
        if type(image) is array:
            total += image.nbytes
            if total > MAX_BYTES:
                raise ValueError("raw image budget exceeded")
            result[name] = image.copy()
        elif type(image) in (bool, int, float):
            result[name] = image
        else:
            raise ValueError("unsupported raw observation buffer")
    return result


def _restore(instance: Any, name: str, previous: Any) -> None:
    """Remove only this instance's scoped override, including exceptional exits."""
    if previous is _MISSING:
        vars(instance).pop(name, None)
    else:
        setattr(instance, name, previous)


class ObservationReadCache:
    """Cache raw renders only during the unchanged original predicate sensor calculation."""

    def __init__(self, sim: Any, sensor: Any) -> None:
        """Retain no world state until the actual original read scope begins."""
        self.sim, self.sensor = sim, sensor
        self.stats = {"scopes": 0, "fresh_reads": 0, "cache_hits": 0, "fallback_reads": 0}
        self._active = False
        self._previous: Any = _MISSING
        self._installed = False
        self._wrapper: Any = None

    def install(self) -> None:
        """Wrap one existing callback without adding predicates, actions or simulation steps."""
        if self._installed:
            raise ValueError("observation cache already installed")
        original_scope = self.sensor.get_observation
        self._previous = vars(self.sensor).get("get_observation", _MISSING)

        def scoped(*args: Any, **kwargs: Any) -> Any:
            """Keep one private sample and always remove its original reader override."""
            if self._active:
                return original_scope(*args, **kwargs)
            self._active = True
            self.stats["scopes"] += 1
            reader = self.sim.get_sensor_observations
            previous = vars(self.sim).get("get_sensor_observations", _MISSING)
            sample: Any = None
            witness: Any = None

            def read(*read_args: Any, **read_kwargs: Any) -> Any:
                """Reuse only exact ordinary reads under a fresh unchanged world witness."""
                nonlocal sample, witness
                try:
                    current = world_witness(self.sim) if not read_args and not read_kwargs else None
                    if current is not None and sample is not None and current == witness:
                        result = _copy_images(sample)
                        self.stats["cache_hits"] += 1
                        return result
                except Exception:  # noqa: BLE001 - uncertain witnesses retain original reads
                    current = None
                result = reader(*read_args, **read_kwargs)
                self.stats["fresh_reads"] += 1
                sample, witness = None, None
                try:
                    if current is not None and world_witness(self.sim) == current:
                        sample, witness = _copy_images(result), current
                    else:
                        self.stats["fallback_reads"] += 1
                except Exception:  # noqa: BLE001 - original observations and errors stay original
                    self.stats["fallback_reads"] += 1
                return result

            self.sim.get_sensor_observations = read
            try:
                return original_scope(*args, **kwargs)
            finally:
                if vars(self.sim).get("get_sensor_observations") is read:
                    _restore(self.sim, "get_sensor_observations", previous)
                self._active = False

        self.sensor.get_observation = scoped
        self._wrapper = scoped
        self._installed = True

    def close(self) -> None:
        """Restore the original sensor instance; no sample or permission survives scope exit."""
        if self._installed:
            if vars(self.sensor).get("get_observation") is self._wrapper:
                _restore(self.sensor, "get_observation", self._previous)
            self._installed = False


def install_observation_read_cache(environment: Any) -> ObservationReadCache | None:
    """Enable only explicitly declared known original read scopes; unsupported inputs stay fresh."""
    if os.environ.get(FLAG) != "1":
        return None
    try:
        import importlib

        for name, expected in _SOURCES.items():
            module = importlib.import_module(name)
            if hashlib.sha256(Path(inspect.getfile(module)).read_bytes()).hexdigest() != expected:
                raise ValueError(
                    "predicate source differs from the admitted read-only implementation"
                )
        module = importlib.import_module("habitat.tasks.rearrange.multi_task.pddl_sensors")
        sensor = environment.task.sensor_suite.sensors["all_predicates"]
        if type(sensor) is not module.GlobalPredicatesSensor:
            raise ValueError("original predicate read scope unavailable")
        world_witness(environment.sim)
        cache = ObservationReadCache(environment.sim, sensor)
        cache.install()
        return cache
    except Exception:  # noqa: BLE001 - unsupported optimizations cannot break original execution
        _LOG.exception("observation read cache unavailable; original reads retained")
        return None


def configure_observation_read_cache(environment: Any, directory: Path) -> None:
    """Own opt-in reuse and evidence for one actual environment, including native workers."""
    if os.environ.get(FLAG) != "1" or getattr(
        environment, "_roboguide_read_cache_configured", False
    ):
        return
    environment._roboguide_read_cache_configured = True
    cache = install_observation_read_cache(environment)

    def record(closed: bool) -> None:
        """Keep performance evidence failures separate from original sensing and execution."""
        try:
            from .evidence_io import write_text_atomic

            value = {
                "schema_version": "roboguide.observation-read-cache/v0.1",
                "declaration": cache_declaration(),
                "status": "active" if cache is not None else "unavailable-original-reads",
                "closed": closed,
                "stats": dict(cache.stats) if cache is not None else None,
                "official_success_authority": "unchanged-original-habitat-pddl",
            }
            write_text_atomic(directory / "observation-read-cache.json", json.dumps(value))
        except Exception:  # noqa: BLE001 - archival must not alter original outcomes
            _LOG.exception("observation read cache evidence unavailable")

    record(False)
    if cache is None:
        return
    original = environment.close
    previous = vars(environment).get("close", _MISSING)

    def close(*args: Any, **kwargs: Any) -> Any:
        """Flush final counters and restore hooks without masking original close failures."""
        try:
            return original(*args, **kwargs)
        finally:
            try:
                cache.close()
            except Exception:  # noqa: BLE001 - preserve original physical/close authority
                _LOG.exception("observation read cache detach unavailable")
            record(True)
            if vars(environment).get("close") is close:
                _restore(environment, "close", previous)

    environment.close = close
