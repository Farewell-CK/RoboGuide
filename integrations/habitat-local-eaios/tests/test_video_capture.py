"""Deterministic tests for best-effort RGB episode evidence capture."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.video_capture import HabitatVideoCapture  # noqa: E402


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
