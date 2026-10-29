"""Declare the explicit controlled-arm lifecycle superseding its frozen predecessor.

The predecessor retains its own validation and archival meaning. Only the named
reset, working-context, pause and inbox policies change in the new generation;
unknown or nested lifecycle envelopes never acquire those permissions.
"""

from __future__ import annotations

from typing import Any

from .model import IntegrationError
from .reset_completion import reset_completion_declaration
from .stage2_context import context_declaration

EXECUTION_LIFECYCLE_SCHEMA = "roboguide.habitat-local-how-profile/v0.21"


def lifecycle_declaration(*, relocation: bool, acquisition: bool) -> dict[str, Any]:
    """Describe effective local behavior independently of benchmark or Core authority."""
    return {
        "stage2_working_context": context_declaration(),
        "reset_completion_binding": reset_completion_declaration() if relocation else None,
        "observation_pause": {
            "profile": "per-step-observed-vendor-pause/v0.1",
            "max_pause_simulator_steps": 500,
            "duration": "original-wait-clipped-to-original-remaining-budget",
            "condition_checks": "existing-cache-after-every-gym-step",
            "early_completion": "actual-current-condition-only",
            "model_action_rewritten": False,
        }
        if acquisition
        else None,
        "peer_message_lifecycle": {
            "profile": "live-attempt-inbox/v0.1",
            "scope": "original-shared-world-child-agents",
            "retired_attempt_inbox": "discard-with-count-before-endpoint-reuse",
            "operation_delegation_supported": False,
        },
    }


def _same_json(left: Any, right: Any) -> bool:
    """Reject boolean/integer substitutions as well as altered or extra declaration fields."""
    if type(left) is not type(right):
        return False
    if isinstance(right, dict):
        return left.keys() == right.keys() and all(_same_json(left[k], v) for k, v in right.items())
    if isinstance(right, list):
        return len(left) == len(right) and all(_same_json(left[i], v) for i, v in enumerate(right))
    return bool(left == right)


def unwrap_execution_lifecycle(
    profile: dict[str, Any], *, live: bool, relocation: bool, acquisition: bool
) -> dict[str, Any]:
    """Verify the new effective policy before checking its unchanged predecessor contracts."""
    if profile.get("schema_version") != EXECUTION_LIFECYCLE_SCHEMA:
        return profile
    expected = lifecycle_declaration(relocation=relocation, acquisition=acquisition)
    predecessor = profile.get("predecessor_profile")
    if (
        not live
        or not (relocation or acquisition)
        or set(profile) != {"schema_version", "predecessor_profile", *expected}
        or not isinstance(predecessor, dict)
        or predecessor.get("schema_version") == EXECUTION_LIFECYCLE_SCHEMA
        or not _same_json({key: profile[key] for key in expected}, expected)
    ):
        raise IntegrationError("controlled execution lifecycle declaration is invalid")
    return predecessor
