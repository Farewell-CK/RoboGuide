"""Offline satisfaction-policy admission and complete Review/Repair regression tests."""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.config import MissionSettings, load_settings
from mission.controller import SubmissionReceipt
from mission.grounding_reader import EmptyMissionGroundingReader
from mission.intent import GroundedIntent
from mission.models import JSONObject, MissionPlan
from mission.request_record import MissionRequestLifecycle
from mission.request_store import MissionRequestStore
from mission.requests import MissionRequestEngine
from mission.responses import (
    ResponsesMissionInterpreter,
    ResponsesMissionPlanner,
    ResponsesMissionRepairer,
    ResponsesMissionReviewer,
)
from mission.review import MissionPlanReview
from mission.satisfaction_policy import (
    MissionSatisfactionPolicy,
    MissionSatisfactionPolicyError,
    validate_satisfaction_policy,
)


def _settings() -> MissionSettings:
    """Load the real configured policy with an offline fake-provider endpoint."""
    settings = load_settings(Path("config/mission.toml"), repository_root=Path.cwd())
    return replace(settings, provider=replace(settings.provider, base_url="http://localhost:8080"))


def _plan(localization: bool = False) -> JSONObject:
    """Build canonical relocation/check or map/localization Tasks without eval case/gold inputs."""
    raw = cast(
        JSONObject,
        json.loads(Path("scenarios/mission-front-half-v0.7/mission-plan.json").read_text()),
    )
    tasks = cast(list[JSONObject], raw["tasks"])
    first = tasks[0]
    first["satisfaction"] = {
        "expected_effect": "The requested delivery or map creation is complete.",
        "basis": "execution-report",
        "verifier": None,
    }
    check = deepcopy(first)
    check["id"] = "verify-result"
    check["description"] = "Independently confirm the requested condition holds."
    check["depends_on"] = [first["id"]]
    check["satisfaction"] = {
        "expected_effect": "The requested condition is independently established as true.",
        "basis": "execution-report",
        "verifier": None,
    }
    operation: JSONObject = {
        "namespace": "spatial.localization" if localization else "observation",
        "name": "verify",
        "version": "v0" if localization else "v1",
    }
    parameters: JSONObject = {"expected": "sample is intact"}
    if localization:
        parameters = {
            "artifact_operation": "verify",
            "artifact_slot": "map",
            "map_id": "new-area",
            "revision_id": "r1",
            "spatial_anchor_id": "warehouse",
        }
        first_role = cast(list[JSONObject], first["roles"])[0]
        map_operation: JSONObject = {"namespace": "spatial.map", "name": "build", "version": "v0"}
        first_role["requirements"] = {
            "capabilities": [{"contract": map_operation, "constraints": []}],
            "resources": [],
        }
        first_role["execution_intent"] = {
            "operation": map_operation,
            "objective": "Build the requested map revision.",
            "parameters": {**parameters, "artifact_operation": "build"},
        }
    role = cast(list[JSONObject], check["roles"])[0]
    role["requirements"] = {
        "capabilities": [{"contract": operation, "constraints": []}],
        "resources": [],
    }
    role["execution_intent"] = {
        "operation": operation,
        "objective": check["description"],
        "parameters": parameters,
    }
    tasks.append(check)
    return raw


def _with_verifier(raw: JSONObject, age: int) -> JSONObject:
    """Add a canonical verifier to the last Task, preserving all other plan semantics."""
    raw = deepcopy(raw)
    task = cast(list[JSONObject], raw["tasks"])[-1]
    role = cast(list[JSONObject], task["roles"])[0]
    intent = cast(JSONObject, role["execution_intent"])
    satisfaction = cast(JSONObject, task["satisfaction"])
    satisfaction["basis"] = "verifier-evidence"
    satisfaction["verifier"] = {
        "contract": intent["operation"],
        "predicate": "the requested condition holds",
        "max_evidence_age_ms": age,
    }
    return raw


def _review(approved: bool) -> JSONObject:
    """Script semantic review of a positive predicate without using an LLM as test oracle."""
    return {
        "approved": approved,
        "issues": []
        if approved
        else [
            {
                "code": "verification-basis-insufficient",
                "path": "/tasks/1/satisfaction",
                "message": "An affirmative condition requires verifier evidence under policy.",
                "required_action": "RepairPlan",
            }
        ],
    }


class ScriptedTransport:
    """Exercise real Responses adapters with finite outputs and inspectable policy inputs."""

    def __init__(self, outputs: list[JSONObject]) -> None:
        """Retain a finite output sequence; no network implementation is reachable."""
        self.outputs = outputs
        self.requests: list[JSONObject] = []

    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: JSONObject,
        timeout_seconds: float,
    ) -> JSONObject:
        """Wrap scripted output, echo Engine identity, and encode the unchanged provider DTO."""
        self.requests.append(deepcopy(payload))
        output = deepcopy(self.outputs.pop(0))
        if output.get("schema_version") == "roboguide.mission-plan/v0.7":
            model_input = json.loads(cast(str, payload["input"]))
            mission = cast(JSONObject, output["mission"])
            mission["id"] = model_input["mission_id"]
            mission["objective"] = model_input["grounded_intent"]["objective"]
            for task in cast(list[JSONObject], output["tasks"]):
                for role in cast(list[JSONObject], task["roles"]):
                    intent = cast(JSONObject, role["execution_intent"])
                    params = cast(JSONObject, intent["parameters"])
                    intent["parameters"] = [
                        {"key": key, "value": val} for key, val in params.items()
                    ]
        return {
            "status": "completed",
            "error": None,
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(output)}],
                }
            ],
        }


class AcceptingController:
    """Observe submission of reviewed canonical plans without executing tasks."""

    def __init__(self) -> None:
        """Start with no accepted plans."""
        self.plans: list[MissionPlan] = []

    def submit_plan(self, plan: MissionPlan) -> SubmissionReceipt:
        """Retain exact submitted output and acknowledge semantic admission only."""
        self.plans.append(plan)
        return SubmissionReceipt(True, 202, "Accepted")


def _clock() -> int:
    """Return a fixed timestamp sufficient for deterministic deliberation persistence."""
    return 100


@pytest.mark.parametrize("localization", [False, True])
def test_verification_repair_uses_same_policy_and_reaches_acceptance(
    tmp_path: Path, localization: bool
) -> None:
    """Real adapters/Engine complete Draft -> Review -> Repair -> Review with one grounded bound."""
    settings = _settings()
    assert settings.satisfaction_policy is not None
    # A non-default deployment value proves the flow never hardcodes 1000/5000/60000.
    policy = replace(
        settings.satisfaction_policy, policy_ref="test-policy/v2", max_evidence_age_ms=3210
    )
    settings = replace(settings, satisfaction_policy=policy)
    initial = _plan(localization)
    transport = ScriptedTransport(
        [
            {
                "objective": "Independently confirm the requested result.",
                "constraints": [],
                "assumptions": [],
                "open_questions": [],
            },
            initial,
            _review(False),
            _with_verifier(initial, policy.max_evidence_age_ms),
            _review(True),
        ]
    )
    environment = {"OPENAI_API_KEY": "test-only"}
    controller = AcceptingController()
    store = MissionRequestStore(tmp_path / "request.sqlite3")
    identities = iter(("1" * 32, "2" * 32))
    engine = MissionRequestEngine(
        store,
        ResponsesMissionInterpreter(settings, environment, transport),
        ResponsesMissionPlanner(settings, environment, transport),
        controller,
        CanonicalCapabilityCatalog.load(settings.capability_catalog_path),
        frozenset(),
        id_generator=identities.__next__,
        clock=_clock,
        reviewer=ResponsesMissionReviewer(settings, environment, transport),
        repairer=ResponsesMissionRepairer(settings, environment, transport),
        max_repair_attempts=2,
    )

    record = engine.create("Independently confirm the requested result.")

    assert record.lifecycle is MissionRequestLifecycle.ACCEPTED
    assert record.repair_attempts == 1
    assert [attempt.review.approved for attempt in record.review_history] == [False, True]
    assert controller.plans == [record.plan]
    assert record.plan is not None
    validate_satisfaction_policy(record.plan, policy)
    assert record.plan.tasks[0].satisfaction.verifier is None
    assert record.plan.tasks[-1].satisfaction.verifier is not None
    assert record.plan.tasks[-1].satisfaction.verifier.max_evidence_age_ms == 3210
    model_inputs = [json.loads(cast(str, req["input"])) for req in transport.requests]
    assert "satisfaction_policy" not in model_inputs[0]  # Interpreter boundary is unchanged.
    assert [item["satisfaction_policy"] for item in model_inputs[1:]] == [policy.to_json()] * 4
    assert not transport.outputs


@pytest.mark.parametrize("stage", ["planner", "repairer"])
@pytest.mark.parametrize("age", [1000, 60000, None])
def test_generated_verifier_cannot_invent_or_omit_policy_provenance(
    stage: str, age: int | None
) -> None:
    """Both generation paths fail closed for invented values or absent configuration."""
    settings = _settings()
    if age is None:
        settings = replace(settings, satisfaction_policy=None)
    raw = _with_verifier(_plan(), 5000 if age is None else age)
    transport = ScriptedTransport([raw])
    catalog = CanonicalCapabilityCatalog.load(settings.capability_catalog_path)
    intent = GroundedIntent("Confirm the result.", (), ())
    context = EmptyMissionGroundingReader().capture("test", (), 1)
    with pytest.raises(MissionSatisfactionPolicyError, match="policy"):
        if stage == "planner":
            ResponsesMissionPlanner(settings, {"OPENAI_API_KEY": "test-only"}, transport).plan(
                "mission-test", intent, catalog, context
            )
        else:
            ResponsesMissionRepairer(settings, {"OPENAI_API_KEY": "test-only"}, transport).repair(
                "mission-test",
                intent,
                MissionPlan.from_json(_plan()),
                MissionPlanReview.from_json(_review(False)),
                catalog,
                context,
            )


def test_ordinary_physical_plan_needs_no_verifier_policy() -> None:
    """The policy cannot force verifier evidence onto an ordinary relocation."""
    raw = _plan()
    tasks = cast(list[JSONObject], raw["tasks"])
    tasks.pop()
    settings = replace(_settings(), satisfaction_policy=None)
    transport = ScriptedTransport([raw, _review(True)])
    context = EmptyMissionGroundingReader().capture("test", (), 1)
    intent = GroundedIntent("Relocate the object.", (), ())
    catalog = CanonicalCapabilityCatalog.load(settings.capability_catalog_path)
    plan = ResponsesMissionPlanner(settings, {"OPENAI_API_KEY": "test-only"}, transport).plan(
        "mission-test", intent, catalog, context
    )
    result = ResponsesMissionReviewer(settings, {"OPENAI_API_KEY": "test-only"}, transport).review(
        intent, plan, catalog, context
    )
    assert result.approved
    assert plan.tasks[0].satisfaction.verifier is None
    assert json.loads(cast(str, transport.requests[1]["input"]))["satisfaction_policy"] is None


def test_reviewer_retains_semantic_rejection_even_with_policy_grounded_freshness() -> None:
    """A valid freshness source never suppresses another semantic issue or forces approval."""
    settings = _settings()
    assert settings.satisfaction_policy is not None
    plan = MissionPlan.from_json(
        _with_verifier(_plan(), settings.satisfaction_policy.max_evidence_age_ms)
    )
    rejection = _review(False)
    issues = cast(list[JSONObject], rejection["issues"])
    issues[0]["code"] = "wrong-expected-effect"
    issues[0]["message"] = "The effect is stronger than requested; preserve the user's goal."
    transport = ScriptedTransport([rejection])
    reviewer = ResponsesMissionReviewer(settings, {"OPENAI_API_KEY": "test-only"}, transport)
    result = reviewer.review(
        GroundedIntent("Report whether the condition holds.", (), ()),
        plan,
        CanonicalCapabilityCatalog.load(settings.capability_catalog_path),
        EmptyMissionGroundingReader().capture("test", (), 1),
    )
    assert result.to_json() == rejection


def test_reviewer_rejects_unproven_bound_before_requesting_model_review() -> None:
    """Manually supplied drafts cannot bypass the shared policy preflight at Review."""
    settings = _settings()
    transport = ScriptedTransport([])
    reviewer = ResponsesMissionReviewer(settings, {"OPENAI_API_KEY": "test-only"}, transport)
    with pytest.raises(MissionSatisfactionPolicyError, match="must match system policy"):
        reviewer.review(
            GroundedIntent("Confirm the result.", (), ()),
            MissionPlan.from_json(_with_verifier(_plan(), 1000)),
            CanonicalCapabilityCatalog.load(settings.capability_catalog_path),
            EmptyMissionGroundingReader().capture("test", (), 1),
        )
    assert transport.requests == []


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"max_evidence_age_ms": 5000},
        {"policy_ref": "", "max_evidence_age_ms": 5000},
        {"policy_ref": "p/v1", "max_evidence_age_ms": True},
        {"policy_ref": "p/v1", "max_evidence_age_ms": 0},
        {"policy_ref": "p/v1", "max_evidence_age_ms": 1.5},
        {"policy_ref": "p/v1", "max_evidence_age_ms": 1 << 64},
    ],
)
def test_policy_configuration_rejects_missing_identity_and_invalid_age(value: object) -> None:
    """A number without a policy source, or an invalid bound, cannot be a default."""
    with pytest.raises(MissionSatisfactionPolicyError):
        MissionSatisfactionPolicy.from_config(value)


def test_policy_digest_and_missing_configuration_are_explicit(tmp_path: Path) -> None:
    """Policy inputs are defensive copies and content identity survives reused references."""
    policy = _settings().satisfaction_policy
    assert policy is not None
    evidence = policy.to_json()
    evidence["max_evidence_age_ms"] = 999
    assert policy.to_json()["max_evidence_age_ms"] == 5000
    changed = replace(policy, max_evidence_age_ms=6000)
    assert changed.to_json()["policy_digest"] != policy.to_json()["policy_digest"]
    configuration = Path("config/mission.toml").read_text()
    before, remainder = configuration.split("[mission.satisfaction_policy]")
    _, after = remainder.split("[mission.prompts]")
    path = tmp_path / "mission.toml"
    path.write_text(before + "[mission.prompts]" + after)
    assert load_settings(path, repository_root=Path.cwd()).satisfaction_policy is None


def test_prompts_keep_policy_provenance_and_verification_meaning_consistent() -> None:
    """Versioned instructions require sourced bounds without a keyword-based verifier rule."""
    prompts = _settings().prompts
    for path in (prompts.planner_path, prompts.reviewer_path, prompts.repairer_path):
        prompt = path.read_text()
        assert "satisfaction_policy" in prompt
        assert "policy_ref" in prompt and "policy_digest" in prompt
        assert "do not alone require a second independent verifier" in prompt
    reviewer = prompts.reviewer_path.read_text()
    assert "Do not label that value model-invented" in reviewer
    assert "return `RejectDraft` for the system policy gap/conflict" in reviewer
