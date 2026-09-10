"""Tests for the read-only pinned-dataset episode identity resolver."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import write_fixture_dataset
from roboguide_eval.dataset_identity import resolve_episode_in_dataset

EPISODES: list[dict[str, object]] = [
    {"episode_id": "0", "scene_id": "mp3d/2azQ1b91cZZ/2azQ1b91cZZ.glb"},
    {"episode_id": "1", "scene_id": "mp3d/5q7pvUzZiYa/5q7pvUzZiYa.glb"},
    {"episode_id": "2", "scene_id": "mp3d/759xd9YjKW5/759xd9YjKW5.glb"},
]


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    """Write the standard fixture dataset.

    Args:
        tmp_path: Per-test temporary directory.

    Returns:
        The dataset file path.
    """
    return tmp_path / "fixture_episodes.json.gz"


def test_resolves_unique_episode_with_scene_and_index(dataset: Path) -> None:
    """A digest-verified unique id resolves scene id, index, and record."""
    digest = write_fixture_dataset(dataset, EPISODES)
    identity = resolve_episode_in_dataset(dataset, digest, "2")
    assert identity.status == "resolved"
    assert identity.resolved_scene_id == "mp3d/759xd9YjKW5/759xd9YjKW5.glb"
    assert identity.dataset_index == 2
    assert identity.dataset_record == "fixture_episodes.json.gz#2@2"


def test_digest_mismatch_fails_closed(dataset: Path) -> None:
    """A file that no longer matches the pinned digest is never trusted."""
    write_fixture_dataset(dataset, EPISODES)
    identity = resolve_episode_in_dataset(dataset, "0" * 64, "2")
    assert identity.status == "digest-mismatch"
    assert identity.resolved_scene_id is None
    assert identity.dataset_index is None
    assert "differs from the pinned" in (identity.detail or "")


def test_missing_and_ambiguous_ids_stay_unresolved(dataset: Path) -> None:
    """Missing ids and duplicated ids yield explicit statuses, not guesses."""
    digest = write_fixture_dataset(dataset, EPISODES)
    missing = resolve_episode_in_dataset(dataset, digest, "99")
    assert missing.status == "not-found"
    duplicated: list[dict[str, object]] = [
        {"episode_id": "7", "scene_id": "mp3d/A/A.glb"},
        {"episode_id": "7", "scene_id": "mp3d/B/B.glb"},
    ]
    write_fixture_dataset(dataset, duplicated)
    ambiguous = resolve_episode_in_dataset(dataset, None, "7")
    assert ambiguous.status == "ambiguous"
    assert ambiguous.resolved_scene_id is None
    assert "additional discriminator" in (ambiguous.detail or "")


def test_unreadable_dataset_is_reported_not_raised(tmp_path: Path) -> None:
    """A corrupt or missing file yields an unreadable status, not a crash."""
    missing = resolve_episode_in_dataset(tmp_path / "nope.json.gz", None, "1")
    assert missing.status == "unreadable"
    broken = tmp_path / "broken.json.gz"
    broken.write_bytes(b"not gzip at all")
    corrupt = resolve_episode_in_dataset(broken, None, "1")
    assert corrupt.status == "unreadable"


def test_real_pinned_mobility_dataset_resolves(tmp_path: Path) -> None:
    """The pinned mobility dataset resolves ids to real MP3D scenes.

    Adapter-style check against the actual benchmark file; skipped when the
    EMOS checkout is not present on this machine.
    """
    dataset_path = Path(
        "/data/workspace/code/emos-baseline/data/datasets/mp3d/mobility_episodes_1.json.gz"
    )
    if not dataset_path.is_file():
        pytest.skip("EMOS baseline dataset not present on this machine")
    pinned_digest = "5d2c6aa6608d5611c73d8f6c688e17613a9898afa5f0f668e66db068598191ca"
    identity = resolve_episode_in_dataset(dataset_path, pinned_digest, "0")
    assert identity.status == "resolved"
    assert identity.resolved_scene_id is not None
    assert "mp3d/" in identity.resolved_scene_id
    assert identity.resolved_scene_id.endswith(".glb")
    assert identity.dataset_index is not None
