"""Regression tests for the Mission command-line contract."""

from pathlib import Path

from mission.cli import _parser


def test_validate_defaults_to_current_capability_catalog() -> None:
    """Keep CLI validation aligned with the current canonical catalog version."""
    arguments = _parser().parse_args(["validate", "--input", "mission.json"])

    assert arguments.catalog == Path("contracts/capability/v0.2/catalog.json")
