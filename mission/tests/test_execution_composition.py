"""Shared synthetic composition cases preserve effects, bounded recovery and real cooperation."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from mission.execution_planning_evidence import (
    ExecutionPlanningEvidence,
    validate_execution_topology,
)
from mission.grounding_context import GroundingContextSnapshot
from mission.intent import GroundedIntent
from mission.models import CapabilityContractRef, JSONObject, MissionPlan, MissionPlanError
from mission.plan_validation import PlanValidationError
from mission.rejected_draft import RejectedPlanError
from mission.responses import ResponsesMissionPlanner
from mission.satisfaction_policy import MissionSatisfactionPolicy, validate_satisfaction_policy
from mission.semantic_evidence import AuthoritativeSemanticEvidence, SemanticExpression
from test_coordination_guidance import _coordination_plan
from test_execution_planning_evidence import _digest, _evidence
from test_planners import (
    FakeTransport,
    _current_catalog,
    _local_settings,
    _provider_plan,
    _response,
)

FIXTURE = Path("contracts/mission/execution-planning-v0.2/fixtures/regression-cases.json")


def _fixture() -> dict[str, Any]:
    """Return a fresh language-shared synthetic workflow, never an experiment output."""
    return cast(dict[str, Any], json.loads(FIXTURE.read_text()))


def _changed_plan(case: dict[str, Any]) -> JSONObject:
    """Apply only explicit fixture changes; production recovery never uses this helper."""
    plan = _fixture()["plan"]
    for change in case["changes"]:
        components = change["path"].strip("/").split("/")
        parent = plan
        for component in components[:-1]:
            parent = parent[int(component)] if isinstance(parent, list) else parent[component]
        parent[components[-1]] = copy.deepcopy(change["value"])
    return cast(JSONObject, plan)


def _composition_evidence() -> ExecutionPlanningEvidence:
    """Publish the fixture's complete terminal predicate with the real source fences."""
    document = _evidence()[0].to_json()
    source = cast(JSONObject, document["verifier_source"])
    source["supported_predicates"] = [_fixture()["predicate"]]
    source["digest"] = _digest({key: value for key, value in source.items() if key != "digest"})
    document["digest"] = _digest({key: value for key, value in document.items() if key != "digest"})
    return ExecutionPlanningEvidence.from_json(document)


@pytest.mark.parametrize("case", _fixture()["cases"], ids=lambda case: str(case["id"]))
def test_published_composition_cases_preserve_rejected_drafts(case: dict[str, Any]) -> None:
    """Correct phases reuse capacity; false terminal owners and unsupported modes stay rejected."""
    raw = _changed_plan(case)
    original = copy.deepcopy(raw)
    plan = MissionPlan.from_json(raw)
    plan.validate_implementation_support()
    _current_catalog().validate_plan(plan)
    evidence = _composition_evidence()
    issues = evidence.plan_issues(plan)
    assert {issue["code"] for issue in issues} == set(case["expected_codes"])
    assert {issue["path"] for issue in issues} == set(case.get("expected_paths", []))
    if issues:
        with pytest.raises(PlanValidationError) as rejected:
            evidence.validate_plan(plan)
        assert list(rejected.value.validation_errors) == issues
    else:
        evidence.validate_plan(plan)
        assert len(plan.tasks) == 4 and len(plan.mission.actors) == 2
        assert sum(task.satisfaction.basis == "verifier-evidence" for task in plan.tasks) == 2
        assert all(
            task.satisfaction.verifier is None
            or task.satisfaction.verifier.predicate == _fixture()["predicate"]
            for task in plan.tasks
        )
    assert raw == original and plan.to_json() == raw


def test_real_cooperation_cannot_be_downgraded_to_fit_the_deployment() -> None:
    """A real active-observer requirement retains its relation and shared execution view."""
    raw = _coordination_plan()
    original = copy.deepcopy(raw)
    plan = MissionPlan.from_json(raw)
    plan.validate_implementation_support()
    with pytest.raises(PlanValidationError) as rejected:
        _evidence()[0].validate_plan(plan)
    assert {issue["code"] for issue in rejected.value.validation_errors} == {
        "deployment.coupling_mode_unsupported",
        "deployment.context_relations_unsupported",
    }
    assert raw == original and plan.to_json() == raw


@pytest.mark.parametrize("fault", [None, "terminal_local_completion", "partial_goal"])
def test_phase_reuse_retains_the_complete_authoritative_terminal_policy(fault: str | None) -> None:
    """Local prerequisites may release while terminal effects and independent evidence remain."""
    fixture = _fixture()
    raw = cast(JSONObject, fixture["plan"])
    tasks = cast(list[JSONObject], raw["tasks"])
    satisfaction = cast(JSONObject, tasks[1]["satisfaction"])
    if fault == "terminal_local_completion":
        satisfaction.update(basis="execution-report", verifier=None)
    elif fault == "partial_goal":
        cast(JSONObject, satisfaction["verifier"])["predicate"] = "any_at(landmark-a)"
    _, base = _evidence()
    semantic = AuthoritativeSemanticEvidence.create(
        run_id=base.run_id,
        episode_id=base.episode_id,
        revision=base.revision,
        dataset_revision=base.dataset_revision,
        dataset_sha256=base.dataset_sha256,
        goal=SemanticExpression.from_json(fixture["goal_expression"]),
        world_context=base.world_context,
    )
    policy = MissionSatisfactionPolicy(
        "synthetic-composition-policy/v0.1",
        30000,
        CapabilityContractRef("observation", "verify", "v1"),
    )
    plan = MissionPlan.from_json(raw)
    if fault is None:
        validate_satisfaction_policy(plan, policy, semantic)
        assert cast(JSONObject, tasks[0]["satisfaction"])["basis"] == "execution-report"
        assert cast(JSONObject, tasks[2]["satisfaction"])["basis"] == "execution-report"
    else:
        with pytest.raises(MissionPlanError, match="exact authoritative joint goal"):
            validate_satisfaction_policy(plan, policy, semantic)


def test_regeneration_receives_exact_mode_and_lifetime_conflicts_without_rewrite() -> None:
    """One recovery call sees all issues and the original output under the same frozen inputs."""
    fixture = _fixture()
    invalid_case = copy.deepcopy(fixture["cases"][3])
    invalid_case["changes"].append(
        {"path": "/contexts/0/coupling_mode", "value": "sequential-handoff"}
    )
    raw = _changed_plan(invalid_case)
    output = _provider_plan(raw)
    _, semantic = _evidence()
    evidence = _composition_evidence()
    grounding = GroundingContextSnapshot.create(
        request_id="request-composition",
        dialogue_digest="sha256:" + "c" * 64,
        captured_at_ms=100,
        semantic_evidence=semantic,
    )
    transport = FakeTransport([_response(output), _response(output)])
    planner = ResponsesMissionPlanner(
        _local_settings(),
        {"OPENAI_API_KEY": "test-only-key"},
        transport,
        execution_planning_evidence=evidence,
    )
    mission = cast(JSONObject, raw["mission"])
    arguments = (
        cast(str, mission["id"]),
        GroundedIntent(cast(str, mission["objective"]), (), ()),
        _current_catalog(),
        grounding,
    )
    with pytest.raises(RejectedPlanError) as first:
        planner.plan(*arguments)
    feedback = [dict(issue) for issue in first.value.validation_errors]
    assert {issue["code"] for issue in feedback} == {
        "deployment.coupling_mode_unsupported",
        "verifier.retained_capacity_exceeded",
    }
    with pytest.raises(RejectedPlanError) as second:
        planner.regenerate(*arguments, output, feedback)
    assert first.value.provider_output == second.value.provider_output == output
    assert len(transport.requests) == 2
    first_input = json.loads(cast(str, transport.requests[0][2]["input"]))
    second_input = json.loads(cast(str, transport.requests[1][2]["input"]))
    actual_feedback = second_input.pop("prevalidation_recovery_feedback")
    assert actual_feedback["previous_rejected_provider_output"] == output
    assert actual_feedback["validation_errors"] == feedback
    assert first_input == second_input


def test_common_prompt_rules_distinguish_goal_facts_from_task_owners() -> None:
    """All three roles receive identical general composition guidance and no workload template."""
    blocks = []
    for role in ("planner", "reviewer", "repairer"):
        text = Path(f"mission/prompts/v0/{role}.md").read_text()
        block = text.split("## Published topology and effect composition\n", 1)[1].split(
            "## Coordination mode and Group shared view\n", 1
        )[0]
        blocks.append(block)
        assert "Independent\nTasks can have `depends_on` edges" in block
        assert "actively making it hold" in block
        assert "not automatically a separate Task or Actor" in block
        assert "all evidence required by the frozen policy" in block
        assert "operation_effect_composition_review" in text
        assert "not a proven conflict or a physical binding" in text
        assert all(name not in block for name in ("Episode", "Habitat", "EMOS", "Spot", "Fetch"))
    assert len(set(blocks)) == 1


def test_effect_review_distinguishes_contradiction_from_unknown_physical_outcome() -> None:
    """All roles retain true conflicts and continuous needs without inventing extra capacity."""
    sections = []
    for role in ("planner", "reviewer", "repairer"):
        text = Path(f"mission/prompts/v0/{role}.md").read_text()
        section = text.split("A joint terminal-state conjunction still requires", 1)[1].split(
            "If a versioned", 1
        )[0]
        sections.append(section)
        normalized = " ".join(section.split())
        assert "An established conflict needs a supported restoration" in normalized
        assert "absence of a future preservation guarantee alone does not establish" in normalized
        assert (
            "a verifier checks the outcome, it does not repair a known contradiction" in normalized
        )
        assert "Additional logical participation capacity is not itself" in normalized
        assert (
            "A genuine continuous requirement still needs its supported execution contract"
            in normalized
        )
        assert "Sequential reuse is supported only when" not in normalized
    assert len(set(sections)) == 1


@pytest.mark.parametrize("fault", [None, "inventory", "mode", "duplicate", "boolean", "binding"])
def test_topology_json_schema_matches_closed_runtime_fields(fault: str | None) -> None:
    """Published schema and runtime decoding agree on concrete valid and malformed declarations."""
    topology = cast(JSONObject, _fixture()["execution_topology"])
    if fault == "inventory":
        topology["node_id"] = "private-selector"
    elif fault == "mode":
        topology["supported_coupling_modes"] = ["invented"]
    elif fault == "duplicate":
        topology["supported_coupling_modes"] = ["independent", "independent"]
    elif fault == "boolean":
        topology["supports_task_dependencies"] = 1
    elif fault == "binding":
        topology["actor_binding"] = "choose-new-node-after-each-tool"
    schema = json.loads((FIXTURE.parent.parent / "execution-topology.schema.json").read_text())
    errors = list(Draft202012Validator(schema).iter_errors(topology))
    if fault is None:
        assert not errors
        validate_execution_topology(topology)
    else:
        assert errors
        with pytest.raises(ValueError):
            validate_execution_topology(topology)
