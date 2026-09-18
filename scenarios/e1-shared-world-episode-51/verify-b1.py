"""Verify one E1 Protocol B1 RoboGuide run against the B1 acceptance gates.

All provenance decisions delegate to the canonical
``roboguide_eval.b1_provenance.verify_b1_provenance`` implementation. The
verifier only extracts structured evidence from run artifacts: the actual
frozen input used by the run, the MI request record, the Controller receipt
(mission id, group id, registered task ids from Controller events), Node
execution attempts, and the shared-world benchmark summary. No substring or
lifecycle-enum heuristics are used as provenance authority.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).parent.parent.parent.parent
STATIC_PLAN = Path(__file__).parent / "mission-plan.json"


def _load(path: Path) -> dict[str, Any] | None:
    """Load one JSON file or return None when absent/malformed."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return document if isinstance(document, dict) else None


def _plan_intents(plan: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Extract (destination, objective) intent pairs from one MissionPlan doc."""
    intents: list[tuple[str, dict[str, Any]]] = []
    for task in plan.get("tasks", []):
        for role in task.get("roles", []):
            intent = role.get("execution_intent", {})
            destination = (intent.get("parameters") or {}).get("destination")
            intents.append((destination if isinstance(destination, str) else "", intent))
    return intents


def _controller_receipt(
    mission: dict[str, Any] | None, events: dict[str, Any] | None
) -> dict[str, Any]:
    """Extract the Controller submission receipt from observable evidence.

    The mission status view supplies mission/group identity; Controller
    events supply the set of task ids actually registered for the Mission
    (``TaskExecutionRegistered`` payloads), which is the structured
    execution evidence that the accepted plan reached the Controller.
    """
    mission_id = (mission or {}).get("mission_id")
    group_id = (mission or {}).get("group_id")
    registered: set[str] = set()
    for event in (events or {}).get("events", []):
        payload = (event or {}).get("payload", {})
        registered_event = payload.get("TaskExecutionRegistered")
        if isinstance(registered_event, dict):
            task_ref = registered_event.get("task_ref", {})
            if isinstance(task_ref, dict) and isinstance(task_ref.get("task_id"), str):
                registered.add(task_ref["task_id"])
        created = payload.get("ExecutionGroupCreated")
        if (
            not isinstance(group_id, str)
            and isinstance(created, dict)
            and isinstance(created.get("group_id"), str)
        ):
            group_id = created["group_id"]
    return {
        "mission_id": mission_id,
        "group_id": group_id,
        "registered_task_ids": sorted(registered),
    }


def verify(run: Path) -> dict[str, Any]:
    """Evaluate the B1 acceptance gates from persisted run evidence."""
    sys.path.insert(0, str(REPO_ROOT / "evaluation" / "src"))
    from roboguide_eval.b1_provenance import (
        B1ProvenanceRecord,
    )
    from roboguide_eval.b1_provenance import (
        verify_b1_provenance as _verify_chain,
    )

    request = _load(run / "b1-request-record.json")
    mission = _load(run / "mission.json")
    shared = _load(run / "evidence/shared-world-summary.json")
    static_plan = _load(STATIC_PLAN)
    events = _load(run / "events.json")

    # The actual frozen input used by this run, archived by the scenario
    # script; the scenario default is only a legacy fallback.
    frozen_input = _load(run / "b1-input-used.json")
    if frozen_input is None:
        frozen_input = _load(Path(__file__).parent / "b1-input.json")
    instruction = str(frozen_input.get("instruction", "")) if frozen_input else ""

    lifecycle = str(request.get("lifecycle")) if request else "missing"
    mi_ran = bool(request and request.get("plan"))

    def _sha(text: str) -> str:
        """Hash one text for evidence."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    plan = (request or {}).get("plan") or {}
    plan_digest = (
        _sha(json.dumps(plan, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
        if plan
        else None
    )

    intents = _plan_intents(plan) if plan else []
    destinations = sorted({d for d, _ in intents})

    # --- F-06 tri-state benchmark authority (no collapse residue) ---
    benchmark_tri_state = "BENCHMARK_UNAVAILABLE"
    benchmark_reason = "authority_document_missing_or_malformed"
    pddl_value: bool | None = None
    if shared:
        pddl = shared.get("official_pddl_success")
        if isinstance(pddl, bool):
            pddl_value = pddl
            benchmark_tri_state = "BENCHMARK_TRUE" if pddl else "BENCHMARK_FALSE"
            benchmark_reason = "official_pddl_success_authoritative"
        else:
            benchmark_reason = "official_pddl_success_missing_or_not_bool"

    # --- F-07 canonical provenance verification ---
    provenance_record = None
    record_doc = _load(run / "b1-provenance.json")
    if record_doc is not None:
        provenance_record = B1ProvenanceRecord(
            run_id=str(record_doc.get("run_id", "")),
            input_digest=str(record_doc.get("input_digest", "")),
            mi_run_identity=str(record_doc.get("mi_run_identity", "")),
            accepted_plan_digest=str(record_doc.get("accepted_plan_digest", "")),
            mission_id=str(record_doc.get("mission_id", "")),
            controller_submission_identity=str(
                record_doc.get("controller_submission_identity", "")
            ),
            execution_identity=str(record_doc.get("execution_identity", "")),
            benchmark_evidence_digest=str(record_doc.get("benchmark_evidence_digest", "")),
        )
    controller_receipt = _controller_receipt(mission, events)
    attempts = _load(run / "execution-attempts.json") or {}
    observed_execution_ids = tuple(
        str(a["execution_id"])
        for a in attempts.get("attempts", [])
        if isinstance(a, dict) and a.get("execution_id")
    )

    provenance = _verify_chain(
        record=provenance_record,
        frozen_input=frozen_input,
        request_record=request,
        controller_submission=controller_receipt,
        benchmark_evidence=shared,
        static_b2_plan=static_plan,
        observed_execution_ids=observed_execution_ids,
        instruction_text=instruction or None,
    )
    provenance_failures = [f.value for f in provenance.failures]
    provenance_passed = provenance.passed

    # --- semantic intent gates (structured, from the accepted plan) ---
    goals_covered = destinations == ["TARGET_any_targets|0", "any_targets|0"]

    # Formal population is provenance + complete identity evidence; the
    # benchmark population additionally requires Habitat authority.
    valid_for_formal_population = provenance_passed
    benchmark_authority_available = benchmark_tri_state != "BENCHMARK_UNAVAILABLE"
    valid_for_benchmark_population = valid_for_formal_population and benchmark_authority_available
    run_validity = "VALID_RUN" if provenance_passed else "INVALID_INFRA"

    checks = {
        "mission_intelligence_ran": mi_ran,
        "static_plan_fallback_absent": "static_b2_plan_equality" not in provenance_failures,
        "full_semantic_goals_covered": goals_covered,
        "mission_terminal_or_mi_terminal": (
            (mission or {}).get("status") in {"Completed", "Failed", "Cancelled"}
            or lifecycle in {"Blocked", "Failed", "NeedsClarification"}
        ),
        "provenance_chain_passed": provenance_passed,
        "valid_for_formal_population": valid_for_formal_population,
        "valid_for_benchmark_population": valid_for_benchmark_population,
    }
    outcomes = (shared or {}).get("outcomes", {})
    context = {
        "lifecycle": lifecycle,
        "mission_status": (mission or {}).get("status"),
        "plan_destinations": destinations,
        "plan_tasks": [task.get("id") for task in plan.get("tasks", [])] if plan else [],
        "official_pddl_success": pddl_value,
        "per_agent_states": {agent: outcome.get("state") for agent, outcome in outcomes.items()},
        "instruction_sha256": _sha(instruction) if instruction else None,
        "accepted_plan_canonical_digest": plan_digest,
        "benchmark_tri_state": benchmark_tri_state,
        "benchmark_outcome_reason": benchmark_reason,
        "provenance_failures": provenance_failures,
        "run_validity": run_validity,
        "provenance_schema": "roboguide.e1.b1-provenance/v0.1",
        "controller_receipt": {
            "mission_id": controller_receipt.get("mission_id"),
            "group_id": controller_receipt.get("group_id"),
            "registered_task_ids": controller_receipt.get("registered_task_ids"),
        },
        "population_admission": {
            "valid_for_formal_population": valid_for_formal_population,
            "benchmark_authority_available": benchmark_authority_available,
            "valid_for_benchmark_population": valid_for_benchmark_population,
            "run_validity": run_validity,
            "invalid_reasons": provenance_failures,
        },
    }
    passed = all(bool(value) for value in checks.values())
    return {
        "schema": "roboguide.e1-b1-verdict/v0.1",
        "run": run.name,
        "checks": checks,
        "context": context,
        "verdict": "PASS" if passed else "FAIL",
    }


def main() -> None:
    """Print one machine-readable B1 verdict."""
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify-b1.py RUN_DIRECTORY")
    print(json.dumps(verify(Path(sys.argv[1])), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
