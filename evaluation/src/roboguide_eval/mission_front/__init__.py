"""Mission Front-half Eval: real-model probes of the Mission Intelligence chain.

This subpackage evaluates the pipeline
Dialogue → Interpreter → Planner → Capability Catalog v0.3 → MissionPlan
v0.7 → Validator → Reviewer → Repair with the production adapters and a real
model, collecting semantic-invariant outcomes and complete per-case evidence
(instruction, GroundedIntent, drafts, review/repair history, accepted plan,
tokens, latency, failure reasons). It never modifies the mission package or
RoboGuide Core; findings are evidence for human review.
"""

from roboguide_eval.mission_front.cases import (
    CASES_SCHEMA,
    CaseExpectations,
    MissionFrontCase,
    MissionFrontCaseError,
    load_cases,
)
from roboguide_eval.mission_front.invariants import InvariantOutcome
from roboguide_eval.mission_front.recording import (
    RecordingSubmitter,
    RecordingTransport,
    StageScope,
    StageTimedPort,
)
from roboguide_eval.mission_front.runner import (
    CaseResult,
    build_suite_components,
    evaluate_case_invariants,
    run_case,
    run_suite,
)

__all__ = [
    "CASES_SCHEMA",
    "CaseExpectations",
    "CaseResult",
    "InvariantOutcome",
    "MissionFrontCase",
    "MissionFrontCaseError",
    "RecordingSubmitter",
    "RecordingTransport",
    "StageScope",
    "StageTimedPort",
    "build_suite_components",
    "evaluate_case_invariants",
    "load_cases",
    "run_case",
    "run_suite",
]
