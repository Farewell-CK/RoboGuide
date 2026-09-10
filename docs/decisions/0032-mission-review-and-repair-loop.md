# ADR-0032: Mission Review and Repair Loop v0.1

- Status: Accepted for Mission Review and Repair v0.1
- Date: 2026-09-10

## Context

Mission Request v0.1 asks a Responses-backed Planner to generate and review a MissionPlan inside
one `plan()` call. A rejected review is reduced to `MissionProviderError`, so the durable request
never records the rejected draft, structured findings, or an actual Review transition. Retry starts
the whole interpretation and planning path again without telling the Planner what was wrong.

This loses useful deliberation evidence and conflates three different outcomes:

- the draft can be repaired from the already grounded Mission intent;
- the draft exposes missing user information that only dialogue can resolve;
- the draft violates a boundary or policy and should not be repaired automatically.

An unbounded self-repair loop would be equally unsafe because repeated model calls could silently
invent facts, drift from the user objective, or consume resources indefinitely.

## Decision

Mission Intelligence separates three ports:

- `MissionPlanner` creates an initial MissionPlan draft;
- `MissionPlanReviewer` returns a read-only structured `MissionPlanReview`;
- `MissionPlanRepairer` creates a replacement draft from GroundedIntent, the rejected draft, and
  the exact review issues.

Every blocking `MissionReviewIssue` contains a stable code, a MissionPlan path, a human-readable
message, and one required action:

- `RepairPlan`: enough grounded information exists to revise the draft;
- `RequestClarification`: new user information is required;
- `RejectDraft`: automatic repair is not permitted.

The Reviewer never edits a plan. The Repairer may not answer clarification questions or introduce
facts absent from GroundedIntent. Mission Request Engine owns orchestration:

```text
GroundedIntent
  -> Plan Draft
  -> deterministic validation
  -> Review
       -> approved -> approval policy / submit
       -> RequestClarification -> NeedsClarification
       -> RejectDraft -> Failed
       -> RepairPlan -> bounded Repair -> deterministic validation -> Review
```

The configured bootstrap permits at most two automatic repair attempts. Any clarification issue
takes precedence over repair, and any `RejectDraft` issue prevents repair. Exhausting the repair
budget produces `Failed`; it does not submit the last rejected draft. Provider, parsing, and
deterministic validation failures also remain explicit failures.

Mission Request projection v0.2 adds `Repairing`, the current repair-attempt count, and immutable
review history keyed by draft revision and digest. User instruction/messages remain dialogue;
review history remains internal planning evidence. Existing v0.1 persisted requests restore with an
empty review history and zero repair attempts, then serialize as v0.2.

Deterministic MissionPlan and Canonical Capability Catalog validation runs before every Review and
again after every Repair. In the default review-enabled production composition, only a reviewed and
approved draft can reach approval or Controller submission. Explicit review-disabled fixture and
offline configurations retain deterministic-only admission; they do not fabricate Review evidence.
Planner and Reviewer model settings remain independently configurable, although a deployment may
intentionally select the same model during bootstrap.

This slice does not change MissionPlan v0.5, Control Matching, Scheduler, Proposal -> Commit -> Bind,
Execution Group, Runtime, or Local EAIOS authority.

## Consequences

- Review rejection becomes inspectable Mission deliberation evidence instead of an opaque provider
  exception.
- Repair is bounded, deterministic at its admission boundaries, and cannot replace user
  clarification.
- Draft revisions and digests identify exactly which plan each Review examined.
- Dialogue is still represented by `instruction` plus text `messages`; a fully structured
  `DialogueTurn` contract remains a later versioned change.
- Review quality remains model-dependent for semantic questions. Deterministic validators continue
  to own schema, reference, implementation-profile, and Catalog checks.
- Historical draft bodies are not retained in v0.1; review history retains their identity and
  findings. Full deliberation artifact retention is deferred.
