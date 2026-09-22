"""Summarize the frozen three-run Episode51 series without modifying run evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path("/data/workspace/code/roboguide-ep51-final-repeat-20260922T050410Z")
KEY_FILES = (
    "b1-input-used.json",
    "b1-request-record.json",
    "mission.json",
    "execution-attempts.json",
    "b1-provenance.json",
    "b1-verdict.json",
    "evidence/assignment-arrival.jsonl",
    "evidence/stage2-actions.jsonl",
    "evidence/stage2-action-audit.json",
    "evidence/diagnostics-initial.json",
    "evidence/diagnostics-steps.jsonl",
    "evidence/diagnostics-terminal.json",
    "evidence/shared-world-summary.json",
    "evidence/runtime-source-manifest.json",
)


def load(path: Path) -> Any:
    """Load one JSON document."""
    return json.loads(path.read_text())


def lines(path: Path) -> list[dict[str, Any]]:
    """Load one JSONL artifact into ordered records."""
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def sha(path: Path) -> str:
    """Hash exact evidence bytes."""
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def predicate_steps(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Find first true step and persistence for both official predicate conjuncts."""
    answer: dict[str, dict[str, Any]] = {}
    for label, selector in (("object", "movable_entity_type"), ("target", "goal_entity_type")):
        observations = [
            (row["simulator_step"], value)
            for row in rows
            for key, value in row["goal_conjunct_values"].items()
            if selector in key
        ]
        first = next((step for step, value in observations if value), None)
        answer[label] = {
            "first_true_step": first,
            "remained_true_after_first": (
                first is not None and all(value for step, value in observations if step >= first)
            ),
        }
    return answer


def summarize_trial(name: str) -> dict[str, Any]:
    """Reduce one immutable run directory to reviewable semantic and physical facts."""
    root = ROOT / name
    record = load(root / "b1-request-record.json")
    plan = record["plan"]
    verdict = load(root / "b1-verdict.json")
    attempts = load(root / "execution-attempts.json")["attempts"]
    arrivals = lines(root / "evidence/assignment-arrival.jsonl")
    actions = lines(root / "evidence/stage2-actions.jsonl")
    diagnostics = lines(root / "evidence/diagnostics-steps.jsonl")
    terminal = load(root / "evidence/diagnostics-terminal.json")
    shared = load(root / "evidence/shared-world-summary.json")
    audit = load(root / "evidence/stage2-action-audit.json")
    runtime = load(root / "evidence/runtime-source-manifest.json")
    roles = [role for task in plan["tasks"] for role in task["roles"]]
    return {
        "run": name,
        "request_id": record["request_id"],
        "mission_id": record["mission_id"],
        "mi": {
            "lifecycle": record["lifecycle"],
            "draft_revision": record["draft_revision"],
            "review_attempts": len(record["review_history"]),
            "review_approved": all(item["review"]["approved"] for item in record["review_history"]),
            "repair_attempts": record["repair_attempts"],
            "actor_count": len(plan["mission"]["actors"]),
            "coupling_modes": [context["coupling_mode"] for context in plan["contexts"]],
            "relations": [relation for context in plan["contexts"] for relation in context["relations"]],
            "tasks": [
                {
                    "id": task["id"],
                    "depends_on": task["depends_on"],
                    "destinations": [role["execution_intent"]["parameters"]["destination"] for role in task["roles"]],
                    "resources": [role["requirements"]["resources"] for role in task["roles"]],
                }
                for task in plan["tasks"]
            ],
            "all_role_operations": [role["execution_intent"]["operation"] for role in roles],
        },
        "control": {
            "attempts": attempts,
            "assignment_arrivals": arrivals,
        },
        "stage2": {
            "actions": actions,
            "audit": audit,
        },
        "habitat": {
            "initial_positions": shared["identity"]["initial_agent_positions"],
            "final_positions": {agent: value["final_position"] for agent, value in shared["outcomes"].items()},
            "simulator_steps": shared["identity"]["simulator_steps"],
            "official_pddl_success": shared["official_pddl_success"],
            "predicate_timeline": predicate_steps(diagnostics),
            "terminal_goal_values": terminal["goal_conjunct_values"],
            "official_metrics": terminal["official_metrics"],
            "diagnostic_collection": terminal["collection_stats"],
            "action_trace_collection": shared["action_trace_collection"],
        },
        "verdict": {
            "system_outcome": verdict["system_outcome"],
            "verdict": verdict["verdict"],
            "admission": verdict["admission"],
        },
        "runtime_source_manifest": runtime,
        "key_evidence_sha256": {path: sha(root / path) for path in KEY_FILES},
    }


trials = [summarize_trial(f"trial-{index}") for index in range(1, 4)]
summary = {
    "schema": "roboguide.ep51-final-repeatability-summary/v0.1",
    "series_root": str(ROOT),
    "acceptance": "three consecutive official pddl_success=true runs on exact final code",
    "roboguide_head": "7dfdf3891487af51cd59f97cabc1796800d073d8",
    "emos_head": "8d082d3a41af8219ca43719d24c553e9d61771fb",
    "episode_id": "51",
    "seed": 40,
    "dataset_sha256": "5d2c6aa6608d5611c73d8f6c688e17613a9898afa5f0f668e66db068598191ca",
    "trials": trials,
    "series_checks": {
        "trial_count": len(trials),
        "all_official_success": all(t["habitat"]["official_pddl_success"] for t in trials),
        "all_mission_completed": all(t["verdict"]["system_outcome"] == "COMPLETED" for t in trials),
        "all_provenance_valid": all(t["verdict"]["admission"]["provenance_valid"] for t in trials),
        "all_formal_population_valid": all(t["verdict"]["admission"]["valid_for_formal_population"] for t in trials),
        "all_benchmark_population_valid": all(t["verdict"]["admission"]["valid_for_benchmark_population"] for t in trials),
        "all_stage2_actions_allowed": all(
            all(action["decision"] == "allowed" for action in t["stage2"]["actions"])
            for t in trials
        ),
        "all_diagnostics_lossless": all(
            t["habitat"]["diagnostic_collection"]["dropped_step_records"] == 0
            and t["habitat"]["diagnostic_collection"]["writer"]["write_failures"] == 0
            and t["stage2"]["audit"]["records_dropped"] == 0
            and t["stage2"]["audit"]["write_failures"] == 0
            for t in trials
        ),
        "same_initial_positions": len({json.dumps(t["habitat"]["initial_positions"], sort_keys=True) for t in trials}) == 1,
        "same_final_positions": len({json.dumps(t["habitat"]["final_positions"], sort_keys=True) for t in trials}) == 1,
        "same_step_count": len({t["habitat"]["simulator_steps"] for t in trials}) == 1,
    },
    "provider_identity_observation": {
        "configured_request_alias": "gpt-5.6-luna",
        "component_validation_response_model": "gpt-6-luna",
        "scope": "Observed in the adjacent current-provider Interpreter/Planner/Reviewer studies; Mission Service does not archive raw provider envelopes in these three B1 runs.",
    },
}
(ROOT / "EP51_FINAL_REPEATABILITY_SUMMARY.json").write_bytes(
    (json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
)
print(json.dumps(summary["series_checks"], indent=2))
