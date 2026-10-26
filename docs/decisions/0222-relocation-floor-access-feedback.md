# ADR-0222: Declared relocation floor access and bounded planning feedback

- Status: Implementation in progress; physical validation pending
- Date: 2026-10-26

## Decision

The optional Habitat integrated relocation deployment explicitly declares
`location-access=base-floor` and its own `supports-floor-transition` capability.
This Local How contract requires access to the object's and destination's floors;
it does not advertise cross-floor manipulation from a different floor. The facts
are deployment restrictions, not an assertion that all other physical methods
are impossible. Legacy declarations acquire no such restriction implicitly.

Operation-admission v0.2 binds that declaration to the original Node config
digest and the same reset source used by the existing floor observations. Only
an explicitly false transition capability and two known, differing floors remove
a candidate. Missing/ambiguous floors remain unknown and keep the candidate.
Matching floor labels establish no route, grasp reachability, placement or success.
Navigation's goal-region distance tests are not reused as manipulation proofs.

The existing read-only published-plan validation returns logical draft paths and
deployment compatibility failures. Planner/regenerate and Repairer reuse their
existing finite draft-recovery budgets and complete review chain. This is not the
navigation-only initial route-support assessment: that endpoint keeps its stated
scope. Feedback neither exposes live Node Inventory nor selects physical executors,
adds a workflow recipe, forces different robots, submits a Mission or reserves
resources. Single-Actor serial reuse remains valid when its declared tasks fit.

No Core, Node, MissionPlan, checkpoint or official benchmark contract changes.
Existing admission/provenance rules remain; actual failures and unknown official
outcomes remain evidence. Source/profile drift refuses rather than silently
downgrading the new declared access policy.
