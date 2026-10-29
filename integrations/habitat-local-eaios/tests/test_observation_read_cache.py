"""Prove render reuse stays inside an unchanged noise-free read and never caches truth."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from habitat_local_eaios import observation_read_cache as cache  # noqa: E402


class Image:
    """Own a small mutable pixel buffer with the installed array copy interface."""

    def __init__(self, value: int = 7, nbytes: int = 4) -> None:
        """Keep cache budget independent from test allocation size."""
        self.values = [value]
        self.nbytes = nbytes

    def copy(self) -> Image:
        """Consumers never borrow the stored cache buffer."""
        return Image(self.values[0], self.nbytes)


class NoNoise:
    """Represent an exact admitted deterministic original noise class."""


class Sim:
    """Expose only read-only world and camera state plus counted original renders."""

    def __init__(self) -> None:
        """Create one body, articulation and sensor with independently mutable witnesses."""
        identity = [[1.0 if a == b else 0.0 for a in range(4)] for b in range(4)]
        self.rigid = SimpleNamespace(object_id=1, transformation=[list(x) for x in identity])
        self.arm = SimpleNamespace(object_id=2, transformation=identity, joint_positions=[0.0])
        self.camera = [list(x) for x in identity]
        self.node = SimpleNamespace(absolute_transformation=lambda: self.camera)
        self.sensor = SimpleNamespace(
            _noise_model=NoNoise(),
            _spec=SimpleNamespace(
                gpu2gpu_transfer=False,
                sensor_type="RGB",
                sensor_subtype="PINHOLE",
                resolution=[1, 1],
                hfov=90.0,
                near=0.1,
                far=100.0,
            ),
            _sensor_object=SimpleNamespace(node=self.node),
        )
        self._sensors = {"camera": self.sensor}
        self.clock, self.renders = 0.0, 0
        self.error: Exception | None = None
        self.pixel, self.nbytes = 7, 4

    def get_world_time(self) -> float:
        """Read an original clock without stepping it."""
        return self.clock

    def get_rigid_object_manager(self) -> Any:
        """Project actual current rigid objects, not a cached deployment inventory."""
        return SimpleNamespace(get_objects_by_handle_substring=lambda _: {"body": self.rigid})

    def get_articulated_object_manager(self) -> Any:
        """Include articulated joint values in unchanged-world evidence."""
        return SimpleNamespace(get_objects_by_handle_substring=lambda _: {"arm": self.arm})

    def get_sensor_observations(self, *args: Any, **kwargs: Any) -> dict[str, Image]:
        """Keep original call multiplicity and errors visible in uncached cases."""
        del args, kwargs
        self.renders += 1
        if self.error:
            raise self.error
        return {"camera": Image(self.pixel, self.nbytes)}


class Predicates:
    """Execute each original predicate while requesting repeated images."""

    def __init__(self, sim: Sim) -> None:
        """Keep optional state mutation separate from the cache implementation."""
        self.sim = sim
        self.predicates = 0
        self.after_first: Any = lambda: None
        self.mutate_pixels = False

    def get_observation(self) -> list[int]:
        """Recompute every truth from its actual image, with no truth-value shortcut."""
        result = []
        for index in range(4):
            obs = self.sim.get_sensor_observations()
            self.predicates += 1
            result.append(obs["camera"].values[0])
            if self.mutate_pixels:
                obs["camera"].values[0] = 99
            if index == 0:
                self.after_first()
        return result


@pytest.fixture(autouse=True)
def interfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace only numerical/noise types, retaining production cache and geometry checks."""
    monkeypatch.setattr(cache, "_array_type", lambda: Image)
    monkeypatch.setattr(cache, "_noise_type", lambda: NoNoise)


def test_same_read_owns_pixels_and_recomputes_every_predicate() -> None:
    """Four predicates may share one unchanged render, never one cached predicate result."""
    sim = Sim()
    sensor = Predicates(sim)
    sensor.mutate_pixels = True
    binding = cache.ObservationReadCache(sim, sensor)
    binding.install()
    try:
        assert sensor.get_observation() == [7] * 4
        assert sim.renders == 1 and sensor.predicates == 4
        assert sensor.get_observation() == [7] * 4
        assert sim.renders == 2 and sensor.predicates == 8
        sim.get_sensor_observations()
        assert sim.renders == 3
    finally:
        binding.close()
        binding.close()
    assert "get_observation" not in vars(sensor)
    assert "get_sensor_observations" not in vars(sim)


@pytest.mark.parametrize("change", ["body", "joint", "camera", "clock", "projection", "noise"])
def test_changed_world_never_reuses_a_prior_frame(change: str) -> None:
    """Even within one read, physical/camera/noise changes force the original reader."""
    sim, sensor = Sim(), None
    sensor = Predicates(sim)

    def mutate() -> None:
        """Change the witnessed state and the next original rendered pixel together."""
        sim.pixel = 11
        if change == "body":
            sim.rigid.transformation[0][3] = 1.0
        elif change == "joint":
            sim.arm.joint_positions[0] = 1.0
        elif change == "camera":
            sim.camera[0][3] = 1.0
        elif change == "clock":
            sim.clock += 1.0
        elif change == "projection":
            sim.sensor._spec.hfov = 60.0
        else:
            sim.sensor._noise_model = object()

    sensor.after_first = mutate
    binding = cache.ObservationReadCache(sim, sensor)
    binding.install()
    try:
        assert sensor.get_observation() == [7, 11, 11, 11]
        assert sim.renders == (4 if change == "noise" else 2)
    finally:
        binding.close()


@pytest.mark.parametrize("unsupported", ["noise", "device", "budget", "unknown-world"])
def test_unsupported_observations_keep_all_original_reads(unsupported: str) -> None:
    """Optimization admission cannot change sensing, RNG or outcomes on unknown interfaces."""
    sim = Sim()
    sensor = Predicates(sim)
    if unsupported == "noise":
        sim.sensor._noise_model = object()
    elif unsupported == "device":
        sim.sensor._spec.gpu2gpu_transfer = True
    elif unsupported == "budget":
        sim.nbytes = cache.MAX_BYTES + 1
    else:
        sim.rigid.transformation = []
    binding = cache.ObservationReadCache(sim, sensor)
    binding.install()
    try:
        assert sensor.get_observation() == [7] * 4
        assert sim.renders == 4
        assert binding.stats["cache_hits"] == 0
    finally:
        binding.close()


def test_original_error_and_both_instance_methods_survive() -> None:
    """Failed original reads cannot leave an override active across steps or replace the error."""
    sim = Sim()
    sensor = Predicates(sim)
    error = RuntimeError("original renderer failed")
    sim.error = error
    binding = cache.ObservationReadCache(sim, sensor)
    binding.install()
    with pytest.raises(RuntimeError) as raised:
        sensor.get_observation()
    assert raised.value is error and "get_sensor_observations" not in vars(sim)
    sim.error = None
    assert sensor.get_observation() == [7] * 4
    binding.close()


def test_default_off_does_not_read_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ordinary and frozen deployments acquire no new behavior from merely importing the module."""
    monkeypatch.delenv(cache.FLAG, raising=False)
    assert cache.install_observation_read_cache(object()) is None


def test_detach_preserves_a_later_observer() -> None:
    """Closing this cache cannot delete a different owner's later instrumentation."""
    sim = Sim()
    sensor = Predicates(sim)
    binding = cache.ObservationReadCache(sim, sensor)
    binding.install()

    def newer() -> list[int]:
        """Represent another instrumentation owner's later callback."""
        return [99]

    sensor.get_observation = newer  # type: ignore[method-assign]
    binding.close()
    assert sensor.get_observation is newer


def test_environment_close_writes_counters_and_retains_original_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing original close cannot leak the sensor hook or replace the original error."""
    sim = Sim()
    sensor = Predicates(sim)
    binding = cache.ObservationReadCache(sim, sensor)
    binding.install()
    error = RuntimeError("original close failed")

    def close() -> None:
        """Represent the real environment owner's original failure."""
        raise error

    environment = SimpleNamespace(close=close)
    monkeypatch.setenv(cache.FLAG, "1")
    monkeypatch.setattr(cache, "install_observation_read_cache", lambda env: binding)
    cache.configure_observation_read_cache(environment, tmp_path)
    assert sensor.get_observation() == [7] * 4
    with pytest.raises(RuntimeError) as raised:
        environment.close()
    assert raised.value is error and environment.close is close
    assert "get_observation" not in vars(sensor)
    import json

    observed = json.loads((tmp_path / "observation-read-cache.json").read_text())
    assert observed["closed"] is True and observed["stats"]["cache_hits"] == 3
