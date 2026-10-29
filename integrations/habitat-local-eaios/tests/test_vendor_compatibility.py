"""Preserve original task aliases in an explicit, source-bound vendor snapshot."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from habitat_local_eaios.vendor_compatibility import (  # noqa: E402
    PROFILE,
    SOURCE_PATH,
    prepare_vendor_view,
    preserve_target_identity,
)

FIXTURE = Path(__file__).parent / "fixtures/emos-hssd-object-description.json"


def published_source() -> bytes:
    """Restore exact published bytes from escaped lines, including upstream whitespace."""
    fixture = json.loads(FIXTURE.read_text())
    raw = "".join(fixture["source_lines"]).encode(fixture["encoding"])
    assert hashlib.sha256(raw).hexdigest() == fixture["sha256"]
    return raw


def source_tree(root: Path) -> bytes:
    """Create original-shaped packages with an actual published function fixture."""
    original = published_source()
    for name in ("habitat-lab", "habitat-baselines", "habitat-mas", "data"):
        (root / name).mkdir(parents=True)
    path = root / SOURCE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(original)
    (root / "habitat-lab/unrelated.py").write_text("# Unrelated original file.\n")
    (root / "data/episode.json").write_text('{"unchanged": true}\n')
    (root / "emos.env").write_text("not-a-credential-fixture\n")
    return original


def describe(raw: bytes, objects: list[Any], aliases: dict[str | None, str]) -> str:
    """Execute only the published function with no sensor, path, RNG or simulator API."""
    namespace: dict[str, Any] = {"np": SimpleNamespace(array=lambda value: value, inf=float("inf"))}
    exec(compile(raw, "<published-description-fixture>", "exec"), namespace)
    layer = SimpleNamespace(obj_ids=list(range(len(objects))), obj_dict=dict(enumerate(objects)))
    sim = SimpleNamespace(_handle_to_goal_name=aliases, pathfinder=SimpleNamespace(num_islands=0))
    result = namespace["generate_objects_description"](sim, layer)
    assert isinstance(result, str)
    return result


def test_exact_alias_height_binding_and_background_fallback() -> None:
    """Fix the observed ID loss without breaking ordinary unlabelled background objects."""
    raw = published_source()
    objects = [
        SimpleNamespace(full_name="high_cup:0000", label="label-ignored", center=[0.0, 1.4, 0.0]),
        SimpleNamespace(full_name="screwdriver:0000", label=None, center=[0.0, 0.4, 0.0]),
        SimpleNamespace(full_name="background:0000", label=None, center=[0.0, 0.7, 0.0]),
        SimpleNamespace(full_name="labelled:0000", label="semantic-label", center=[0.0, 0.8, 0.0]),
        SimpleNamespace(full_name=None, label=None, center=[0.0, 0.9, 0.0]),
    ]
    aliases: dict[str | None, str] = {
        "high_cup:0000": "any_targets|0",
        "screwdriver:0000": "any_targets|1",
    }
    before = describe(raw, objects, aliases)
    patched = preserve_target_identity(raw)
    after = describe(patched, objects, aliases)
    assert 'The height of "high_cup:0000" is 1.4.' in before
    assert 'The height of "any_targets|0" is 1.4.' in after
    assert 'The height of "any_targets|1" is 0.4.' in after
    assert 'The height of "background:0000" is 0.7.' in before
    assert 'The height of "background:0000" is 0.7.' in after
    assert 'The height of "semantic-label" is 0.8.' in after
    assert 'The height of "any_targets|4" is 0.9.' in after
    assert (
        patched.replace(
            b"        elif obj.full_name is None:\n", b"        if obj.full_name is None:\n"
        )
        == raw
    )


@pytest.mark.parametrize(
    "change", ["unknown", "missing", "duplicate", "decorated", "already-patched", "large"]
)
def test_unknown_source_refuses_before_creating_output(tmp_path: Path, change: str) -> None:
    """Only the declared original function can authorize this narrowly scoped patch."""
    source = tmp_path / "source"
    raw = source_tree(source)
    if change == "unknown":
        raw = raw.replace(b"horizontal_dist = np.inf", b"horizontal_dist = 0.0")
    elif change == "missing":
        raw = b"# No original function.\n"
    elif change == "duplicate":
        raw += published_source()
    elif change == "decorated":
        raw = raw.replace(
            b"def generate_objects_description", b"@unknown\ndef generate_objects_description"
        )
    elif change == "already-patched":
        raw = preserve_target_identity(raw)
    else:
        raw = b"#" * 1_000_001
    (source / SOURCE_PATH).write_bytes(raw)
    output = tmp_path / "prepared"
    with pytest.raises(ValueError):
        prepare_vendor_view(source, output)
    assert not output.exists()
    assert (source / SOURCE_PATH).read_bytes() == raw


def test_preparation_preserves_source_and_both_arm_views(tmp_path: Path) -> None:
    """Native and controlled copies inherit the same explicitly prepared vendor bytes."""
    from roboguide_eval.e1_pair import private_vendor_view  # type: ignore[import-untyped]

    source = tmp_path / "source"
    raw = source_tree(source)
    executable = source / "habitat-lab/unrelated.py"
    executable.chmod(0o755)
    output = tmp_path / "prepared"
    receipt = prepare_vendor_view(source, output)
    assert receipt["state"] == "complete" and receipt["profile"] == PROFILE
    assert receipt["changed_files"] == [str(SOURCE_PATH)]
    assert (source / SOURCE_PATH).read_bytes() == raw
    assert (output / SOURCE_PATH).read_bytes() == preserve_target_identity(raw)
    assert (output / "habitat-lab/unrelated.py").stat().st_mode & 0o777 == 0o755
    assert (output / "data").is_symlink()
    assert not (output / "emos.env").exists()
    for arm in ("native", "roboguide"):
        view = tmp_path / arm
        private_vendor_view(output, view, ("data/episode.json",))
        assert (view / SOURCE_PATH).read_bytes() == preserve_target_identity(raw)
        (view / "data/episode.json").write_text('{"private": true}\n')
    assert (source / "data/episode.json").read_text() == '{"unchanged": true}\n'
    saved = json.loads((output / "VENDOR_COMPATIBILITY.json").read_text())
    assert saved == receipt
    assert (
        saved["prepared_files_sha256"][str(SOURCE_PATH)]
        == hashlib.sha256(preserve_target_identity(raw)).hexdigest()
    )


@pytest.mark.parametrize(
    "destination", ["existing", "within-source", "source-symlink", "output-symlink"]
)
def test_unsafe_view_ownership_refuses_without_source_effects(
    tmp_path: Path, destination: str
) -> None:
    """Existing destinations and escaping package links cannot authorize private writes."""
    source = tmp_path / "source"
    raw = source_tree(source)
    output = tmp_path / "prepared"
    if destination == "existing":
        output.mkdir()
        (output / "user-file").write_text("keep")
    elif destination == "within-source":
        output = source / "nested-output"
    elif destination == "source-symlink":
        (source / "habitat-lab/foreign.py").symlink_to(tmp_path / "foreign-file")
    else:
        output.symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError):
        prepare_vendor_view(source, output)
    assert (source / SOURCE_PATH).read_bytes() == raw
    assert not (output / "VENDOR_COMPATIBILITY.json").exists()
    if destination == "existing":
        assert (output / "user-file").read_text() == "keep"


def test_copy_failure_cannot_publish_complete_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed copy retains original source and an explicitly unusable output."""
    source = tmp_path / "source"
    raw = source_tree(source)
    output = tmp_path / "prepared"

    def fail(*args: Any, **kwargs: Any) -> None:
        """Inject a filesystem failure without changing the supplied source."""
        raise OSError("copy unavailable")

    monkeypatch.setattr(shutil, "copy2", fail)
    with pytest.raises(OSError, match="copy unavailable"):
        prepare_vendor_view(source, output)
    assert output.is_dir() and not (output / "VENDOR_COMPATIBILITY.json").exists()
    assert (source / SOURCE_PATH).read_bytes() == raw


@pytest.mark.parametrize("where", ["source", "copy"])
def test_concurrent_source_or_copy_change_keeps_output_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    """Actual source drift or changed copied bytes cannot receive a complete receipt."""
    source = tmp_path / "source"
    source_tree(source)
    output = tmp_path / "prepared"
    original_copy = shutil.copy2

    def changed_copy(src: Path, dst: Path) -> None:
        """Change a real fixture file only after the original filesystem copy."""
        original_copy(src, dst)
        if src.name == "unrelated.py":
            (src if where == "source" else dst).write_text("# Concurrent change.\n")

    monkeypatch.setattr(shutil, "copy2", changed_copy)
    with pytest.raises(ValueError, match="changed during preparation"):
        prepare_vendor_view(source, output)
    assert not (output / "VENDOR_COMPATIBILITY.json").exists()
