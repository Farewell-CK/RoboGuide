"""Verify one E1 Protocol B1 RoboGuide run against the B1 acceptance gates."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

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


def verify(run: Path) -> dict[str, Any]:
    """Evaluate the B1 acceptance gates from persisted run evidence."""
    request = _load(run / "b1-request-record.json")
    mission = _load(run / "mission.json")
    shared = _load(run / "evidence/shared-world-summary.json")
    static_plan = _load(STATIC_PLAN)
    instruction = ""
    b1_input = _load(Path(__file__).parent / "b1-input.json")
    if b1_input:
        instruction = str(b1_input.get("instruction", ""))

    lifecycle = str(request.get("lifecycle")) if request else "missing"
    dialogue = json.dumps(request.get("dialogue", []), ensure_ascii=False) if request else ""
    mi_ran = bool(request and request.get("plan"))
    instruction_reached_mi = instruction.strip() and instruction.strip()[:80] in dialogue

    def _sha(text: str) -> str:
        """Hash one text for evidence."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    plan = (request or {}).get("plan") or {}
    plan_text = json.dumps(plan, sort_keys=True, ensure_ascii=False)
    static_text = json.dumps(static_plan, sort_keys=True, ensure_ascii=False)
    static_fallback = bool(plan) and plan_text == static_text

    intents = _plan_intents(plan) if plan else []
    destinations = sorted({destination for destination, _ in intents})
    goals_covered = destinations == ["TARGET_any_targets|0", "any_targets|0"]

    checks = {
        "same_high_level_task": instruction_reached_mi,
        "mission_intelligence_ran": mi_ran,
        "static_plan_fallback_absent": not static_fallback,
        "full_semantic_goals_covered": goals_covered,
        "mission_terminal_or_mi_terminal": (
            (mission or {}).get("status") in {"Completed", "Failed", "Cancelled"}
            or lifecycle in {"Blocked", "Failed", "NeedsClarification"}
        ),
    }
    outcomes = (shared or {}).get("outcomes", {})
    context = {
        "lifecycle": lifecycle,
        "mission_status": (mission or {}).get("status"),
        "plan_destinations": destinations,
        "plan_tasks": [task.get("id") for task in plan.get("tasks", [])] if plan else [],
        "official_pddl_success": bool((shared or {}).get("official_pddl_success", False)),
        "per_agent_states": {agent: outcome.get("state") for agent, outcome in outcomes.items()},
        "instruction_sha256": _sha(instruction) if instruction else None,
        "generated_plan_sha256": _sha(plan_text) if plan else None,
        "static_plan_sha256": _sha(static_text),
        "stage2_context_diff": {
            "roboguide_assignment_texts": [intent.get("objective") for _, intent in intents],
            "roboguide_assignment_sha256": _sha(
                json.dumps([intent.get("objective") for _, intent in intents], ensure_ascii=False)
            )
            if intents
            else None,
            "scene_context": "shared scene_description.txt retained in evidence/",
            "note": "EMOS-side leader assignment text/hash is extracted into the pair record "
            "from the EMOS run's fresh chat-history evidence",
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
