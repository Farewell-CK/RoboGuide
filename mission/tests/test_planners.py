"""Offline tests for fixture and Responses-compatible Mission planners."""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from mission.capability_catalog import CanonicalCapabilityCatalog, CapabilityCatalogError
from mission.config import MissionSettings, load_settings
from mission.grounding_context import (
    PHYSICAL_ENTITY_REFERENCE_SCHEMA,
    GroundingContextSnapshot,
    GroundingFreshness,
    StateGroundingEvidence,
    grounding_selection_policy_ref,
)
from mission.grounding_reader import EmptyMissionGroundingReader
from mission.intent import GroundedIntent
from mission.models import JSONObject, JSONValue, MissionPlan
from mission.planners import FixturePlanner
from mission.provider_mission_plan import (
    ProviderMissionPlanError,
    normalize_mission_plan_provider_output,
)
from mission.request_record import DialogueSpeaker, DialogueTurn, DialogueTurnKind
from mission.responses import (
    ResponsesMissionInterpreter,
    ResponsesMissionPlanner,
    ResponsesMissionRepairer,
    ResponsesMissionReviewer,
)
from mission.review import MissionPlanReview, ReviewIssueAction
from mission.semantic_evidence import AuthoritativeSemanticEvidence, SemanticExpression

FIXTURE = Path("scenarios/phase1-mission-v0.3/mission-plan.json")
CATALOG = Path("contracts/capability/v0.1/catalog.json")
CURRENT_FIXTURE = Path("scenarios/mission-front-half-v0.7/mission-plan.json")
CURRENT_CATALOG = Path("contracts/capability/v0.3/catalog.json")
CURRENT_SCHEMA = Path("contracts/mission/v0.8/mission-plan.schema.json")


def _response(output: JSONObject) -> JSONObject:
    """Wrap structured output in the minimal completed Responses payload shape."""
    return {
        "status": "completed",
        "error": None,
        "output": [
            {
                "type": "message",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(output, ensure_ascii=False),
                    }
                ],
            }
        ],
    }


class FakeTransport:
    """Return scripted provider responses and retain inspectable requests."""

    def __init__(self, responses: list[JSONObject]) -> None:
        """Initialize a finite response queue used without network access."""
        self.responses = responses
        self.requests: list[tuple[str, Mapping[str, str], JSONObject, float]] = []

    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: JSONObject,
        timeout_seconds: float,
    ) -> JSONObject:
        """Record a request and return the next scripted response."""
        self.requests.append((url, headers, payload, timeout_seconds))
        if not self.responses:
            raise AssertionError("fake provider response queue is empty")
        return self.responses.pop(0)


def _local_settings() -> MissionSettings:
    """Build repository settings with a safe localhost provider for offline tests."""
    settings = load_settings(Path("config/mission.toml"), repository_root=Path.cwd())
    return replace(
        settings,
        provider=replace(settings.provider, base_url="http://127.0.0.1:8080"),
    )


def _catalog() -> CanonicalCapabilityCatalog:
    """Load the checked-in semantic vocabulary used by deterministic planner tests."""
    return CanonicalCapabilityCatalog.load(CATALOG)


def _current_catalog() -> CanonicalCapabilityCatalog:
    """Load the current split capability and operation Catalog."""
    return CanonicalCapabilityCatalog.load(CURRENT_CATALOG)


def _current_schema() -> JSONObject:
    """Load the canonical MissionPlan schema used by provider-boundary normalization."""
    return cast(JSONObject, json.loads(CURRENT_SCHEMA.read_text(encoding="utf-8")))


def _provider_plan(plan: JSONObject) -> JSONObject:
    """Encode current canonical parameter maps as provider DTO entries for fake responses."""
    encoded = deepcopy(plan)
    if encoded.get("schema_version") not in {
        "roboguide.mission-plan/v0.7",
        "roboguide.mission-plan/v0.8",
    }:
        return encoded
    tasks = cast(list[JSONObject], encoded["tasks"])
    for task in tasks:
        roles = cast(list[JSONObject], task["roles"])
        for role in roles:
            intent = cast(JSONObject, role["execution_intent"])
            parameters = cast(JSONObject, intent["parameters"])
            intent["parameters"] = [
                {"key": key, "value": parameters[key]} for key in sorted(parameters)
            ]
    return encoded


def _v0_8_plan() -> JSONObject:
    """Upgrade a canonical normalized fixture without inferring deployment identities."""
    plan = cast(JSONObject, json.loads(CURRENT_FIXTURE.read_text(encoding="utf-8")))
    plan["schema_version"] = "roboguide.mission-plan/v0.8"
    for context in cast(list[JSONObject], plan["contexts"]):
        context["executor_constraints"] = []
    return plan


def test_v0_8_planner_and_repairer_use_same_closed_provider_boundary() -> None:
    """Both model writers preserve v0.8 Context fields through strict DTO normalization."""
    plan_json = _v0_8_plan()
    transport = FakeTransport([_response(_provider_plan(plan_json))] * 2)
    settings = _local_settings()
    planner = ResponsesMissionPlanner(settings, {"OPENAI_API_KEY": "test-only-key"}, transport)
    repairer = ResponsesMissionRepairer(settings, {"OPENAI_API_KEY": "test-only-key"}, transport)
    mission = cast(JSONObject, plan_json["mission"])
    mission_id = cast(str, mission["id"])
    intent = GroundedIntent(cast(str, mission["objective"]), (), ())
    grounding = _grounding()
    catalog = _current_catalog()

    plan = planner.plan(mission_id, intent, catalog, grounding)
    repaired = repairer.repair(
        mission_id, intent, plan, MissionPlanReview.from_json(_review_output()), catalog, grounding
    )
    assert plan.to_json() == plan_json
    assert repaired == plan
    for _, _, payload, _ in transport.requests:
        output = cast(JSONObject, cast(JSONObject, payload["text"])["format"])
        schema = cast(JSONObject, output["schema"])
        _assert_strict_provider_objects(schema)
        context_schema = cast(JSONObject, cast(JSONObject, schema["$defs"])["context"])
        assert "executor_constraints" in cast(JSONObject, context_schema["properties"])


def test_v0_8_provider_output_cannot_invent_physical_grounding() -> None:
    """A model-provided deployment identity needs exact admitted Grounding evidence."""
    plan_json = _v0_8_plan()
    mission = cast(JSONObject, plan_json["mission"])
    cast(list[JSONObject], mission["actors"])[0]["physical_entity"] = "made-up-robot"
    transport = FakeTransport([_response(_provider_plan(plan_json))])
    planner = ResponsesMissionPlanner(
        _local_settings(), {"OPENAI_API_KEY": "test-only-key"}, transport
    )
    with pytest.raises(ValueError, match="unadmitted physical entity"):
        planner.plan(
            cast(str, mission["id"]),
            GroundedIntent(cast(str, mission["objective"]), (), ()),
            _current_catalog(),
            _grounding(),
        )


@pytest.mark.parametrize("freshness", [GroundingFreshness.FRESH, GroundingFreshness.STALE])
def test_v0_8_grounded_actor_requires_admitted_fresh_entity_reference(
    freshness: GroundingFreshness,
) -> None:
    """Only exact deployment identities carried in fresh attributed evidence can be named."""
    plan_json = _v0_8_plan()
    mission = cast(JSONObject, plan_json["mission"])
    cast(list[JSONObject], mission["actors"])[0]["physical_entity"] = "entity-courier"
    grounding = GroundingContextSnapshot.create(
        request_id="request-test",
        dialogue_digest=_grounding().dialogue_digest,
        captured_at_ms=20,
        selection_policy_ref=grounding_selection_policy_ref(
            frozenset({PHYSICAL_ENTITY_REFERENCE_SCHEMA})
        ),
        state_evidence=(
            StateGroundingEvidence(
                evidence_id="state-entity-courier",
                object_type="physical-entity",
                object_id="entity-courier",
                semantic="observed",
                source="admitted-deployment-world",
                channel_id="entity-reference",
                payload_schema=PHYSICAL_ENTITY_REFERENCE_SCHEMA,
                value={"entity_id": "entity-courier"},
                source_observed_at_ms=None,
                received_at_ms=10,
                valid_for_ms=20,
                freshness=freshness,
                confidence_millionths=None,
                source_epoch=None,
                sequence=1,
            ),
        ),
    )
    transport = FakeTransport([_response(_provider_plan(plan_json))])
    planner = ResponsesMissionPlanner(
        _local_settings(), {"OPENAI_API_KEY": "test-only-key"}, transport
    )
    intent = GroundedIntent(cast(str, mission["objective"]), (), ())
    if freshness is GroundingFreshness.STALE:
        with pytest.raises(ValueError, match="unadmitted physical entity"):
            planner.plan(cast(str, mission["id"]), intent, _current_catalog(), grounding)
    else:
        assert (
            planner.plan(cast(str, mission["id"]), intent, _current_catalog(), grounding)
            .mission.actors[0]
            .physical_entity
            == "entity-courier"
        )


def _first_role(plan: JSONObject) -> JSONObject:
    """Return the first role from a mutable deterministic MissionPlan fixture."""
    tasks = cast(list[JSONObject], plan["tasks"])
    roles = cast(list[JSONObject], tasks[0]["roles"])
    return roles[0]


def _assert_strict_provider_objects(value: object, path: str = "schema") -> None:
    """Assert every object schema has the provider-required closed field contract."""
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_strict_provider_objects(item, f"{path}[{index}]")
        return
    if not isinstance(value, dict):
        return
    if value.get("type") == "object":
        properties = value.get("properties")
        assert isinstance(properties, dict), path
        assert value.get("additionalProperties") is False, path
        required = value.get("required")
        assert isinstance(required, list), path
        assert set(required) == set(properties), path
    for key, item in value.items():
        _assert_strict_provider_objects(item, f"{path}.{key}")


def _grounding(
    dialogue: tuple[DialogueTurn, ...] = (),
) -> GroundingContextSnapshot:
    """Build one attributed empty snapshot for deterministic provider tests."""
    return EmptyMissionGroundingReader().capture("request-test", dialogue, 10)


def _semantic_grounding() -> GroundingContextSnapshot:
    """Build a fixed joint-goal snapshot for Reviewer/Repairer input assertions."""
    evidence = AuthoritativeSemanticEvidence.create(
        run_id="run-test",
        episode_id="episode-51",
        revision="goal-1",
        dataset_revision="dataset-1",
        dataset_sha256="a" * 64,
        goal=SemanticExpression.logical(
            "and",
            (
                SemanticExpression.predicate("at", ("target-a",)),
                SemanticExpression.predicate("at", ("target-b",)),
            ),
        ),
        world_context={"scene_id": "scene-51", "agent_ids": [0, 1]},
    )
    return GroundingContextSnapshot.create(
        request_id="request-test",
        dialogue_digest="sha256:" + "0" * 64,
        captured_at_ms=10,
        semantic_evidence=evidence,
    )


def _review_output(action: str = "RepairPlan") -> JSONObject:
    """Build one strict rejected-review provider payload for adapter tests."""
    return {
        "approved": False,
        "issues": [
            {
                "code": "task.over_decomposed",
                "path": "/tasks/0",
                "message": "The Task exposes Local How.",
                "required_action": action,
            }
        ],
    }


def test_fixture_planner_loads_the_approved_plan() -> None:
    """The deterministic planner returns the approved artifact for the exact request."""
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    mission = raw["mission"]
    plan = FixturePlanner(FIXTURE).plan(
        mission["id"], GroundedIntent(mission["objective"], (), ()), _catalog(), _grounding()
    )
    assert plan.mission.mission_id == "mission-phase1-001"


def test_fixture_planner_rejects_unrepresented_grounding_facts() -> None:
    """A fixture cannot silently claim that unknown constraints are encoded in its plan."""
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    mission = raw["mission"]
    with pytest.raises(ValueError, match="cannot prove"):
        FixturePlanner(FIXTURE).plan(
            mission["id"],
            GroundedIntent(mission["objective"], ("keep the marked aisle clear",), ()),
            _catalog(),
            _grounding(),
        )


def test_responses_planner_uses_strict_output_without_hiding_review() -> None:
    """The Planner returns one validated draft without invoking semantic Review internally."""
    plan_json = cast(JSONObject, json.loads(CURRENT_FIXTURE.read_text(encoding="utf-8")))
    transport = FakeTransport([_response(_provider_plan(plan_json))])
    settings = _local_settings()
    planner = ResponsesMissionPlanner(
        settings,
        {"OPENAI_API_KEY": "test-only-key"},
        transport,
    )
    mission = cast(JSONObject, plan_json["mission"])
    mission_id = cast(str, mission["id"])
    mission_objective = cast(str, mission["objective"])
    grounded_intent = GroundedIntent(
        mission_objective,
        ("keep the marked aisle clear",),
        ("the payload remains available at the pickup point",),
    )

    capability_catalog = _current_catalog()
    grounding = _grounding()
    plan = planner.plan(mission_id, grounded_intent, capability_catalog, grounding)

    assert plan.to_json() == plan_json
    assert len(transport.requests) == 1
    planning_payload = transport.requests[0][2]
    assert planning_payload["model"] == "gpt-5.6-luna"
    assert (
        planning_payload["instructions"]
        == Path("mission/prompts/v0/planner.md").read_text(encoding="utf-8").strip()
    )
    assert planning_payload["store"] is False
    planning_input = json.loads(cast(str, planning_payload["input"]))
    assert planning_input == {
        "mission_id": mission_id,
        "grounded_intent": grounded_intent.to_json(),
        "satisfaction_policy": settings.satisfaction_policy.to_json()
        if settings.satisfaction_policy is not None
        else None,
        "capability_catalog": capability_catalog.to_json(),
        "grounding_context": grounding.to_json(),
    }
    text_config = planning_payload["text"]
    assert isinstance(text_config, dict)
    output_format = text_config["format"]
    assert isinstance(output_format, dict)
    assert output_format["strict"] is True
    provider_schema = output_format["schema"]
    assert isinstance(provider_schema, dict)
    assert "$schema" not in provider_schema
    _assert_strict_provider_objects(provider_schema)
    definitions = cast(JSONObject, provider_schema["$defs"])
    tasks_schema = cast(JSONObject, definitions["task"])
    task_properties = cast(JSONObject, tasks_schema["properties"])
    depends_on_schema = cast(JSONObject, task_properties["depends_on"])
    assert "uniqueItems" not in depends_on_schema
    contexts_schema = cast(JSONObject, definitions["context"])
    context_properties = cast(JSONObject, contexts_schema["properties"])
    assert set(cast(list[str], contexts_schema["required"])) == set(context_properties)
    shared_view_schema = cast(JSONObject, context_properties["shared_view"])
    assert {"type": "null"} in cast(list[JSONObject], shared_view_schema["anyOf"])
    relation_schema = cast(JSONObject, definitions["relation"])
    relation_properties = cast(JSONObject, relation_schema["properties"])
    assert "allOf" not in relation_schema
    assert set(cast(list[str], relation_schema["required"])) == set(relation_properties)
    state_key_schema = cast(JSONObject, relation_properties["state_key"])
    assert {"type": "null"} in cast(list[JSONObject], state_key_schema["anyOf"])
    role_schema = cast(JSONObject, definitions["role"])
    role_properties = cast(JSONObject, role_schema["properties"])
    intent_schema = cast(JSONObject, role_properties["execution_intent"])
    intent_properties = cast(JSONObject, intent_schema["properties"])
    parameters_schema = cast(JSONObject, intent_properties["parameters"])
    assert parameters_schema["type"] == "array"
    parameter_entry = cast(JSONObject, parameters_schema["items"])
    assert parameter_entry["additionalProperties"] is False
    assert set(cast(list[str], parameter_entry["required"])) == {"key", "value"}


def test_responses_reviewer_returns_structured_findings_with_independent_model() -> None:
    """The Reviewer uses its configured model and never mutates the examined MissionPlan."""
    plan_json = json.loads(FIXTURE.read_text(encoding="utf-8"))
    transport = FakeTransport([_response(_review_output())])
    settings = _local_settings()
    settings = replace(settings, llm=replace(settings.llm, review_model="reviewer-model"))
    reviewer = ResponsesMissionReviewer(
        settings,
        {"OPENAI_API_KEY": "test-only-key"},
        transport,
    )
    mission = plan_json["mission"]
    plan = MissionPlan.from_json(plan_json)
    grounded_intent = GroundedIntent(mission["objective"], ("avoid stairs",), ())

    grounding = _grounding()
    review = reviewer.review(grounded_intent, plan, _catalog(), grounding)

    assert review.approved is False
    assert review.issues[0].required_action is ReviewIssueAction.REPAIR_PLAN
    payload = transport.requests[0][2]
    assert payload["model"] == "reviewer-model"
    assert (
        payload["instructions"]
        == Path("mission/prompts/v0/reviewer.md").read_text(encoding="utf-8").strip()
    )
    assert json.loads(cast(str, payload["input"])) == {
        "grounded_intent": grounded_intent.to_json(),
        "satisfaction_policy": settings.satisfaction_policy.to_json()
        if settings.satisfaction_policy is not None
        else None,
        "mission_plan": plan_json,
        "capability_catalog": _catalog().to_json(),
        "grounding_context": grounding.to_json(),
    }


def test_reviewer_and_repairer_receive_frozen_joint_goal_guidance() -> None:
    """Reviewer and Repairer receive the exact joint goal without changing MissionPlan fields."""
    plan_json = _v0_8_plan()
    grounding = _semantic_grounding()
    settings = _local_settings()
    reviewer_transport = FakeTransport([_response(_review_output())])
    reviewer = ResponsesMissionReviewer(
        settings,
        {"OPENAI_API_KEY": "test-only-key"},
        reviewer_transport,
    )
    plan = MissionPlan.from_json(plan_json)
    intent = GroundedIntent(cast(str, cast(JSONObject, plan_json["mission"])["objective"]), (), ())
    reviewer.review(intent, plan, _current_catalog(), grounding)
    reviewer_input = json.loads(cast(str, reviewer_transport.requests[0][2]["input"]))

    repair_transport = FakeTransport([_response(_provider_plan(plan_json))])
    repairer = ResponsesMissionRepairer(
        settings,
        {"OPENAI_API_KEY": "test-only-key"},
        repair_transport,
    )
    repairer.repair(
        cast(str, cast(JSONObject, plan_json["mission"])["id"]),
        intent,
        plan,
        MissionPlanReview.from_json(_review_output()),
        _current_catalog(),
        grounding,
    )
    repair_input = json.loads(cast(str, repair_transport.requests[0][2]["input"]))

    for payload in (reviewer_input, repair_input):
        guidance = payload["authoritative_semantic_goal"]
        assert guidance["objective_scope"] == "joint_terminal_state"
        assert guidance["goal"]["operator"] == "and"
        assert len(guidance["goal"]["operands"]) == 2
        assert guidance["review_requirements"]["preserve_logical_tree"] is True


def test_responses_repairer_receives_exact_rejection_and_returns_complete_plan() -> None:
    """Repair input preserves rejected evidence and output passes the normal draft gates."""
    plan_json = cast(JSONObject, json.loads(CURRENT_FIXTURE.read_text(encoding="utf-8")))
    transport = FakeTransport([_response(_provider_plan(plan_json))])
    settings = _local_settings()
    repairer = ResponsesMissionRepairer(
        settings,
        {"OPENAI_API_KEY": "test-only-key"},
        transport,
    )
    plan = MissionPlan.from_json(plan_json)
    mission = cast(JSONObject, plan_json["mission"])
    mission_id = cast(str, mission["id"])
    grounded_intent = GroundedIntent(cast(str, mission["objective"]), (), ())
    review = MissionPlanReview.from_json(_review_output())

    grounding = _grounding()
    repaired = repairer.repair(
        mission_id,
        grounded_intent,
        plan,
        review,
        _current_catalog(),
        grounding,
    )

    assert repaired == plan
    payload = transport.requests[0][2]
    assert (
        payload["instructions"]
        == Path("mission/prompts/v0/repairer.md").read_text(encoding="utf-8").strip()
    )
    assert json.loads(cast(str, payload["input"])) == {
        "mission_id": mission_id,
        "satisfaction_policy": settings.satisfaction_policy.to_json()
        if settings.satisfaction_policy is not None
        else None,
        "grounded_intent": grounded_intent.to_json(),
        "rejected_plan": plan_json,
        "review": review.to_json(),
        "capability_catalog": _current_catalog().to_json(),
        "grounding_context": grounding.to_json(),
    }


def test_provider_parameter_entries_preserve_all_canonical_scalar_types() -> None:
    """Provider DTO normalization preserves bool, integer, float, and string values."""
    plan_json = cast(JSONObject, json.loads(CURRENT_FIXTURE.read_text(encoding="utf-8")))
    role = _first_role(plan_json)
    intent = cast(JSONObject, role["execution_intent"])
    expected: JSONObject = {
        "enabled": True,
        "attempts": 2,
        "threshold": 1.25,
        "destination": "reception-1f",
    }
    intent["parameters"] = expected

    normalized = normalize_mission_plan_provider_output(
        _provider_plan(plan_json), _current_schema()
    )
    plan = MissionPlan.from_json(normalized)

    assert dict(plan.tasks[0].roles[0].execution.parameters) == expected


def test_provider_parameter_entries_allow_an_empty_canonical_map() -> None:
    """An empty provider entry list normalizes to a valid empty canonical parameter map."""
    plan_json = cast(JSONObject, json.loads(CURRENT_FIXTURE.read_text(encoding="utf-8")))
    role = _first_role(plan_json)
    intent = cast(JSONObject, role["execution_intent"])
    intent["parameters"] = {}

    normalized = normalize_mission_plan_provider_output(
        _provider_plan(plan_json), _current_schema()
    )
    plan = MissionPlan.from_json(normalized)

    assert plan.tasks[0].roles[0].execution.parameters == ()


def test_provider_output_normalizes_before_canonical_and_catalog_validation() -> None:
    """Current DTO output becomes canonical v0.7 before all existing validation gates."""
    plan_json = cast(JSONObject, json.loads(CURRENT_FIXTURE.read_text(encoding="utf-8")))

    normalized = normalize_mission_plan_provider_output(
        _provider_plan(plan_json), _current_schema()
    )
    plan = MissionPlan.from_json(normalized)
    plan.validate_implementation_support()
    _current_catalog().validate_plan(plan)

    assert plan.to_json() == plan_json


def test_provider_output_with_unknown_catalog_parameter_fails_closed() -> None:
    """DTO normalization cannot bypass the Catalog's closed operation parameter set."""
    plan_json = cast(JSONObject, json.loads(CURRENT_FIXTURE.read_text(encoding="utf-8")))
    role = _first_role(plan_json)
    intent = cast(JSONObject, role["execution_intent"])
    parameters = cast(JSONObject, intent["parameters"])
    parameters["unsupported"] = "must-fail"

    normalized = normalize_mission_plan_provider_output(
        _provider_plan(plan_json), _current_schema()
    )
    plan = MissionPlan.from_json(normalized)

    with pytest.raises(CapabilityCatalogError, match="unsupported"):
        _current_catalog().validate_plan(plan)


@pytest.mark.parametrize(
    "malformed",
    [
        [{"key": "destination"}],
        [{"key": "destination", "value": "reception-1f", "extra": True}],
        [{"key": "destination", "value": ["reception-1f"]}],
        [
            {"key": "destination", "value": "reception-1f"},
            {"key": "destination", "value": "other"},
        ],
    ],
)
def test_malformed_provider_parameter_entry_fails_closed(malformed: list[JSONValue]) -> None:
    """Malformed, structured, and duplicate parameter entries never reach canonical parsing."""
    plan_json = cast(JSONObject, json.loads(CURRENT_FIXTURE.read_text(encoding="utf-8")))
    provider_plan = _provider_plan(plan_json)
    role = _first_role(provider_plan)
    intent = cast(JSONObject, role["execution_intent"])
    intent["parameters"] = malformed

    with pytest.raises(ProviderMissionPlanError):
        normalize_mission_plan_provider_output(provider_plan, _current_schema())


def test_responses_planner_rejects_unknown_contract_before_review() -> None:
    """Deterministic Catalog admission prevents an invented contract reaching Reviewer."""
    plan_json = cast(JSONObject, json.loads(CURRENT_FIXTURE.read_text(encoding="utf-8")))
    role = _first_role(plan_json)
    invented: JSONObject = {
        "namespace": "delivery",
        "name": "magic_move",
        "version": "v1",
    }
    requirements = cast(JSONObject, role["requirements"])
    capabilities = cast(list[JSONObject], requirements["capabilities"])
    capabilities[0]["contract"] = invented
    intent = cast(JSONObject, role["execution_intent"])
    intent["operation"] = invented
    transport = FakeTransport([_response(_provider_plan(plan_json))])
    planner = ResponsesMissionPlanner(
        _local_settings(),
        {"OPENAI_API_KEY": "test-only-key"},
        transport,
    )

    mission = cast(JSONObject, plan_json["mission"])
    with pytest.raises(CapabilityCatalogError, match="delivery.magic_move@v1"):
        planner.plan(
            cast(str, mission["id"]),
            GroundedIntent(cast(str, mission["objective"]), (), ()),
            _current_catalog(),
            _grounding(),
        )

    assert len(transport.requests) == 1


def test_responses_interpreter_preserves_open_questions_before_planning() -> None:
    """Structured interpretation exposes ambiguity without inventing a Task Graph."""
    output: JSONObject = {
        "objective": "让一只可建图的机器狗建立地图",
        "constraints": [],
        "assumptions": [],
        "open_questions": ["需要建立哪个区域的地图？"],
    }
    transport = FakeTransport([_response(output)])
    interpreter = ResponsesMissionInterpreter(
        _local_settings(), {"OPENAI_API_KEY": "test-only-key"}, transport
    )
    dialogue = (
        DialogueTurn(
            "turn-0001",
            DialogueSpeaker.USER,
            DialogueTurnKind.INSTRUCTION,
            "一只可建图的机器狗",
            1,
        ),
    )
    grounding = _grounding(dialogue)
    assessment = interpreter.interpret(dialogue, grounding)

    assert assessment.open_questions == ("需要建立哪个区域的地图？",)
    assert len(transport.requests) == 1
    request_input = json.loads(cast(str, transport.requests[0][2]["input"]))
    assert request_input == {
        "dialogue": [dialogue[0].to_json()],
        "grounding_context": grounding.to_json(),
    }
    response_format = cast(JSONObject, cast(JSONObject, transport.requests[0][2]["text"])["format"])
    schema = cast(JSONObject, response_format["schema"])
    properties = cast(JSONObject, schema["properties"])
    assert "blocking" in cast(str, cast(JSONObject, properties["open_questions"])["description"])
