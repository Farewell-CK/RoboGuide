"""Prepare an explicit private EMOS source view with preserved HSSD target identity.

This is deployment preparation, not runtime policy. It never imports EMOS,
constructs a simulator, contacts a Provider or edits the supplied source tree.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

PROFILE = "emos-hssd-target-identity/v0.1"
SOURCE_PATH = Path("habitat-mas/habitat_mas/scene_graph/utils.py")
FUNCTION = "generate_objects_description"
BEFORE_FUNCTION_SHA256 = "c5cad64d2e27d8ee3101a78236f3b8fdaeef4e9b8b385cf9e1ea70df0fd0769c"
AFTER_FUNCTION_SHA256 = "fc227e75b074dd8752bf555dd3fee3510469477a9034e8e9f575f6c43a9e0112"
_PACKAGES = ("habitat-lab", "habitat-baselines", "habitat-mas")
_EXCLUDED = {"__pycache__", ".git", ".env"}


def _digest(raw: bytes) -> str:
    """Compute one exact byte identity without interpreting vendor behavior."""
    return hashlib.sha256(raw).hexdigest()


def preserve_target_identity(raw: bytes) -> bytes:
    """Patch only the known published function, preserving all other module bytes.

    Already patched, unknown and ambiguous functions refuse. The branch keeps
    the original alias, label, unnamed-object and ordinary-handle precedence;
    only the independent fallback becomes part of that precedence chain.
    """
    if len(raw) > 1_000_000:
        raise ValueError("vendor description module exceeds the bounded patch input")
    text = raw.decode("utf-8")
    functions = [
        node
        for node in ast.parse(text).body
        if isinstance(node, ast.FunctionDef) and node.name == FUNCTION
    ]
    if len(functions) != 1:
        raise ValueError("vendor target description function is missing or ambiguous")
    function = functions[0]
    segment = ast.get_source_segment(text, function)
    if (
        function.decorator_list
        or segment is None
        or _digest(segment.encode()) != BEFORE_FUNCTION_SHA256
    ):
        raise ValueError("vendor target description function is not the declared public version")
    old = "        if obj.full_name is None:\n"
    if segment.count(old) != 1:
        raise ValueError("vendor target description fallback is ambiguous")
    updated = segment.replace(old, "        elif obj.full_name is None:\n", 1)
    if _digest(updated.encode()) != AFTER_FUNCTION_SHA256:
        raise ValueError("vendor target description patch identity differs")
    lines = text.splitlines(keepends=True)
    start = sum(len(line) for line in lines[: function.lineno - 1])
    if text[start : start + len(segment)] != segment:
        raise ValueError("vendor target description span differs")
    result = text[:start] + updated + text[start + len(segment) :]
    compile(result, str(SOURCE_PATH), "exec")
    return result.encode("utf-8")


def _excluded(path: Path) -> bool:
    """Keep interpreter caches and credential files out of a new source view."""
    return any(part in _EXCLUDED for part in path.parts) or path.suffix in {
        ".pyc",
        ".pyo",
        ".env",
    }


def _source_files(source: Path) -> dict[Path, str]:
    """Inventory only real package files, never shared datasets or user root files."""
    files: dict[Path, str] = {}
    for name in _PACKAGES:
        package = source / name
        if not package.is_dir() or package.is_symlink():
            raise ValueError("vendor source requires real package directories")
        for path in package.rglob("*"):
            relative = path.relative_to(source)
            if _excluded(relative):
                continue
            if path.is_symlink():
                raise ValueError("vendor package source may not escape through symbolic links")
            if path.is_file():
                files[relative] = _digest(path.read_bytes())
    return files


def prepare_vendor_view(source: Path, output: Path) -> dict[str, Any]:
    """Copy a frozen source view and publish a complete compatibility receipt.

    The output must be new and outside the supplied source. Shared datasets are
    shared references which this step never writes; actual run-private asset
    writes remain the existing pair driver's responsibility. Failure retains an
    incomplete output without a complete receipt, never a usable source claim.
    """
    source = source.resolve(strict=True)
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("vendor compatibility output must be a new directory")
    resolved_output = output.resolve()
    if source == resolved_output or source in resolved_output.parents:
        raise ValueError("vendor compatibility output must be outside its source")
    if not (source / "data").is_dir():
        raise ValueError("vendor source data directory is unavailable")
    before = _source_files(source)
    original = (source / SOURCE_PATH).read_bytes()
    patched = preserve_target_identity(original)
    if before.get(SOURCE_PATH) != _digest(original):
        raise ValueError("vendor description changed during preparation")
    output.mkdir(parents=True)
    for name in _PACKAGES:
        (output / name).mkdir()
    for relative in before:
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, destination)
    (output / "data").symlink_to(source / "data", target_is_directory=True)
    if _source_files(output) != before or _source_files(source) != before:
        raise ValueError("vendor source or copied view changed during preparation")
    (output / SOURCE_PATH).write_bytes(patched)
    after = _source_files(output)
    expected = {**before, SOURCE_PATH: _digest(patched)}
    if after != expected or _source_files(source) != before:
        raise ValueError("vendor compatibility changed undeclared source bytes")
    receipt: dict[str, Any] = {
        "schema_version": "roboguide.vendor-compatibility/v0.1",
        "profile": PROFILE,
        "state": "complete",
        "source_root": str(source),
        "prepared_root": str(output.resolve()),
        "upstream_reference": "e9501db45d634b087bf5d1a14228266685e8feeb",
        "upstream_reference_scope": "named-function-only; other deployment bytes are retained",
        "upstream_regression": "c70286ea470dce825bcf27aae7a4b17e9f2a2811",
        "changed_files": [str(SOURCE_PATH)],
        "function": FUNCTION,
        "before_function_sha256": BEFORE_FUNCTION_SHA256,
        "after_function_sha256": AFTER_FUNCTION_SHA256,
        "source_files_sha256": {str(path): value for path, value in sorted(before.items())},
        "prepared_files_sha256": {str(path): value for path, value in sorted(after.items())},
        "source_unchanged": True,
        "data_policy": "shared-reference; actual runs require private writable assets",
        "native_planner_or_skill_changes": False,
        "official_goal_or_metric_changes": False,
        "provider_calls": 0,
        "simulator_steps": 0,
    }
    (output / "VENDOR_COMPATIBILITY.json").write_text(
        json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


def main() -> int:
    """Prepare only an explicitly selected private source view from CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    arguments = parser.parse_args()
    receipt = prepare_vendor_view(arguments.source_root, arguments.output_root)
    print(
        json.dumps(
            {
                "profile": PROFILE,
                "state": receipt["state"],
                "output_root": str(arguments.output_root),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
