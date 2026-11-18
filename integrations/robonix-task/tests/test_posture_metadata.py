"""Keep explicit RG posture verification metadata separate from native action choice."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from robonix_task.adapter import Adapter
from robonix_task.protocol import Liaison, task


def request(parameters: dict[str, Any]) -> dict[str, Any]:
    """Construct a local-only validation input without connecting to any Robonix endpoint."""
    return {
        "attempt_id": "metadata-fixture",
        "budget_ms": 3000,
        "invocation": {
            "operation": "task.delegate@v1",
            "objective": "Retain this exact complete local objective.",
            "parameters": parameters,
        },
    }


@pytest.mark.parametrize("posture", ["standing", "down"])
def test_metadata_preserves_exact_local_objective(tmp_path: Path, posture: str) -> None:
    """The adapter accepts explicit target metadata and does not issue another local action."""
    adapter = Adapter(
        tmp_path / "journal",
        Liaison("127.0.0.1:1", "fixture", "fixture"),
        allowed_operations=("task.delegate@v1",),
        enabled=True,
        task_profile="fixture-profile",
    )
    try:
        body = request({"profile": "fixture-profile", "expected_posture": posture})
        adapter.validate(body)
        native = task(
            {
                "request": body,
                "task_id": "original-task",
                "abort_id": "original-abort",
                "session_id": "original-session",
                "created_ns": 1_000_000,
            },
            "fixture",
        )
        assert native.text.startswith(body["invocation"]["objective"] + "\nRG intent: ")
        assert f'"expected_posture": "{posture}"' in native.text
        assert native.task_id == "original-task"
        assert adapter.jobs == set()
    finally:
        adapter.close()


@pytest.mark.parametrize(
    "parameters",
    [
        {"profile": "foreign", "expected_posture": "standing"},
        {"profile": "fixture-profile", "expected_posture": "walking"},
        {"profile": "fixture-profile", "expected_posture": []},
        {"profile": "fixture-profile", "expected_posture": "down", "extra": "unknown"},
    ],
)
def test_metadata_cannot_bypass_frozen_profile(tmp_path: Path, parameters: dict[str, Any]) -> None:
    """Unknown targets, parameters and profile changes are rejected before any native I/O."""
    adapter = Adapter(
        tmp_path / "journal",
        Liaison("127.0.0.1:1", "fixture", "fixture"),
        allowed_operations=("task.delegate@v1",),
        enabled=True,
        task_profile="fixture-profile",
    )
    try:
        with pytest.raises(ValueError, match="profile or explicit posture"):
            adapter.validate(request(parameters))
        assert adapter.jobs == set()
    finally:
        adapter.close()
