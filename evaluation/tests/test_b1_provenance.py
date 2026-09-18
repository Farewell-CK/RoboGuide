"""F-07 deterministic tests for Formal B1 provenance and population admission."""

from __future__ import annotations

import copy
from typing import Any

from roboguide_eval.b1_provenance import (
    B1ProvenanceRecord,
    ProvenanceFailure,
    admit_to_formal_population,
    digest,
    verify_b1_provenance,
)

B2_STATIC_PLAN: dict[str, Any] = {
    "schema_version": "roboguide.mission-plan/v0.7",
    "mission": {
        "id": "mission-e1-i-shared-world-episode-51",
        "objective": "Cover both official navigation goals.",
        "actors": [{"id": "shared-robot-a"}, {"id": "shared-robot-b"}],
    },
    "contexts": [],
    "tasks": [
        {
            "id": "task-navigate-goal-a",
            "description": "Navigate one robot to any_targets|0.",
            "depends_on": [],
            "roles": [],
            "context_id": "ctx-goal-a",
            "timing": None,
            "satisfaction": None,
        }
    ],
}

MI_PLAN_DIFFERENT_TASK_IDS: dict[str, Any] = {
    "schema_version": "roboguide.mission-plan/v0.8",
    "mission": {
        "id": "mission-mi-runtime-xyz",
        "objective": "Cover both official navigation goals.",
        "actors": [{"id": "actor-robot-object"}, {"id": "actor-robot-goal"}],
    },
    "contexts": [],
    "tasks": [
        {
            "id": "reach-object",
            "description": "MI-chosen decomposition A.",
            "depends_on": [],
            "roles": [],
            "context_id": "ctx-a",
            "timing": None,
            "satisfaction": None,
        },
        {
            "id": "reach-goal-receptacle",
            "description": "MI-chosen decomposition B.",
            "depends_on": [],
            "roles": [],
            "context_id": "ctx-a",
            "timing": None,
            "satisfaction": None,
        },
    ],
}

FROZEN_INPUT: dict[str, Any] = {"instruction": "two robots, two goals"}
INSTRUCTION = "two robots, two goals"


def _request(
    plan: dict[str, Any],
    *,
    draft: bool = True,
    instruction: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build one Mission Service request record around an accepted plan.

    Includes genuine MI generation evidence (draft digest + first dialogue
    instruction turn) so the canonical verifier can bind the invocation.
    """
    dialogue = (
        [{"kind": "instruction", "content": instruction}]
        if instruction is not None
        else [{"kind": "instruction", "content": "two robots, two goals"}]
    )
    return {
        "request_id": "request-mi-run-1",
        "mission_id": plan["mission"]["id"],
        "lifecycle": "Accepted",
        "plan": plan,
        "draft_digest": "digest-placeholder" if draft else None,
        "draft_revision": 1 if draft else 0,
        "dialogue": dialogue,
        **extra,
    }


def _receipt(
    plan: dict[str, Any],
    mission_id: str | None = None,
    group_id: str | None = None,
) -> dict[str, Any]:
    """Build one Controller submission receipt from observable evidence."""
    task_ids = [task["id"] for task in plan.get("tasks", [])]
    return {
        "mission_id": mission_id or plan["mission"]["id"],
        "group_id": group_id or "group-mi-runtime-xyz",
        "registered_task_ids": task_ids,
    }


def _record(
    input_doc: dict[str, Any],
    plan: dict[str, Any],
    mission_id: str,
    benchmark: dict[str, Any],
) -> B1ProvenanceRecord:
    """Build one provenance record whose digests match the given evidence."""
    return B1ProvenanceRecord(
        run_id="run-1",
        input_digest=digest(input_doc),
        mi_run_identity="request-mi-run-1",
        accepted_plan_digest=digest(plan),
        mission_id=mission_id,
        controller_submission_identity=f"group-{mission_id}",
        execution_identity="exec-1",
        benchmark_evidence_digest=digest(benchmark),
    )


def _benchmark(pddl: bool = True) -> dict[str, Any]:
    """Build one minimal shared-world benchmark authority document."""
    return {"official_pddl_success": pddl, "outcomes": {}}


def test_f07_modified_b2_plan_forgery_is_rejected() -> None:
    """A copied B2 plan with a new MissionId fails provenance."""
    forged = copy.deepcopy(B2_STATIC_PLAN)
    forged["mission"]["id"] = "mission-forged-123"
    # A forged record has no MI draft/review generation evidence to bind.
    request = _request(forged, draft=False)
    benchmark = _benchmark()
    record = _record(FROZEN_INPUT, forged, "mission-forged-123", benchmark)
    result = verify_b1_provenance(
        record=record,
        frozen_input=FROZEN_INPUT,
        request_record=request,
        controller_submission=_receipt(forged, group_id=record.controller_submission_identity),
        benchmark_evidence=benchmark,
        static_b2_plan=B2_STATIC_PLAN,
        observed_execution_ids=("exec-1",),
        instruction_text=INSTRUCTION,
    )
    assert not result.passed
    assert ProvenanceFailure.STATIC_B2_PLAN_EQUALITY in result.failures
    assert result.is_static_b2_plan is True


def test_f07_genuine_mi_plan_with_different_task_ids_passes() -> None:
    """Genuine MI output with different TaskIds passes the full chain."""
    plan = MI_PLAN_DIFFERENT_TASK_IDS
    request = _request(plan)
    benchmark = _benchmark()
    record = _record(FROZEN_INPUT, plan, "mission-mi-runtime-xyz", benchmark)
    result = verify_b1_provenance(
        record=record,
        frozen_input=FROZEN_INPUT,
        request_record=request,
        controller_submission=_receipt(plan, group_id=record.controller_submission_identity),
        benchmark_evidence=benchmark,
        static_b2_plan=B2_STATIC_PLAN,
        observed_execution_ids=("exec-1",),
        instruction_text=INSTRUCTION,
    )
    assert result.passed, result.failures
    assert not result.is_static_b2_plan
    assert result.plan_digest == digest(plan)


def test_f07_plan_digest_mismatch_fails() -> None:
    """A record claiming a different accepted-plan digest fails."""
    plan = MI_PLAN_DIFFERENT_TASK_IDS
    request = _request(plan)
    benchmark = _benchmark()
    record = _record(FROZEN_INPUT, plan, "mission-mi-runtime-xyz", benchmark)
    tampered = copy.deepcopy(record)
    object.__setattr__(tampered, "accepted_plan_digest", digest(B2_STATIC_PLAN))
    result = verify_b1_provenance(
        record=tampered,
        frozen_input=FROZEN_INPUT,
        request_record=request,
        controller_submission=_receipt(plan, group_id=record.controller_submission_identity),
        benchmark_evidence=benchmark,
        static_b2_plan=B2_STATIC_PLAN,
        observed_execution_ids=("exec-1",),
        instruction_text=INSTRUCTION,
    )
    assert not result.passed
    assert ProvenanceFailure.PLAN_DIGEST_MISMATCH in result.failures


def test_f07_request_run_identity_mismatch_fails() -> None:
    """A record bound to another MI run identity fails provenance."""
    plan = MI_PLAN_DIFFERENT_TASK_IDS
    request = _request(plan)
    benchmark = _benchmark()
    record = _record(FROZEN_INPUT, plan, "mission-mi-runtime-xyz", benchmark)
    object.__setattr__(record, "mi_run_identity", "request-other-run")
    result = verify_b1_provenance(
        record=record,
        frozen_input=FROZEN_INPUT,
        request_record=request,
        controller_submission=_receipt(plan, group_id=record.controller_submission_identity),
        benchmark_evidence=benchmark,
        static_b2_plan=B2_STATIC_PLAN,
        observed_execution_ids=("exec-1",),
        instruction_text=INSTRUCTION,
    )
    assert not result.passed
    assert ProvenanceFailure.MI_RUN_MISSING in result.failures


def test_f07_controller_task_registration_mismatch_fails() -> None:
    """The Controller registering different task ids than MI accepted fails."""
    plan = MI_PLAN_DIFFERENT_TASK_IDS
    request = _request(plan)
    benchmark = _benchmark()
    record = _record(FROZEN_INPUT, plan, "mission-mi-runtime-xyz", benchmark)
    wrong_receipt = _receipt(plan, group_id=record.controller_submission_identity)
    wrong_receipt["registered_task_ids"] = ["some-other-task"]
    result = verify_b1_provenance(
        record=record,
        frozen_input=FROZEN_INPUT,
        request_record=request,
        controller_submission=wrong_receipt,
        benchmark_evidence=benchmark,
        static_b2_plan=B2_STATIC_PLAN,
        observed_execution_ids=("exec-1",),
        instruction_text=INSTRUCTION,
    )
    assert not result.passed
    assert ProvenanceFailure.CONTROLLER_TASK_REGISTRATION_MISMATCH in result.failures


def test_f07_admission_failure_excluded_from_population() -> None:
    """A provenance-failing run is excluded from the formal population."""
    admission = admit_to_formal_population(
        provenance_passed=False,
        provenance_failures=(ProvenanceFailure.STATIC_B2_PLAN_EQUALITY,),
        benchmark_tri_state="BENCHMARK_TRUE",
        valid_run="INVALID_INFRA",
        valid_for_benchmark_population=True,
    )
    assert admission.valid_for_formal_population is False
    assert "static_b2_plan_equality" in admission.invalid_reasons


def test_f07_admission_pass_included() -> None:
    """A passing chain with an authoritative outcome enters the population."""
    admission = admit_to_formal_population(
        provenance_passed=True,
        provenance_failures=(),
        benchmark_tri_state="BENCHMARK_TRUE",
        valid_run="VALID_RUN",
        valid_for_benchmark_population=True,
    )
    assert admission.valid_for_formal_population is True
    assert admission.invalid_reasons == ()


def test_f07_unavailable_outcome_excluded_even_with_provenance() -> None:
    """Provenance alone cannot admit a run without benchmark authority."""
    admission = admit_to_formal_population(
        provenance_passed=True,
        provenance_failures=(),
        benchmark_tri_state="BENCHMARK_UNAVAILABLE",
        valid_run="INVALID_INFRA",
        valid_for_benchmark_population=False,
    )
    assert admission.valid_for_formal_population is False
    assert "benchmark_outcome_unavailable" in admission.invalid_reasons


def test_f07_genuine_mi_with_b2_equal_plan_still_passes_provenance() -> None:
    """Genuine MI provenance + coincidental B2-equal plan: diagnostic only."""
    plan = copy.deepcopy(B2_STATIC_PLAN)
    plan["mission"]["id"] = "mission-genuine-mi-999"
    request = _request(plan)
    benchmark = _benchmark()
    record = _record(FROZEN_INPUT, plan, "mission-genuine-mi-999", benchmark)
    result = verify_b1_provenance(
        record=record,
        frozen_input=FROZEN_INPUT,
        request_record=request,
        controller_submission=_receipt(plan, group_id=record.controller_submission_identity),
        benchmark_evidence=benchmark,
        static_b2_plan=B2_STATIC_PLAN,
        observed_execution_ids=("exec-1",),
        instruction_text=INSTRUCTION,
    )
    assert result.passed
    assert result.is_static_b2_plan is True  # diagnostic flag only


def test_f07_forged_b2_without_genuine_mi_fails() -> None:
    """B2-equal plan without genuine MI invocation: provenance fails."""
    plan = copy.deepcopy(B2_STATIC_PLAN)
    plan["mission"]["id"] = "mission-forged-123"
    # A forged request record: no MI draft/review evidence, so the canonical
    # verifier cannot bind a genuine MI generation invocation.
    request = _request(plan, draft=False)
    benchmark = _benchmark()
    record = _record(FROZEN_INPUT, plan, "mission-forged-123", benchmark)
    result = verify_b1_provenance(
        record=record,
        frozen_input=FROZEN_INPUT,
        request_record=request,
        controller_submission=_receipt(plan, group_id=record.controller_submission_identity),
        benchmark_evidence=benchmark,
        static_b2_plan=B2_STATIC_PLAN,
        observed_execution_ids=("exec-1",),
        instruction_text=INSTRUCTION,
    )
    assert not result.passed
    assert ProvenanceFailure.STATIC_B2_PLAN_EQUALITY in result.failures


def test_f07_record_missing_fails_closed() -> None:
    """A run with no provenance record at all fails closed."""
    plan = MI_PLAN_DIFFERENT_TASK_IDS
    result = verify_b1_provenance(
        record=None,
        frozen_input=FROZEN_INPUT,
        request_record=_request(plan),
        controller_submission=_receipt(plan),
        benchmark_evidence=_benchmark(),
        static_b2_plan=B2_STATIC_PLAN,
        observed_execution_ids=("exec-1",),
        instruction_text=INSTRUCTION,
    )
    assert not result.passed
    assert ProvenanceFailure.PROVENANCE_RECORD_MISSING in result.failures


def test_f07_controller_group_id_mismatch_fails() -> None:
    """Controller submission identity mismatch fails provenance."""
    plan = MI_PLAN_DIFFERENT_TASK_IDS
    request = _request(plan)
    benchmark = _benchmark()
    record = _record(FROZEN_INPUT, plan, "mission-mi-runtime-xyz", benchmark)
    result = verify_b1_provenance(
        record=record,
        frozen_input=FROZEN_INPUT,
        request_record=request,
        controller_submission=_receipt(plan, group_id="group-other"),
        benchmark_evidence=benchmark,
        static_b2_plan=B2_STATIC_PLAN,
        observed_execution_ids=("exec-1",),
        instruction_text=INSTRUCTION,
    )
    assert not result.passed
    assert ProvenanceFailure.CONTROLLER_SUBMISSION_IDENTITY_MISMATCH in result.failures


def test_f07_execution_identity_mismatch_fails() -> None:
    """Execution identity mismatch fails provenance."""
    plan = MI_PLAN_DIFFERENT_TASK_IDS
    request = _request(plan)
    benchmark = _benchmark()
    record = _record(FROZEN_INPUT, plan, "mission-mi-runtime-xyz", benchmark)
    result = verify_b1_provenance(
        record=record,
        frozen_input=FROZEN_INPUT,
        request_record=request,
        controller_submission=_receipt(plan, group_id=record.controller_submission_identity),
        benchmark_evidence=benchmark,
        static_b2_plan=B2_STATIC_PLAN,
        observed_execution_ids=("exec-99",),
        instruction_text=INSTRUCTION,
    )
    assert not result.passed
    assert ProvenanceFailure.EXECUTION_IDENTITY_MISMATCH in result.failures


def test_f07_missing_group_id_fails_not_skipped() -> None:
    """A Controller receipt without a group id fails, never skips the check."""
    plan = MI_PLAN_DIFFERENT_TASK_IDS
    request = _request(plan)
    benchmark = _benchmark()
    record = _record(FROZEN_INPUT, plan, "mission-mi-runtime-xyz", benchmark)
    receipt = _receipt(plan, group_id=record.controller_submission_identity)
    receipt["group_id"] = None
    result = verify_b1_provenance(
        record=record,
        frozen_input=FROZEN_INPUT,
        request_record=request,
        controller_submission=receipt,
        benchmark_evidence=benchmark,
        static_b2_plan=B2_STATIC_PLAN,
        observed_execution_ids=("exec-1",),
        instruction_text=INSTRUCTION,
    )
    assert not result.passed
    assert ProvenanceFailure.CONTROLLER_GROUP_ID_MISSING in result.failures


def test_f07_empty_execution_attempts_fail_not_skip() -> None:
    """Empty execution attempt evidence fails, never skips the identity check."""
    plan = MI_PLAN_DIFFERENT_TASK_IDS
    request = _request(plan)
    benchmark = _benchmark()
    record = _record(FROZEN_INPUT, plan, "mission-mi-runtime-xyz", benchmark)
    result = verify_b1_provenance(
        record=record,
        frozen_input=FROZEN_INPUT,
        request_record=request,
        controller_submission=_receipt(plan, group_id=record.controller_submission_identity),
        benchmark_evidence=benchmark,
        static_b2_plan=B2_STATIC_PLAN,
        observed_execution_ids=(),
        instruction_text=INSTRUCTION,
    )
    assert not result.passed
    assert ProvenanceFailure.EXECUTION_ATTEMPTS_EMPTY in result.failures


def test_f07_lifecycle_alone_is_not_genuine_mi() -> None:
    """A lifecycle-Accepted record without MI draft evidence fails B2 equality."""
    plan = copy.deepcopy(B2_STATIC_PLAN)
    plan["mission"]["id"] = "mission-lifecycle-only"
    # draft=False: no MI generation evidence despite lifecycle Accepted.
    request = _request(plan, draft=False)
    benchmark = _benchmark()
    record = _record(FROZEN_INPUT, plan, "mission-lifecycle-only", benchmark)
    result = verify_b1_provenance(
        record=record,
        frozen_input=FROZEN_INPUT,
        request_record=request,
        controller_submission=_receipt(plan, group_id=record.controller_submission_identity),
        benchmark_evidence=benchmark,
        static_b2_plan=B2_STATIC_PLAN,
        observed_execution_ids=("exec-1",),
        instruction_text=INSTRUCTION,
    )
    assert not result.passed
    assert ProvenanceFailure.STATIC_B2_PLAN_EQUALITY in result.failures
    assert ProvenanceFailure.MI_GENERATION_EVIDENCE_MISSING in result.failures


def test_f07_instruction_mismatch_breaks_genuine_mi() -> None:
    """A dialogue instruction that differs from the input breaks genuine MI."""
    plan = MI_PLAN_DIFFERENT_TASK_IDS
    request = _request(plan, instruction="some other instruction entirely")
    benchmark = _benchmark()
    record = _record(FROZEN_INPUT, plan, "mission-mi-runtime-xyz", benchmark)
    result = verify_b1_provenance(
        record=record,
        frozen_input=FROZEN_INPUT,
        request_record=request,
        controller_submission=_receipt(plan, group_id=record.controller_submission_identity),
        benchmark_evidence=benchmark,
        static_b2_plan=B2_STATIC_PLAN,
        observed_execution_ids=("exec-1",),
        instruction_text=INSTRUCTION,
    )
    assert not result.passed
    assert ProvenanceFailure.MI_GENERATION_EVIDENCE_MISSING in result.failures
