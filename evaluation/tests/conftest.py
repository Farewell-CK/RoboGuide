"""Shared pytest fixtures for Eval Harness tests.

Fixtures only wrap the deterministic builders from :mod:`helpers`; see that
module for the offline child-process scripts and YAML text builders.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from helpers import BASE_SPEC_YAML, local_config_yaml

SpecFactory = Callable[..., Path]
LocalConfigFactory = Callable[..., Path]


@pytest.fixture
def make_spec(tmp_path: Path) -> SpecFactory:
    """Provide a factory writing experiment spec YAML variants.

    Args:
        tmp_path: Per-test temporary directory.

    Returns:
        A factory mapping YAML text to a written spec path.
    """

    def _factory(yaml_text: str = BASE_SPEC_YAML) -> Path:
        """Write one spec YAML text into the test directory.

        Args:
            yaml_text: The spec YAML content.

        Returns:
            Path of the written spec file.
        """
        target = tmp_path / "spec.yaml"
        target.write_text(yaml_text, encoding="utf-8")
        return target

    return _factory


@pytest.fixture
def fixture_workdir(tmp_path: Path) -> Path:
    """Provide an existing working directory for the fixture system.

    Args:
        tmp_path: Per-test temporary directory.

    Returns:
        The created working directory path.
    """
    workdir = tmp_path / "emos-workdir"
    workdir.mkdir()
    return workdir


@pytest.fixture
def make_local_config(tmp_path: Path) -> LocalConfigFactory:
    """Provide a factory writing local configuration YAML variants.

    Args:
        tmp_path: Per-test temporary directory.

    Returns:
        A factory mapping YAML text to a written config path.
    """

    def _factory(yaml_text: str) -> Path:
        """Write one local config YAML text into the test directory.

        Args:
            yaml_text: The local config YAML content.

        Returns:
            Path of the written config file.
        """
        target = tmp_path / "local.yaml"
        target.write_text(yaml_text, encoding="utf-8")
        return target

    return _factory


@pytest.fixture
def fixture_local_config(
    tmp_path: Path,
    fixture_workdir: Path,
    make_local_config: LocalConfigFactory,
) -> Path:
    """Provide the standard fixture local config for the ``emos`` system.

    Args:
        tmp_path: Per-test temporary directory.
        fixture_workdir: Existing fixture working directory.
        make_local_config: Local config factory.

    Returns:
        Path of the written local config file.
    """
    return make_local_config(local_config_yaml(fixture_workdir))


@pytest.fixture
def fixed_environment() -> dict[str, str]:
    """Provide a deterministic harness environment for provenance.

    Returns:
        An environment mapping pinning the RoboGuide Git SHA override.
    """
    return {"ROBOGUIDE_EVAL_GIT_SHA": "fixture0000000000000000000000000000000"}
