"""Runtime source provenance for controlled Local EAIOS executions."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path


class SourceProvenanceError(RuntimeError):
    """Report a runtime module whose loaded source cannot be proven."""


def build_runtime_source_manifest(module_names: tuple[str, ...]) -> dict[str, object]:
    """Resolve and digest the exact Python sources selected by the child process.

    Raises:
        SourceProvenanceError: If a requested module has no readable filesystem
            source. Controlled executions fail closed instead of claiming an
            EMOS checkout that the Python import system did not actually load.
    """
    modules: dict[str, object] = {}
    for module_name in module_names:
        specification = importlib.util.find_spec(module_name)
        origin = None if specification is None else specification.origin
        if origin is None or origin in {"built-in", "frozen"}:
            raise SourceProvenanceError(f"runtime module {module_name!r} has no source path")
        path = Path(origin).resolve()
        try:
            content = path.read_bytes()
        except OSError as error:
            raise SourceProvenanceError(
                f"runtime module {module_name!r} source is unreadable: {error}"
            ) from error
        modules[module_name] = {
            "path": str(path),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    return {
        "schema_version": "roboguide.local-eaios-source-provenance/v0.1",
        "python_executable": str(Path(sys.executable).resolve()),
        "modules": modules,
    }
