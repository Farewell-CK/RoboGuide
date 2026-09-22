"""Offline coordination guidance, provider delivery, and unchanged contract fences."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import cast

import pytest
from mission.contract_values import MissionPlanError
from mission.grounding_context import (
    PHYSICAL_ENTITY_REFERENCE_SCHEMA,
    GroundingContextSnapshot,
    GroundingFreshness,
    StateGroundingEvidence,
    grounding_selection_policy_ref,
)
from mission.intent import GroundedIntent
from mission.models import JSONObject, JSONValue, MissionPlan
from mission.rejected_draft import RejectedPlanError
from mission.responses import (
    ResponsesMissionPlanner,
    ResponsesMissionRepairer,
    ResponsesMissionReviewer,
)
from mission.review import MissionPlanReview
from mission.semantic_evidence import AuthoritativeSemanticEvidence, SemanticExpression
from test_planners import (
    FakeTransport,
    _current_catalog,
    _grounding,
    _local_settings,
    _provider_plan,
    _response,
    _review_output,
    _semantic_grounding,
)


def _coordination_plan(*, cooperative: bool = True) -> JSONObject:
    """Declare observation/inference with an optional, explicit active-observer dependency."""
    objective = (
        "Infer a result while the safety observer remains active."
        if cooperative
        else "Observe safety conditions and produce an inference result."
    )
    tasks: list[JSONValue] = []
    for task_id, namespace, operation, parameters in (
        ("watch", "safety", "observe", {"mode": "continuous"}),
        ("infer", "compute", "infer", {"model": "inspection"}),
    ):
        contract: JSONObject = {"namespace": namespace, "name": operation, "version": "v1"}
        tasks.append(
            {
                "id": task_id,
                "description": objective,
                "context_id": "inspection",
                "depends_on": [],
                "roles": [
                    {
                        "id": task_id,
                        "context_role": task_id,
                        "requirements": {
                            "capabilities": [{"contract": contract, "constraints": []}],
                            "resources": [],
                        },
                        "execution_intent": {
                            "operation": contract,
                            "objective": objective,
                            "parameters": cast(JSONObject, parameters),
                        },
                        "resource_scope": "task",
                    }
                ],
                "timing": {
                    "earliest_start_offset_ms": 0,
                    "latest_start_offset_ms": None,
                    "completion_deadline_offset_ms": None,
                },
                "satisfaction": {
                    "expected_effect": objective,
                    "basis": "execution-report",
                    "verifier": None,
                },
            }
        )
    context: JSONObject = {
        "id": "inspection",
        "roles": [{"id": role, "actor": role} for role in ("watch", "infer")],
        "relations": [],
        "executor_constraints": [],
        "coupling_mode": "independent",
    }
    if cooperative:
        context.update(
            coupling_mode="concurrent-cooperation",
            shared_view={
                "bindings": [{"context_role_id": "watch", "field": "execution"}],
                "include_freshness": True,
            },
            relations=[
                {
                    "id": "observer-active",
                    "kind": "requires-active",
                    "source": {"task_id": "watch", "role_id": "watch"},
                    "target": {"task_id": "infer", "role_id": "infer"},
                }
            ],
        )
    return {
        "schema_version": "roboguide.mission-plan/v0.8",
        "mission": {
            "id": "mission-inspection",
            "objective": objective,
            "actors": [{"id": role} for role in ("watch", "infer")],
        },
        "contexts": [context],
        "tasks": tasks,
    }


def _context(raw: JSONObject) -> JSONObject:
    """Return the sole synthetic Context for precise contract mutations."""
    return cast(list[JSONObject], raw["contexts"])[0]


def _intent(raw: JSONObject) -> GroundedIntent:
    """Preserve the synthetic Mission objective at the provider boundary."""
    return GroundedIntent(cast(str, cast(JSONObject, raw["mission"])["objective"]), (), ())


def _physical_entity_evidence(entity_id: str, sequence: int) -> StateGroundingEvidence:
    """Create one fresh deployment identity admitted into an immutable snapshot."""
    return StateGroundingEvidence(
        evidence_id=f"state-{entity_id}",
        object_type="physical-entity",
        object_id=entity_id,
        semantic="observed",
        source="test-deployment-world",
        channel_id="entity-reference",
        payload_schema=PHYSICAL_ENTITY_REFERENCE_SCHEMA,
        value={"entity_id": entity_id},
        source_observed_at_ms=None,
        received_at_ms=10,
        valid_for_ms=20,
        freshness=GroundingFreshness.FRESH,
        confidence_millionths=None,
        source_epoch=None,
        sequence=sequence,
    )


def _entity_grounded_semantics(
    goal_arguments: tuple[str, str] = ("entity-observer", "entity-inference"),
) -> GroundingContextSnapshot:
    """Bind two explicit physical identities into one authoritative objective."""
    entities = ("entity-observer", "entity-inference")
    evidence = AuthoritativeSemanticEvidence.create(
        run_id="run-entities",
        episode_id="episode-entities",
        revision="goal-entities",
        dataset_revision="dataset-entities",
        dataset_sha256="b" * 64,
        goal=SemanticExpression.logical(
            "and",
            tuple(
                SemanticExpression.predicate("assigned", (argument,)) for argument in goal_arguments
            ),
        ),
        world_context={"scene_id": "scene-entities", "agent_ids": [0, 1]},
    )
    return GroundingContextSnapshot.create(
        request_id="request-test",
        dialogue_digest="sha256:" + "0" * 64,
        captured_at_ms=10,
        selection_policy_ref=grounding_selection_policy_ref(
            frozenset({PHYSICAL_ENTITY_REFERENCE_SCHEMA})
        ),
        state_evidence=tuple(
            _physical_entity_evidence(entity, index)
            for index, entity in enumerate(entities, start=1)
        ),
        semantic_evidence=evidence,
    )


def _guidance(instructions: str) -> str:
    """Extract shared semantics before each role-specific instruction section."""
    return (
        instructions.split("## Coordination mode and Group shared view\n", 1)[1]
        .split("\n## ", 1)[0]
        .strip()
    )


def test_all_deliberation_paths_receive_the_same_coordination_contract() -> None:
    """All four adapter paths deliver identical semantics without claiming model adherence."""
    raw = _coordination_plan()
    transport = FakeTransport(
        [
            _response(_provider_plan(raw)),
            _response(_provider_plan(raw)),
            _response({"approved": True, "issues": []}),
            _response(_provider_plan(raw)),
        ]
    )
    settings = _local_settings()
    planner = ResponsesMissionPlanner(settings, {"OPENAI_API_KEY": "test-only-key"}, transport)
    repairer = ResponsesMissionRepairer(settings, {"OPENAI_API_KEY": "test-only-key"}, transport)
    intent, catalog, grounding = _intent(raw), _current_catalog(), _grounding()
    plan = planner.plan("mission-inspection", intent, catalog, grounding)
    previous = _provider_plan(raw)
    _context(previous).pop("shared_view")
    errors: list[JSONObject] = [{"stage": "plan_validation", "message": "missing shared view"}]
    assert (
        planner.regenerate("mission-inspection", intent, catalog, grounding, previous, errors)
        == plan
    )
    reviewer = ResponsesMissionReviewer(settings, {"OPENAI_API_KEY": "test-only-key"}, transport)
    assert reviewer.review(intent, plan, catalog, grounding).approved
    assert (
        repairer.repair(
            "mission-inspection",
            intent,
            plan,
            MissionPlanReview.from_json(_review_output()),
            catalog,
            grounding,
        )
        == plan
    )
    instructions = [cast(str, request[2]["instructions"]) for request in transport.requests]
    assert instructions[0] == instructions[1]
    assert len({_guidance(text) for text in instructions}) == 1
    rules = " ".join(_guidance(instructions[0]).split())
    for requirement in (
        "It does not mean serial execution",
        "Tasks without DAG dependencies may run in parallel",
        "Control coordinates resource competition",
        "`sequential-handoff` expresses a real before/after handoff",
        "a legal `shared_view`, and at least one semantically justified execution `relation`",
        "a valid `peer_channel`, and at least two ContextRoles",
        "A Task-level `independent` override does not remove a Context's required",
        "`execution` bindings expose Runtime logical execution state",
        "They do not provide pose or velocity observations",
        "It cannot substitute for required pose or velocity sharing",
        "explicitly supplied by a trusted state contract in the planning input",
        "Never guess deployment identifiers, use placeholder strings, "
        "or treat null as a valid pose/velocity export declaration",
        "A capability name, robot label, or operation/resource profile "
        "does not supply a State contract",
        "do not invent an export, delete the state requirement, or downgrade "
        "to `independent` or execution-only",
        "Do not query live Node Inventory or choose concrete Nodes, ResourceIds, "
        "or physical robots",
        "Never fabricate relations, exports, or peer contracts",
        "Never remove a genuine dependency or required state observation",
        "a joint terminal-state conjunction alone does not require executions "
        "to stay active together",
        "Parallelism, a previous draft, and a validation error are not such a basis",
        "check whether the source completing while the target is still running "
        "would violate that requirement",
        "not continued physical occupancy or persistence of a completed Task's effect",
        "if the supplied operation contracts support that requirement",
        "all of its effects to coexist at the terminal state",
        "perform an effect-interference check",
        "later operation can invalidate an earlier required effect",
        "grounded requirement or admitted evidence establishes persistence",
        "preserve enough logical participation capacity",
        "does not by itself prove that eventual PhysicalEntity bindings must be distinct",
    ):
        assert requirement in rules
    for instructions_for_role in instructions:
        normalized = " ".join(instructions_for_role.split())
        assert "goal and admitted physical-entity evidence jointly ground" in normalized
    assert "Do not mechanically add a view or relation" in instructions[1]
    assert "Validation may report only the first defect" in instructions[1]
    review_rules = " ".join(instructions[2].split())
    assert "Review semantic necessity separately from structural validity" in review_rules
    assert "use `RejectDraft`: this is a deployment contract gap" in review_rules
    assert "Report all observed blockers together" in review_rules
    assert "invalid mechanism is not an unaffected constraint" in instructions[3]
    inputs = [json.loads(cast(str, request[2]["input"])) for request in transport.requests]
    for model_input in inputs:
        assert model_input["grounded_intent"] == intent.to_json()
        assert model_input["grounding_context"] == grounding.to_json()
    assert inputs[1]["prevalidation_recovery_feedback"] == {
        "previous_rejected_provider_output": previous,
        "validation_errors": errors,
    }


def test_authoritative_goal_rejects_ungrounded_executor_distinctness() -> None:
    """A hard executor constraint cannot strengthen an authoritative joint objective."""
    raw = _coordination_plan(cooperative=False)
    _context(raw)["executor_constraints"] = [
        {
            "kind": "distinct-physical-entities",
            "context_roles": ["watch", "infer"],
        }
    ]
    transport = FakeTransport([_response(_provider_plan(raw))])
    planner = ResponsesMissionPlanner(
        _local_settings(), {"OPENAI_API_KEY": "test-only-key"}, transport
    )
    with pytest.raises(RejectedPlanError, match="lacks authoritative physical-entity grounding"):
        planner.plan(
            "mission-inspection",
            _intent(raw),
            _current_catalog(),
            _semantic_grounding(),
        )


def test_recovery_removes_only_unsupported_authoritative_constraint() -> None:
    """Regeneration can retain Tasks and outcomes while removing unsupported strengthening."""
    rejected = _coordination_plan(cooperative=False)
    _context(rejected)["executor_constraints"] = [
        {
            "kind": "distinct-physical-entities",
            "context_roles": ["watch", "infer"],
        }
    ]
    accepted = _coordination_plan(cooperative=False)
    transport = FakeTransport(
        [_response(_provider_plan(rejected)), _response(_provider_plan(accepted))]
    )
    planner = ResponsesMissionPlanner(
        _local_settings(), {"OPENAI_API_KEY": "test-only-key"}, transport
    )
    intent, catalog, grounding = _intent(rejected), _current_catalog(), _semantic_grounding()
    with pytest.raises(RejectedPlanError) as caught:
        planner.plan("mission-inspection", intent, catalog, grounding)
    recovered = planner.regenerate(
        "mission-inspection",
        intent,
        catalog,
        grounding,
        caught.value.provider_output,
        [{"stage": caught.value.stage, "message": str(caught.value)}],
    )
    assert recovered.contexts[0].executor_constraints == ()
    assert [task.task_id for task in recovered.tasks] == ["watch", "infer"]
    accepted_tasks = cast(list[JSONObject], accepted["tasks"])
    assert [task.satisfaction.expected_effect for task in recovered.tasks] == [
        cast(str, cast(JSONObject, task["satisfaction"])["expected_effect"])
        for task in accepted_tasks
    ]


def test_authoritative_distinctness_accepts_exact_grounded_entities() -> None:
    """Exact goal-bound identities preserve valid physical executor requirements."""
    raw = _coordination_plan(cooperative=False)
    actors = cast(list[JSONObject], cast(JSONObject, raw["mission"])["actors"])
    actors[0]["physical_entity"] = "entity-observer"
    actors[1]["physical_entity"] = "entity-inference"
    _context(raw)["executor_constraints"] = [
        {
            "kind": "distinct-physical-entities",
            "context_roles": ["watch", "infer"],
        }
    ]
    transport = FakeTransport([_response(_provider_plan(raw))])
    planner = ResponsesMissionPlanner(
        _local_settings(), {"OPENAI_API_KEY": "test-only-key"}, transport
    )
    plan = planner.plan(
        "mission-inspection",
        _intent(raw),
        _current_catalog(),
        _entity_grounded_semantics(),
    )
    assert plan.contexts[0].executor_constraints


def test_authoritative_distinctness_rejects_entities_absent_from_goal() -> None:
    """Deployment-known identities alone cannot strengthen an authoritative objective."""
    raw = _coordination_plan(cooperative=False)
    actors = cast(list[JSONObject], cast(JSONObject, raw["mission"])["actors"])
    actors[0]["physical_entity"] = "entity-observer"
    actors[1]["physical_entity"] = "entity-inference"
    _context(raw)["executor_constraints"] = [
        {
            "kind": "distinct-physical-entities",
            "context_roles": ["watch", "infer"],
        }
    ]
    transport = FakeTransport([_response(_provider_plan(raw))])
    planner = ResponsesMissionPlanner(
        _local_settings(), {"OPENAI_API_KEY": "test-only-key"}, transport
    )
    with pytest.raises(RejectedPlanError, match="lacks authoritative physical-entity grounding"):
        planner.plan(
            "mission-inspection",
            _intent(raw),
            _current_catalog(),
            _entity_grounded_semantics(("target-a", "target-b")),
        )


def test_authoritative_distinctness_rejects_duplicate_physical_identity() -> None:
    """Distinct ContextRoles cannot satisfy hard distinctness through one entity."""
    raw = _coordination_plan(cooperative=False)
    actors = cast(list[JSONObject], cast(JSONObject, raw["mission"])["actors"])
    actors[0]["physical_entity"] = "entity-observer"
    actors[1]["physical_entity"] = "entity-observer"
    _context(raw)["executor_constraints"] = [
        {
            "kind": "distinct-physical-entities",
            "context_roles": ["watch", "infer"],
        }
    ]
    transport = FakeTransport([_response(_provider_plan(raw))])
    planner = ResponsesMissionPlanner(
        _local_settings(), {"OPENAI_API_KEY": "test-only-key"}, transport
    )
    with pytest.raises(RejectedPlanError, match="pairwise distinct authoritative entities"):
        planner.plan(
            "mission-inspection",
            _intent(raw),
            _current_catalog(),
            _entity_grounded_semantics(),
        )


def test_non_authoritative_mission_retains_general_distinctness_contract() -> None:
    """The new authority fence does not narrow ordinary reviewed v0.8 Missions."""
    raw = _coordination_plan(cooperative=False)
    _context(raw)["executor_constraints"] = [
        {
            "kind": "distinct-physical-entities",
            "context_roles": ["watch", "infer"],
        }
    ]
    transport = FakeTransport([_response(_provider_plan(raw))])
    planner = ResponsesMissionPlanner(
        _local_settings(), {"OPENAI_API_KEY": "test-only-key"}, transport
    )
    plan = planner.plan("mission-inspection", _intent(raw), _current_catalog(), _grounding())
    assert plan.contexts[0].executor_constraints


@pytest.mark.parametrize("sequential", [False, True])
def test_independent_does_not_add_dag_dependencies(sequential: bool) -> None:
    """Independent plans preserve parallel readiness or explicitly requested sequencing."""
    raw = _coordination_plan(cooperative=False)
    tasks = cast(list[JSONObject], raw["tasks"])
    if sequential:
        tasks[1]["depends_on"] = ["watch"]
    plan = MissionPlan.from_json(raw)
    plan.validate_implementation_support()
    _current_catalog().validate_plan(plan)
    assert plan.to_json() == raw
    assert plan.contexts[0].shared_view is None
    assert tasks[1]["depends_on"] == (["watch"] if sequential else [])


def test_real_active_dependency_allows_execution_only_view() -> None:
    """Runtime execution state suffices for a real active-observer relation, without pose."""
    raw = _coordination_plan()
    plan = MissionPlan.from_json(raw)
    plan.validate_implementation_support()
    _current_catalog().validate_plan(plan)
    view = plan.contexts[0].shared_view
    assert view is not None
    assert view.to_json() == {
        "bindings": [{"context_role_id": "watch", "field": "execution"}],
        "include_freshness": True,
    }
    assert plan.to_json() == raw


@pytest.mark.parametrize(
    "missing", [("shared_view",), ("relations",), ("shared_view", "relations")]
)
def test_cooperation_requires_both_mechanisms(missing: tuple[str, ...]) -> None:
    """Missing view, relation, or both remains invalid even with independent Task overrides."""
    raw = _coordination_plan()
    for key in missing:
        if key == "relations":
            _context(raw)[key] = []
        else:
            _context(raw).pop(key)
    for task in cast(list[JSONObject], raw["tasks"]):
        task["coupling_mode"] = "independent"
    with pytest.raises(
        MissionPlanError, match="requires a Group shared view|requires an execution"
    ):
        MissionPlan.from_json(raw)


@pytest.mark.parametrize("field", ["pose", "velocity"])
@pytest.mark.parametrize("key", ["state_export_id", "payload_schema"])
@pytest.mark.parametrize("value", [None, "", " "])
def test_spatial_binding_rejects_missing_contract(field: str, key: str, value: JSONValue) -> None:
    """Both spatial fields still require both exact nonblank State contract identifiers."""
    raw = _coordination_plan()
    binding: JSONObject = {
        "context_role_id": "watch",
        "field": field,
        "state_export_id": "test-observer-state",
        "payload_schema": "test.observer-state/v1",
    }
    binding[key] = value
    cast(JSONObject, _context(raw)["shared_view"])["bindings"] = [binding]
    with pytest.raises(MissionPlanError, match=key + " must be nonblank text"):
        MissionPlan.from_json(raw)
    binding.pop(key)
    with pytest.raises(MissionPlanError, match=key + " must be nonblank text"):
        MissionPlan.from_json(raw)


def test_missing_state_contract_is_not_rewritten_during_regeneration() -> None:
    """A frozen pose requirement and invalid raw binding survive rejection without hidden repair."""
    raw = _coordination_plan()
    cast(JSONObject, _context(raw)["shared_view"])["bindings"] = [
        {"context_role_id": "watch", "field": "pose"}
    ]
    original = deepcopy(raw)
    intent = GroundedIntent(_intent(raw).objective, ("Share the observer's actual pose.",), ())
    transport = FakeTransport([_response(_provider_plan(raw)) for _ in range(2)])
    planner = ResponsesMissionPlanner(
        _local_settings(), {"OPENAI_API_KEY": "test-only-key"}, transport
    )
    catalog, grounding = _current_catalog(), _grounding()
    with pytest.raises(RejectedPlanError) as first:
        planner.plan("mission-inspection", intent, catalog, grounding)
    with pytest.raises(RejectedPlanError) as second:
        planner.regenerate(
            "mission-inspection",
            intent,
            catalog,
            grounding,
            first.value.provider_output,
            [{"message": str(first.value)}],
        )
    for rejection in (first.value, second.value):
        assert rejection.normalized_output == original
        assert "state_export_id must be nonblank text" in str(rejection)
    for request in transport.requests:
        model_input = json.loads(cast(str, request[2]["input"]))
        assert model_input["grounded_intent"] == intent.to_json()
        assert model_input["grounding_context"] == grounding.to_json()
    assert raw == original


def test_sequential_handoff_does_not_require_concurrent_mechanisms() -> None:
    """A real handoff retains its DAG prerequisite without inventing an active relation."""
    raw = _coordination_plan(cooperative=False)
    _context(raw)["coupling_mode"] = "sequential-handoff"
    cast(list[JSONObject], raw["tasks"])[1]["depends_on"] = ["watch"]
    plan = MissionPlan.from_json(raw)
    plan.validate_implementation_support()
    assert plan.to_json() == raw
    assert plan.contexts[0].shared_view is None and not plan.contexts[0].relations


def test_tightly_coupled_cooperation_still_needs_peer_channel() -> None:
    """Valid view and relation never waive the tight mode's additional peer contract."""
    raw = _coordination_plan()
    _context(raw)["coupling_mode"] = "tightly-coupled-cooperation"
    with pytest.raises(MissionPlanError, match="requires a direct peer channel"):
        MissionPlan.from_json(raw)
    _context(raw)["peer_channel"] = {
        "profile_id": "test-observer-peer",
        "message_schema": "test.observer-peer/v1",
    }
    plan = MissionPlan.from_json(raw)
    plan.validate_implementation_support()
    assert plan.to_json() == raw


@pytest.mark.parametrize("field", ["pose", "velocity"])
def test_declared_state_contract_remains_part_of_the_plan(field: str) -> None:
    """Well-formed state bindings are preserved; this is shape, not deployment availability."""
    raw = _coordination_plan()
    binding: JSONObject = {
        "context_role_id": "watch",
        "field": field,
        "state_export_id": "test-observer-state",
        "payload_schema": "test.observer-state/v1",
    }
    cast(JSONObject, _context(raw)["shared_view"])["bindings"] = [binding]
    plan = MissionPlan.from_json(raw)
    plan.validate_implementation_support()
    assert plan.to_json() == raw


@pytest.mark.parametrize("key", ["state_export_id", "payload_schema"])
def test_execution_view_cannot_claim_a_state_export(key: str) -> None:
    """Execution status cannot be relabeled as a State sensor by attaching an export."""
    raw = _coordination_plan()
    view = cast(JSONObject, _context(raw)["shared_view"])
    cast(list[JSONObject], view["bindings"])[0][key] = "test-observer-state"
    with pytest.raises(MissionPlanError, match="execution field cannot select a State export"):
        MissionPlan.from_json(raw)


def test_dag_ordering_cannot_be_replaced_with_an_active_relation() -> None:
    """A retained active-source relation between sequential Tasks is still contradictory."""
    raw = _coordination_plan()
    cast(list[JSONObject], raw["tasks"])[1]["depends_on"] = ["watch"]
    with pytest.raises(MissionPlanError, match="DAG"):
        MissionPlan.from_json(raw)
