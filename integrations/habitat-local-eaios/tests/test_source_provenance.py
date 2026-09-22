"""Deterministic checks for controlled runtime source provenance."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import cast

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.source_provenance import (  # noqa: E402
    SourceProvenanceError,
    build_runtime_source_manifest,
)


def test_runtime_source_manifest_records_resolved_bytes() -> None:
    """Each claimed module path is absolute and bound to its actual bytes."""
    manifest = build_runtime_source_manifest(("habitat_local_eaios.model",))
    modules = cast(dict[str, dict[str, str]], manifest["modules"])
    module = modules["habitat_local_eaios.model"]
    path = Path(module["path"])
    assert path.is_absolute()
    assert module["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert Path(cast(str, manifest["python_executable"])).is_absolute()


def test_runtime_source_manifest_rejects_missing_module() -> None:
    """A controlled run cannot claim provenance for an unavailable module."""
    with pytest.raises(SourceProvenanceError, match="has no source path"):
        build_runtime_source_manifest(("roboguide_test_module_does_not_exist",))
