# ADR-0067: Bounded MI reconsideration before Controller submission

- Status: Proposed for review; default-off implementation
- Date: 2026-10-04

## Context

ADR-0066 holds an already reviewed plan when a first-world readiness query is
blocked or unavailable. Repeating the unchanged query cannot resolve a grounded
task-organization problem. Conversely, a shortage does not authorize deleting a
goal, inventing abilities or sharing a physically exclusive executor. The model
needs attributed feedback and a bounded proposal boundary, rather than a generic
retry instruction or live Node inventory.

## Decision

1. The Mission Service may opt into at most three deployment reconsideration
   calls per Request. The existing Responses Repairer receives a separate
   `deployment_recovery` request mode with the exact reviewed plan, frozen
   grounded intent/context/catalog/policies/profiles, neutral assessment v0.2,
   original budget and prior decisions. It returns one closed proposal:
   `recheck`, `revise_plan`, or `wait_for_evidence`, with an explanation. This is
   not a Reviewer approval or a Controller command.
2. `recheck` queries the same plan through the existing read-only Controller
   endpoint. Identical feedback, excluding the query timestamp, stops the loop.
   `wait_for_evidence` keeps the current hold. A source/context identity change,
   expired or unattributed source, legacy v0.1 feedback, restored Controller or
   previously admitted world cannot provide another reasoning opportunity.
   Controller source times are compared only with Controller assessment times;
   they are never compared with MI's clock.
3. `revise_plan` returns a complete MissionPlan. Existing provider normalization,
   identity, canonical contract, capability/catalog, execution-profile,
   authoritative executor and satisfaction-policy gates still apply. The Engine
   repeats its ordinary draft checks, increments revision, calls Reviewer and
   requires fresh risk approval when applicable. Only then does it requery
   current Control and use the ordinary submit path. No old approval authorizes
   a changed digest. No special Actor merge, Task deletion, cooperation downgrade,
   resource relaxation, executor selector or route synthesis is implemented.
4. A full goal/witness may inform a model-proposed organization only through the
   existing frozen task/world evidence. Candidate count feedback is not evidence
   that one robot can achieve a joint terminal goal. Bounded misses/unknowns are
   not disjoint evidence; a broad `role_contract_unavailable` counter does not
   identify a missing ability. Real cooperation, distinctness and State export
   requirements stay intact. Unsupported deployment scope remains a hold when
   no grounded, semantically valid alternative exists.
5. Persist a pending attempt **before** the Provider effect. Freeze the original
   count limit and MI-local deadline (at most 900 seconds); retries, revisions,
   restart and configuration increases never renew them. The Responses socket
   timeout is capped by remaining time, and late results are recorded but not
   applied. This is an application result deadline, not proof of remote call
   cancellation or a hard wall-clock deadline for slow-drip HTTP. Normal Review,
   Controller query and deployment observation budgets remain separate.
6. Pending calls restore as `interrupted`, consuming their original attempt. No
   model is called during restore. A malformed/oversized decision or transport
   fault retains a bounded failure observation; it never triggers an automatic
   transport retry. Query-time exclusions may change on a later recheck, but the
   reset source, Local How, original receive/expiry and grounding identity stay
   frozen within the session. Explicit new dialogue keeps the original Request
   budget; it cannot reuse old-context deployment attempts for new reasoning.
7. Submission ambiguity retains the existing exact-body admission fence. Once
   any POST might have reached Control, this loop is disabled. Only existing
   read-only reconciliation can resolve ambiguity. Acceptance or physical
   execution never invokes this pre-submission port; Runtime/Control retain
   stopping, repetition, reassignment, resources and recovery authority.
8. Immutable `deployment-recovery-session/v0.1` evidence records full reviewed
   input, draft/context digests, exact neutral feedback, timestamps, raw bounded
   Provider decision, canonical proposal and failure/interruption state. New
   observations v0.4 carry it beside the unchanged public Request v0.4. Requests
   without a session continue emitting observations v0.3; v0.1-v0.3 archives remain
   compatible. Current B1 validates session identity, approved input digest,
   frozen grounding and completed-attempt consistency. Missing or downgraded
   session fields fail closed. Discarded/expired proposals do not require a
   fabricated Review. Final-draft Review and actual-submit checks remain intact.
9. Configuration defaults to zero attempts. The B1 launcher separately accepts
   `ROBOGUIDE_B1_DEPLOYMENT_RECOVERY_ATTEMPTS=0..3`, requiring initial-support
   assessment and its existing geometry/preferences/Local How prerequisites.
   Actual run-local service config freezes the option. The archived theoretical
   wait derivation includes reconsideration, new reviews and queries; it does
   not automatically increase the deployment-chosen observation deadline.

## Ownership and validation

Control publishes typed, bounded first-failed-predicate counts from the same
eligibility rules used by actual Matching, on a private read-only view. MI owns
proposals, durable budgets and draft review. Control alone accepts, matches,
commits and binds. State, Memory, MissionPlan, Node Protocol, official benchmark
authority and Formal population protocol are unchanged. No external EMOS edit
is involved. Models still cannot consume live Node/Resource inventory.

Offline tests exercise exact Responses inputs/schema and real normalization,
genuine cooperation preservation, bad identities/contracts/decision envelopes,
one-POST behavior, unchanged/source-changed feedback, Reviewer/approval vetoes,
deadline/interruption/count persistence, malformed archival evidence and B1
population compatibility including semantic-goal omission diagnostics. These
tests do not establish future model adherence or physical success.

## Limits

The current Controller assessment covers the existing initial independent
one/two-endpoint profile, not arbitrary execution-time topology or navigation
feasibility. Model-proposed semantic fidelity is still reviewed rather than
proven by a general semantic-equivalence algorithm. Source expiry can prevent
a late, otherwise legitimate proposal from reaching submission. A new dialogue
requires its own normal deliberation; a B1 frozen-input run cannot conceal that
change as the original snapshot. Real single-workload validation of this opt-in
profile remains necessary before any success-rate or batch claim.

Contract: [deployment recovery session v0.1](../../contracts/mission/deployment-recovery-session-v0.1/README.md).
