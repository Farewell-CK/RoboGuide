"""Verify call-matched sampling, selection independence and complete branch evidence offline."""

import json
from pathlib import Path

import pytest
from mission.config import MissionSettings, load_settings
from mission.provider_errors import MissionProviderError
from roboguide_eval.mi_ablation.blind import export_blind
from roboguide_eval.mi_ablation.cli import freeze, run_slot, summarize
from roboguide_eval.mi_ablation.evidence import digest, read_object, write_new
from roboguide_eval.mi_ablation.matched import POLICY, run_matched
from roboguide_eval.mi_ablation.scoring import object_value, objects
from test_mi_ablation import ROOT, ScriptedTransport, _case, _plan, _provider, _review, _run

ENV = {"OPENAI_API_KEY": "test-secret", "ROBOGUIDE_ALLOW_INSECURE_LLM_HTTP": "1"}


def _settings() -> MissionSettings:
    """Reuse the production experiment configuration with its explicit common model."""
    from dataclasses import replace

    base = load_settings(ROOT / "config/mission.toml", repository_root=ROOT)
    return replace(base, llm=replace(base.llm, model="gpt-6.1-sol", review_model="gpt-6.1-sol"))


def test_d_matches_complete_prefix_and_never_receives_review(tmp_path: Path) -> None:
    """Two initial calls stay shared and each C suffix call buys exactly one D sample."""
    wrong, right = _plan(_case(), wrong=True), _plan(_case())
    abc, _ = _run(
        tmp_path,
        [{"bad": True}, _provider(wrong), _review("RepairPlan"), _provider(right), _review()],
    )
    transport = ScriptedTransport([_provider(right), _provider(right), _provider(wrong)])
    d = run_matched(tmp_path / "trial", _settings(), ENV, transport)
    branch = object_value(d["branch"])
    assert d["shared_prefix_calls"] == 2 and d["target_extra_calls"] == 3
    assert d["actual_provider_calls"] == 3 and branch["logical_provider_calls"] == 5
    assert abc["actual_provider_calls"] == 5 and d["matched_call_count"] is True
    assert branch["candidate_digest"] == digest(wrong)  # No oracle picks the earlier correct plan.
    assert object_value(branch["scoring"])["necessary_conditions_pass"] is False
    assert all(r == transport.requests[0] for r in transport.requests)
    body = json.loads(str(transport.requests[0]["input"]))
    assert (
        not {"review", "rejected_plan", "checks", "prevalidation_recovery_feedback"} & body.keys()
    )
    assert (
        body["grounding_context"] == read_object(tmp_path / "trial/input.json")["grounding_context"]
    )


def test_d_rejected_sample_consumes_budget_and_keeps_last_valid(tmp_path: Path) -> None:
    """An invalid independent sample consumes its one call without hidden regeneration."""
    right = _plan(_case())
    _run(tmp_path, [_provider(right), _review()])
    transport = ScriptedTransport([{"not-a-plan": True}])
    d = run_matched(tmp_path / "trial", _settings(), ENV, transport)
    assert d["actual_provider_calls"] == 1 and d["selected_attempt"] == 0
    assert object_value(d["branch"])["candidate_digest"] == digest(right)
    assert read_object(tmp_path / "trial/matched-planner/attempt-1.json")["valid"] is False


def test_d_source_structure_failure_shares_zero_suffix(tmp_path: Path) -> None:
    """No accepted shared draft and no suffix means no invented extra Planner budget."""
    _run(tmp_path, [{"bad": True}] * 3)
    transport = ScriptedTransport([])
    d = run_matched(tmp_path / "trial", _settings(), ENV, transport)
    assert d["actual_provider_calls"] == d["target_extra_calls"] == 0
    assert object_value(d["branch"])["status"] == "structure_rejected"
    assert d["matched_call_count"] is True


def test_d_skips_infrastructure_source_failure(tmp_path: Path) -> None:
    """A failed Reviewer never authorizes hidden further calls or changes the A result."""
    abc, _ = _run(tmp_path, [_provider(_plan(_case())), MissionProviderError("timeout")])
    d = run_matched(tmp_path / "trial", _settings(), ENV, ScriptedTransport([]))
    assert d["actual_provider_calls"] == 0 and d["matched_call_count"] is False
    assert object_value(d["branch"])["status"] == "not_run_source_unavailable"
    assert read_object(tmp_path / "trial/result.json") == abc


def test_d_preserves_error_without_retry_or_credential(tmp_path: Path) -> None:
    """A D infrastructure failure remains unavailable despite an earlier valid shared plan."""
    _run(tmp_path, [_provider(_plan(_case())), _review()])
    transport = ScriptedTransport([MissionProviderError("test-secret")])
    d = run_matched(
        tmp_path / "trial", _settings(), {**ENV, "OPENAI_API_KEY": "test-secret"}, transport
    )
    assert d["actual_provider_calls"] == 1 and d["matched_call_count"] is False
    assert object_value(d["branch"])["status"] == "unavailable"
    assert "test-secret" not in (tmp_path / "trial/matched-planner/result.json").read_text()
    with pytest.raises(FileExistsError):
        run_matched(tmp_path / "trial", _settings(), ENV, ScriptedTransport([]))


def test_d_refuses_tampered_input_before_claim(tmp_path: Path) -> None:
    """A source/public mismatch cannot manufacture a matching experiment branch."""
    _run(tmp_path, [_provider(_plan(_case())), _review()])
    (tmp_path / "trial/input.json").write_text("{}")
    with pytest.raises(ValueError, match="identity mismatch"):
        run_matched(tmp_path / "trial", _settings(), ENV, ScriptedTransport([]))
    assert not (tmp_path / "trial/matched-planner/claim.json").exists()


def test_d_summary_and_blind_export_retain_extra_requests(tmp_path: Path) -> None:
    """Summary counts physical D calls once and blind export includes its exact candidate."""
    suite = tmp_path / "suite"
    freeze(
        ROOT,
        ROOT / "evaluation/mi_ablation_cases/development.json",
        suite,
        "development",
        1,
        matched_planner=True,
    )
    manifest = read_object(suite / "manifest.json")
    assert manifest["matched_planner_policy"] == POLICY
    abc, _ = _run(tmp_path, [_provider(_plan(_case())), _review()])
    run_matched(
        tmp_path / "trial",
        _settings(),
        ENV,
        ScriptedTransport([_provider(_plan(_case(), wrong=True))]),
    )
    (suite / "trials").mkdir()
    (tmp_path / "trial").rename(suite / "trials" / f"{_case()['id']}-r1")
    summary = summarize(suite)
    assert summary["closed_trial_provider_calls"] == abc["actual_provider_calls"] == 2
    assert summary["total_closed_provider_calls"] == summary["persisted_provider_requests"] == 3
    assert summary["matched_planner_closed"] == 1 and summary["matched_planner_not_started"] == 11
    exported = export_blind(suite, tmp_path / "blind")
    assert exported["distinct_candidates"] == 2
    maps = json.loads((tmp_path / "blind/PRIVATE-branch-map.json").read_text())
    assert {o["branch"] for row in maps for o in row["origins"]} == set("ABCD")


def test_d_interrupted_requests_stay_visible(tmp_path: Path) -> None:
    """A claimed D without result cannot disappear from pending and physical request totals."""
    suite = tmp_path / "suite"
    freeze(
        ROOT,
        ROOT / "evaluation/mi_ablation_cases/development.json",
        suite,
        "development",
        1,
        matched_planner=True,
    )
    path = suite / "trials/dev0-relocate-one-r1/matched-planner"
    write_new(path / "claim.json", {"claimed": True})
    write_new(path / "calls/call-01/request.json", {"sent": "unknown"})
    summary = summarize(suite)
    assert summary["matched_planner_unclosed"] == 1
    assert summary["requests_without_call_result"] == 1


def test_d_stops_on_wrong_response_model(tmp_path: Path) -> None:
    """An unmatched model response stays retained and cannot become a comparable candidate."""
    _run(tmp_path, [_provider(_plan(_case())), _review()])
    d = run_matched(
        tmp_path / "trial",
        _settings(),
        ENV,
        ScriptedTransport([_provider(_plan(_case()))], "different-model"),
    )
    assert d["matched_call_count"] is False
    assert object_value(d["branch"])["status"] == "unavailable"
    assert (tmp_path / "trial/matched-planner/calls/call-01/response.json").exists()


def test_blind_refuses_d_from_another_abc_result(tmp_path: Path) -> None:
    """A retained D receipt must belong to this exact original closed ABC result."""
    suite = tmp_path / "suite"
    freeze(
        ROOT,
        ROOT / "evaluation/mi_ablation_cases/development.json",
        suite,
        "development",
        1,
        matched_planner=True,
    )
    _run(tmp_path, [_provider(_plan(_case())), _review()])
    run_matched(
        tmp_path / "trial", _settings(), ENV, ScriptedTransport([_provider(_plan(_case()))])
    )
    path = tmp_path / "trial/matched-planner/result.json"
    data = read_object(path)
    data["source_result_digest"] = "changed"
    path.write_text(json.dumps(data))
    (suite / "trials").mkdir()
    (tmp_path / "trial").rename(suite / "trials" / f"{_case()['id']}-r1")
    with pytest.raises(ValueError, match="another source"):
        export_blind(suite, tmp_path / "blind")


def test_composition_checks_use_existing_catalog_contracts(tmp_path: Path) -> None:
    """A synthetic task rubric cannot silently name a nonexistent operation or parameter."""
    path = ROOT / "evaluation/mi_ablation_cases/composition-candidate.json"
    document = read_object(path)
    cases = objects(document["cases"])
    assert document["model_output_used_for_case_design"] is True
    assert len(cases) == len({c["template_id"] for c in cases}) == 24
    assert sum(c["expected_disposition"] == "ready" for c in cases) == 20
    assert len({c["family"] for c in cases}) == 6
    catalog = read_object(ROOT / "contracts/capability/v0.3/catalog.json")
    operations = {
        ".".join(str(object_value(o["operation"])[k]) for k in ["namespace", "name"])
        + "@"
        + str(object_value(o["operation"])["version"]): o
        for o in objects(catalog["operations"])
    }
    for case in cases:
        for check in objects(case["checks"]):
            for field in ("selector", "before", "after", "source", "target"):
                if field not in check:
                    continue
                selected = object_value(check[field])
                operation = operations[str(selected["operation"])]
                allowed = {str(p["name"]) for p in objects(operation["parameters"])}
                assert set(object_value(selected["parameters"])) <= allowed
    freeze(ROOT, path, tmp_path / "candidate", "evaluation", 1, matched_planner=True)
    summary = summarize(tmp_path / "candidate")
    assert summary["planned"] == summary["unstarted"] == 24
    assert summary["total_closed_provider_calls"] == 0


def test_opted_in_slot_runs_d_and_exposes_its_original_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public slot entry retains ABC success while returning D's distinct failure."""
    suite = tmp_path / "suite"
    freeze(
        ROOT,
        ROOT / "evaluation/mi_ablation_cases/development.json",
        suite,
        "development",
        1,
        matched_planner=True,
    )
    transport = ScriptedTransport(
        [_provider(_plan(_case())), _review(), MissionProviderError("D original failure")]
    )
    monkeypatch.setattr("roboguide_eval.mi_ablation.runner.UrllibJsonTransport", lambda: transport)
    monkeypatch.setattr("roboguide_eval.mi_ablation.matched.UrllibJsonTransport", lambda: transport)
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    result = run_slot(ROOT, suite, f"{_case()['id']}-r1")
    assert result["error"] is None
    d = object_value(result["matched_planner"])
    assert object_value(d["branch"])["status"] == "unavailable"
    assert object_value(d["error"])["message"] == "D original failure"
    assert len(transport.requests) == 3
    assert "matched_planner" not in read_object(
        suite / "trials" / f"{_case()['id']}-r1/result.json"
    )
