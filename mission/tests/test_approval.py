"""Deterministic tests for context-aware Mission approval policy."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from mission.approval import ApprovalPolicy, ApprovalRule
from mission.intent import GroundedIntent
from mission.models import JSONObject, MissionPlan

FIXTURE = Path("scenarios/mission-front-half-v0.7/mission-plan.json")


def _plan(*, object_id: str = "first-aid-kit-2f") -> MissionPlan:
    """Load one normalized plan while allowing a typed semantic parameter variation."""
    raw = cast(JSONObject, json.loads(FIXTURE.read_text(encoding="utf-8")))
    tasks = cast(list[JSONObject], raw["tasks"])
    roles = cast(list[JSONObject], tasks[0]["roles"])
    intent = cast(JSONObject, roles[0]["execution_intent"])
    parameters = cast(JSONObject, intent["parameters"])
    parameters["object"] = object_id
    return MissionPlan.from_json(raw)


def test_same_operation_can_have_different_parameter_risk() -> None:
    """Operation membership alone does not gate a semantically different safe object."""
    policy = ApprovalPolicy(
        (
            ApprovalRule(
                "hazardous-relocation",
                "object.relocate@v1",
                parameter_equals=(("object", "hazardous-device"),),
            ),
        )
    )
    grounded = GroundedIntent("relocate the requested object", (), ())

    assert policy.evaluate(_plan(), grounded).required is False
    decision = policy.evaluate(_plan(object_id="hazardous-device"), grounded)
    assert decision.required is True
    assert decision.matched_rule_ids == ("hazardous-relocation",)


def test_approval_rule_can_require_objective_and_grounded_context() -> None:
    """Semantic objective and confirmed constraints participate without consulting inventory."""
    policy = ApprovalPolicy(
        (
            ApprovalRule(
                "restricted-destination",
                "object.relocate@v1",
                objective_contains=("安全交付",),
                grounded_constraint_contains=("authorized escort",),
            ),
        )
    )

    assert policy.evaluate(
        _plan(), GroundedIntent("deliver aid", ("authorized escort required",), ())
    ).required
    assert not policy.evaluate(
        _plan(), GroundedIntent("deliver aid", ("normal access",), ())
    ).required
