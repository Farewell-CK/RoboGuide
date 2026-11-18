"""Recover actual lazy empty legacy Artifact layouts without weakening byte or lease checks."""

from __future__ import annotations

import fcntl
import shutil
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery import artifact_layout, backup, restore, verify
from test_legacy_artifact_v32 import legacy as legacy


@pytest.fixture
def empty_legacy(
    legacy: tuple[dict[str, Any], Path, bytes],
) -> tuple[dict[str, Any], Path]:
    """Reuse complete Controller authority with the backend's pre-first-upload directory layout."""
    profile, blob, _ = legacy
    root = blob.parents[3]
    blob.unlink()
    blob.parent.rmdir()
    blob.parent.parent.rmdir()
    assert sorted(path.name for path in root.iterdir()) == [".writer.lock", "blobs", "staging"]
    assert list((root / "blobs").iterdir()) == []
    return profile, root


def test_lazy_empty_original_backup_verify_and_new_directory_restore(
    empty_legacy: tuple[dict[str, Any], Path], tmp_path: Path
) -> None:
    """An uninitialized hash subtree stays empty through complete backup and independent restore."""
    profile, original = empty_legacy
    writer = original / ".writer.lock"
    identity = writer.stat().st_dev, writer.stat().st_ino
    receipt = backup(profile, tmp_path / "bundle", "empty-original")
    manifest = verify(tmp_path / "bundle", receipt["manifest_digest"])
    assert not any(row["path"].startswith("stores/artifact/") for row in manifest["files"])
    assert manifest["artifact_snapshot_operations"] == []
    copied = tmp_path / "bundle/stores/artifact"
    assert sorted(path.name for path in copied.iterdir()) == ["blobs", "staging"]
    assert list((copied / "blobs").iterdir()) == []
    restore(tmp_path / "bundle", receipt["manifest_digest"], tmp_path / "new", "empty-restore")
    recovered = tmp_path / "new/stores/artifact"
    artifact_layout.validate_legacy(recovered)
    assert sorted(path.name for path in recovered.iterdir()) == [".writer.lock", "blobs", "staging"]
    assert list((recovered / "blobs").iterdir()) == []
    assert (writer.stat().st_dev, writer.stat().st_ino) == identity
    assert list((original / "blobs").iterdir()) == []
    assert not (recovered / ".retention-owner.json").exists()


@pytest.mark.parametrize("name", [".hidden", "unexpected", "objects-v1"])
def test_missing_hash_directory_never_hides_unknown_blob_children(
    empty_legacy: tuple[dict[str, Any], Path], tmp_path: Path, name: str
) -> None:
    """A nonempty malformed blobs directory cannot borrow the lazy empty-layout exception."""
    profile, root = empty_legacy
    (root / "blobs" / name).write_bytes(b"unaccounted original bytes")
    with pytest.raises(ValueError, match="unknown child"):
        backup(profile, tmp_path / "refused", "unexpected")
    assert not (tmp_path / "refused").exists()


@pytest.mark.parametrize("target", ["missing", "directory", "file"])
def test_sha256_symlink_or_file_is_not_an_empty_legacy_directory(
    empty_legacy: tuple[dict[str, Any], Path], tmp_path: Path, target: str
) -> None:
    """Missing, existing and non-directory link targets never replace original real CAS paths."""
    profile, root = empty_legacy
    external = tmp_path / "external"
    sha256 = root / "blobs/sha256"
    if target == "file":
        sha256.write_bytes(b"not a directory")
    else:
        if target == "directory":
            external.mkdir()
        sha256.symlink_to(external)
    with pytest.raises((ValueError, OSError)):
        backup(profile, tmp_path / "refused", "invalid-sha256")
    assert not (tmp_path / "refused").exists()


def test_empty_legacy_still_requires_its_independent_writer_lease(
    empty_legacy: tuple[dict[str, Any], Path], tmp_path: Path
) -> None:
    """Zero blob content never permits backup while the actual Artifact writer remains active."""
    profile, root = empty_legacy
    with (root / ".writer.lock").open("rb") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="still active"):
            backup(profile, tmp_path / "refused", "active-empty-writer")
    assert not (tmp_path / "refused").exists()


@pytest.mark.parametrize("replacement", ["absent", "symlink", "unknown"])
def test_populated_bundle_cannot_be_reinterpreted_as_an_empty_layout(
    legacy: tuple[dict[str, Any], Path, bytes], tmp_path: Path, replacement: str
) -> None:
    """The original manifest still requires every copied blob before verify or restore succeeds."""
    profile, blob, _ = legacy
    receipt = backup(profile, tmp_path / "bundle", "populated-original")
    copied = tmp_path / "bundle/stores/artifact/blobs"
    shutil.rmtree(copied / "sha256")
    if replacement == "symlink":
        (copied / "sha256").symlink_to(blob.parents[1])
    elif replacement == "unknown":
        (copied / ".hidden").write_bytes(b"unaccounted")
    with pytest.raises((ValueError, OSError)):
        verify(tmp_path / "bundle", receipt["manifest_digest"])
    with pytest.raises((ValueError, OSError)):
        restore(tmp_path / "bundle", receipt["manifest_digest"], tmp_path / "refused", "lost-blob")
    assert blob.is_file() and not (tmp_path / "refused").exists()


@pytest.mark.parametrize("name", [".hidden", "unexpected"])
def test_empty_bundle_rejects_undeclared_content_in_the_lazy_subtree(
    empty_legacy: tuple[dict[str, Any], Path], tmp_path: Path, name: str
) -> None:
    """A valid empty snapshot never turns later hidden or unrelated files into declared content."""
    profile, _ = empty_legacy
    receipt = backup(profile, tmp_path / "bundle", "empty-original")
    (tmp_path / "bundle/stores/artifact/blobs" / name).write_bytes(b"undeclared")
    with pytest.raises(ValueError, match="unknown child"):
        verify(tmp_path / "bundle", receipt["manifest_digest"])
    with pytest.raises(ValueError, match="unknown child"):
        restore(tmp_path / "bundle", receipt["manifest_digest"], tmp_path / "refused", "invalid")
    assert not (tmp_path / "refused").exists()
