"""Call-count-matched Planner sampling with no semantic oracle or Reviewer feedback."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.config import MissionSettings
from mission.grounding_context import GroundingContextSnapshot
from mission.intent import GroundedIntent
from mission.models import JSONObject, MissionPlan
from mission.rejected_draft import RejectedPlanError
from mission.responses import JsonTransport, ResponsesMissionPlanner, UrllibJsonTransport

from roboguide_eval.mi_ablation.evidence import RecordingTransport, digest, read_object, write_new
from roboguide_eval.mi_ablation.runner import _branch
from roboguide_eval.mi_ablation.scoring import object_value, objects

POLICY = "shared-a-prefix-last-structurally-valid/v0.1"


def run_matched(
    directory: Path,
    settings: MissionSettings,
    environment: Mapping[str, str],
    delegate: JsonTransport | None = None,
) -> JSONObject:
    """Consume D once from a closed ABC trial; never choose a plan by semantic score."""
    source = read_object(directory / "result.json")
    public = read_object(directory / "input.json")
    case = read_object(directory / "private-rubric.json")
    if source["input_digest"] != digest(public) or source["case_digest"] != digest(case):
        raise ValueError("matched Planner source identity mismatch")
    branches = object_value(source["branches"])
    a, c = object_value(branches["A"]), object_value(branches["C"])
    prefix = int(cast(int, a["logical_provider_calls"]))
    total = int(cast(int, c["logical_provider_calls"]))
    if not 0 <= prefix <= total <= 12 or total - prefix > 9:
        raise ValueError("matched Planner source call budget is invalid")
    if total != source["actual_provider_calls"] or len(objects(source["calls"])) != total:
        raise ValueError("matched Planner source call ledger is incomplete")
    selected = MissionPlan.from_json(object_value(a["plan"])) if a["plan"] else None
    if a["candidate_digest"] != (digest(selected.to_json()) if selected else None):
        raise ValueError("matched Planner shared candidate digest mismatch")
    context = GroundingContextSnapshot.from_json(object_value(public["grounding_context"]))
    if context.context_digest != source["grounding_context_digest"]:
        raise ValueError("matched Planner context mismatch")
    intent_input = object_value(public["grounded_intent"])
    intent = GroundedIntent(
        str(intent_input["objective"]),
        tuple(cast(list[str], intent_input["constraints"])),
        tuple(cast(list[str], intent_input["assumptions"])),
    )
    catalog = CanonicalCapabilityCatalog.from_json(object_value(public["capability_catalog"]))
    policy = settings.satisfaction_policy.to_json() if settings.satisfaction_policy else None
    if policy != public["satisfaction_policy"] or settings.llm.model != settings.llm.review_model:
        raise ValueError("matched Planner configuration differs from shared source")
    if any(call.get("response_model") != settings.llm.model for call in objects(source["calls"])):
        # A failed/missing model is allowed only in the explicitly unavailable source path.
        if source["error"] is None:
            raise ValueError("matched Planner source model differs")
    output = directory / "matched-planner"
    write_new(output / "claim.json", {"source_result_digest": digest(source), "policy": POLICY})
    target = total - prefix
    recording = RecordingTransport(
        delegate or UrllibJsonTransport(),
        output / "calls",
        settings.llm.model,
        (environment.get(settings.provider.api_key_env, ""),),
    )
    recording.stage = "matched-planner"
    recording.limit = target
    planner = ResponsesMissionPlanner(settings, environment, recording)
    error: JSONObject | None = None
    selected_attempt = 0 if selected else None
    source_error = source.get("error")
    source_unavailable = source_error is not None and (
        object_value(source_error).get("type") != "RejectedPlanError"
    )
    status = "candidate_ready" if selected else "structure_rejected"
    if source_unavailable:
        status = "not_run_source_unavailable"
    else:
        for index in range(1, target + 1):
            try:
                candidate = planner.plan(str(public["mission_id"]), intent, catalog, context)
                write_new(
                    output / f"attempt-{index}.json",
                    {
                        "valid": True,
                        "plan": candidate.to_json(),
                    },
                )
                selected, selected_attempt, status = candidate, index, "candidate_ready"
            except RejectedPlanError as rejected:
                write_new(
                    output / f"attempt-{index}.json",
                    {
                        "valid": False,
                        "provider_output": rejected.provider_output,
                        "normalized_output": rejected.normalized_output,
                        "validation_errors": list(rejected.validation_errors),
                    },
                )
            except Exception as failure:
                status = "unavailable"
                error = {"type": type(failure).__name__, "message": str(failure)}
                break
    branch = _branch(status, selected, prefix + len(recording.calls), objects(case["checks"]))
    calls = objects(source["calls"])[:prefix] + recording.calls
    usage = [call.get("usage") for call in calls]
    branch["logical_provider_elapsed_seconds"] = sum(
        float(cast(float, c["elapsed_seconds"])) for c in calls
    )
    branch["logical_total_tokens"] = (
        sum(cast(int, object_value(u)["total_tokens"]) for u in usage)
        if all(isinstance(u, dict) and type(u.get("total_tokens")) is int for u in usage)
        else None
    )
    result: JSONObject = {
        "schema_version": "roboguide.mi-matched-planner/v0.1",
        "policy": POLICY,
        "source_result_digest": digest(source),
        "shared_prefix_calls": prefix,
        "target_extra_calls": target,
        "actual_provider_calls": len(recording.calls),
        "selected_attempt": selected_attempt,
        "branch": branch,
        "calls": recording.calls,
        "error": error,
        "matched_call_count": not source_unavailable
        and error is None
        and prefix + len(recording.calls) == total,
        "selection_basis": "last_structurally_valid_including_shared_A_never_semantic_score",
    }
    safe = object_value(recording._safe(result))
    write_new(output / "result.json", safe)
    return safe
