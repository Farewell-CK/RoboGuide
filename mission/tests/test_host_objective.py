"""Offline conformance for explicit host-owned objectives across every MI writer."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from mission.config import MissionConfigError, load_settings
from mission.intent import GroundedIntent
from mission.models import JSONObject, MissionPlan
from mission.provenance import MAX_CALL_BYTES, current_trace, trace_deliberation
from mission.provider_errors import MissionIdentityError
from mission.provider_mission_plan import (
    ECHO_OBJECTIVE_PROFILE,
    HOST_OBJECTIVE_PROFILE,
    ProviderMissionPlanError,
    build_mission_plan_provider_schema,
    normalize_mission_plan_provider_output,
)
from mission.rejected_draft import RejectedPlanError
from mission.responses import (
    ResponsesMissionPlanner,
    ResponsesMissionRepairer,
    ResponsesMissionReviewer,
)
from mission.review import MissionPlanReview
from mission.submission_evidence import canonical_plan_digest
from test_planners import (
    FakeTransport,
    _current_catalog,
    _grounding,
    _local_settings,
    _provider_plan,
    _response,
    _review_output,
    _v0_8_plan,
)


def _draft() -> JSONObject:
    """Create a marked provider candidate with the objective genuinely absent."""
    raw = _provider_plan(_v0_8_plan())
    del cast(JSONObject, raw["mission"])["objective"]
    raw["provider_profile"] = HOST_OBJECTIVE_PROFILE
    return raw


@pytest.mark.parametrize("version", ["v0.8", "v0.9", "v0.10", "v0.11"])
def test_profile_projects_only_objective_and_round_trips_each_explicit_contract(
    version: str,
) -> None:
    """Host assembly preserves every other canonical field and exact selected generation."""
    schema = json.loads(Path(f"contracts/mission/{version}/mission-plan.schema.json").read_bytes())
    before = deepcopy(schema)
    projected = build_mission_plan_provider_schema(
        schema, mission_id="exact-mission", profile=HOST_OBJECTIVE_PROFILE
    )
    properties = cast(JSONObject, projected["properties"])
    mission = cast(JSONObject, properties["mission"])
    assert "objective" not in cast(JSONObject, mission["properties"])
    assert "objective" not in cast(list[str], mission["required"])
    assert cast(JSONObject, mission["properties"])["id"] == {
        "type": "string",
        "enum": ["exact-mission"],
    }
    assert properties["provider_profile"] == {"type": "string", "enum": [HOST_OBJECTIVE_PROFILE]}
    raw = _draft()
    raw["schema_version"] = f"roboguide.mission-plan/{version}"
    original = deepcopy(raw)
    objective = "Put object 17 into basket 53.\nPreserve the user constraint."
    normalized = normalize_mission_plan_provider_output(
        raw, schema, profile=HOST_OBJECTIVE_PROFILE, objective=objective
    )
    assert cast(JSONObject, normalized["mission"])["objective"] == objective
    assert "provider_profile" not in normalized
    assert raw == original and schema == before
    assert normalized["tasks"] == _v0_8_plan()["tasks"]


@pytest.mark.parametrize(
    "writer", ["planner", "regenerate", "repair", "repair-regenerate", "runtime"]
)
@pytest.mark.parametrize("length", [8825, 12205, 19537])
def test_all_writers_preserve_long_original_without_model_echo(writer: str, length: int) -> None:
    """Long frozen input enters Review intact without being regenerated in provider output."""
    original = "Original goal: object 17 into basket 53.\n" + "observation\n" * 2000
    original = original[:length]
    intent = GroundedIntent(original, ("keep the aisle clear",), ("use the admitted frame",))
    raw = _draft()
    settings = replace(_local_settings(), provider_plan_profile=HOST_OBJECTIVE_PROFILE)
    transport = FakeTransport([_response(raw)])
    planner = ResponsesMissionPlanner(settings, {"OPENAI_API_KEY": "offline-only"}, transport)
    repairer = ResponsesMissionRepairer(settings, {"OPENAI_API_KEY": "offline-only"}, transport)
    canonical = _v0_8_plan()
    mission_id = cast(str, cast(JSONObject, canonical["mission"])["id"])
    plan = MissionPlan.from_json(canonical)
    review = MissionPlanReview.from_json(_review_output())
    args = (mission_id, intent, _current_catalog(), _grounding())
    with trace_deliberation("request", "original-dialogue"):
        if writer == "planner":
            result = planner.plan(*args)
        elif writer == "regenerate":
            result = planner.regenerate(*args, raw, [{"message": "original error"}])
        elif writer == "repair":
            result = repairer.repair(mission_id, intent, plan, review, args[2], args[3])
        elif writer == "repair-regenerate":
            result = repairer.regenerate(
                mission_id, intent, plan, review, args[2], args[3], raw, [{"message": "error"}]
            )
        else:
            result = repairer.replan(mission_id, intent, plan, args[2], args[3], {"original": True})
        assert result.mission.objective == original
        assert result.to_json()["tasks"] == canonical["tasks"]
        trace = current_trace("request")
        assert trace is not None
        assembly = json.loads(trace.calls[0])["plan_assembly"]
        assert assembly["provider_output_digest"] == canonical_plan_digest(raw)
        assert assembly["assembled_plan_digest"] == canonical_plan_digest(result.to_json())
        assert assembly["grounded_intent_digest"] == canonical_plan_digest(intent.to_json())
        assert len(trace.calls[0].encode()) <= MAX_CALL_BYTES
    request = transport.requests[0][2]
    assert "max_output_tokens" not in request
    payload = json.loads(cast(str, request["input"]))
    assert payload["grounded_intent"] == intent.to_json()
    assert payload["grounding_context"] == args[3].to_json()
    assert "Omit mission.objective entirely" in cast(str, request["instructions"])
    reviewer_transport = FakeTransport([_response({"approved": True, "issues": []})])
    ResponsesMissionReviewer(
        settings, {"OPENAI_API_KEY": "offline-only"}, reviewer_transport
    ).review(intent, result, args[2], args[3])
    review_input = json.loads(cast(str, reviewer_transport.requests[0][2]["input"]))
    assert "max_output_tokens" not in reviewer_transport.requests[0][2]
    assert review_input["grounded_intent"] == intent.to_json()
    assert review_input["mission_plan"] == result.to_json()


@pytest.mark.parametrize("objective", ["same", "changed target", "line\nbreak", None])
def test_new_profile_refuses_any_model_objective_without_overwriting_raw(
    objective: str | None,
) -> None:
    """Even a correct echo is a forbidden new-profile field, never silently overwritten."""
    raw = _draft()
    cast(JSONObject, raw["mission"])["objective"] = objective
    original = deepcopy(raw)
    settings = replace(_local_settings(), provider_plan_profile=HOST_OBJECTIVE_PROFILE)
    with pytest.raises(
        RejectedPlanError, match="host-owned mission.objective must be omitted"
    ) as e:
        ResponsesMissionPlanner(
            settings, {"OPENAI_API_KEY": "offline-only"}, FakeTransport([_response(raw)])
        ).plan("mission", GroundedIntent("original", (), ()), _current_catalog(), _grounding())
    assert e.value.provider_output == original == raw
    assert e.value.normalized_output is None


def test_host_assembly_does_not_bypass_wrong_mission_or_catalog_validation() -> None:
    """Original identity and semantic contract checks still fence candidates before admission."""
    settings = replace(_local_settings(), provider_plan_profile=HOST_OBJECTIVE_PROFILE)
    raw = _draft()
    expected = cast(str, cast(JSONObject, raw["mission"])["id"])
    cast(JSONObject, raw["mission"])["id"] = "another-mission"
    with pytest.raises(MissionIdentityError, match="requested mission id") as e:
        ResponsesMissionPlanner(
            settings, {"OPENAI_API_KEY": "offline-only"}, FakeTransport([_response(raw)])
        ).plan(expected, GroundedIntent("original", (), ()), _current_catalog(), _grounding())
    assert e.value.provider_output == raw
    assert cast(JSONObject, e.value.normalized_output["mission"])["objective"] == "original"
    raw = _draft()
    task = cast(list[JSONObject], raw["tasks"])[0]
    role = cast(list[JSONObject], task["roles"])[0]
    cast(JSONObject, cast(JSONObject, role["execution_intent"])["operation"])["name"] = "invented"
    original = deepcopy(raw)
    with pytest.raises(RejectedPlanError, match="operation is unknown") as rejected:
        ResponsesMissionPlanner(
            settings, {"OPENAI_API_KEY": "offline-only"}, FakeTransport([_response(raw)])
        ).plan(expected, GroundedIntent("original", (), ()), _current_catalog(), _grounding())
    assert rejected.value.stage == "plan_validation"
    assert rejected.value.provider_output == original == raw
    normalized = rejected.value.normalized_output
    assert normalized is not None
    assert cast(JSONObject, normalized["mission"])["objective"] == "original"
    assert rejected.value.validation_errors == (
        {
            "stage": "plan_validation",
            "code": "plan.capability_catalog_invalid",
            "path": "/",
            "message": str(rejected.value),
        },
    )


@pytest.mark.parametrize("marker", [None, "echo-objective-v1", "future-v99"])
def test_profiles_cannot_be_inferred_from_missing_or_changed_markers(marker: str | None) -> None:
    """A selected new profile never borrows an old or unknown provider response."""
    schema = json.loads(Path("contracts/mission/v0.8/mission-plan.schema.json").read_bytes())
    raw = _draft()
    if marker is None:
        del raw["provider_profile"]
    else:
        raw["provider_profile"] = marker
    with pytest.raises(ProviderMissionPlanError, match="profile"):
        normalize_mission_plan_provider_output(
            raw, schema, profile=HOST_OBJECTIVE_PROFILE, objective="original"
        )
    with pytest.raises(ProviderMissionPlanError):
        normalize_mission_plan_provider_output(_draft(), schema)


def test_config_defaults_legacy_and_requires_known_explicit_profile(tmp_path: Path) -> None:
    """Existing deployments keep strict echo validation; typos refuse before Provider effects."""
    source = Path("config/mission.toml").read_text()
    assert (
        load_settings(Path("config/mission.toml")).provider_plan_profile == ECHO_OBJECTIVE_PROFILE
    )
    config = tmp_path / "mission.toml"
    for profile in (HOST_OBJECTIVE_PROFILE, "unknown-v9"):
        config.write_text(
            source.replace("[mission]\n", f'[mission]\nprovider_plan_profile = "{profile}"\n')
        )
        if profile == HOST_OBJECTIVE_PROFILE:
            assert load_settings(config).provider_plan_profile == profile
        else:
            with pytest.raises(MissionConfigError, match="provider_plan_profile"):
                load_settings(config)


def test_deployment_reconsideration_uses_host_profile_in_nested_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deployment advice shares the same assembly and exact nested provider generation."""
    from test_deployment_recovery_provider import invoke_decision

    monkeypatch.setattr("mission.responses.time.time", lambda: 0.1)
    output: JSONObject = {
        "action": "revise_plan",
        "explanation": "original evidence",
        "replacement_plan": _draft(),
    }
    transport = FakeTransport([_response(output)])
    decision, raw = invoke_decision(transport, profile=HOST_OBJECTIVE_PROFILE)
    assert raw == output
    assert decision.replacement_plan is not None
    assert decision.replacement_plan.to_json() == _v0_8_plan()
    request = transport.requests[0][2]
    schema = cast(JSONObject, cast(JSONObject, request["text"])["format"])["schema"]
    replacement = cast(JSONObject, cast(JSONObject, schema)["properties"])["replacement_plan"]
    nested = cast(list[JSONObject], cast(JSONObject, replacement)["anyOf"])[0]
    properties = cast(JSONObject, nested["properties"])
    assert "provider_profile" in properties
    assert "objective" not in cast(
        JSONObject, cast(JSONObject, properties["mission"])["properties"]
    )


def test_legacy_whitespace_difference_remains_an_identity_failure() -> None:
    """Opt-in host assembly does not silently normalize previously refused legacy output."""
    raw = _provider_plan(_v0_8_plan())
    mission = cast(JSONObject, raw["mission"])
    mission["objective"] = "original goal unchanged"
    with pytest.raises(MissionIdentityError, match="requested mission objective"):
        ResponsesMissionPlanner(
            _local_settings(), {"OPENAI_API_KEY": "offline-only"}, FakeTransport([_response(raw)])
        ).plan(
            cast(str, mission["id"]),
            GroundedIntent("original goal\nunchanged", (), ()),
            _current_catalog(),
            _grounding(),
        )


@pytest.mark.parametrize("wrong", ["object", "relation", "condition"])
def test_host_assembly_preserves_exact_authoritative_goal_gate(wrong: str) -> None:
    """Host-supplied display text never permits a weaker or different terminal predicate."""
    from test_authoritative_goal_satisfaction import _grounding as goal_context
    from test_authoritative_goal_satisfaction import _plan as goal_plan
    from test_authoritative_goal_satisfaction import _settings as goal_settings

    document = goal_plan()
    document["schema_version"] = "roboguide.mission-plan/v0.8"
    for context in cast(list[JSONObject], document["contexts"]):
        context["executor_constraints"] = []
    raw = _provider_plan(document)
    del cast(JSONObject, raw["mission"])["objective"]
    raw["provider_profile"] = HOST_OBJECTIVE_PROFILE
    task = cast(list[JSONObject], raw["tasks"])[0]
    satisfaction = cast(JSONObject, task["satisfaction"])
    verifier = cast(JSONObject, satisfaction["verifier"])
    if wrong == "object":
        verifier["predicate"] = "at(other-object) AND at(target-beta)"
    elif wrong == "relation":
        verifier["predicate"] = "inside(target-alpha) AND at(target-beta)"
    else:
        verifier["predicate"] = "at(target-alpha)"
    settings = replace(goal_settings(), provider_plan_profile=HOST_OBJECTIVE_PROFILE)
    mission = cast(JSONObject, document["mission"])
    with pytest.raises(RejectedPlanError, match="exact authoritative joint goal"):
        ResponsesMissionPlanner(
            settings, {"OPENAI_API_KEY": "offline-only"}, FakeTransport([_response(raw)])
        ).plan(
            cast(str, mission["id"]),
            GroundedIntent(cast(str, mission["objective"]), (), ()),
            _current_catalog(),
            goal_context(),
        )


def test_assembly_trace_retains_raw_or_explicit_truncation_with_original_digest() -> None:
    """Large original drafts stay bounded without claiming that their bytes were retained."""
    from mission.provenance import provider_call, record_plan_assembly

    outputs: tuple[JSONObject, ...] = ({"short": True}, {"large": "x" * MAX_CALL_BYTES})
    for raw in outputs:
        with trace_deliberation("request", "input"):
            provider_call({"model": "offline-fixture"}, {}, None, 0, 1)
            record_plan_assembly(raw, {"canonical": True}, {"objective": "original"})
            trace = current_trace("request")
            assert trace is not None
            call = json.loads(trace.calls[0])
            assembly = call["plan_assembly"]
            assert assembly["provider_output_digest"] == canonical_plan_digest(raw)
            assert len(trace.calls[0].encode()) <= MAX_CALL_BYTES
            if "short" in raw:
                assert assembly["provider_output"] == raw
            else:
                assert assembly["provider_output"]["truncated"] is True
                assert assembly["provider_output"]["byte_length"] > MAX_CALL_BYTES


@pytest.mark.parametrize("submission_mode", [None, "draft_only"])
def test_service_stores_original_draft_and_reviewed_assembly_across_sql_restart(
    tmp_path: Path,
    submission_mode: str | None,
) -> None:
    """The normal Request engine persists raw/canonical attribution and full independent Review."""
    from mission.provenance import validate_snapshot
    from mission.request_engine import MissionRequestEngine
    from mission.request_record import MissionRequestError, MissionRequestLifecycle
    from mission.request_store import MissionRequestStore
    from test_requests import FakeController, FakeInterpreter, _assessment, _inventory

    settings = replace(_local_settings(), provider_plan_profile=HOST_OBJECTIVE_PROFILE)
    raw = _draft()
    cast(JSONObject, raw["mission"])["id"] = "mission-host-test"
    transport = FakeTransport([_response(raw), _response({"approved": True, "issues": []})])
    environment = {"OPENAI_API_KEY": "offline-only"}
    path = tmp_path / "requests.sqlite3"
    controller = FakeController(_inventory())
    engine = MissionRequestEngine(
        MissionRequestStore(path),
        FakeInterpreter([_assessment()]),
        ResponsesMissionPlanner(settings, environment, transport),
        controller,
        _current_catalog(),
        frozenset(),
        id_generator=lambda: "host-test",
        reviewer=ResponsesMissionReviewer(settings, environment, transport),
        provider_identity={"provider_plan_profile": HOST_OBJECTIVE_PROFILE},
    )
    record = engine.create("original instruction", submission_mode)
    assert record.plan is not None and record.review_history[-1].review.approved
    if submission_mode == "draft_only":
        assert record.lifecycle is MissionRequestLifecycle.AWAITING_EXECUTION
        assert controller.submissions == [] and record.draft_digest is not None
        with pytest.raises(MissionRequestError, match="exact"):
            engine.execute(record.request_id, record.draft_revision + 1, record.draft_digest)
        assert controller.submissions == []
        record = engine.execute(record.request_id, record.draft_revision, record.draft_digest)
    assert record.lifecycle is MissionRequestLifecycle.ACCEPTED
    assert record.plan is not None
    assert len(controller.submissions) == 1 and len(transport.requests) == 2
    store = MissionRequestStore(path)
    page = store.provenance(record.request_id, limit=64)
    # Explicit execution adds its own zero-Provider checkpoint; retain the original model trace.
    evidence = next(
        item
        for item in reversed(cast(list[JSONObject], page["snapshots"]))
        if item.get("provider_calls")
    )
    calls = cast(list[JSONObject], evidence["provider_calls"])
    assembly = cast(JSONObject, calls[0]["plan_assembly"])
    assert assembly["provider_output"] == raw
    assert assembly["assembled_plan_digest"] == canonical_plan_digest(record.plan.to_json())
    assert "offline-only" not in json.dumps(evidence)
    validate_snapshot(evidence)
    tampered = deepcopy(evidence)
    raw_evidence = cast(
        JSONObject,
        cast(JSONObject, cast(list[JSONObject], tampered["provider_calls"])[0]["plan_assembly"])[
            "provider_output"
        ],
    )
    raw_evidence["provider_profile"] = "tampered"
    with pytest.raises(ValueError, match="digest mismatch"):
        validate_snapshot(tampered)


def test_profile_prompt_is_frozen_and_drift_refuses_before_provider_effect(tmp_path: Path) -> None:
    """The new versioned Prompt obeys the same startup identity fence as other MI assets."""
    from mission.provider_errors import MissionProviderError

    settings = _local_settings()
    prompt = tmp_path / "profile.md"
    prompt.write_text("Original profile instruction")
    settings = replace(
        settings,
        provider_plan_profile=HOST_OBJECTIVE_PROFILE,
        prompts=replace(settings.prompts, host_objective_path=prompt),
    )
    transport = FakeTransport([])
    planner = ResponsesMissionPlanner(settings, {"OPENAI_API_KEY": "offline-only"}, transport)
    prompt.write_text("changed since startup")
    with pytest.raises(MissionProviderError, match="changed since startup") as failure:
        planner.plan(
            "mission", GroundedIntent("original", (), ()), _current_catalog(), _grounding()
        )
    assert failure.value.configuration_failure
    assert transport.requests == []


def test_opt_in_example_changes_only_the_provider_profile() -> None:
    """The ready-to-use E2 example keeps the original model, schema and call budgets."""
    base = load_settings(Path("tools/e2-generic/mission-dag-gpt-6.1-sol.toml"))
    selected = load_settings(Path("tools/e2-generic/mission-dag-host-objective-gpt-6.1-sol.toml"))
    assert selected == replace(base, provider_plan_profile=HOST_OBJECTIVE_PROFILE)


@pytest.mark.parametrize("route", ["offline", "路" * 256])
def test_optional_assembly_never_reduces_original_provider_call_budget(route: str) -> None:
    """Optional raw observations yield space before original admission, including UTF-8 routes."""
    from mission.provenance import (
        MAX_PROVIDER_CALLS,
        MAX_TRACE_BYTES,
        ensure_provider_budget,
        provider_call,
        record_plan_assembly,
        snapshot,
        validate_snapshot,
    )

    def exercise(enrich: bool) -> list[JSONObject]:
        """Spend the real admission guard until its original count or byte bound refuses."""
        with trace_deliberation("request", "input"):
            retained = None
            retained_digest = None
            for index in range(MAX_PROVIDER_CALLS):
                try:
                    ensure_provider_budget()
                except ValueError:
                    break
                provider_call(
                    {"model": route}, {"model": route, "id": route}, None, index, index + 1
                )
                if enrich:
                    record_plan_assembly(
                        {"draft": "x" * 7000}, {"plan": True}, {"objective": "original"}
                    )
                trace = current_trace("request")
                assert trace is not None
                assert sum(len(item.encode()) for item in trace.calls) <= MAX_TRACE_BYTES
                if index == 1:
                    retained = snapshot("{}", "request", "input", "request", None, 1, "drafted")
                    retained_digest = canonical_plan_digest(retained)
            with pytest.raises(ValueError, match="before dispatch"):
                ensure_provider_budget()
            assert retained is not None
            assert canonical_plan_digest(retained) == retained_digest
            validate_snapshot(retained)
            trace = current_trace("request")
            assert trace is not None
            calls = [cast(JSONObject, json.loads(item)) for item in trace.calls]
            for call in calls:
                call.pop("call_id")
                call.pop("plan_assembly", None)
            return calls

    plain, enriched = exercise(False), exercise(True)
    assert enriched == plain
    if route == "offline":
        assert len(enriched) == MAX_PROVIDER_CALLS


def test_profile_prompt_scope_preserves_role_execution_objectives() -> None:
    """Only the Mission header is host-owned; missing action meaning must still be rejected."""
    prompt = Path("mission/prompts/v0/host-objective.md").read_text()
    assert "Retain every required Role execution_intent.objective" in prompt
    assert "Never emit an objective field" not in prompt
    raw = _draft()
    mission_id = cast(str, cast(JSONObject, raw["mission"])["id"])
    role = cast(list[JSONObject], cast(list[JSONObject], raw["tasks"])[0]["roles"])[0]
    del cast(JSONObject, role["execution_intent"])["objective"]
    settings = replace(_local_settings(), provider_plan_profile=HOST_OBJECTIVE_PROFILE)
    with pytest.raises(RejectedPlanError, match="objective"):
        ResponsesMissionPlanner(
            settings, {"OPENAI_API_KEY": "offline-only"}, FakeTransport([_response(raw)])
        ).plan(mission_id, GroundedIntent("original", (), ()), _current_catalog(), _grounding())


def test_actual_runtime_composition_fences_profile_prompt_drift_on_sql_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Actual compose/TLS/SQL retains original work when only the new Prompt changes on restart."""
    import shutil

    from mission.runtime_replanning_cli import compose
    from mission.runtime_work_store import RuntimeWorkError
    from test_http_security import _pki

    for relative in ("mission/prompts/v0", "contracts/mission/v0.8", "contracts/capability/v0.3"):
        shutil.copytree(relative, tmp_path / relative)
    shutil.copyfile("uv.lock", tmp_path / "uv.lock")
    config = tmp_path / "config/mission.toml"
    config.parent.mkdir()
    settings = _local_settings()
    source = Path("config/mission.toml").read_text()
    source = source.replace("[mission]\n", '[mission]\nprovider_plan_profile="host-objective-v1"\n')
    original_endpoint = load_settings(Path("config/mission.toml")).provider.base_url
    config.write_text(source.replace(original_endpoint, settings.provider.base_url))
    _pki(tmp_path / "pki")
    profile = tmp_path / "worker.toml"
    profile.write_text("""schema_version = "roboguide.runtime-mi-worker/v0.1"
state_db = "work.sqlite3"
mission_config = "config/mission.toml"
request_ids = []
[service]
endpoint = "https://localhost:65535"
tenant = "test-tenant"
principal = "service"
certificate = "pki/tenant-a.crt"
key = "pki/tenant-a.key"
ca = "pki/ca.crt"
[proposer]
endpoint = "https://localhost:65535"
tenant = "test-tenant"
principal = "proposer"
certificate = "pki/tenant-a.crt"
key = "pki/tenant-a.key"
ca = "pki/ca.crt"
""")
    monkeypatch.setenv("OPENAI_API_KEY", "offline-only")
    store, worker, _, _ = compose(profile, tmp_path)
    original_identity = deepcopy(worker.identity)
    try:
        store.admit("original-work", {"source": "original"}, original_identity, 10000)
        assert store.effects("original-work") == []
    finally:
        store.close()
    prompt = tmp_path / "mission/prompts/v0/host-objective.md"
    prompt.write_text(prompt.read_text() + "\nChanged profile instruction.\n")
    restored, next_worker, _, _ = compose(profile, tmp_path)
    try:
        assert next_worker.identity["config_digest"] == original_identity["config_digest"]
        assert next_worker.identity["code_digest"] == original_identity["code_digest"]
        assert next_worker.identity["prompt_digest"] != original_identity["prompt_digest"]
        with pytest.raises(RuntimeWorkError, match="configuration drift"):
            restored.original("original-work", next_worker.identity)
        assert restored.original("original-work", original_identity) == {"source": "original"}
        assert restored.effects("original-work") == []
    finally:
        restored.close()
