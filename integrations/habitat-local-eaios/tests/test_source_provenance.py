"""Deterministic checks for controlled runtime source provenance."""

from __future__ import annotations

import hashlib
import importlib.util
import json
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
    main,
    runtime_source_modules,
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


def test_original_reset_sources_are_common_without_controlled_policy() -> None:
    """Both producers observe original reset bytes without installing RG policy on native EMOS."""
    native = runtime_source_modules("emos")
    controlled = runtime_source_modules("roboguide")
    required = {
        "habitat.tasks.rearrange.actions.actions",
        "habitat.tasks.rearrange.actions.reset_action",
        "habitat_baselines.rl.hrl.skills.reset",
    }
    assert required <= set(native) <= set(controlled)
    assert "habitat_local_eaios.reset_completion" not in native
    assert "habitat_local_eaios.reset_completion" in controlled
    assert runtime_source_modules("emos", relocation_binding=True, spatial_arrival=True) == native
    assert len(native) == len(set(native)) and len(controlled) == len(set(controlled))


def test_conditional_source_observations_retain_their_scope() -> None:
    """The guaranteed declaration never promises observations from disabled optional bindings."""
    base = set(runtime_source_modules("roboguide"))
    relocation = set(runtime_source_modules("roboguide", relocation_binding=True))
    spatial = set(runtime_source_modules("roboguide", spatial_arrival=True))
    assert "habitat_local_eaios.live_bindings" in relocation - base
    assert "habitat_local_eaios.navigation_preparation" in spatial - base
    assert not (relocation - base) & (spatial - base)
    with pytest.raises(ValueError, match="unknown runtime source arm"):
        runtime_source_modules("unknown")


def test_declaration_does_not_resolve_or_import_runtime_modules(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Offline coverage is only a declaration, with no vendor imports or simulated observations."""

    def forbidden(name: str) -> None:
        """Fail if a description starts resolving Habitat, a model or any runtime module."""
        raise AssertionError("declaration must not resolve " + name)

    monkeypatch.setattr(importlib.util, "find_spec", forbidden)
    main(["--describe"])
    declaration = json.loads(capsys.readouterr().out)
    assert declaration["schema_version"] == "roboguide.local-eaios-source-coverage/v0.1"
    assert declaration["basis"] == "declared-emission-coverage-not-runtime-observation"
    assert declaration["arms"] == {
        arm: list(runtime_source_modules(arm)) for arm in ("emos", "roboguide")
    }
