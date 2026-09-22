"""Best-effort operator-view evidence for one shared Habitat episode.

The recorder consumes observations that the execution loop already received.
It never steps the simulator, calls the policy, reads mutable simulator state,
or participates in terminal classification.  Capture is disabled unless a
deployment-owned output path is supplied.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

VIDEO_AUDIT_SCHEMA = "roboguide.e1.rgb-video-capture/v0.2"


class _FrameWriter(Protocol):
    """Minimal streaming-writer surface used by the capture boundary."""

    def append_data(self, frame: Any) -> None:
        """Append one RGB frame to the output stream."""

    def close(self) -> None:
        """Finalize the output stream."""


FrameRenderer = Callable[[Any, dict[str, Any], Any, int, str], Any]
WriterFactory = Callable[[Path, int], _FrameWriter]
PreviewEncoder = Callable[[Any], bytes]


@dataclass(frozen=True)
class AgentViewSpec:
    """One configured Habitat agent and its human-readable embodiment label."""

    agent_name: str
    robot_type: str


def _visual_observation(observations: Any, keys: tuple[str, ...]) -> tuple[str, Any] | None:
    """Select the first available exact visual key from a preference list."""
    if not isinstance(observations, dict):
        return None
    for key in keys:
        value = observations.get(key)
        shape = list(getattr(value, "shape", ()))
        if len(shape) == 3 and int(shape[2]) in {3, 4}:
            return key, value
    return None


def _agent_view_specs(observations: Any, habitat_config: Any) -> tuple[AgentViewSpec, ...]:
    """Discover ordered agents from config, falling back to exact head-RGB keys."""
    specs: list[AgentViewSpec] = []
    try:
        simulator = habitat_config.habitat.simulator
        for agent_name in simulator.agents_order:
            agent_config = simulator.agents[agent_name]
            specs.append(
                AgentViewSpec(
                    agent_name=str(agent_name),
                    robot_type=str(agent_config.articulated_agent_type),
                )
            )
    except (AttributeError, KeyError, TypeError):
        specs = []
    if specs or not isinstance(observations, dict):
        return tuple(specs)
    suffix = "_head_rgb"
    for key in observations:
        name = str(key)
        if name.endswith(suffix):
            agent_name = name[: -len(suffix)]
            specs.append(AgentViewSpec(agent_name=agent_name, robot_type=agent_name))
    return tuple(sorted(specs, key=lambda spec: spec.agent_name))


def _operator_grid_shape(agent_count: int) -> tuple[int, int]:
    """Choose a compact agent-card grid nearest a 16:9 operator display."""
    if agent_count < 1:
        return 1, 1
    card_width, card_height, status_height = 640, 270, 72
    target_ratio = 16 / 9
    candidates: list[tuple[float, int, int]] = []
    for columns in range(1, agent_count + 1):
        rows = math.ceil(agent_count / columns)
        ratio = (columns * card_width) / (rows * card_height + status_height)
        unused = rows * columns - agent_count
        score = abs(ratio - target_ratio) + unused * 0.02
        candidates.append((score, columns, rows))
    _, columns, rows = min(candidates)
    return columns, rows


def _rgb_array(value: Any) -> Any:
    """Convert one already-returned RGB observation to uint8 without new simulator reads."""
    import numpy as np  # type: ignore[import-not-found]

    array = value if isinstance(value, np.ndarray) else value.cpu().numpy()
    array = np.asarray(array)
    if array.dtype != np.uint8:
        array = np.clip(array * 255.0, 0, 255).astype(np.uint8)
    if array.shape[2] == 4:
        array = array[:, :, :3]
    return array


def _operator_tile(
    selected: tuple[str, Any] | None,
    label: str,
    width: int,
    height: int,
) -> Any:
    """Render one labelled RGB sensor or an explicit unavailable tile."""
    import cv2  # type: ignore[import-not-found]
    import numpy as np

    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    if selected is None:
        cv2.putText(
            canvas,
            f"{label}: unavailable",
            (16, height // 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        return canvas
    sensor_key, value = selected
    image = _rgb_array(value)
    source_height, source_width = image.shape[:2]
    scale = min(width / source_width, height / source_height)
    resized_width = max(1, round(source_width * scale))
    resized_height = max(1, round(source_height * scale))
    resized = cv2.resize(image, (resized_width, resized_height), interpolation=cv2.INTER_AREA)
    left = (width - resized_width) // 2
    top = (height - resized_height) // 2
    canvas[top : top + resized_height, left : left + resized_width] = resized
    caption = f"{label} [{sensor_key}]"
    cv2.rectangle(canvas, (0, 0), (width, 34), (0, 0, 0), -1)
    cv2.putText(
        canvas,
        caption,
        (10, 24),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.42,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    return canvas


def _default_renderer(
    observations: Any,
    info: dict[str, Any],
    habitat_config: Any,
    step: int,
    episode_id: str,
) -> Any:
    """Render labelled first- and third-person RGB views for a human operator."""
    import cv2
    import numpy as np

    tile_width, tile_height = 320, 270
    card_width = tile_width * 2
    specs = _agent_view_specs(observations, habitat_config)
    columns, rows = _operator_grid_shape(len(specs))
    cards = []
    for spec in specs:
        identity = f"{spec.agent_name} / {spec.robot_type}"
        third = _operator_tile(
            _visual_observation(observations, (f"{spec.agent_name}_third_rgb",)),
            f"{identity} third person",
            tile_width,
            tile_height,
        )
        first = _operator_tile(
            _visual_observation(
                observations,
                (
                    f"{spec.agent_name}_head_rgb",
                    f"{spec.agent_name}_rgb",
                    f"{spec.agent_name}_articulated_agent_arm_rgb",
                    f"{spec.agent_name}_articulated_agent_jaw_rgb",
                ),
            ),
            f"{identity} first/onboard",
            tile_width,
            tile_height,
        )
        cards.append(np.concatenate((third, first), axis=1))
    empty_card = np.zeros((tile_height, card_width, 3), dtype=np.uint8)
    if not cards:
        cv2.putText(
            empty_card,
            "No configured agent RGB views are available",
            (24, tile_height // 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        cards.append(empty_card)
    while len(cards) < columns * rows:
        unused = empty_card.copy()
        cv2.putText(
            unused,
            "Unused agent grid cell",
            (24, tile_height // 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (140, 140, 140),
            1,
            cv2.LINE_AA,
        )
        cards.append(unused)
    view_rows = []
    for start in range(0, len(cards), columns):
        view_rows.append(np.concatenate(cards[start : start + columns], axis=1))
    views = np.concatenate(view_rows, axis=0)
    status = np.zeros((72, views.shape[1], 3), dtype=np.uint8)
    lines = (
        f"RoboGuide / EMOS Stage2 / Habitat | Episode {episode_id} | Step {step}",
        f"Official pddl_success: {info.get('pddl_success', 'unavailable')} | "
        "Existing observations only",
    )
    for index, line in enumerate(lines):
        cv2.putText(
            status,
            line,
            (20, 28 + index * 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
    return np.concatenate((views, status), axis=0)


def _default_preview_encoder(frame: Any) -> bytes:
    """Encode one operator frame as bounded-quality JPEG bytes for live viewing."""
    import cv2

    # OpenCV expects BGR while the Habitat observations and MP4 writer use RGB.
    encoded, payload = cv2.imencode(
        ".jpg",
        cv2.cvtColor(frame, cv2.COLOR_RGB2BGR),
        [cv2.IMWRITE_JPEG_QUALITY, 82],
    )
    if not encoded:
        raise RuntimeError("OpenCV could not encode the live preview frame")
    return bytes(payload)


def _default_writer(path: Path, fps: int) -> _FrameWriter:
    """Open an imageio/FFmpeg writer without importing it in non-Habitat processes."""
    import imageio.v2 as imageio  # type: ignore[import-not-found]

    return cast(
        _FrameWriter,
        imageio.get_writer(
            str(path),
            fps=fps,
            quality=7,
            codec="libx264",
            pixelformat="yuv420p",
            macro_block_size=None,
        ),
    )


def _sha256(path: Path) -> str:
    """Hash one completed video without loading it into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class HabitatVideoCapture:
    """Stream bounded-memory RGB evidence while isolating all capture failures."""

    def __init__(
        self,
        output_path: Path | None,
        fps: int,
        *,
        preview_path: Path | None = None,
        preview_period_steps: int = 5,
        renderer: FrameRenderer | None = None,
        writer_factory: WriterFactory | None = None,
        preview_encoder: PreviewEncoder | None = None,
    ) -> None:
        """Bind immutable capture choices without touching Habitat or the filesystem."""
        if fps <= 0:
            raise ValueError("video fps must be positive")
        if preview_period_steps <= 0:
            raise ValueError("live preview period must be positive")
        self._output_path = output_path
        self._partial_path = (
            output_path.with_name(f"{output_path.stem}.partial{output_path.suffix}")
            if output_path is not None
            else None
        )
        self._audit_path = (
            output_path.parent / "video-capture-audit.json" if output_path is not None else None
        )
        self._fps = fps
        self._preview_path = preview_path
        self._preview_status_path = (
            preview_path.with_name("latest-frame.json") if preview_path is not None else None
        )
        self._preview_period_steps = preview_period_steps
        self._renderer = renderer or _default_renderer
        self._writer_factory = writer_factory or _default_writer
        self._preview_encoder = preview_encoder or _default_preview_encoder
        self._writer: _FrameWriter | None = None
        self._closed = False
        self._failed = False
        self._render_failed = False
        self._video_failed = False
        self._frames_seen = 0
        self._frames_written = 0
        self._frames_dropped = 0
        self._write_failures = 0
        self._first_step: int | None = None
        self._last_step: int | None = None
        self._capture_seconds = 0.0
        self._capture_max_seconds = 0.0
        self._error: str | None = None
        self._preview_frames_written = 0
        self._preview_write_failures = 0
        self._preview_error: str | None = None

    @property
    def enabled(self) -> bool:
        """Report whether the deployment explicitly requested RGB capture."""
        return self._output_path is not None or self._preview_path is not None

    def record(
        self,
        step: int,
        observations: Any,
        info: dict[str, Any],
        habitat_config: Any,
        episode_id: str,
    ) -> None:
        """Append one existing observation frame without affecting execution failures."""
        if not self.enabled or self._closed:
            return
        self._frames_seen += 1
        if self._render_failed:
            self._frames_dropped += 1
            return
        started = time.perf_counter()
        try:
            frame = self._renderer(observations, info, habitat_config, step, episode_id)
        except Exception as error:  # noqa: BLE001 - capture must not change execution
            self._failed = True
            self._render_failed = True
            self._frames_dropped += 1
            self._write_failures += 1
            self._error = f"{type(error).__name__}: {error}"
            return
        try:
            if self._output_path is not None and not self._video_failed:
                if self._writer is None:
                    assert self._partial_path is not None
                    self._partial_path.parent.mkdir(parents=True, exist_ok=True)
                    self._writer = self._writer_factory(self._partial_path, self._fps)
                self._writer.append_data(frame)
                self._frames_written += 1
                self._first_step = step if self._first_step is None else self._first_step
                self._last_step = step
        except Exception as error:  # noqa: BLE001 - video never affects execution
            self._failed = True
            self._video_failed = True
            self._frames_dropped += 1
            self._write_failures += 1
            self._error = f"{type(error).__name__}: {error}"
        finally:
            if self._preview_path is not None and step % self._preview_period_steps == 0:
                self._write_preview(frame, step, episode_id)
            elapsed = time.perf_counter() - started
            self._capture_seconds += elapsed
            self._capture_max_seconds = max(self._capture_max_seconds, elapsed)

    def close(self, termination_reason: str) -> None:
        """Finalize any stream and persist an explicit completeness audit."""
        if not self.enabled or self._closed:
            return
        self._closed = True
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception as error:  # noqa: BLE001 - capture must not mask SUT outcome
                self._failed = True
                self._video_failed = True
                self._write_failures += 1
                self._error = f"{type(error).__name__}: {error}"
        complete = False
        digest: str | None = None
        if not self._failed and self._frames_written > 0:
            try:
                assert self._partial_path is not None and self._output_path is not None
                self._partial_path.replace(self._output_path)
                digest = _sha256(self._output_path)
                complete = True
            except Exception as error:  # noqa: BLE001 - capture must not mask SUT outcome
                self._failed = True
                self._write_failures += 1
                self._error = f"{type(error).__name__}: {error}"
        self._write_audit(complete, digest, termination_reason)

    def _write_preview(self, frame: Any, step: int, episode_id: str) -> None:
        """Atomically replace one bounded live JPEG and its factual status sidecar."""
        assert self._preview_path is not None and self._preview_status_path is not None
        try:
            self._preview_path.parent.mkdir(parents=True, exist_ok=True)
            partial_image = self._preview_path.with_suffix(".partial.jpg")
            partial_image.write_bytes(self._preview_encoder(frame))
            partial_image.replace(self._preview_path)
            status = {
                "episode_id": episode_id,
                "schema_version": "roboguide.e1.live-operator-frame/v0.1",
                "simulator_step": step,
                "updated_unix": time.time(),
            }
            partial_status = self._preview_status_path.with_suffix(".partial.json")
            partial_status.write_text(
                json.dumps(status, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            partial_status.replace(self._preview_status_path)
            self._preview_frames_written += 1
        except Exception as error:  # noqa: BLE001 - preview never affects execution
            self._preview_write_failures += 1
            self._preview_error = f"{type(error).__name__}: {error}"

    def _write_audit(
        self,
        complete: bool,
        digest: str | None,
        termination_reason: str,
    ) -> None:
        """Best-effort persist capture accounting beside the requested video."""
        if self._audit_path is None:
            return
        document = {
            "capture_max_seconds": self._capture_max_seconds,
            "capture_seconds": self._capture_seconds,
            "complete": complete,
            "enabled": True,
            "error": self._error,
            "first_simulator_step": self._first_step,
            "fps": self._fps,
            "frames_dropped": self._frames_dropped,
            "frames_seen": self._frames_seen,
            "frames_written": self._frames_written,
            "last_simulator_step": self._last_step,
            "output_path": str(self._output_path),
            "live_preview_enabled": self._preview_path is not None,
            "live_preview_path": str(self._preview_path) if self._preview_path else None,
            "live_preview_period_steps": self._preview_period_steps,
            "preview_error": self._preview_error,
            "preview_frames_written": self._preview_frames_written,
            "preview_write_failures": self._preview_write_failures,
            "schema_version": VIDEO_AUDIT_SCHEMA,
            "sha256": f"sha256:{digest}" if digest is not None else None,
            "termination_reason": termination_reason,
            "write_failures": self._write_failures,
        }
        try:
            self._audit_path.parent.mkdir(parents=True, exist_ok=True)
            self._audit_path.write_text(
                json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001 - audit failure cannot change execution
            return
