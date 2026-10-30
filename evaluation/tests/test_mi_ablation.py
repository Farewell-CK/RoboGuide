"""Exercise shared-prefix evidence, budget/refusal paths and independent scoring offline."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest
from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.config import load_settings
from mission.models import JSONObject, MissionPlan
from mission.provider_errors import MissionProviderError
from roboguide_eval.mi_ablation.blind import export_blind, label_counts
from roboguide_eval.mi_ablation.cli import freeze, load_frozen, summarize
from roboguide_eval.mi_ablation.evidence import digest, read_object, write_new
from roboguide_eval.mi_ablation.runner import run_trial
from roboguide_eval.mi_ablation.scoring import check_plan, object_value, objects

ROOT = Path(__file__).resolve().parents[2]
MODEL = "gpt-6.1-sol"
SECRET = "unit-test-credential-never-record"


def _case() -> JSONObject:
    """Choose a public synthetic task with a private exact-parameter scoring requirement."""
    return objects(read_object(ROOT / "evaluation/mi_ablation_cases/development.json")["cases"])[0]


def _plan(case: JSONObject, *, wrong: bool = False) -> JSONObject:
    """Build a structurally legal candidate whose operation may contradict its objective."""
    plan = read_object(ROOT / "scenarios/mission-front-half-v0.7/mission-plan.json")
    plan["schema_version"] = "roboguide.mission-plan/v0.8"
    mission = object_value(plan["mission"])
    mission.update(
        id=f"mi-ablation-{case['id']}-r1",
        objective=object_value(case["grounded_intent"])["objective"],
    )
    for context in objects(plan["contexts"]):
        context["executor_constraints"] = []
    role = objects(objects(plan["tasks"])[0]["roles"])[0]
    parameters = copy.deepcopy(
        object_value(object_value(objects(case["checks"])[0]["selector"])["parameters"])
    )
    if wrong:
        parameters["destination"] = "unrequested-destination"
    object_value(role["execution_intent"])["parameters"] = parameters
    return plan


def _provider(plan: JSONObject) -> JSONObject:
    """Encode actual production DTO parameter arrays for transport-only test responses."""
    raw = copy.deepcopy(plan)
    for task in objects(raw["tasks"]):
        for role in objects(task["roles"]):
            intent = object_value(role["execution_intent"])
            intent["parameters"] = [
                {"key": k, "value": v} for k, v in object_value(intent["parameters"]).items()
            ]
    return raw


def _review(action: str | None = None) -> JSONObject:
    """Return one contractual review, with no scripted semantic score."""
    return {
        "approved": action is None,
        "issues": []
        if action is None
        else [
            {
                "code": "goal.wrong_target",
                "path": "/tasks/0/roles/0/execution_intent/parameters",
                "message": "Destination differs from the supplied task.",
                "required_action": action,
            }
        ],
    }


class ScriptedTransport:
    """Return a finite scripted sequence while exercising real parsing and adapter envelopes."""

    def __init__(self, outputs: list[JSONObject | Exception], model: str = MODEL) -> None:
        """Keep fake responses separate from any asserted real-model behavior."""
        self.outputs = list(outputs)
        self.requests: list[JSONObject] = []
        self.model = model

    def post_json(
        self, url: str, headers: Mapping[str, str], payload: JSONObject, timeout_seconds: float
    ) -> JSONObject:
        """Consume exactly one scripted output and preserve an original exception."""
        self.requests.append(copy.deepcopy(payload))
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return {
            "model": self.model,
            "status": "completed",
            "error": None,
            "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
            "output": [
                {
                    "type": "message",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": json.dumps(output)},
                    ],
                }
            ],
        }


def _run(
    tmp_path: Path, outputs: list[JSONObject | Exception], model: str = MODEL
) -> tuple[JSONObject, ScriptedTransport]:
    """Compose the production adapters with a local fake transport and immutable evidence."""
    settings = load_settings(ROOT / "config/mission.toml", repository_root=ROOT)
    settings = replace(settings, llm=replace(settings.llm, model=MODEL, review_model=MODEL))
    transport = ScriptedTransport(outputs, model)
    result = run_trial(
        _case(),
        1,
        tmp_path / "trial",
        settings,
        CanonicalCapabilityCatalog.load(settings.capability_catalog_path),
        {"OPENAI_API_KEY": SECRET, "ROBOGUIDE_ALLOW_INSECURE_LLM_HTTP": "1"},
        transport,
    )
    return result, transport


def test_ablation_shared_draft_review_and_repair_inputs(tmp_path: Path) -> None:
    """A/B share the exact candidate; C repairs the exact B review under one context."""
    wrong, right = _plan(_case(), wrong=True), _plan(_case())
    result, transport = _run(
        tmp_path, [_provider(wrong), _review("RepairPlan"), _provider(right), _review()]
    )
    branches = object_value(result["branches"])
    a, b, c = (object_value(branches[n]) for n in "ABC")
    assert result["actual_provider_calls"] == 4
    assert a["candidate_digest"] == b["candidate_digest"] == digest(wrong)
    assert c["candidate_digest"] == digest(right)
    assert c["status"] == "Approved"
    assert object_value(a["scoring"])["necessary_conditions_pass"] is False
    assert object_value(c["scoring"])["necessary_conditions_pass"] is True
    assert object_value(c["scoring"])["semantic_correctness"] == "pending_blind_human_review"
    repair_input = json.loads(str(transport.requests[2]["input"]))
    assert repair_input["review"] == _review("RepairPlan")
    assert repair_input["rejected_plan"] == wrong
    contexts = [json.loads(str(p["input"]))["grounding_context"] for p in transport.requests]
    assert all(ctx == contexts[0] for ctx in contexts)
    assert all("checks" not in json.loads(str(p["input"])) for p in transport.requests)
    assert [object_value(branches[n])["logical_provider_calls"] for n in "ABC"] == [1, 2, 4]


@pytest.mark.parametrize(
    "action,route",
    [(None, "Approved"), ("RequestClarification", "Clarification"), ("RejectDraft", "Rejected")],
)
def test_no_repair_for_other_routes(tmp_path: Path, action: str | None, route: str) -> None:
    """Approval, clarification and terminal rejection consume no hidden repair request."""
    result, transport = _run(tmp_path, [_provider(_plan(_case())), _review(action)])
    assert result["actual_provider_calls"] == len(transport.requests) == 2
    assert object_value(object_value(result["branches"])["C"])["status"] == route


def test_repair_budget_is_bounded(tmp_path: Path) -> None:
    """Repeated valid RepairPlan reviews stop after the exact two authorized repairs."""
    raw = _provider(_plan(_case(), wrong=True))
    result, transport = _run(
        tmp_path,
        [raw, _review("RepairPlan"), raw, _review("RepairPlan"), raw, _review("RepairPlan")],
    )
    assert len(transport.requests) == 6
    assert (
        object_value(object_value(result["branches"])["C"])["status"] == "repair_budget_exhausted"
    )


def test_structural_recovery_retains_raw_draft_and_feedback(tmp_path: Path) -> None:
    """A failed schema draft reaches regeneration intact without changing rubric or context."""
    result, transport = _run(tmp_path, [{"not_a_plan": True}, _provider(_plan(_case())), _review()])
    request = json.loads(str(transport.requests[1]["input"]))
    feedback = request["prevalidation_recovery_feedback"]
    assert feedback["previous_rejected_provider_output"] == {"not_a_plan": True}
    assert feedback["validation_errors"]
    assert result["actual_provider_calls"] == 3
    assert read_object(tmp_path / "trial/planner/attempt-0.json")["valid"] is False


def test_structural_budget_exhaustion_never_reaches_reviewer(tmp_path: Path) -> None:
    """Three invalid drafts retain a shared refusal in all branches, without a review call."""
    result, transport = _run(tmp_path, [{"bad": True}] * 3)
    assert len(transport.requests) == 3
    assert all(
        object_value(b)["status"] == "structure_rejected"
        for b in object_value(result["branches"]).values()
    )


def test_provider_failure_no_retry_and_credentials_redacted(tmp_path: Path) -> None:
    """Infrastructure errors remain unavailable and cannot leak the authorized credential."""
    result, transport = _run(tmp_path, [MissionProviderError(SECRET, status_code=502)])
    assert len(transport.requests) == 1
    assert object_value(result["error"])["status_code"] == 502
    assert all(SECRET not in p.read_text() for p in tmp_path.rglob("*.json"))
    assert all("Authorization" not in p.read_text() for p in tmp_path.rglob("request.json"))


def test_response_model_mismatch_preserved_and_stopped(tmp_path: Path) -> None:
    """A different returned identity is retained as raw evidence but cannot advance."""
    result, transport = _run(tmp_path, [_provider(_plan(_case()))], "other-model")
    assert len(transport.requests) == 1
    assert object_value(object_value(result["branches"])["A"])["status"] == "unavailable"
    assert read_object(tmp_path / "trial/calls/call-01/response.json")["model"] == "other-model"


def test_trial_and_evidence_are_not_overwritten(tmp_path: Path) -> None:
    """Repeated slot consumption refuses before another model invocation."""
    _run(tmp_path, [_provider(_plan(_case())), _review()])
    with pytest.raises(FileExistsError):
        _run(tmp_path, [])
    with pytest.raises(FileExistsError):
        write_new(tmp_path / "trial/result.json", {})


def test_objective_keywords_cannot_hide_wrong_parameters() -> None:
    """A correct objective with an incorrect executable destination fails the necessary check."""
    raw = _plan(_case(), wrong=True)
    result = check_plan(MissionPlan.from_json(raw), objects(_case()["checks"]))
    assert result["necessary_conditions_pass"] is False
    with pytest.raises(ValueError):
        check_plan(MissionPlan.from_json(raw), [{"id": "x", "kind": "invented-check"}])


def test_frozen_sources_and_rubrics_are_checked_before_calls(tmp_path: Path) -> None:
    """Mutation of a private rubric cannot silently change a frozen evaluation."""
    directory = tmp_path / "suite"
    freeze(
        ROOT, ROOT / "evaluation/mi_ablation_cases/development.json", directory, "development", 1
    )
    manifest, cases = load_frozen(ROOT, directory)
    assert len(cases) == 12 and len(objects(manifest["slots"])) == 12
    summary = summarize(directory)
    assert summary["planned"] == summary["unstarted"] == 12
    assert summary["success_rate"] is None
    document = read_object(directory / "cases.json")
    objects(document["cases"])[0]["expected_disposition"] = "tampered"
    (directory / "cases.json").write_text(json.dumps(document))
    with pytest.raises(ValueError, match="case/rubric drift"):
        load_frozen(ROOT, directory)


def test_candidate_set_is_disjoint_and_explicitly_template_clustered() -> None:
    """Keep holdout variants separate while disclosing the actual twelve template clusters."""
    dev = objects(read_object(ROOT / "evaluation/mi_ablation_cases/development.json")["cases"])
    evaluation = objects(
        read_object(ROOT / "evaluation/mi_ablation_cases/evaluation-candidate.json")["cases"]
    )
    assert len(evaluation) == 48
    assert {c["id"] for c in dev}.isdisjoint({c["id"] for c in evaluation})
    assert len({c["template_id"] for c in evaluation}) == 12
    assert len({c["family"] for c in evaluation}) == 6


def test_repair_structure_recovery_binds_original_review(tmp_path: Path) -> None:
    """Repair regeneration receives the original rejection plus the invalid repair evidence."""
    wrong, right = _plan(_case(), wrong=True), _plan(_case())
    result, transport = _run(
        tmp_path,
        [
            _provider(wrong),
            _review("RepairPlan"),
            {"broken_repair": True},
            _provider(right),
            _review(),
        ],
    )
    payload = json.loads(str(transport.requests[3]["input"]))
    assert payload["rejected_plan"] == wrong
    assert payload["review"] == _review("RepairPlan")
    assert payload["repair_prevalidation_recovery_feedback"][
        "previous_rejected_provider_output"
    ] == {
        "broken_repair": True,
    }
    assert result["actual_provider_calls"] == 5
    assert object_value(object_value(result["branches"])["C"])["logical_total_tokens"] == 150


def test_review_failure_retains_original_a_result(tmp_path: Path) -> None:
    """A later infrastructure failure cannot rewrite the shared Planner branch as failed."""
    result, _ = _run(tmp_path, [_provider(_plan(_case())), MissionProviderError("offline")])
    branches = object_value(result["branches"])
    assert object_value(branches["A"])["status"] == "candidate_ready"
    assert object_value(branches["B"])["status"] == "unavailable"
    assert object_value(branches["C"])["status"] == "unavailable"


def test_blind_packets_deduplicate_shared_plans_and_fence_unreviewed_labels(tmp_path: Path) -> None:
    """Blind reviewers see the original task/candidate without branch or model-review clues."""
    _run(tmp_path, [_provider(_plan(_case())), _review()])
    suite = tmp_path / "suite"
    freeze(ROOT, ROOT / "evaluation/mi_ablation_cases/development.json", suite, "development", 1)
    trials = suite / "trials"
    trials.mkdir()
    (tmp_path / "trial").rename(trials / f"{_case()['id']}-r1")
    summary = export_blind(suite, tmp_path / "blind")
    assert summary["distinct_candidates"] == 1
    assert len(summary["unclosed_or_unstarted_slots"]) == 11
    packet = read_object(tmp_path / "blind/packets/candidate-0001.json")
    assert set(packet) == {"id", "input", "candidate"}
    with pytest.raises(ValueError, match="unreviewed"):
        label_counts(tmp_path / "blind/labels-template.json")


def test_trial_missing_result_stays_unclosed_in_denominator(tmp_path: Path) -> None:
    """An interrupted claimed slot is neither silently retried nor removed from reporting."""
    suite = tmp_path / "suite"
    freeze(ROOT, ROOT / "evaluation/mi_ablation_cases/development.json", suite, "development", 1)
    write_new(suite / "trials/dev0-relocate-one-r1/claim.json", {"claimed": True})
    summary = summarize(suite)
    assert summary["planned"] == 12
    assert summary["unclosed_claims"] == 1
    assert summary["unstarted"] == 11


def test_missing_evidence_cannot_be_scored_as_a_correct_plan() -> None:
    """A structurally valid plan does not fill the explicit missing-information case."""
    raw = MissionPlan.from_json(_plan(_case()))
    score = check_plan(raw, [{"id": "gap", "kind": "requires_missing_evidence"}])
    assert score["necessary_conditions_pass"] is False
    assert score["semantic_correctness"] == "pending_blind_human_review"


@pytest.mark.parametrize("kind", ["resource", "timing", "capability_constraint"])
def test_scoring_checks_actual_contract_fields(kind: str) -> None:
    """Resource, deadline and capability checks reject a missing executable declaration."""
    raw = _plan(_case())
    task = objects(raw["tasks"])[0]
    role = objects(task["roles"])[0]
    requirement = object_value(role["requirements"])
    selector = objects(_case()["checks"])[0]["selector"]
    check: JSONObject = {"id": "field", "kind": kind, "selector": selector}
    if kind == "resource":
        check.update(resource_kind="space", units=1)
    elif kind == "timing":
        check.update(field="completion_deadline_offset_ms", value=300000)
    else:
        check.update(
            contract="object.relocate@v1",
            constraint=objects(objects(requirement["capabilities"])[0]["constraints"])[0],
        )
    assert check_plan(MissionPlan.from_json(raw), [check])["necessary_conditions_pass"] is True
    if kind == "resource":
        requirement["resources"] = []
    elif kind == "timing":
        object_value(task["timing"])["completion_deadline_offset_ms"] = None
    else:
        objects(requirement["capabilities"])[0]["constraints"] = []
    assert check_plan(MissionPlan.from_json(raw), [check])["necessary_conditions_pass"] is False


def test_scoring_independent_parallel_and_required_execution_relation() -> None:
    """No DAG does not imply cooperation; only the exact active relation satisfies that check."""
    raw = _plan(_case())
    tasks = objects(raw["tasks"])
    first = tasks[0]
    second = copy.deepcopy(first)
    second["id"] = "second"
    role = objects(second["roles"])[0]
    role.update(id="second-role", context_role="second-context-role")
    parameters = object_value(object_value(role["execution_intent"])["parameters"])
    parameters["object"] = "second-object"
    tasks.append(second)
    context = objects(raw["contexts"])[0]
    objects(context["roles"]).append({"id": "second-context-role", "actor": "second-actor"})
    objects(object_value(raw["mission"])["actors"]).append({"id": "second-actor"})
    one = object_value(objects(_case()["checks"])[0]["selector"])
    two: JSONObject = {"operation": "object.relocate@v1", "parameters": parameters}
    precedence: JSONObject = {"id": "order", "kind": "precedence", "before": one, "after": two}
    independent: JSONObject = {"id": "independent", "kind": "independent"}
    active: JSONObject = {"id": "active", "kind": "requires_active", "source": one, "target": two}
    plan = MissionPlan.from_json(raw)
    assert check_plan(plan, [independent])["necessary_conditions_pass"] is True
    assert check_plan(plan, [precedence, active])["necessary_conditions_pass"] is False
    second["depends_on"] = [first["id"]]
    assert check_plan(MissionPlan.from_json(raw), [precedence])["necessary_conditions_pass"] is True
    second["depends_on"] = []
    context["coupling_mode"] = "concurrent-cooperation"
    context["shared_view"] = {
        "bindings": [{"context_role_id": "carrier", "field": "execution"}],
        "include_freshness": True,
    }
    context["relations"] = [
        {
            "id": "active",
            "kind": "requires-active",
            "source": {"task_id": first["id"], "role_id": objects(first["roles"])[0]["id"]},
            "target": {"task_id": "second", "role_id": "second-role"},
        }
    ]
    plan = MissionPlan.from_json(raw)
    assert check_plan(plan, [active])["necessary_conditions_pass"] is True
    assert check_plan(plan, [independent])["necessary_conditions_pass"] is False
