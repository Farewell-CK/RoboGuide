"""Deterministic tests for best-effort RGB episode evidence capture."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.video_capture import (  # noqa: E402
    HabitatVideoCapture,
    _agent_view_specs,
    _operator_grid_shape,
    _visual_observation,
)


class FakeWriter:
    """Write deterministic bytes while recording appended frames."""

    def __init__(self, path: Path, fail_append: bool = False) -> None:
        """Retain the output path and optional failure behavior."""
        self.path = path
        self.fail_append = fail_append
        self.frames: list[Any] = []

    def append_data(self, frame: Any) -> None:
        """Append a frame or raise the configured writer failure."""
        if self.fail_append:
            raise OSError("writer unavailable")
        self.frames.append(frame)
        self.path.write_bytes(b"frame\n" * len(self.frames))

    def close(self) -> None:
        """Accept stream finalization without changing the deterministic bytes."""


class FakeVisual:
    """Expose only shape metadata needed by deterministic sensor selection."""

    shape = (256, 256, 3)


def test_video_capture_is_default_off_and_does_not_read_observations(tmp_path: Path) -> None:
    """A missing output path performs no render, write, or audit work."""
    calls = 0

    def render(*args: Any) -> object:
        """Count any unexpected disabled capture access."""
        nonlocal calls
        calls += 1
        return object()

    capture = HabitatVideoCapture(None, 30, renderer=render)
    capture.record(0, object(), {}, object(), "51")
    capture.close("episode_done")
    assert calls == 0
    assert not any(tmp_path.iterdir())


def test_video_capture_streams_frames_and_publishes_complete_audit(tmp_path: Path) -> None:
    """Successful capture atomically publishes a video and exact accounting."""
    output = tmp_path / "episode.mp4"
    writers: list[FakeWriter] = []

    def writer_factory(path: Path, fps: int) -> FakeWriter:
        """Create one fake writer and assert the playback-only rate."""
        assert fps == 30
        writer = FakeWriter(path)
        writers.append(writer)
        return writer

    def render(
        observations: Any,
        info: dict[str, Any],
        habitat_config: Any,
        step: int,
        episode_id: str,
    ) -> dict[str, Any]:
        """Return an inspectable frame without touching external libraries."""
        return {"episode": episode_id, "step": step}

    capture = HabitatVideoCapture(
        output,
        30,
        renderer=render,
        writer_factory=writer_factory,
    )
    capture.record(0, object(), {}, object(), "51")
    capture.record(1, object(), {"pddl_success": False}, object(), "51")
    capture.close("episode_done")
    assert output.read_bytes() == b"frame\nframe\n"
    assert [frame["step"] for frame in writers[0].frames] == [0, 1]
    audit = json.loads((tmp_path / "video-capture-audit.json").read_text())
    assert audit["complete"] is True
    assert audit["frames_seen"] == 2
    assert audit["frames_written"] == 2
    assert audit["frames_dropped"] == 0
    assert audit["first_simulator_step"] == 0
    assert audit["last_simulator_step"] == 1
    assert audit["termination_reason"] == "episode_done"
    assert audit["sha256"].startswith("sha256:")


def test_operator_sensor_selection_uses_only_named_rgb_views() -> None:
    """First-person and third-person selection never substitutes arm or depth views."""
    observations = {
        "agent_0_head_depth": FakeVisual(),
        "agent_0_arm_rgb": FakeVisual(),
        "agent_0_head_rgb": FakeVisual(),
        "agent_0_third_rgb": FakeVisual(),
        "agent_1_head_rgb": FakeVisual(),
        "agent_1_third_rgb": FakeVisual(),
    }
    spot = _visual_observation(observations, ("agent_0_head_rgb",))
    fetch = _visual_observation(observations, ("agent_1_head_rgb",))
    spot_third = _visual_observation(observations, ("agent_0_third_rgb",))
    fetch_third = _visual_observation(observations, ("agent_1_third_rgb",))
    assert spot is not None and spot[0] == "agent_0_head_rgb"
    assert fetch is not None and fetch[0] == "agent_1_head_rgb"
    assert spot_third is not None and spot_third[0] == "agent_0_third_rgb"
    assert fetch_third is not None and fetch_third[0] == "agent_1_third_rgb"
    assert _visual_observation(observations, ("agent_1_missing_rgb",)) is None


def test_agent_view_specs_follow_configured_order_and_robot_types() -> None:
    """Operator labels and ordering come from deployment config rather than episode names."""
    simulator = SimpleNamespace(
        agents_order=["agent_alpha", "agent_beta", "agent_gamma"],
        agents={
            "agent_alpha": SimpleNamespace(articulated_agent_type="LeggedRobot"),
            "agent_beta": SimpleNamespace(articulated_agent_type="WheeledRobot"),
            "agent_gamma": SimpleNamespace(articulated_agent_type="DroneRobot"),
        },
    )
    config = SimpleNamespace(habitat=SimpleNamespace(simulator=simulator))
    specs = _agent_view_specs({}, config)
    assert [(spec.agent_name, spec.robot_type) for spec in specs] == [
        ("agent_alpha", "LeggedRobot"),
        ("agent_beta", "WheeledRobot"),
        ("agent_gamma", "DroneRobot"),
    ]


def test_operator_grid_generalizes_one_through_four_agents() -> None:
    """Agent cards use deterministic near-widescreen grids without cardinality templates."""
    assert _operator_grid_shape(1) == (1, 1)
    assert _operator_grid_shape(2) == (1, 2)
    assert _operator_grid_shape(3) == (2, 2)
    assert _operator_grid_shape(4) == (2, 2)
    columns, rows = _operator_grid_shape(9)
    assert columns * rows >= 9


def test_live_preview_is_bounded_and_atomically_replaces_latest_frame(tmp_path: Path) -> None:
    """Preview sampling retains one latest JPEG and exact step sidecar only."""
    output = tmp_path / "episode.mp4"
    preview = tmp_path / "live" / "latest-frame.jpg"

    def writer_factory(path: Path, fps: int) -> FakeWriter:
        """Create the deterministic video writer."""
        del fps
        return FakeWriter(path)

    capture = HabitatVideoCapture(
        output,
        30,
        preview_path=preview,
        preview_period_steps=2,
        renderer=lambda _observations, _info, _config, step, _episode: step,
        writer_factory=writer_factory,
        preview_encoder=lambda frame: f"jpeg-{frame}".encode(),
    )
    for step in range(4):
        capture.record(step, object(), {}, object(), "51")
    capture.close("episode_done")
    assert preview.read_bytes() == b"jpeg-2"
    status = json.loads((preview.parent / "latest-frame.json").read_text())
    assert status["simulator_step"] == 2
    assert list(preview.parent.glob("*.jpg")) == [preview]
    audit = json.loads((tmp_path / "video-capture-audit.json").read_text())
    assert audit["preview_frames_written"] == 2
    assert audit["preview_write_failures"] == 0


def test_preview_failure_does_not_change_complete_video(tmp_path: Path) -> None:
    """A live JPEG failure remains diagnostic and cannot invalidate MP4 evidence."""
    output = tmp_path / "episode.mp4"

    def fail_preview(_frame: Any) -> bytes:
        """Raise the configured isolated preview error."""
        raise OSError("preview unavailable")

    capture = HabitatVideoCapture(
        output,
        30,
        preview_path=tmp_path / "live" / "latest-frame.jpg",
        preview_period_steps=1,
        renderer=lambda *args: object(),
        writer_factory=lambda path, _fps: FakeWriter(path),
        preview_encoder=fail_preview,
    )
    capture.record(0, object(), {}, object(), "51")
    capture.close("episode_done")
    audit = json.loads((tmp_path / "video-capture-audit.json").read_text())
    assert audit["complete"] is True
    assert audit["frames_written"] == 1
    assert audit["preview_write_failures"] == 1
    assert "preview unavailable" in audit["preview_error"]


def test_render_failure_is_isolated_and_explicitly_incomplete(tmp_path: Path) -> None:
    """A visual conversion failure cannot escape or claim a complete video."""
    output = tmp_path / "episode.mp4"

    def broken_renderer(*args: Any) -> object:
        """Raise the visual-sensor failure under test."""
        raise RuntimeError("visual sensor unavailable")

    capture = HabitatVideoCapture(output, 30, renderer=broken_renderer)
    capture.record(0, object(), {}, object(), "51")
    capture.record(1, object(), {}, object(), "51")
    capture.close("execution_exception")
    audit = json.loads((tmp_path / "video-capture-audit.json").read_text())
    assert audit["complete"] is False
    assert audit["frames_seen"] == 2
    assert audit["frames_written"] == 0
    assert audit["frames_dropped"] == 2
    assert audit["write_failures"] == 1
    assert "visual sensor unavailable" in audit["error"]
    assert not output.exists()


def test_writer_failure_does_not_publish_partial_as_complete(tmp_path: Path) -> None:
    """An encoder failure leaves raw partial evidence and a fail-closed audit."""
    output = tmp_path / "episode.mp4"

    def writer_factory(path: Path, fps: int) -> FakeWriter:
        """Return the failing encoder double."""
        del fps
        return FakeWriter(path, fail_append=True)

    capture = HabitatVideoCapture(
        output,
        30,
        renderer=lambda *args: object(),
        writer_factory=writer_factory,
    )
    capture.record(0, object(), {}, object(), "51")
    capture.close("episode_done")
    audit = json.loads((tmp_path / "video-capture-audit.json").read_text())
    assert audit["complete"] is False
    assert audit["frames_written"] == 0
    assert audit["write_failures"] == 1
    assert not output.exists()
