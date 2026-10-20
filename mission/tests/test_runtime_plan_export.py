"""Cross-language nullable export compatibility, without changing accepted authority identities."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from mission.models import MissionPlanError
from mission.runtime_replanning import controller_plan
from mission.submission_evidence import canonical_plan_digest
from test_planners import _v0_8_plan


def test_controller_nullable_export_preserves_original_bytes_and_published_meaning() -> None:
    """Read the actual renderer's omission while preserving its original source/digest."""
    published: dict[str, Any] = deepcopy(_v0_8_plan())
    for task in published["tasks"]:
        task["satisfaction"] = {
            "expected_effect": "explicit unit effect",
            "basis": "execution-report",
            "verifier": None,
        }
    original = deepcopy(published)
    for task in original["tasks"]:
        del task["satisfaction"]["verifier"]
    digest = canonical_plan_digest(original)
    restored = controller_plan(original)
    assert restored.to_json() == published
    assert canonical_plan_digest(original) == digest
    assert all("verifier" not in task["satisfaction"] for task in original["tasks"])


def test_missing_independent_verifier_never_becomes_execution_report_permission() -> None:
    """The compatibility read cannot invent missing verifier evidence or change its basis."""
    original: dict[str, Any] = deepcopy(_v0_8_plan())
    original["tasks"][0]["satisfaction"] = {
        "expected_effect": "explicit unit effect",
        "basis": "verifier-evidence",
    }
    with pytest.raises(MissionPlanError):
        controller_plan(original)
