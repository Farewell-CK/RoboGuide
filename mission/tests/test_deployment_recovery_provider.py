"""Production Responses reconsideration conformance without external model calls."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import cast

import pytest
from mission.deployment_assessment import DIAGNOSTIC_SCHEMA, CandidateDiagnostics
from mission.deployment_recovery import (
    DeploymentRecoveryAction,
    DeploymentRecoveryAttempt,
    DeploymentRecoveryDecision,
    DeploymentRecoverySession,
    frozen_document,
)
from mission.intent import GroundedIntent
from mission.models import JSONObject, MissionPlan
from mission.provider_errors import MissionIdentityError, MissionProviderError
from mission.rejected_draft import RejectedPlanError
from mission.responses import ResponsesMissionRepairer
from mission.submission_evidence import canonical_plan_digest
from test_coordination_guidance import _coordination_plan
from test_deployment_assessment import feedback
from test_planners import (
    FakeTransport,
    _assert_strict_provider_objects,
    _current_catalog,
    _grounding,
    _local_settings,
    _provider_plan,
    _response,
    _v0_8_plan,
)


@pytest.fixture(autouse=True)
def frozen_provider_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep Provider deadline and draft freshness checks on the same deterministic clock."""
    monkeypatch.setattr("mission.responses.time.time", lambda: 0.1)


def invoke_decision(
    transport: FakeTransport, document: JSONObject | None = None
) -> tuple[DeploymentRecoveryDecision, JSONObject]:
    """Call the real port with one attributed negative assessment and a durable pending attempt."""
    document = document or _v0_8_plan()
    plan, context = MissionPlan.from_json(document), _grounding()
    observed = feedback(plan)
    observed = replace(
        observed,
        schema_version=DIAGNOSTIC_SCHEMA,
        candidate_diagnostics=tuple(
            CandidateDiagnostics(role.task_id, role.role_id, 2, 2, ()) for role in observed.roles
        ),
    )
    if any(context.coupling_mode != "independent" for context in plan.contexts):
        observed = replace(
            observed,
            decision="unavailable",
            reason_code="unsupported_plan_scope",
            roles=(),
            candidate_diagnostics=(),
            checked_combinations=0,
        )
    session = DeploymentRecoverySession(
        context.request_id,
        plan.mission.mission_id,
        2,
        100,
        900100,
        (
            DeploymentRecoveryAttempt(
                1,
                frozen_document(document),
                canonical_plan_digest(document),
                context.context_digest,
                observed,
                100,
            ),
        ),
    )
    repairer = ResponsesMissionRepairer(
        _local_settings(), {"OPENAI_API_KEY": "test-only-key"}, transport
    )
    return repairer.reconsider_deployment(
        plan.mission.mission_id,
        GroundedIntent(plan.mission.objective, (), ()),
        plan,
        observed,
        _current_catalog(),
        context,
        session,
    )


@pytest.mark.parametrize("action", list(DeploymentRecoveryAction))
def test_actual_adapter_uses_frozen_inputs_and_closed_decision_schema(
    action: DeploymentRecoveryAction,
) -> None:
    """Nested DTO refs, identity, Prompt and common policy inputs match the production path."""
    document = _v0_8_plan()
    plan, context = MissionPlan.from_json(document), _grounding()
    observed = feedback(plan)
    observed = replace(
        observed,
        schema_version=DIAGNOSTIC_SCHEMA,
        candidate_diagnostics=tuple(
            CandidateDiagnostics(role.task_id, role.role_id, 2, 2, ()) for role in observed.roles
        ),
    )
    output: JSONObject = {
        "action": action.value,
        "explanation": "Use only the supplied task and world evidence.",
        "replacement_plan": _provider_plan(document)
        if action is DeploymentRecoveryAction.REVISE
        else None,
    }
    transport = FakeTransport([_response(output)])
    settings = _local_settings()
    repairer = ResponsesMissionRepairer(settings, {"OPENAI_API_KEY": "test-only-key"}, transport)
    session = DeploymentRecoverySession(
        context.request_id,
        plan.mission.mission_id,
        2,
        100,
        900100,
        (
            DeploymentRecoveryAttempt(
                1,
                frozen_document(document),
                canonical_plan_digest(document),
                context.context_digest,
                observed,
                100,
            ),
        ),
    )
    result, raw = repairer.reconsider_deployment(
        plan.mission.mission_id,
        GroundedIntent(plan.mission.objective, (), ()),
        plan,
        observed,
        _current_catalog(),
        context,
        session,
    )
    assert result.action is action and raw == output
    assert result.replacement_plan == (plan if action is DeploymentRecoveryAction.REVISE else None)
    assert len(transport.requests) == 1
    endpoint, _, request, timeout = transport.requests[0]
    assert endpoint.endswith("/responses") and timeout == settings.llm.timeout_seconds
    assert request["model"] == settings.llm.model
    assert request["reasoning"] == {"effort": settings.llm.reasoning_effort}
    assert request["instructions"] == settings.prompts.repairer_path.read_text().strip()
    sent = json.loads(cast(str, request["input"]))
    assert sent["request_mode"] == "deployment_recovery"
    assert sent["reviewed_plan"] == document
    assert sent["grounding_context"] == context.to_json()
    assert sent["initial_operation_assessment"] == observed.to_json()
    assert sent["capability_catalog"] == _current_catalog().to_json()
    assert "review" not in sent and "node_inventory" not in sent
    assert sent["prior_attempts"] == []
    output_format = cast(JSONObject, cast(JSONObject, request["text"])["format"])
    schema = cast(JSONObject, output_format["schema"])
    _assert_strict_provider_objects(schema)
    properties = cast(JSONObject, schema["properties"])
    union = cast(list[JSONObject], cast(JSONObject, properties["replacement_plan"])["anyOf"])
    nested = union[0]
    assert "$defs" not in nested and isinstance(schema["$defs"], dict)
    mission = cast(JSONObject, cast(JSONObject, nested["properties"])["mission"])
    identity = cast(JSONObject, mission["properties"])["id"]
    assert identity == {"type": "string", "enum": [plan.mission.mission_id]}


@pytest.mark.parametrize("damage", ["identity", "coordination"])
def test_bad_replacement_preserves_raw_envelope_without_extra_call(damage: str) -> None:
    """A wrong Mission or missing cooperation contract remains rejected, never auto-downgraded."""
    document = _v0_8_plan()
    damaged = json.loads(json.dumps(document))
    if damage == "identity":
        damaged["mission"]["id"] = "unrequested-mission"
    else:
        damaged["contexts"][0]["coupling_mode"] = "concurrent-cooperation"
        damaged["contexts"][0]["relations"] = []
        damaged["contexts"][0].pop("shared_view", None)
    raw: JSONObject = {
        "action": "revise_plan",
        "explanation": "Proposed replacement.",
        "replacement_plan": _provider_plan(damaged),
    }
    transport = FakeTransport([_response(raw)])
    with pytest.raises(
        MissionIdentityError if damage == "identity" else RejectedPlanError
    ) as caught:
        invoke_decision(transport, document)
    assert getattr(caught.value, "recovery_provider_output", None) == raw
    assert len(transport.requests) == 1


@pytest.mark.parametrize("damage", ["action", "explanation", "missing", "extra", "oversized"])
def test_malformed_decision_is_a_bounded_provider_failure(damage: str) -> None:
    """Invalid decisions cannot become an untyped internal success or spend another call."""
    raw: JSONObject = {
        "action": "recheck",
        "explanation": "Check the source.",
        "replacement_plan": None,
    }
    if damage == "action":
        raw["action"] = "submit_to_node"
    elif damage == "explanation":
        raw["explanation"] = ""
    elif damage == "missing":
        raw.pop("replacement_plan")
    elif damage == "extra":
        raw["node_id"] = "invented-owner"
    else:
        raw["explanation"] = "x" * 262144
    transport = FakeTransport([_response(raw)])
    with pytest.raises(MissionProviderError, match="malformed") as failure:
        invoke_decision(transport)
    if damage == "oversized":
        assert not hasattr(failure.value, "recovery_provider_output")
    else:
        assert getattr(failure.value, "recovery_provider_output", None) == raw
    assert len(transport.requests) == 1


def test_call_timeout_is_capped_by_remaining_durable_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Near-expiry calls cannot renew the full timeout; expired input sends nothing."""
    raw: JSONObject = {
        "action": "recheck",
        "explanation": "Check the source.",
        "replacement_plan": None,
    }
    transport = FakeTransport([_response(raw)])
    monkeypatch.setattr("mission.responses.time.time", lambda: 899.5)
    invoke_decision(transport)
    assert transport.requests[0][3] == pytest.approx(0.6)
    expired = FakeTransport([])
    monkeypatch.setattr("mission.responses.time.time", lambda: 900.1)
    with pytest.raises(TimeoutError, match="deadline expired"):
        invoke_decision(expired)
    assert not expired.requests


def test_real_cooperation_is_preserved_by_reconsideration_validation() -> None:
    """The new mode retains an actual requires-active dependency and its execution view."""
    document = _coordination_plan()
    raw: JSONObject = {
        "action": "revise_plan",
        "explanation": "Keep the active safety dependency.",
        "replacement_plan": _provider_plan(document),
    }
    decision, _ = invoke_decision(FakeTransport([_response(raw)]), document)
    assert decision.replacement_plan == MissionPlan.from_json(document)
