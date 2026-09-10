"""Read-only dataset identity resolution against a pinned benchmark dataset.

The E1 paired comparison needs more than the resolved habitat episode id: it
needs the scene and a stable dataset record identity so that an EMOS run and
a RoboGuide run can be proven to cover the same benchmark episode. This
module provides that lookup without importing any Habitat/EMOS package: it
reads the pinned dataset ``episodes`` JSON (gzipped) directly, verifies the
file's SHA-256 digest against the digest pinned in the ExperimentSpec, and
resolves ``(dataset, episode_id) -> (scene_id, index, record identity)``.

The resolver never guesses: digest mismatch, missing ids, or duplicated ids
yield explicit non-resolved statuses that callers surface in the manifest.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

type DatasetResolutionStatus = str


@dataclass(frozen=True, slots=True)
class DatasetEpisodeIdentity:
    """Describe the resolution of one episode id inside a pinned dataset.

    ``status`` is ``resolved`` on a unique digest-verified match, and
    ``digest-mismatch`` / ``not-found`` / ``ambiguous`` / ``unreadable``
    otherwise; the payload fields stay ``None`` unless resolved.
    """

    status: DatasetResolutionStatus
    resolved_episode_id: str | None
    resolved_scene_id: str | None
    dataset_index: int | None
    dataset_record: str | None
    detail: str | None = None


def file_digest(path: Path) -> str:
    """Return the SHA-256 digest of one file's raw bytes.

    Args:
        path: The dataset file to digest.

    Returns:
        The lowercase hexadecimal SHA-256 digest.

    Raises:
        OSError: If the file cannot be read.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_episode_in_dataset(
    dataset_path: Path,
    expected_digest: str | None,
    episode_id: str,
) -> DatasetEpisodeIdentity:
    """Resolve one episode id against a pinned dataset file.

    Args:
        dataset_path: Path to the gzipped episodes JSON file.
        expected_digest: Digest pinned in the ExperimentSpec; the file must
            match it exactly, otherwise the lookup fails closed.
        episode_id: The resolved habitat episode id to look up.

    Returns:
        The :class:`DatasetEpisodeIdentity` describing the outcome; the
        payload fields are only populated for a unique digest-verified
        match.
    """
    if expected_digest is not None:
        try:
            actual_digest = file_digest(dataset_path)
        except OSError as error:
            return _failure("unreadable", episode_id, f"cannot read dataset: {error}")
        if actual_digest != expected_digest:
            return _failure(
                "digest-mismatch",
                episode_id,
                f"dataset digest {actual_digest} differs from the pinned {expected_digest}",
            )
    try:
        with gzip.open(dataset_path, "rt", encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, json.JSONDecodeError, EOFError) as error:
        return _failure("unreadable", episode_id, f"cannot parse dataset: {error}")
    episodes = document.get("episodes") if isinstance(document, dict) else None
    if not isinstance(episodes, list):
        return _failure("unreadable", episode_id, "dataset has no episodes array")
    matches = [
        (index, episode)
        for index, episode in enumerate(episodes)
        if isinstance(episode, dict) and str(episode.get("episode_id")) == episode_id
    ]
    if not matches:
        return _failure("not-found", episode_id, f"episode id {episode_id!r} is not in the dataset")
    if len(matches) > 1:
        return _failure(
            "ambiguous",
            episode_id,
            f"episode id {episode_id!r} occurs {len(matches)} times; "
            "an additional discriminator is required",
        )
    index, episode = matches[0]
    scene_id = episode.get("scene_id")
    if not isinstance(scene_id, str) or not scene_id:
        return _failure("unreadable", episode_id, "episode record has no scene_id")
    dataset_name = dataset_path.name
    return DatasetEpisodeIdentity(
        status="resolved",
        resolved_episode_id=episode_id,
        resolved_scene_id=scene_id,
        dataset_index=index,
        dataset_record=f"{dataset_name}#{episode_id}@{index}",
        detail=None,
    )


def _failure(
    status: DatasetResolutionStatus,
    episode_id: str,
    detail: str,
) -> DatasetEpisodeIdentity:
    """Build a non-resolved identity result with an explanatory detail.

    Args:
        status: The failure status label.
        episode_id: The episode id that could not be resolved.
        detail: Human-readable failure reason.

    Returns:
        The failure :class:`DatasetEpisodeIdentity`.
    """
    return DatasetEpisodeIdentity(
        status=status,
        resolved_episode_id=episode_id,
        resolved_scene_id=None,
        dataset_index=None,
        dataset_record=None,
        detail=detail,
    )
