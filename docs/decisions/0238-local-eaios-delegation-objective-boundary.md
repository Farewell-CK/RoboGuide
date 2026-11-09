# ADR-0238: Keep RG admission checks out of Local EAIOS execution goals

Status: Accepted for task-level delegation planning.

The first real dog2 MI experiment exposed a boundary error. MI copied the operator's
pre-dispatch check of the unique RG candidate into the objective sent to Robonix Pilot.
Pilot correctly refused: its local robot is named lite3_01, and it has no authority to
inspect RG's candidate set or recursively perform task.delegate@v1. No native plan ran.

Planner, Reviewer and Repairer instructions now distinguish RG participant selection,
tenant authorization, resource admission, approval and dispatch from the receiving
EAIOS's local goal, fresh state checks, local action planning and safety. The local
ExecutionIntent must retain every local action constraint, while RG checks and planning
phase bookkeeping remain outside that executable goal. Experiment catalog descriptions
state the same operation boundary.

Participant scope remains a Mission/Role requirement. Referring to an RG-established
receiver as this robot does not create a physical identity binding or permit a different
participant. Unresolved selection still blocks RG admission. The Node adapter passes
the generated objective unchanged; no text stripper, extra process, primitive action
registration, new authority, or weakened approval/identity check is introduced.

Retain original MI drafts, reviews, rejected/refused tasks and provenance. Test the
existing catalog, Responses and Request regressions, then validate a new actual MI
Request through ordinary approval, Controller admission and Robonix execution. Prompt
guidance is not a deterministic tool permission boundary or physical-effect verifier.
