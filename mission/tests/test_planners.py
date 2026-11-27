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
from mission.execution_planning_evidence import ExecutionPlanningEvidence
from mission.execution_profile import DeploymentExecutionProfile
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
from mission.planning_profile import DeploymentPlanningProfile
from mission.provider_errors import MissionIdentityError, MissionProviderError
from mission.provider_mission_plan import (
    ProviderMissionPlanError,
    normalize_mission_plan_provider_output,
)
from mission.rejected_draft import RejectedPlanError
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


def _shared_world_execution_profile() -> DeploymentExecutionProfile:
    """Load the checked-in shared-world operation/resource deployment profile."""
    return DeploymentExecutionProfile.load(
        Path("scenarios/e1-shared-world-episode-51/execution-profile.json")
    )


def _execution_planning_evidence() -> ExecutionPlanningEvidence:
    """Load one synthetic deployment artifact without depending on a live run."""
    from test_execution_planning_evidence import _evidence

    return _evidence()[0]


def _execution_planning_evidence_semantic() -> AuthoritativeSemanticEvidence:
    """Return the semantic snapshot bound to the synthetic planning artifact."""
    from test_execution_planning_evidence import _evidence

    return _evidence()[1]


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
        sent_input = json.loads(cast(str, payload["input"]))
        assert sent_input["mission_id"] == mission_id
        output = cast(JSONObject, cast(JSONObject, payload["text"])["format"])
        schema = cast(JSONObject, output["schema"])
        mission_schema = cast(JSONObject, cast(JSONObject, schema["properties"])["mission"])
        mission_properties = cast(JSONObject, mission_schema["properties"])
        assert mission_properties["id"] == {"type": "string", "enum": [mission_id]}
        _assert_strict_provider_objects(schema)
        context_schema = cast(JSONObject, cast(JSONObject, schema["$defs"])["context"])
        assert "executor_constraints" in cast(JSONObject, context_schema["properties"])


@pytest.mark.parametrize("writer", ["planner", "repairer"])
@pytest.mark.parametrize("unadmitted_entity", [False, True])
def test_model_identity_violation_preserves_raw_draft_without_admission(
    writer: str, unadmitted_entity: bool
) -> None:
    """Wrong identity wins over later contract faults and retains the raw candidate."""
    plan_json = _v0_8_plan()
    mission = cast(JSONObject, plan_json["mission"])
    expected_id = cast(str, mission["id"])
    bad_json = deepcopy(plan_json)
    cast(JSONObject, bad_json["mission"])["id"] = "mission-unrequested"
    if unadmitted_entity:
        actors = cast(list[JSONObject], cast(JSONObject, bad_json["mission"])["actors"])
        actors[0]["physical_entity"] = "unadmitted-robot"
    raw = _provider_plan(bad_json)
    transport = FakeTransport([_response(raw)])
    settings = _local_settings()
    intent = GroundedIntent(cast(str, mission["objective"]), (), ())
    catalog = _current_catalog()
    grounding = _grounding()
    with pytest.raises(
        MissionIdentityError, match="model changed the requested mission id"
    ) as caught:
        if writer == "planner":
            ResponsesMissionPlanner(settings, {"OPENAI_API_KEY": "test-only-key"}, transport).plan(
                expected_id, intent, catalog, grounding
            )
        else:
            ResponsesMissionRepairer(
                settings, {"OPENAI_API_KEY": "test-only-key"}, transport
            ).repair(
                expected_id,
                intent,
                MissionPlan.from_json(plan_json),
                MissionPlanReview.from_json(_review_output()),
                catalog,
                grounding,
            )
    error = caught.value
    assert isinstance(error, MissionProviderError)
    assert error.provider_output == raw
    assert cast(JSONObject, error.normalized_output["mission"])["id"] == "mission-unrequested"
    assert error.stage == "identity_validation"
    assert len(transport.requests) == 1
    payload = transport.requests[0][2]
    schema = cast(
        JSONObject, cast(JSONObject, cast(JSONObject, payload["text"])["format"])["schema"]
    )
    mission_schema = cast(JSONObject, cast(JSONObject, schema["properties"])["mission"])
    assert cast(JSONObject, mission_schema["properties"])["id"] == {
        "type": "string",
        "enum": [expected_id],
    }


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


@pytest.mark.parametrize("override", [None, "medium"])
def test_planner_effort_only_reaches_planner_and_regeneration(override: str | None) -> None:
    """Keep the same canonical draft and shared effort for the other MI adapters."""
    fixture = _v0_8_plan()
    mission = cast(JSONObject, fixture["mission"])
    mission_id = cast(str, mission["id"])
    objective = cast(str, mission["objective"])
    output = _provider_plan(fixture)
    transport = FakeTransport(
        [
            _response(
                {"objective": objective, "constraints": [], "assumptions": [], "open_questions": []}
            ),
            _response(output),
            _response(output),
            _response(_review_output()),
            _response(output),
        ]
    )
    original = _local_settings()
    settings = replace(original, llm=replace(original.llm, planner_reasoning_effort=override))
    environment = {"OPENAI_API_KEY": "test-only-key"}
    grounding = _grounding()
    intent = ResponsesMissionInterpreter(settings, environment, transport).interpret((), grounding)
    grounded = intent.grounded_intent()
    catalog = _current_catalog()
    planner = ResponsesMissionPlanner(settings, environment, transport)
    plan = planner.plan(mission_id, grounded, catalog, grounding)
    regenerated = planner.regenerate(
        mission_id, grounded, catalog, grounding, output, [{"message": "fixture rejection"}]
    )
    review = ResponsesMissionReviewer(settings, environment, transport).review(
        grounded, regenerated, catalog, grounding
    )
    repaired = ResponsesMissionRepairer(settings, environment, transport).repair(
        mission_id, grounded, regenerated, review, catalog, grounding
    )
    assert grounded == GroundedIntent(objective, (), ())
    assert plan.to_json() == fixture and plan == regenerated == repaired
    shared = settings.llm.reasoning_effort
    expected_planner = shared if override is None else override
    expected_efforts = (shared, expected_planner, expected_planner, shared, shared)
    assert [payload["reasoning"] for _, _, payload, _ in transport.requests] == [
        {"effort": effort} for effort in expected_efforts
    ]


def _grounding(
    dialogue: tuple[DialogueTurn, ...] = (),
) -> GroundingContextSnapshot:
    """Build one attributed empty snapshot for deterministic provider tests."""
    return EmptyMissionGroundingReader().capture("request-test", dialogue, 10)


def test_planner_service_tier_applies_to_both_planner_attempts() -> None:
    """Keep the tier opt-in scoped to first generation and its structural regeneration."""
    fixture = _v0_8_plan()
    plan = MissionPlan.from_json(fixture)
    output = _provider_plan(fixture)
    objective = plan.mission.objective
    transport = FakeTransport(
        [
            _response(
                {"objective": objective, "constraints": [], "assumptions": [], "open_questions": []}
            ),
            _response(output),
            _response(output),
            _response(_review_output()),
            _response(output),
        ]
    )
    original = _local_settings()
    settings = replace(original, llm=replace(original.llm, planner_service_tier="ultrafast"))
    environment = {"OPENAI_API_KEY": "test-only-key"}
    context = _grounding()
    catalog = _current_catalog()
    assessment = ResponsesMissionInterpreter(settings, environment, transport).interpret(
        (), context
    )
    intent = assessment.grounded_intent()
    mission_id = plan.mission.mission_id
    planner = ResponsesMissionPlanner(settings, environment, transport)
    assert planner.plan(mission_id, intent, catalog, context) == plan
    regenerated = planner.regenerate(
        mission_id, intent, catalog, context, output, [{"message": "fixture rejection"}]
    )
    review = ResponsesMissionReviewer(settings, environment, transport).review(
        intent, regenerated, catalog, context
    )
    repaired = ResponsesMissionRepairer(settings, environment, transport).repair(
        mission_id, intent, regenerated, review, catalog, context
    )
    assert plan == regenerated == repaired
    assert [payload.get("service_tier") for _, _, payload, _ in transport.requests] == [
        None,
        "ultrafast",
        "ultrafast",
        None,
        None,
    ]


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
        == Path("mission/prompts/v0/planner.md").read_bytes().decode("utf-8").strip()
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


@pytest.mark.parametrize("operation", ["move", "navigate"])
@pytest.mark.parametrize("version", ["v0.7", "v0.8"])
def test_shared_world_profile_binds_space_resource_to_model_mobility_roles(
    operation: str, version: str
) -> None:
    """MI binds deployment capacity facts even when the provider omits Role resources."""
    plan_json = cast(
        JSONObject,
        json.loads(Path("scenarios/e1-shared-world-episode-51/mission-plan.json").read_text()),
    )
    plan_json["schema_version"] = f"roboguide.mission-plan/{version}"
    if version == "v0.8":
        for context in cast(list[JSONObject], plan_json["contexts"]):
            context["executor_constraints"] = []
    for task in cast(list[JSONObject], plan_json["tasks"]):
        for role in cast(list[JSONObject], task["roles"]):
            requirements = cast(JSONObject, role["requirements"])
            requirements["resources"] = []
            capability = cast(list[JSONObject], requirements["capabilities"])[0]
            cast(JSONObject, capability["contract"])["name"] = operation
            intent = cast(JSONObject, role["execution_intent"])
            cast(JSONObject, intent["operation"])["name"] = operation
    transport = FakeTransport([_response(_provider_plan(plan_json))])
    settings = _local_settings()
    profile = _shared_world_execution_profile()
    planner = ResponsesMissionPlanner(
        settings,
        {"OPENAI_API_KEY": "test-only-key"},
        transport,
        execution_profile=profile,
    )
    mission = cast(JSONObject, plan_json["mission"])
    plan = planner.plan(
        cast(str, mission["id"]),
        GroundedIntent(cast(str, mission["objective"]), (), ()),
        _current_catalog(),
        _grounding(),
    )

    assert all(
        role.resources[0].kind == "space" and role.resources[0].units == 1
        for task in plan.tasks
        for role in task.roles
    )
    planning_input = json.loads(cast(str, transport.requests[0][2]["input"]))
    assert planning_input["deployment_execution_profile"] == profile.to_json()
    assert plan.to_json()["contexts"] == plan_json["contexts"]
    assert plan.to_json()["mission"] == plan_json["mission"]


def test_shared_world_profile_is_frozen_across_review_and_repair_inputs() -> None:
    """Reviewer and Repairer receive the same deployment profile as the Planner."""
    plan_json = cast(
        JSONObject,
        json.loads(Path("scenarios/e1-shared-world-episode-51/mission-plan.json").read_text()),
    )
    plan = MissionPlan.from_json(plan_json)
    profile = _shared_world_execution_profile()
    settings = _local_settings()
    grounding = _grounding()
    mission = cast(JSONObject, plan_json["mission"])
    intent = GroundedIntent(cast(str, mission["objective"]), (), ())
    reviewer_transport = FakeTransport([_response(_review_output())])
    ResponsesMissionReviewer(
        settings,
        {"OPENAI_API_KEY": "test-only-key"},
        reviewer_transport,
        execution_profile=profile,
    ).review(intent, plan, _current_catalog(), grounding)
    for task in cast(list[JSONObject], plan_json["tasks"]):
        for role in cast(list[JSONObject], task["roles"]):
            cast(JSONObject, role["requirements"])["resources"] = []
    repair_transport = FakeTransport([_response(_provider_plan(plan_json))])
    repaired = ResponsesMissionRepairer(
        settings,
        {"OPENAI_API_KEY": "test-only-key"},
        repair_transport,
        execution_profile=profile,
    ).repair(
        cast(str, mission["id"]),
        intent,
        plan,
        MissionPlanReview.from_json(_review_output()),
        _current_catalog(),
        grounding,
    )
    assert all(role.resources[0].kind == "space" for task in repaired.tasks for role in task.roles)

    assert (
        json.loads(cast(str, reviewer_transport.requests[0][2]["input"])).get(
            "deployment_execution_profile"
        )
        == profile.to_json()
    )
    assert (
        json.loads(cast(str, repair_transport.requests[0][2]["input"])).get(
            "deployment_execution_profile"
        )
        == profile.to_json()
    )


def test_execution_planning_evidence_is_frozen_across_planner_reviewer_and_repairer() -> None:
    """The exact deployment verifier/lifetime artifact reaches every model writer."""
    plan_json = cast(
        JSONObject,
        json.loads(Path("scenarios/e1-shared-world-episode-51/mission-plan.json").read_text()),
    )
    plan = MissionPlan.from_json(plan_json)
    mission = cast(JSONObject, plan_json["mission"])
    intent = GroundedIntent(cast(str, mission["objective"]), (), ())
    evidence = _execution_planning_evidence()
    evidence_semantic = _execution_planning_evidence_semantic()
    evidence_grounding = GroundingContextSnapshot.create(
        request_id="request-test",
        dialogue_digest="sha256:" + "0" * 64,
        captured_at_ms=10,
        semantic_evidence=evidence_semantic,
    )
    profile = _shared_world_execution_profile()
    settings = _local_settings()
    planner_transport = FakeTransport([_response(_provider_plan(plan_json))])
    planner = ResponsesMissionPlanner(
        settings,
        {"OPENAI_API_KEY": "test-only-key"},
        planner_transport,
        execution_profile=profile,
        execution_planning_evidence=evidence,
    )
    planner.plan(cast(str, mission["id"]), intent, _current_catalog(), evidence_grounding)
    reviewer_transport = FakeTransport([_response(_review_output())])
    reviewer = ResponsesMissionReviewer(
        settings,
        {"OPENAI_API_KEY": "test-only-key"},
        reviewer_transport,
        execution_profile=profile,
        execution_planning_evidence=evidence,
    )
    reviewer.review(intent, plan, _current_catalog(), evidence_grounding)
    repair_transport = FakeTransport([_response(_provider_plan(plan_json))])
    ResponsesMissionRepairer(
        settings,
        {"OPENAI_API_KEY": "test-only-key"},
        repair_transport,
        execution_profile=profile,
        execution_planning_evidence=evidence,
    ).repair(
        cast(str, mission["id"]),
        intent,
        plan,
        MissionPlanReview.from_json(_review_output()),
        _current_catalog(),
        evidence_grounding,
    )
    for transport in (planner_transport, reviewer_transport, repair_transport):
        payload = json.loads(cast(str, transport.requests[0][2]["input"]))
        assert payload["execution_planning_evidence"] == evidence.to_json()


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
        == Path("mission/prompts/v0/reviewer.md").read_bytes().decode("utf-8").strip()
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


def test_review_static_prefix_cache_preserves_the_complete_review_input() -> None:
    """Cache frozen reviewer assets while preserving the exact request evidence."""
    settings = _local_settings()
    environment = {"OPENAI_API_KEY": "test-only-key"}
    plan = MissionPlan.from_json(_v0_8_plan())
    grounded = GroundedIntent(plan.mission.objective, (), ())
    catalog = _current_catalog()
    context = _semantic_grounding()
    profile = DeploymentPlanningProfile.load(
        Path("scenarios/e1-shared-world-episode-51/planning-profile.json")
    )
    runtime: JSONObject = {"review_purpose": "runtime-replan"}
    plain = FakeTransport([_response(_review_output())])
    cached = FakeTransport([_response(_review_output())])
    cached_settings = replace(settings, llm=replace(settings.llm, review_static_prefix_cache=True))
    original_review = ResponsesMissionReviewer(
        settings, environment, plain, planning_profile=profile
    ).review_runtime(grounded, plan, catalog, context, runtime)
    cached_review = ResponsesMissionReviewer(
        cached_settings, environment, cached, planning_profile=profile
    ).review_runtime(grounded, plan, catalog, context, runtime)

    assert cached_review == original_review
    standard_payload = plain.requests[0][2]
    cached_payload = cached.requests[0][2]
    assert cached_payload["prompt_cache_options"] == {"mode": "explicit"}
    developer, user = cast(list[JSONObject], cached_payload["input"])
    assert developer == {
        "role": "developer",
        "content": [{"type": "input_text", "text": standard_payload["instructions"]}],
    }
    assert user["role"] == "user"
    prefix, suffix = cast(list[JSONObject], user["content"])
    assert prefix["prompt_cache_breakpoint"] == {"mode": "explicit"}
    assert set(suffix) == {"type", "text"}
    prefix_text = cast(str, prefix["text"])
    assert json.loads(prefix_text.removesuffix(", ") + "}") == {
        "capability_catalog": catalog.to_json(),
        "deployment_planning_profile": profile.to_json(),
        "satisfaction_policy": (
            settings.satisfaction_policy.to_json() if settings.satisfaction_policy else None
        ),
    }
    assert json.loads(prefix_text + cast(str, suffix["text"])) == json.loads(
        cast(str, standard_payload["input"])
    )
    suffix_text = cast(str, suffix["text"])
    dynamic = json.loads("{" + suffix_text)
    assert "runtime_replanning_context" in dynamic
    assert "grounding_context" in dynamic
    assert (
        suffix_text
        == json.dumps(dynamic, ensure_ascii=False, sort_keys=True, separators=(",", ":"))[1:]
    )
    for field in ("model", "store", "text", "reasoning"):
        assert cached_payload[field] == standard_payload[field]


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
        == Path("mission/prompts/v0/repairer.md").read_bytes().decode("utf-8").strip()
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
    with pytest.raises(RejectedPlanError, match="delivery.magic_move@v1") as rejected:
        planner.plan(
            cast(str, mission["id"]),
            GroundedIntent(cast(str, mission["objective"]), (), ()),
            _current_catalog(),
            _grounding(),
        )

    assert len(transport.requests) == 1
    assert rejected.value.provider_output == _provider_plan(plan_json)
    assert rejected.value.validation_errors[0]["code"] == "plan.capability_catalog_invalid"


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


def test_per_stage_model_effort_tier_and_prefix_cache_routing() -> None:
    """Interpreter, Planner, Reviewer, and Repairer independently honor stage overrides."""
    fixture = _v0_8_plan()
    plan = MissionPlan.from_json(fixture)
    output = _provider_plan(fixture)
    objective = plan.mission.objective
    transport = FakeTransport(
        [
            _response(
                {"objective": objective, "constraints": [], "assumptions": [], "open_questions": []}
            ),
            _response(output),
            _response(_review_output()),
            _response(output),
        ]
    )
    original = _local_settings()
    settings = replace(
        original,
        llm=replace(
            original.llm,
            interpreter_model="interp-fast",
            interpreter_reasoning_effort="minimal",
            interpreter_service_tier="ultrafast",
            interpreter_static_prefix_cache=True,
            planner_model="plan-sol",
            planner_reasoning_effort="low",
            planner_service_tier="ultrafast",
            planner_static_prefix_cache=True,
            review_model="rev-fast",
            review_reasoning_effort="low",
            review_service_tier="priority",
            review_static_prefix_cache=True,
            repair_model="rep-sol",
            repair_reasoning_effort="medium",
            repair_service_tier="ultrafast",
            repair_static_prefix_cache=True,
        ),
    )
    environment = {"OPENAI_API_KEY": "test-only-key"}
    grounding = _grounding()
    catalog = _current_catalog()

    intent = ResponsesMissionInterpreter(settings, environment, transport).interpret((), grounding)
    grounded = intent.grounded_intent()
    planned = ResponsesMissionPlanner(settings, environment, transport).plan(
        plan.mission.mission_id, grounded, catalog, grounding
    )
    review = ResponsesMissionReviewer(settings, environment, transport).review(
        grounded, planned, catalog, grounding
    )
    repaired = ResponsesMissionRepairer(settings, environment, transport).repair(
        plan.mission.mission_id, grounded, planned, review, catalog, grounding
    )
    assert planned == plan == repaired

    models = [p["model"] for _, _, p, _ in transport.requests]
    efforts = [p["reasoning"] for _, _, p, _ in transport.requests]
    tiers = [p.get("service_tier") for _, _, p, _ in transport.requests]
    assert models == ["interp-fast", "plan-sol", "rev-fast", "rep-sol"]
    assert efforts == [
        {"effort": "minimal"},
        {"effort": "low"},
        {"effort": "low"},
        {"effort": "medium"},
    ]
    assert tiers == ["ultrafast", "ultrafast", "priority", "ultrafast"]
    # Every stage has explicit cache breakpoint enabled
    assert all(
        p.get("prompt_cache_options") == {"mode": "explicit"} for _, _, p, _ in transport.requests
    )
    # Interpreter puts breakpoint on developer instructions
    interp_dev = cast(list[JSONObject], transport.requests[0][2]["input"])[0]
    assert cast(list[JSONObject], interp_dev["content"])[0]["prompt_cache_breakpoint"] == {
        "mode": "explicit"
    }
    # Planner & Repairer preserve lossless static + dynamic JSON split
    for idx in (1, 3):
        user = cast(list[JSONObject], transport.requests[idx][2]["input"])[1]
        prefix, suffix = cast(list[JSONObject], user["content"])
        assert prefix["prompt_cache_breakpoint"] == {"mode": "explicit"}
        combined = json.loads(cast(str, prefix["text"]) + cast(str, suffix["text"]))
        assert "capability_catalog" in combined and "grounding_context" in combined


def test_compact_grounding_for_llm_preserves_semantics_without_mutating_snapshot() -> None:
    """Compact LLM projection keeps battery, posture, and scene centers while stripping noise."""
    import hashlib

    from mission.grounding_context import (
        GroundingContextSnapshot,
        GroundingFreshness,
        MemoryContentStatus,
        MemoryGroundingEvidence,
        StateGroundingEvidence,
        dialogue_digest,
        evidence_id,
        grounding_selection_policy_ref,
    )
    from mission.origin_evidence import canonical_origin
    from mission.responses import project_grounding_for_llm

    scene_def = json.dumps(
        {
            "map_binding": {"map_id": "1101_visual_dog2", "mode": "localization"},
            "regions": [
                {
                    "annotation_id": "anno.4c5ac08a",
                    "kind": "region",
                    "name": "王老师办公室门口",
                    "points": [[7.0, -6.5], [8.0, -6.5], [8.0, -5.5], [7.0, -5.5]],
                    "stale": False,
                    "created_at": 1793584041.0,
                }
            ],
        },
        ensure_ascii=False,
    )
    phys_val: JSONObject = {
        "physical_entity_id": "entity-lite3-dog2",
        "motion_authorized": False,
        "posture": {
            "availability": "reported_current",
            "reported_posture": "down",
            "aggregate_age_ms": 100.0,
            "sample_received_at_ns": 1795078876487807064,
        },
        "battery": {
            "availability": "reported_current",
            "percent": 25.0,
            "remaining_at_publisher_ms": 4800,
        },
        "mapping": {
            "map_id": "1101_visual_dog2",
            "mode": "localization",
            "response_received_at_ns": 1795078875000000000,
            "reported_pose": {"x": 1.23456, "y": -0.98765, "theta": 0.54321},
        },
    }
    scene_val: JSONObject = {
        "physical_entity_id": "entity-lite3-dog2",
        "availability": "current",
        "definition_json": scene_def,
        "localization_verified": False,
        "arrival_verified": False,
        "reason": None,
        "sample_started_at_ns": 1795078870000000000,
        "source_documents": {"doc": "sha256:" + "0" * 64},
    }
    phys_ev = StateGroundingEvidence(
        evidence_id("state", {"id": "phys"}),
        "robot-physical-observation",
        "entity-lite3-dog2",
        "reported",
        "node:lite3-dog2/physical-observer",
        "physical-observation",
        "roboguide.world.robot-physical-observation/v0.1",
        phys_val,
        1000,
        1000,
        5000,
        GroundingFreshness.FRESH,
        None,
        "epoch-1",
        1,
    )
    scene_ev = StateGroundingEvidence(
        evidence_id("state", {"id": "scene"}),
        "scene-targets",
        "entity-lite3-dog2",
        "reported",
        "node:lite3-dog2/physical-observer",
        "scene-targets",
        "roboguide.world.scene-targets/v0.2",
        scene_val,
        1000,
        1000,
        15000,
        GroundingFreshness.FRESH,
        None,
        "epoch-1",
        2,
    )
    d_digest = dialogue_digest(())
    policy_ref = grounding_selection_policy_ref(frozenset())
    origin_content: JSONObject = {
        "schema_version": "roboguide.reported-origin/v0.1",
        "node_id": "lite3-dog2",
        "memory_id": "origin-compact-1",
        "captured_at_ns": 1794296015841507192,
        "attempt_id": "attempt-1",
        "source_mission_id": "mission-origin-1",
        "source_task_id": "record-origin",
        "source_execution_id": "exec-origin-1",
        "position": {
            "frame_basis": "local Mapping map; shared physical frame unverified",
            "map_id": "1101_visual_dog2",
            "map_revision": None,
            "x_m": 0.48122513399,
            "y_m": 1.03076049063,
            "yaw_rad": -0.21670256745,
        },
        "mapping_observation": {
            "availability": "current",
            "age_ms": 150.0,
            "freshness_basis": "verbose explanatory text",
            "received_at_ns": 1794296015691507192,
            "value": {
                "has_backup": True,
                "has_map": True,
                "map_id": "1101_visual_dog2",
                "mode": "localization",
                "pose": {"x": 0.48122513399, "y": 1.03076049063, "theta": -0.21670256745},
            },
        },
        "pose_measurement_time": None,
        "localization_verified": False,
        "motion_authorized": False,
    }
    origin_raw = canonical_origin(origin_content)
    origin_ev = MemoryGroundingEvidence(
        evidence_id=evidence_id("memory", {"memory_id": "origin-compact-1", "revision_id": "r1"}),
        memory_id="origin-compact-1",
        revision_id="r1",
        kind="experience",
        provider_id="reported-origins",
        owner={"owner": "node", "node_id": "lite3-dog2", "local_system_id": "robot-observer"},
        scope="global",
        visibility="exchangeable",
        payload_schema="roboguide.reported-origin/v0.1",
        media_type="application/json",
        artifact={
            "content_digest": "sha256:" + hashlib.sha256(origin_raw).hexdigest(),
            "byte_size": len(origin_raw),
        },
        source_mission_id="mission-origin-1",
        source_execution_id="exec-origin-1",
        source_task_ref={"mission_id": "mission-origin-1", "task_id": "record-origin"},
        created_at_ms=1000,
        content_status=MemoryContentStatus.VERIFIED_ORIGIN,
        content=origin_content,
    )
    snap = GroundingContextSnapshot.create(
        request_id="request-compact",
        dialogue_digest=d_digest,
        captured_at_ms=1000,
        selection_policy_ref=policy_ref,
        state_evidence=(phys_ev, scene_ev),
        memory_evidence=(origin_ev,),
        gaps=(),
    )
    states_by_type = {
        cast(str, cast(JSONObject, s["object"])["object_type"]): s
        for s in cast(list[JSONObject], project_grounding_for_llm(snap)["state_evidence"])
    }
    before_raw = snap.to_json()
    projected = project_grounding_for_llm(snap)
    assert snap.to_json() == before_raw  # snapshot immutable!
    assert "state_evidence" in projected
    p_val = cast(JSONObject, states_by_type["robot-physical-observation"]["value"])
    assert p_val["battery"] == {"availability": "reported_current", "percent": 25.0}
    assert p_val["posture"] == {"availability": "reported_current", "reported_posture": "down"}
    assert "motion_authorized" not in p_val
    assert p_val["observation_grants_motion_authority"] is False
    assert "response_received_at_ns" not in cast(JSONObject, p_val["mapping"])
    s_val = cast(JSONObject, states_by_type["scene-targets"]["value"])
    assert "sample_started_at_ns" not in s_val
    regions = cast(list[JSONObject], cast(JSONObject, s_val["scene"])["regions"])
    assert regions[0]["name"] == "王老师办公室门口"
    assert regions[0]["center_xy"] == [7.5, -6.0]
    assert regions[0]["bounds_xy"] == [7.0, -6.5, 8.0, -5.5]
    assert "points" not in regions[0]
    mems = cast(list[JSONObject], projected["memory_evidence"])
    m_content = cast(JSONObject, mems[0]["content"])
    assert "motion_authorized" not in m_content
    assert m_content["observation_grants_motion_authority"] is False
    raw_mem = cast(list[JSONObject], before_raw["memory_evidence"])[0]
    assert cast(JSONObject, raw_mem["content"])["motion_authorized"] is False
    assert m_content["position"] == {
        "frame_basis": "local Mapping map; shared physical frame unverified",
        "map_id": "1101_visual_dog2",
        "map_revision": None,
        "x_m": 0.481,
        "y_m": 1.031,
        "yaw_rad": -0.217,
    }
    assert m_content["mapping_observation"] == {
        "availability": "current",
        "map_id": "1101_visual_dog2",
        "mode": "localization",
        "has_map": True,
        "has_backup": True,
    }
    assert "captured_at_ns" not in m_content
    assert mems[0]["media_type"] == "application/json"
    assert mems[0]["visibility"] == "exchangeable"
    assert mems[0]["provider_id"] == "reported-origins"


def test_static_provider_schema_reuses_identical_schema_and_still_rejects_wrong_id() -> None:
    """Static provider schema stays identical across Missions and still rejects ID drift."""
    fixture_alpha = _v0_8_plan()
    cast(JSONObject, fixture_alpha["mission"])["id"] = "mission-alpha"
    plan_alpha = MissionPlan.from_json(fixture_alpha)
    fixture_wrong = _v0_8_plan()
    cast(JSONObject, fixture_wrong["mission"])["id"] = "mission-wrong-id"
    transport = FakeTransport(
        [_response(_provider_plan(fixture_alpha)), _response(_provider_plan(fixture_wrong))]
    )
    original = _local_settings()
    settings = replace(original, llm=replace(original.llm, static_provider_schema=True))
    planner = ResponsesMissionPlanner(settings, {"OPENAI_API_KEY": "test-only-key"}, transport)
    intent = GroundedIntent(plan_alpha.mission.objective, (), ())
    planned = planner.plan("mission-alpha", intent, _current_catalog(), _grounding())
    assert planned.mission.mission_id == "mission-alpha"
    with pytest.raises(MissionIdentityError, match="model changed the requested mission id"):
        planner.plan("mission-beta", intent, _current_catalog(), _grounding())
    schemas = [
        cast(JSONObject, cast(JSONObject, p["text"])["format"])["schema"]
        for _, _, p, _ in transport.requests
    ]
    assert len(schemas) == 2
    assert schemas[0] == schemas[1]


def test_compact_planner_uses_full_admission_and_refuses_wrong_mission_id() -> None:
    """Exercise the opt-in Planner through transport, canonical assembly and identity rejection."""
    canonical = _v0_8_plan()
    mission = cast(JSONObject, canonical["mission"])
    mission["id"] = "mission-compact"
    intent = GroundedIntent(cast(str, mission["objective"]), (), ())
    compact = _provider_plan(canonical)
    compact["schema_version"] = "roboguide.mission-plan/v0.10"
    compact["provider_profile"] = "host-objective-v1"
    del cast(JSONObject, compact["mission"])["objective"]
    for task in cast(list[JSONObject], compact["tasks"]):
        if task.get("timing") == {
            "earliest_start_offset_ms": 0,
            "latest_start_offset_ms": None,
            "completion_deadline_offset_ms": None,
        }:
            task["timing"] = None
        satisfaction = cast(JSONObject, task["satisfaction"])
        if satisfaction.get("basis") == "execution-report":
            del satisfaction["verifier"]
    wrong = deepcopy(compact)
    cast(JSONObject, wrong["mission"])["id"] = "wrong-mission"
    transport = FakeTransport([_response(compact), _response(wrong)])
    original = _local_settings()
    settings = replace(
        original,
        contract_version="roboguide.mission-plan/v0.10",
        schema_path=Path("contracts/mission/v0.10/mission-plan.schema.json"),
        provider_plan_profile="host-objective-v1",
        llm=replace(original.llm, planner_compact_output=True, static_provider_schema=True),
    )
    planner = ResponsesMissionPlanner(settings, {"OPENAI_API_KEY": "test-only-key"}, transport)
    plan = planner.plan("mission-compact", intent, _current_catalog(), _grounding())
    canonical["schema_version"] = "roboguide.mission-plan/v0.10"
    assert plan.to_json() == MissionPlan.from_json(canonical).to_json()
    with pytest.raises(MissionIdentityError, match="model changed the requested mission id"):
        planner.plan("mission-compact", intent, _current_catalog(), _grounding())
    for _, _, payload, _ in transport.requests:
        assert cast(JSONObject, cast(JSONObject, payload["text"])["format"])["name"] == (
            "mission_plan_compact_v1"
        )
