"""Offline policy fences for environment-authoritative joint terminal outcomes."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from mission.config import MissionSettings, load_settings
from mission.contract_values import MissionPlanError
from mission.grounding_context import GroundingContextSnapshot
from mission.intent import GroundedIntent
from mission.models import JSONObject, MissionPlan
from mission.rejected_draft import RejectedPlanError
from mission.responses import ResponsesMissionPlanner, ResponsesMissionReviewer
from mission.satisfaction_policy import (
    MissionSatisfactionPolicy,
    MissionSatisfactionPolicyError,
    validate_satisfaction_policy,
)
from mission.semantic_evidence import (
    AuthoritativeSemanticEvidence,
    SemanticExpression,
    exact_goal_verifier_predicate,
)
from test_planners import FakeTransport, _current_catalog, _provider_plan, _response

_B1_CONFIG = Path("scenarios/e1-shared-world-episode-51/mission-config-b1.toml")
_TWO_TASK_SHAPE = Path("scenarios/e1-shared-world-episode-51/mission-plan.json")
_PREDICATE = "at(target-alpha) AND at(target-beta)"


def _policy() -> MissionSatisfactionPolicy:
    """Load the explicit deployment policy rather than inventing test-only defaults."""
    policy = load_settings(_B1_CONFIG, repository_root=Path.cwd()).satisfaction_policy
    assert policy is not None
    return policy


def _semantic(goal: SemanticExpression | None = None) -> AuthoritativeSemanticEvidence:
    """Freeze a generic two-condition terminal goal with attributed identity."""
    return AuthoritativeSemanticEvidence.create(
        run_id="run-generic",
        episode_id="episode-generic",
        revision="goal-revision-1",
        dataset_revision="dataset-generic",
        dataset_sha256="a" * 64,
        goal=goal
        or SemanticExpression.logical(
            "and",
            (
                SemanticExpression.predicate("at", ("target-alpha",)),
                SemanticExpression.predicate("at", ("target-beta",)),
            ),
        ),
        world_context={"scene_id": "scene-generic", "agent_ids": [0, 1]},
    )


def _grounding(goal: SemanticExpression | None = None) -> GroundingContextSnapshot:
    """Bind the test goal into one immutable MI request snapshot."""
    return GroundingContextSnapshot.create(
        request_id="request-generic",
        dialogue_digest="sha256:" + "0" * 64,
        captured_at_ms=10,
        semantic_evidence=_semantic(goal),
    )


def _plan(*, serial: bool = False, verified: tuple[bool, bool] = (True, True)) -> JSONObject:
    """Reuse a validated two-Task shape with generic destinations and policy bases."""
    raw = cast(JSONObject, json.loads(_TWO_TASK_SHAPE.read_text(encoding="utf-8")))
    mission = cast(JSONObject, raw["mission"])
    mission["id"] = "mission-generic"
    mission["objective"] = "Establish both target conditions at the joint terminal state."
    tasks = cast(list[JSONObject], raw["tasks"])
    for index, task in enumerate(tasks):
        task["id"] = f"reach-{index}"
        task["description"] = f"Reach target-{'alpha' if index == 0 else 'beta'}."
        role = cast(list[JSONObject], task["roles"])[0]
        intent = cast(JSONObject, role["execution_intent"])
        intent["objective"] = cast(str, task["description"])
        cast(JSONObject, intent["parameters"])["destination"] = (
            "target-alpha" if index == 0 else "target-beta"
        )
        satisfaction = cast(JSONObject, task["satisfaction"])
        satisfaction["expected_effect"] = cast(str, task["description"])
        satisfaction["basis"] = "verifier-evidence" if verified[index] else "execution-report"
        satisfaction["verifier"] = (
            {
                "contract": {"namespace": "observation", "name": "verify", "version": "v1"},
                "predicate": _PREDICATE,
                "max_evidence_age_ms": 5000,
            }
            if verified[index]
            else None
        )
    if serial:
        tasks[1]["depends_on"] = ["reach-0"]
    return raw


def _settings() -> MissionSettings:
    """Use the committed B1 policy with a non-network fake Provider endpoint."""
    settings = load_settings(_B1_CONFIG, repository_root=Path.cwd())
    return replace(settings, provider=replace(settings.provider, base_url="http://127.0.0.1:8080"))


def test_b1_policy_is_an_explicit_deployment_override() -> None:
    """The B1 config changes the goal policy without drifting model or Provider settings."""
    base = load_settings(Path("config/mission.toml"), repository_root=Path.cwd())
    b1 = load_settings(_B1_CONFIG, repository_root=Path.cwd())
    assert base.satisfaction_policy is not None
    assert base.satisfaction_policy.authoritative_goal_verifier is None
    assert b1.satisfaction_policy is not None
    assert b1.satisfaction_policy.authoritative_goal_verifier is not None
    assert (
        b1.satisfaction_policy.max_evidence_age_ms == base.satisfaction_policy.max_evidence_age_ms
    )
    assert b1.llm == base.llm
    assert b1.provider == base.provider
    assert b1.prompts == base.prompts
    assert b1.schema_path == base.schema_path
    assert b1.capability_catalog_path == base.capability_catalog_path
    assert (
        b1.satisfaction_policy.to_json()["policy_digest"]
        != (base.satisfaction_policy.to_json()["policy_digest"])
    )


@pytest.mark.parametrize(
    ("goal", "expected"),
    [
        (SemanticExpression.predicate("at", ("target-a",)), "at(target-a)"),
        (
            SemanticExpression.logical(
                "or",
                (
                    SemanticExpression.predicate("at", ("target-a",)),
                    SemanticExpression.logical(
                        "and",
                        (
                            SemanticExpression.predicate("at", ("target-b",)),
                            SemanticExpression.predicate("at", ("target-c",)),
                        ),
                    ),
                ),
            ),
            "at(target-a) OR (at(target-b) AND at(target-c))",
        ),
        (
            SemanticExpression.logical(
                "nand",
                (
                    SemanticExpression.predicate("at", ("target-a",)),
                    SemanticExpression.predicate("at", ("target-b",)),
                ),
            ),
            None,
        ),
    ],
)
def test_exact_goal_renderer_preserves_supported_logic(
    goal: SemanticExpression, expected: str | None
) -> None:
    """Unsupported syntax cannot be weakened into an apparently verifiable predicate."""
    assert exact_goal_verifier_predicate(goal) == expected


def test_mi_goal_predicate_matches_the_deployment_source_fixture() -> None:
    """The MI renderer and Habitat source agree on the versioned full-goal text."""
    source = json.loads(
        Path("integrations/habitat-local-eaios/tests/fixtures/task-verifier-source.json").read_text(
            encoding="utf-8"
        )
    )
    goal = SemanticExpression.logical(
        "and",
        (
            SemanticExpression.predicate("any_at", ("any_targets|0",)),
            SemanticExpression.predicate("any_at", ("TARGET_any_targets|0",)),
        ),
    )
    assert exact_goal_verifier_predicate(goal) == source["supported_predicates"][0]


def test_parallel_terminal_tasks_require_full_independent_verdict() -> None:
    """Both independent DAG sinks need the same full goal without forcing serial order."""
    policy, semantic = _policy(), _semantic()
    valid = MissionPlan.from_json(_plan())
    validate_satisfaction_policy(valid, policy, semantic)
    assert all(not task.depends_on for task in valid.tasks)
    with pytest.raises(MissionPlanError, match="DAG-terminal Task"):
        validate_satisfaction_policy(
            MissionPlan.from_json(_plan(verified=(True, False))), policy, semantic
        )


def test_serial_prerequisite_may_report_but_final_task_must_be_verified() -> None:
    """The policy permits local prerequisite completion before final-world proof."""
    policy, semantic = _policy(), _semantic()
    validate_satisfaction_policy(
        MissionPlan.from_json(_plan(serial=True, verified=(False, True))), policy, semantic
    )
    with pytest.raises(MissionPlanError, match="DAG-terminal Task"):
        validate_satisfaction_policy(
            MissionPlan.from_json(_plan(serial=True, verified=(True, False))), policy, semantic
        )


@pytest.mark.parametrize("mutation", ["predicate", "contract"])
def test_wrong_verifier_identity_fails_closed(mutation: str) -> None:
    """A partial goal or an unconfigured verifier cannot satisfy the policy."""
    raw = _plan()
    satisfaction = cast(JSONObject, cast(list[JSONObject], raw["tasks"])[0]["satisfaction"])
    verifier = cast(JSONObject, satisfaction["verifier"])
    if mutation == "predicate":
        verifier["predicate"] = "at(target-alpha)"
    else:
        verifier["contract"] = {"namespace": "observation", "name": "other", "version": "v1"}
    with pytest.raises(MissionPlanError, match="DAG-terminal Task"):
        validate_satisfaction_policy(MissionPlan.from_json(raw), _policy(), _semantic())


def test_no_authoritative_goal_does_not_force_verifier_on_ordinary_tasks() -> None:
    """Deployment policy applies only to a frozen authoritative goal snapshot."""
    validate_satisfaction_policy(
        MissionPlan.from_json(_plan(verified=(False, False))), _policy(), None
    )


def test_unsupported_goal_stops_before_provider_call() -> None:
    """An unrepresentable authoritative goal is a system gap, not a weaker draft."""
    transport = FakeTransport([])
    planner = ResponsesMissionPlanner(_settings(), {"OPENAI_API_KEY": "test-only-key"}, transport)
    grounding = _grounding(
        SemanticExpression.logical(
            "nand",
            (
                SemanticExpression.predicate("at", ("target-alpha",)),
                SemanticExpression.predicate("at", ("target-beta",)),
            ),
        )
    )
    with pytest.raises(MissionSatisfactionPolicyError, match="cannot represent"):
        planner.plan(
            "mission-generic",
            GroundedIntent("Establish both target conditions at the joint terminal state.", (), ()),
            _current_catalog(),
            grounding,
        )
    assert transport.requests == []


def test_planner_recovery_and_reviewer_use_the_same_exact_policy() -> None:
    """Reject a raw report draft, recover it, then review the policy-bound plan."""
    initial, repaired = _plan(verified=(False, False)), _plan()
    transport = FakeTransport(
        [
            _response(_provider_plan(initial)),
            _response(_provider_plan(repaired)),
            _response({"approved": True, "issues": []}),
        ]
    )
    settings = _settings()
    planner = ResponsesMissionPlanner(settings, {"OPENAI_API_KEY": "test-only-key"}, transport)
    grounding = _grounding()
    intent = GroundedIntent("Establish both target conditions at the joint terminal state.", (), ())
    with pytest.raises(RejectedPlanError, match="DAG-terminal Task") as failure:
        planner.plan("mission-generic", intent, _current_catalog(), grounding)
    assert failure.value.stage == "plan_validation"
    assert failure.value.provider_output is not initial
    plan = planner.regenerate(
        "mission-generic",
        intent,
        _current_catalog(),
        grounding,
        failure.value.provider_output,
        [{"stage": "plan_validation", "message": str(failure.value)}],
    )
    reviewer = ResponsesMissionReviewer(settings, {"OPENAI_API_KEY": "test-only-key"}, transport)
    assert reviewer.review(intent, plan, _current_catalog(), grounding).approved
    assert len(transport.requests) == 3
    inputs = [json.loads(cast(str, request[2]["input"])) for request in transport.requests]
    for value in inputs:
        policy_input = value["satisfaction_policy"]
        goal_input = value["authoritative_semantic_goal"]
        assert policy_input["authoritative_goal_confirmation"]["task_scope"] == (
            "all-dag-terminal-tasks"
        )
        assert goal_input["required_verifier_predicate"] == _PREDICATE
    assert inputs[1]["prevalidation_recovery_feedback"]["previous_rejected_provider_output"] == (
        failure.value.provider_output
    )
    assert inputs[0]["grounding_context"] == inputs[1]["grounding_context"]
    assert inputs[1]["grounding_context"] == inputs[2]["grounding_context"]


def test_reviewer_rejects_policy_bypass_before_model_call() -> None:
    """A directly supplied report-only draft cannot bypass the same policy check."""
    transport = FakeTransport([])
    reviewer = ResponsesMissionReviewer(_settings(), {"OPENAI_API_KEY": "test-only-key"}, transport)
    raw = deepcopy(_plan(verified=(False, False)))
    with pytest.raises(MissionPlanError, match="DAG-terminal Task"):
        reviewer.review(
            GroundedIntent("Establish both target conditions at the joint terminal state.", (), ()),
            MissionPlan.from_json(raw),
            _current_catalog(),
            _grounding(),
        )
    assert transport.requests == []
