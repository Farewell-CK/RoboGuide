"""Contract feedback preserves original drafts, bounded recovery and deployment separation."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from mission.controller import MissionControllerError
from mission.deployment_assessment import plan_body_digest
from mission.intent import GroundedIntent
from mission.models import JSONObject, JSONValue, MissionPlan
from mission.plan_assessment import SCHEMA, validate_assessment
from mission.plan_validation import MAX_PLAN_ISSUES, PlanValidationError, reference_issues
from mission.provider_errors import MissionProviderError
from mission.rejected_draft import (
    UNVERIFIED_DRAFT_SCHEMA,
    RejectedDraftEvidence,
    RejectedPlanError,
    build_rejected_draft_evidence,
)
from mission.responses import ResponsesMissionPlanner, ResponsesMissionRepairer
from mission.review import MissionPlanReview
from test_execution_planning_evidence import _evidence
from test_planners import (
    FakeTransport,
    _current_catalog,
    _grounding,
    _local_settings,
    _provider_plan,
    _response,
    _semantic_grounding,
)
from test_rejected_draft_recovery import ScriptedRecoveryPlanner, _engine, _rejection


def _raw() -> JSONObject:
    """Load a generic delivery contract without a simulator, Provider or execution."""
    return cast(
        JSONObject,
        json.loads(Path("scenarios/mission-front-half-v0.7/mission-plan.json").read_text()),
    )


def _report(plan: MissionPlan, issues: list[JSONObject] | None = None) -> JSONObject:
    """Construct a closed reply whose identity can be independently checked and tampered."""
    evidence, _ = _evidence()
    document = evidence.to_json()
    source = cast(JSONObject, document["verifier_source"])
    return {
        "schema_version": SCHEMA,
        "scope": "published-contracts",
        "mission_id": plan.mission.mission_id,
        "plan_body_sha256": plan_body_digest(plan),
        "decision": "blocked" if issues else "not_blocked",
        "verifier_source_digest": source["digest"],
        "deployment_digest": "sha256:" + "c" * 64,
        "execution_lifetime": document["execution_lifetime"],
        "world_identity": document["identity"],
        "issues": cast(JSONValue, issues or []),
        "issues_truncated": False,
    }


def _issues() -> list[JSONObject]:
    """Expose two distinct real final-world conflicts without suggesting a repaired plan."""
    return [
        {
            "code": "verifier.blocks_actor_reuse",
            "path": "/tasks/0/satisfaction",
            "message": "A final verdict retains an endpoint needed by remaining work.",
        },
        {
            "code": "deployment.contract_incompatible",
            "path": "/tasks",
            "message": "Retained endpoint demand has no feasible assignment.",
        },
    ]


def test_readonly_feedback_requires_exact_plan_and_frozen_sources() -> None:
    """A plan query cannot borrow another world's apparent approval or capacity declaration."""
    plan = MissionPlan.from_json(_raw())
    evidence, _ = _evidence()
    validate_assessment(_report(plan), plan, evidence)
    for field, value in (
        ("plan_body_sha256", "sha256:" + "a" * 64),
        ("mission_id", "other-mission"),
        ("verifier_source_digest", "sha256:" + "d" * 64),
        ("execution_lifetime", None),
        ("deployment_digest", None),
        ("world_identity", None),
        ("scope", "initial_static_world"),
        ("issues_truncated", True),
        ("unknown", "inventory"),
    ):
        raw = _report(plan)
        raw[field] = value
        with pytest.raises(ValueError):
            validate_assessment(raw, plan, evidence)


def test_multiple_controller_findings_retain_exact_plan_and_source_binding() -> None:
    """Typed negative feedback remains separate from a malformed/unavailable Controller reply."""
    plan = MissionPlan.from_json(_raw())
    with pytest.raises(PlanValidationError) as rejected:
        validate_assessment(_report(plan, _issues()), plan, _evidence()[0])
    assert len(rejected.value.validation_errors) == 2
    assert all(
        issue["plan_body_sha256"] == plan_body_digest(plan)
        for issue in rejected.value.validation_errors
    )


def test_same_capacity_cannot_hide_a_different_deployment_world() -> None:
    """An unchanged verifier file and registry count cannot authorize another reset snapshot."""
    plan = MissionPlan.from_json(_raw())
    report = _report(plan)
    world = cast(JSONObject, report["world_identity"])
    world["scene_id"] = "different-scene"
    with pytest.raises(ValueError, match="differs from frozen"):
        validate_assessment(report, plan, _evidence()[0])


@pytest.mark.parametrize("role", ["planner", "repairer"])
def test_source_world_drift_fails_before_a_provider_call(role: str) -> None:
    """A model cannot repair a deployment artifact bound to another immutable snapshot."""
    raw = _raw()
    plan = MissionPlan.from_json(raw)
    intent = GroundedIntent(plan.mission.objective, (), ())
    transport = FakeTransport([])
    arguments = (_local_settings(), {"OPENAI_API_KEY": "test-only-key"}, transport)
    with pytest.raises(MissionProviderError) as failure:
        if role == "planner":
            ResponsesMissionPlanner(*arguments, execution_planning_evidence=_evidence()[0]).plan(
                plan.mission.mission_id, intent, _current_catalog(), _grounding()
            )
        else:
            ResponsesMissionRepairer(*arguments, execution_planning_evidence=_evidence()[0]).repair(
                plan.mission.mission_id,
                intent,
                plan,
                MissionPlanReview(True, ()),
                _current_catalog(),
                _grounding(),
            )
    assert failure.value.configuration_failure and not transport.requests


def test_service_composition_checks_every_writer_with_the_same_frozen_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Configured production composition installs one checker for Planner and Repairer only."""
    import mission.api as api
    from mission.service_config import load_service_settings

    path = tmp_path / "planning.json"
    path.write_text(json.dumps(_evidence()[0].to_json()))
    service = replace(
        load_service_settings(Path("config/mission-service.toml"), repository_root=Path.cwd()),
        state_db=tmp_path / "requests.sqlite3",
        execution_planning_evidence_path=path,
        execution_planning_evidence_required=True,
    )
    monkeypatch.setattr(api, "load_service_settings", lambda *args, **kwargs: service)
    monkeypatch.setattr(api, "load_settings", lambda *args, **kwargs: _local_settings())
    monkeypatch.setattr(api, "current_environment", lambda: {"OPENAI_API_KEY": "test-only-key"})
    engine, _ = api.build_engine(
        Path("config/mission.toml"), Path("config/mission-service.toml"), Path.cwd()
    )
    assert isinstance(engine._planner, ResponsesMissionPlanner)
    assert isinstance(engine._repairer, ResponsesMissionRepairer)
    assert engine._planner._plan_validator is engine._repairer._plan_validator
    assert engine._planner._plan_validator is not None


@pytest.mark.parametrize(
    "fault", ["unknown_code", "oversize", "contradiction", "malformed", "inventory"]
)
def test_untrusted_feedback_is_never_a_model_repair_opportunity(fault: str) -> None:
    """Malformed diagnostics and inventory-shaped fields fail as system/configuration errors."""
    plan = MissionPlan.from_json(_raw())
    raw = _report(plan, _issues())
    if fault == "unknown_code":
        cast(list[JSONObject], raw["issues"])[0]["code"] = "choose-node-a"
    elif fault == "oversize":
        raw["issues"] = cast(JSONValue, _issues() * MAX_PLAN_ISSUES)
    elif fault == "contradiction":
        raw["decision"] = "not_blocked"
    elif fault == "inventory":
        cast(list[JSONObject], raw["issues"])[0]["node_id"] = "private-node"
    else:
        cast(list[JSONObject], raw["issues"])[0]["code"] = []
    with pytest.raises(ValueError) as failed:
        validate_assessment(raw, plan)
    assert not isinstance(failed.value, PlanValidationError)


def test_reference_diagnostics_collect_independent_errors_without_repair() -> None:
    """A spelling mismatch and another missing logical reference are reported together."""
    raw = _raw()
    raw["schema_version"] = "roboguide.mission-plan/v0.8"
    contexts = cast(list[JSONObject], raw["contexts"])
    cast(list[JSONObject], contexts[0]["roles"])[0]["actor"] = "not-declared"
    tasks = cast(list[JSONObject], raw["tasks"])
    cast(list[JSONObject], tasks[0]["roles"])[0]["context_role"] = "missing-role"
    original = copy.deepcopy(raw)
    issues = reference_issues(raw)
    assert {issue["code"] for issue in issues} == {
        "plan.undeclared_actor",
        "plan.undeclared_context_role",
    }
    assert raw == original
    contexts[0]["executor_constraints"] = [
        {"kind": "distinct-physical-entities", "context_roles": ["carrier", "observer"]}
    ]
    assert "plan.executor_constraint_unfounded" in {
        issue["code"] for issue in reference_issues(raw, _semantic_grounding())
    }
    contexts.extend(copy.deepcopy(contexts) * 40)
    assert len(reference_issues(raw)) == MAX_PLAN_ISSUES
    cast(JSONObject, raw["mission"])["actors"] = [{"id": []}]
    assert reference_issues(raw) == []


def test_ambiguous_contexts_are_left_to_canonical_structural_validation() -> None:
    """A duplicate Context cannot justify claiming a TaskRole reference is absent."""
    raw = _raw()
    raw["schema_version"] = "roboguide.mission-plan/v0.8"
    contexts = cast(list[JSONObject], raw["contexts"])
    other = copy.deepcopy(contexts[0])
    cast(list[JSONObject], other["roles"])[0]["id"] = "other-role"
    contexts.append(other)
    assert reference_issues(raw) == []


@pytest.mark.parametrize("stage", ["plan", "regenerate", "repair", "regenerate_repair"])
def test_all_generated_drafts_cross_same_readonly_boundary(stage: str) -> None:
    """Every model-produced revision retains original output and all Controller findings."""
    raw = _raw()
    mission = cast(JSONObject, raw["mission"])
    intent = GroundedIntent(cast(str, mission["objective"]), (), ())
    output = _provider_plan(raw)
    transport = FakeTransport([_response(output)])
    checked: list[MissionPlan] = []

    def validate(plan: MissionPlan) -> None:
        """Observe the fully validated candidate and reject only explicit contract conflicts."""
        checked.append(plan)
        validate_assessment(_report(plan, _issues()), plan)

    args = (cast(str, mission["id"]), intent)
    settings, context, catalog = _local_settings(), _grounding(), _current_catalog()
    with pytest.raises(RejectedPlanError) as rejected:
        if stage in {"plan", "regenerate"}:
            planner = ResponsesMissionPlanner(
                settings, {"OPENAI_API_KEY": "test-only-key"}, transport, plan_validator=validate
            )
            if stage == "plan":
                planner.plan(*args, catalog, context)
            else:
                planner.regenerate(*args, catalog, context, output, _issues())
        else:
            repairer = ResponsesMissionRepairer(
                settings, {"OPENAI_API_KEY": "test-only-key"}, transport, plan_validator=validate
            )
            plan, review = MissionPlan.from_json(raw), MissionPlanReview(True, ())
            if stage == "repair":
                repairer.repair(*args, plan, review, catalog, context)
            else:
                repairer.regenerate(*args, plan, review, catalog, context, output, _issues())
    assert len(checked) == len(transport.requests) == 1
    assert rejected.value.provider_output == output
    assert len(rejected.value.validation_errors) == 2
    evidence = build_rejected_draft_evidence(
        request_id="request",
        mission_id=cast(str, mission["id"]),
        attempt_index=1,
        error=rejected.value,
        grounding_context_digest=context.context_digest,
        semantic_evidence_digest=None,
        provider_identity={},
        persisted_at_ms=123,
    )
    restored = RejectedDraftEvidence.from_json(evidence.to_json())
    restored.verify_integrity()
    assert restored.validation_errors == rejected.value.validation_errors


def test_request_recovery_receives_all_saved_issues_under_existing_budget(tmp_path: Path) -> None:
    """Several findings consume one regeneration, not one call per reported error."""
    rejected = _rejection()
    rejected.validation_errors = tuple(_issues())
    planner = ScriptedRecoveryPlanner([rejected], [rejected])
    engine = _engine(tmp_path, planner, budget=1)
    result = engine.create("deliver the declared payload")
    assert planner.regenerate_calls[0]["validation_errors"] == _issues()
    assert len(planner.regenerate_calls) == 1 and len(result.rejected_drafts) == 2


def test_system_validation_failure_never_regenerates_a_model_draft(tmp_path: Path) -> None:
    """A Controller timeout is retained as system failure with zero recovery calls."""

    class OfflinePlanner:
        """Simulate a validation boundary outage after a model call without network effects."""

        def plan(self, **kwargs: Any) -> MissionPlan:
            """Preserve a real infrastructure failure instead of returning a draft rejection."""
            raise MissionControllerError("Controller validation request timed out")

        def regenerate(self, **kwargs: Any) -> MissionPlan:
            """Forbid spending model recovery calls on unavailable Controller evidence."""
            raise AssertionError("unexpected regeneration")

    result = _engine(tmp_path, OfflinePlanner(), budget=2).create("deliver the declared payload")
    assert not result.rejected_drafts and result.failure_evidence is not None
    assert result.failure_evidence["failure_owner"] == "SUT_SYSTEM"


def test_post_model_validation_outage_preserves_candidate_without_regeneration(
    tmp_path: Path,
) -> None:
    """A real adapter's returned output survives a subsequent read-only Controller failure."""
    emitted: list[JSONObject] = []

    def unavailable(plan: MissionPlan) -> None:
        """Keep the actual candidate unchanged and reproduce a Controller boundary timeout."""
        raise MissionControllerError("Controller plan validation request timed out")

    class Planner:
        """Compose production normalization with a finite offline Provider response."""

        def plan(self, **kwargs: Any) -> MissionPlan:
            """Create one exact-request response and pass it through the real adapter."""
            raw = _raw()
            mission = cast(JSONObject, raw["mission"])
            mission["id"] = kwargs["mission_id"]
            mission["objective"] = kwargs["grounded_intent"].objective
            output = _provider_plan(raw)
            emitted.append(output)
            adapter = ResponsesMissionPlanner(
                _local_settings(),
                {"OPENAI_API_KEY": "test-only-key"},
                FakeTransport([_response(output)]),
                plan_validator=unavailable,
            )
            return adapter.plan(**kwargs)

        def regenerate(self, **kwargs: Any) -> MissionPlan:
            """Forbid retrying the model when the checked service is unavailable."""
            raise AssertionError("unexpected model regeneration")

    engine = _engine(tmp_path, Planner(), budget=2)
    result = engine.create("deliver the declared payload")
    assert len(emitted) == len(result.rejected_drafts) == 1
    assert (
        result.failure_evidence is not None
        and result.failure_evidence["failure_owner"] == "SUT_SYSTEM"
    )
    evidence = result.rejected_drafts[0]
    assert evidence.schema_version == UNVERIFIED_DRAFT_SCHEMA
    assert evidence.stage == "controller_validation_unavailable"
    assert evidence.provider_output == emitted[0] and evidence.normalized_output is not None
    assert engine.get(result.request_id).rejected_drafts == result.rejected_drafts
    evidence.verify_integrity()


def test_prompts_share_lifetime_rule_and_keep_generic_semantics() -> None:
    """The shared prompts preserve independent reuse and never copy benchmark identifiers."""
    for role in ("planner", "reviewer", "repairer"):
        text = Path(f"mission/prompts/v0/{role}.md").read_text()
        assert "Adding a DAG edge" in text and "do not downgrade verification" in text
        assert "only for simultaneous Role demand within one Task" not in text
        assert "Episode98" not in text and "Episode64" not in text
