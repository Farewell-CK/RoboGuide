"""Cached pixel/camera evidence stays read-only, scoped, bounded and explicitly uncertain."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from habitat_local_eaios import perception_diagnostics  # noqa: E402
from habitat_local_eaios.perception_diagnostics import (  # noqa: E402
    PerceptionDiagnostics,
    cached_detection_snapshot,
)
from test_perception import environment, invocation  # noqa: E402


class Frame:
    """Expose the integer-frame equality/reduction interface without NumPy in root tests."""

    def __init__(self, pixels: list[int]) -> None:
        """Retain a tiny immutable frame and count the number of existing-data reads."""
        self.pixels = tuple(pixels)
        self.shape = (1, len(pixels))
        self.dtype = SimpleNamespace(kind="i")
        self.reads = 0

    def __eq__(self, other: object) -> Any:
        """Mirror vectorized integer comparison without exposing a sensor method."""
        self.reads += 1
        return SimpleNamespace(sum=lambda: sum(value == other for value in self.pixels))


def world() -> Any:
    """Provide original mapping plus two independently scoped, already-created detectors."""
    result = environment()
    detector = type("DetectedObjectsSensor", (), {})
    sensors = {}
    for agent in (0, 1):
        sensor = detector()
        sensor.agent_id, sensor.pixel_threshold = agent, 2
        sensors[f"agent_{agent}_detected_objects"] = sensor
    result.task.sensor_suite.sensors = sensors
    result.task.pddl_problem.sim_info.get_entity_pos = lambda entity: (1.0, 2.0, 3.0)
    return result


def snapshot(observations: Any, env: Any = None) -> dict[str, Any]:
    """Read one exact original Gym result for a two-agent world, never invoking sensors."""
    return cached_detection_snapshot(
        invocation(),
        world() if env is None else env,
        observations,
        {0: "agent_0_detected_objects", 1: "agent_1_detected_objects"},
        12,
        "sha256:world",
    )


@pytest.mark.parametrize("pixels,detected", [(2, False), (3, True)])
def test_pixel_threshold_and_world_union_reproduce_current_sensor_semantics(
    pixels: int, detected: bool
) -> None:
    """Strict > threshold and current cross-agent union never become own-agent detection."""
    observations = {
        "agent_0_detected_objects": [],
        "agent_1_detected_objects": [107] if detected else [],
        "agent_0_head_semantic": Frame([0, 0, 0]),
        "agent_1_head_semantic": Frame([107] * pixels + [0]),
    }
    result = snapshot(observations)
    assert result["detectors"]["0"]["condition_holds"] is False
    peer = result["detectors"]["1"]
    assert peer["condition_holds"] is peer["detection_from_current_frames"] is detected
    assert peer["cache_matches_current_frames"] is True
    assert result["cached_world_union"] is detected
    assert result["official_goal_truth"] == "unavailable"
    assert "raw_image" not in json.dumps(result)


def test_mismatched_cache_is_recorded_without_fabricating_an_outcome() -> None:
    """A detector/image disagreement remains visible instead of silently substituting truth."""
    result = snapshot(
        {
            "agent_0_detected_objects": [],
            "agent_1_detected_objects": [],
            "agent_0_head_semantic": Frame([107] * 3),
            "agent_1_head_semantic": Frame([0] * 3),
        }
    )
    own = result["detectors"]["0"]
    assert own["condition_holds"] is False and own["detection_from_current_frames"] is True
    assert own["cache_matches_current_frames"] is False
    assert result["cached_world_union"] is False


def test_missing_peer_cache_is_unknown_and_previous_true_is_not_latched() -> None:
    """One earlier positive does not fill a current evidence gap or establish joint success."""
    positive = snapshot({"agent_1_detected_objects": [107]})
    assert positive["cached_world_union"] is True
    current = snapshot({"agent_0_detected_objects": []})
    assert current["cached_world_union"] is None
    assert current["historical_samples_latched"] is False
    assert current["detectors"]["1"]["condition_holds"] is None


def test_mapping_uses_real_object_ids_for_pixels_as_well_as_detection_cache() -> None:
    """An entity index is never compared directly with actual semantic frame IDs."""
    env = world()
    env.sim.scene_obj_ids[7] = 42
    result = snapshot(
        {
            "agent_0_detected_objects": [142],
            "agent_0_head_semantic": Frame([142, 142, 142, 107]),
        },
        env,
    )
    own = result["detectors"]["0"]
    assert own["simulator_semantic_object_id"] == 142
    assert own["cache_matches_current_frames"] is True
    assert own["semantic_frames"]["agent_0_head_semantic"]["target_pixels"] == 3


def test_camera_geometry_is_not_reported_as_visibility_or_official_success() -> None:
    """A target inside the frustum still requires actual semantic pixels; occlusion is unknown."""
    env = world()
    matrix = SimpleNamespace(transform_point=lambda target: (0.0, 0.0, -1.0))
    env.sim._sensors["agent_0_head_semantic"] = SimpleNamespace(
        _sensor_object=SimpleNamespace(
            hfov=90, render_camera=SimpleNamespace(viewport=(4, 4), camera_matrix=matrix)
        )
    )
    result = snapshot(
        {"agent_0_detected_objects": [], "agent_0_head_semantic": Frame([0, 0, 0])}, env
    )
    row = result["detectors"]["0"]["semantic_frames"]["agent_0_head_semantic"]
    assert row["geometry"]["target_center_inside_frustum"] is True
    assert row["geometry"]["occlusion"] == "unavailable"
    assert row["detected"] is False


def test_capture_cadence_disabled_path_and_pixel_budget_are_bounded(tmp_path: Path) -> None:
    """Disabled diagnostics do no work; repeated steps and oversized images cannot grow work."""
    frame = Frame([0, 0, 0])
    observations = {"agent_0_detected_objects": [], "agent_0_head_semantic": frame}
    recorder = PerceptionDiagnostics(tmp_path / "disabled", enabled=False)
    recorder.record(
        invocation(), world(), observations, {0: "agent_0_detected_objects"}, 0, "world"
    )
    assert frame.reads == 0 and not (tmp_path / "disabled").exists()
    recorder = PerceptionDiagnostics(tmp_path, enabled=True)
    for step in range(33):
        recorder.record(
            invocation(), world(), observations, {0: "agent_0_detected_objects"}, step, "world"
        )
    summary = recorder.close()
    assert summary["records_accepted"] == 3 and frame.reads == 3
    frame.shape = (4096, 4096)
    result = snapshot(observations)
    assert result["detectors"]["0"]["frame_comparison"]["status"] == "unavailable"
    assert frame.reads == 3


def test_write_and_read_failures_cannot_escape_into_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failed diagnostic sources and flushes retain explicit loss and preserve callers."""
    recorder = PerceptionDiagnostics(tmp_path, enabled=True)

    def fail(*args: Any, **kwargs: Any) -> Any:
        """Represent an optional diagnostic failure without any physical operation."""
        raise OSError("diagnostic unavailable")

    with monkeypatch.context() as patched:
        patched.setattr(perception_diagnostics, "cached_detection_snapshot", fail)
        recorder.record(invocation(), world(), {}, {0: "agent_0_detected_objects"}, 0, "world")
    recorder.record(invocation(), world(), {}, {0: "agent_0_detected_objects"}, 16, "world")
    monkeypatch.setattr(recorder._writer, "_serializer", fail)
    stats = recorder.close()
    assert stats["records_dropped"] == 1
    assert stats["writer"]["records_dropped"] == stats["writer"]["write_failures"] == 1
