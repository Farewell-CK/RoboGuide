"""Deterministic tests for operation readiness across the Habitat IPC boundary."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.model import SUPPORTED_OPERATIONS, IntegrationError  # noqa: E402
from habitat_local_eaios.process_backend import _parse_ready_payload  # noqa: E402


def test_child_ready_envelope_preserves_operation_list() -> None:
    """The parent receives the child's exact supported operation identities."""
    detail, operations = _parse_ready_payload(
        {
            "detail": "loaded Stage2",
            "operations": ["mobility.navigate@v1", "object.relocate@v1"],
        }
    )
    assert detail == "loaded Stage2"
    assert operations == ("mobility.navigate@v1", "object.relocate@v1")


def test_legacy_string_ready_envelope_remains_navigation_only() -> None:
    """Old child processes remain readable without gaining unproven operations."""
    detail, operations = _parse_ready_payload("legacy Stage2")
    assert detail == "legacy Stage2"
    assert operations == SUPPORTED_OPERATIONS


@pytest.mark.parametrize(
    "payload",
    [
        {"detail": "", "operations": ["object.relocate@v1"]},
        {"detail": "ready", "operations": []},
        {"detail": "ready", "operations": ["object.relocate@v1", "object.relocate@v1"]},
        {"detail": "ready", "operations": [None]},
        {"operations": ["mobility.navigate@v1"]},
    ],
)
def test_invalid_child_ready_envelope_fails_closed(payload: object) -> None:
    """Malformed or ambiguous operation readiness cannot pass the IPC boundary."""
    with pytest.raises(IntegrationError):
        _parse_ready_payload(payload)
