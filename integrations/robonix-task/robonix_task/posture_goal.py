"""Assess a reported-posture goal without executing, retrying or authorizing any action.

The deployment must capture the anchor after its exact current local attempt completes.
This reader supplies a verdict, not Controller authority or physical verification. Only
an authenticated deployment verifier may bind it to an admitted Task and publish it.
"""

from __future__ import annotations

from typing import Any

from robonix_task.posture import inspect_posture

VERIFIER = {"namespace": "observation.posture", "name": "verify-reported", "version": "v1"}


def predicate_for(posture: str) -> str:
    """Name the limited reported-state predicate, rejecting unsupported physical claims."""
    if posture not in {"standing", "down"}:
        raise ValueError("expected posture must be standing or down")
    return f"fresh post-completion Robonix aggregate reports {posture}"


def assess_goal(
    anchor: dict[str, Any],
    state: dict[str, Any],
    inventory: dict[str, Any],
    *,
    node_id: str,
    expected_posture: str,
) -> dict[str, Any]:
    """Require a newer qualified native sample than the post-completion RG anchor.

    A fresh contradictory report is unsatisfied; missing, old or incomparable evidence
    stays unknown. Source epochs fence restart, and native receive identity prevents
    republishing one cached sample with a newer RG sequence from satisfying a goal.
    """
    result: dict[str, Any] = {
        "schema_version": "roboguide.reported-posture-goal-assessment/v0.1",
        "node_id": node_id,
        "predicate": predicate_for(expected_posture),
        "expected_posture": expected_posture,
        "verdict": "unknown",
        "finality": "provisional",
        "valid_for_ms": 0,
        "motion_authorized": False,
        "physical_effect_verified": False,
    }

    def refuse(reason: str) -> dict[str, Any]:
        """Withhold finality when the observations do not establish the declared predicate."""
        return {**result, "reason": reason}

    current = inspect_posture(state, inventory, node_id=node_id)
    result["observation"] = current
    if (
        anchor.get("schema_version") != "roboguide.reported-posture-inspection/v0.1"
        or anchor.get("node_id") != node_id
        or anchor.get("status") != "reported"
        or current["status"] != "reported"
    ):
        return refuse("anchor_or_current_observation_unavailable")
    before, after = anchor.get("evidence"), current.get("evidence")
    if not isinstance(before, dict) or not isinstance(after, dict):
        return refuse("missing_observation_identity")
    for field in ("source", "channel_id", "source_epoch", "provider_id", "registration_id"):
        if (
            not isinstance(before.get(field), str)
            or not before[field]
            or after.get(field) != before[field]
        ):
            return refuse("observation_source_changed_or_missing")
    for evidence in (before, after):
        for field in (
            "sequence",
            "chassis_received_at_ns",
            "controller_received_at_ms",
            "controller_observed_at_ms",
        ):
            if type(evidence.get(field)) is not int or evidence[field] < 0:
                return refuse("missing_or_invalid_observation_order")
    if (
        after["sequence"] <= before["sequence"]
        or after["chassis_received_at_ns"] <= before["chassis_received_at_ns"]
        or after["controller_received_at_ms"] <= before["controller_observed_at_ms"]
    ):
        return refuse("sample_not_newer_than_post_completion_anchor")
    remaining = after["remaining_valid_for_ms"]
    if remaining <= 0:
        return refuse("no_observation_budget_remaining")
    result.update(
        verdict="satisfied" if current["posture"] == expected_posture else "unsatisfied",
        finality="final",
        valid_for_ms=remaining,
        reason="newer_bounded_age_reported_aggregate",
        anchor_evidence=before,
    )
    return result
