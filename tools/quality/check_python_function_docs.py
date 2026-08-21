"""Reject Python functions and methods that lack useful docstrings."""

from __future__ import annotations

import ast
import sys
from pathlib import Path


def _python_files(paths: list[Path]) -> list[Path]:
    """Expand files and directories into a stable list of Python source files."""
    files: set[Path] = set()
    for path in paths:
        if path.is_file() and path.suffix == ".py":
            files.add(path)
        elif path.is_dir():
            files.update(path.rglob("*.py"))
    return sorted(files)


def _missing_docstrings(path: Path) -> list[str]:
    """Return source locations for every function without a nonblank docstring."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    missing: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and not ast.get_docstring(node):
            missing.append(f"{path}:{node.lineno}: function {node.name!r} has no docstring")
    return missing


def main(arguments: list[str]) -> int:
    """Check supplied paths and return nonzero when any function lacks documentation."""
    failures = [
        failure
        for path in _python_files([Path(argument) for argument in arguments])
        for failure in _missing_docstrings(path)
    ]
    for failure in failures:
        print(failure)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
