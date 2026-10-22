"""Check retained Artifact backup closure with explicit storage-contract fixtures, not devices."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery.bundle import backup, canonical, digest, restore, verify


@pytest.fixture
def retained(tmp_path: Path) -> tuple[Path, dict[str, Any], str]:
    """Build bounded contract metadata around actual independently hashable filesystem bytes."""
    root = tmp_path / "artifact"
    root.mkdir()
    (root / ".writer.lock").touch()
    (root / "staging").mkdir()
    payload = Path(__file__).read_bytes()
    checksum = digest(payload)
    directory = root / "objects-v1/sha256" / checksum[7:9]
    directory.mkdir(parents=True)
    (directory / checksum[7:]).write_bytes(payload)
    # These two fixture values exercise schema parsing, never OS/device observation. The native
    # actual-process gate separately creates the real owner with production Artifact Store code.
    owner = {
        "schema": "roboguide.artifact-storage/v1",
        "owner_id": "artifact-store:" + "a" * 64,
        "source_device": 1,
        "source_inode": 2,
    }
    for name in ("blobs", ".retention-owner.json"):
        (root / name).write_bytes(canonical(owner))
    profile: dict[str, Any] = {
        "schema": "roboguide.recovery-profile/v0.9",
        "profile_id": "retained-artifact-test",
        "revision": 1,
        "max_bytes": 4 * 1024 * 1024,
        "max_files": 32,
        "budget_seconds": 20,
        "artifact_replica_sets": [],
        "components": [
            {
                "component_id": "artifact",
                "kind": "artifact",
                "root": str(root),
                "database": None,
                "trees": [],
                "depends_on": [],
            }
        ],
    }
    return root, profile, checksum


def test_retained_backup_restore_keeps_actual_owner_files_and_content(
    retained: tuple[Path, dict[str, Any], str],
    tmp_path: Path,
) -> None:
    """A complete new-format bundle includes both original owner/fence records and exact bytes."""
    root, profile, checksum = retained
    target = tmp_path / "bundle"
    result = backup(profile, target, "original")
    manifest = verify(target, result["manifest_digest"])
    assert manifest["schema"] == "roboguide.recovery-bundle/v0.2"
    assert manifest["snapshot_operations"] == []
    receipt = restore(target, result["manifest_digest"], tmp_path / "restored", "restore")
    assert receipt["schema"] == "roboguide.restore-receipt/v0.9"
    assert receipt["artifact_replica_placements"] == []
    recovered = tmp_path / "restored/stores/artifact"
    for path in (
        "blobs",
        ".retention-owner.json",
        "objects-v1/sha256/" + checksum[7:9] + "/" + checksum[7:],
    ):
        assert (root / path).read_bytes() == (recovered / path).read_bytes()
    assert (recovered / "staging").is_dir()
    assert restore(target, result["manifest_digest"], tmp_path / "restored", "restore") == receipt


def test_old_profiles_cannot_publish_retained_artifact_without_original_identity(
    retained: tuple[Path, dict[str, Any], str],
    tmp_path: Path,
) -> None:
    """Legacy tree selection refuses the explicit retained format before publishing a bundle."""
    _, profile, _ = retained
    for version in ("v0.1", "v0.4", "v0.8"):
        selected = copy.deepcopy(profile)
        selected["schema"] = "roboguide.recovery-profile/" + version
        if version == "v0.1":
            selected.pop("artifact_replica_sets")
        with pytest.raises(ValueError, match="explicit recovery profile v0.9"):
            backup(selected, tmp_path / version, "legacy")
        assert not (tmp_path / version).exists()


@pytest.mark.parametrize("missing", ["blobs", ".retention-owner.json"])
def test_repinned_bundle_cannot_omit_either_mandatory_original_owner_record(
    retained: tuple[Path, dict[str, Any], str],
    tmp_path: Path,
    missing: str,
) -> None:
    """Omitting a mandatory identity file is rejected even when the attacker repins its manifest."""
    _, profile, _ = retained
    target = tmp_path / "bundle"
    backup(profile, target, "original")
    path = target / "manifest.json"
    manifest = json.loads(path.read_bytes())
    selected = "stores/artifact/" + missing
    manifest["files"] = [row for row in manifest["files"] if row["path"] != selected]
    (target / selected).unlink()
    raw = canonical(manifest)
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="mandatory original owner files"):
        verify(target, digest(raw))


def test_conflicting_owner_fence_refuses_before_any_backup_copy(
    retained: tuple[Path, dict[str, Any], str],
    tmp_path: Path,
) -> None:
    """Equal CAS bytes do not let another backend identity replace the original old-layout fence."""
    root, profile, _ = retained
    owner = json.loads((root / "blobs").read_bytes())
    owner["owner_id"] = "artifact-store:" + "b" * 64
    (root / "blobs").write_bytes(canonical(owner))
    with pytest.raises(ValueError, match="owner and old-layout fence conflict"):
        backup(profile, tmp_path / "refused", "original")
    assert not (tmp_path / "refused").exists()
