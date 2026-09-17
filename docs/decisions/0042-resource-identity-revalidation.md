# ADR-0042: Current resource identity at Commit

- Status: Proposed for review
- Date: 2026-09-17

## Decision

Proposal is not a resource reservation. Both state-aware normal Commit entrypoints revalidate
every selected ResourceId against the selected Node's current declarations, including exact
resource kind and minimum capacity, as well as current Role eligibility and operation support.
Control checks all assignments and ownership/calendar conflicts before writing reservations.
Equivalent resources with different IDs never repair a stale selection implicitly.

The legacy Commit methods without a State reader reject resource-bearing proposals. Their
remaining compatibility scope is zero-resource input without independent operation constraints.
Existing resource-bearing callers use `commit_with_state` or `commit_for_group_with_state`.
This preserves one State evidence source and one Control commitment authority instead of
adding a cached resource inventory to Control. MissionPlan v0.7 compatibility is unchanged.

Selected-resource withdrawal, replacement, reduced capacity and changed kind produce typed
assignment-unavailable evidence. Invalid proposal shape remains a rejected contract/invariant;
neither path partially commits or binds. Recovery retains its existing exact-resource checks.

## Validation

Deterministic two-resource tests retain a valid first resource and invalidate the selected
second resource between Proposal and Commit. Both Commit entrypoints must reject without
reserving either resource or emitting PlanCommitted. A separate test proves the State-free
compatibility methods cannot bypass revalidation.
