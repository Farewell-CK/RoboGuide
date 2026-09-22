"""Best-effort RGB evidence capture for one shared Habitat episode.

The recorder consumes observations that the execution loop already received.
It never steps the simulator, calls the policy, reads mutable simulator state,
or participates in terminal classification.  Capture is disabled unless a
deployment-owned output path is supplied.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, cast

VIDEO_AUDIT_SCHEMA = "roboguide.e1.rgb-video-capture/v0.1"


class _FrameWriter(Protocol):
    """Minimal streaming-writer surface used by the capture boundary."""

    def append_data(self, frame: Any) -> None:
        """Append one RGB frame to the output stream."""

    def close(self) -> None:
        """Finalize the output stream."""


FrameRenderer = Callable[[Any, dict[str, Any], Any, int, str], Any]
WriterFactory = Callable[[Path, int], _FrameWriter]


def _default_renderer(
    observations: Any,
    info: dict[str, Any],
    habitat_config: Any,
    step: int,
    episode_id: str,
) -> Any:
    """Render the already-returned visual observations as the EMOS evaluator does."""
    from habitat.utils.visualizations.utils import (  # type: ignore[import-not-found]
        observations_to_image,
        overlay_frame,
    )

    frame = observations_to_image(observations, info, habitat_config, step, episode_id)
    pddl_success = info.get("pddl_success", "unavailable")
    return overlay_frame(
        frame,
        {},
        additional=[
            "RoboGuide RGB evidence capture",
            f"episode: {episode_id}",
            f"simulator step: {step}",
            f"official pddl_success: {pddl_success}",
        ],
    )


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
        renderer: FrameRenderer | None = None,
        writer_factory: WriterFactory | None = None,
    ) -> None:
        """Bind immutable capture choices without touching Habitat or the filesystem."""
        if fps <= 0:
            raise ValueError("video fps must be positive")
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
        self._renderer = renderer or _default_renderer
        self._writer_factory = writer_factory or _default_writer
        self._writer: _FrameWriter | None = None
        self._closed = False
        self._failed = False
        self._frames_seen = 0
        self._frames_written = 0
        self._frames_dropped = 0
        self._write_failures = 0
        self._first_step: int | None = None
        self._last_step: int | None = None
        self._capture_seconds = 0.0
        self._capture_max_seconds = 0.0
        self._error: str | None = None

    @property
    def enabled(self) -> bool:
        """Report whether the deployment explicitly requested RGB capture."""
        return self._output_path is not None

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
        if self._failed:
            self._frames_dropped += 1
            return
        started = time.perf_counter()
        try:
            frame = self._renderer(observations, info, habitat_config, step, episode_id)
            if self._writer is None:
                assert self._partial_path is not None
                self._partial_path.parent.mkdir(parents=True, exist_ok=True)
                self._writer = self._writer_factory(self._partial_path, self._fps)
            self._writer.append_data(frame)
            self._frames_written += 1
            self._first_step = step if self._first_step is None else self._first_step
            self._last_step = step
        except Exception as error:  # noqa: BLE001 - capture must not change execution
            self._failed = True
            self._frames_dropped += 1
            self._write_failures += 1
            self._error = f"{type(error).__name__}: {error}"
        finally:
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
