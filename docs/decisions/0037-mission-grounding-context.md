# ADR-0037: Mission Grounding Context v0.1

- Status: Accepted for Mission Grounding v0.1
- Date: 2026-09-12

## Context

ADR-0030 correctly removed live Node inventory from Mission semantic admission, while ADR-0024
established source-aware World State and selective Memory catalogs. The current Mission Service,
however, supplies only Dialogue and the Canonical Capability Catalog to its model adapters. World
and historical evidence can exist in State & Memory without becoming available to Interpreter,
Planner, Reviewer, or Repairer.

Giving the model the complete Controller inventory or an unbounded State/Memory dump would close
that interface gap by violating stronger boundaries. Mission meaning must remain independent of
current Node health, provider availability, leases, reservations, and scheduling choices. State is
not a global truth, and discovering Memory metadata does not prove that its content was fetched,
authorized, or interpreted. State receive timestamps also belong to a RoboGuide-local clock domain
that the Mission Service wall clock cannot safely compare.

## Decision

### 1. Add one explicit read-only Mission Grounding boundary

Mission Intelligence depends on a narrow `MissionGroundingReader` and consumes an immutable
`GroundingContextSnapshot`. The Mission Service composition root reads existing Controller State
and Memory catalog HTTP facades; Mission domain logic receives normalized evidence and does not
depend on Rust State implementations, Artifact storage, or Control internals.

The v0.1 selection policy admits only:

- World State records with `Reported`, `Observed`, `Derived`, or explicit `Belief` semantics;
- Global `Semantic`, `Experience`, or `Spatial` Memory revision metadata.

It excludes Node/RoboGuide deployment records, Desired/Committed state, health, liveness,
capability and operation support, resources, leases, candidate sets, reservations, calendars,
placements, Execution Groups, and Runtime attempts. Those facts remain owned by deployment State,
Control, Orchestration, and Runtime and cannot alter Mission semantic admission through grounding.

### 2. Preserve evidence attribution instead of flattening context

Each State item retains object identity, semantic, source, channel, payload schema, value,
source-local observation time, RoboGuide receive time, TTL, Controller-computed freshness,
confidence, source epoch, and sequence. Independent records are not fused or silently resolved.

The Mission Service copies `Fresh` or `Stale` from the Controller query. It does not subtract its
Unix wall time from State receive time. `captured_at_ms` records Mission Service capture evidence;
it does not make the two process-local clocks globally comparable.

Each Memory item retains selector, kind, provider, owner, scope, visibility, payload schema, media
type, Artifact reference, provenance, and creation time. Every v0.1 entry is explicitly
`MetadataOnly`. The reader never fetches Artifact bytes, and model prompts must not treat metadata
as consumed semantic content.

### 3. Bind one snapshot to one deliberation cycle

The snapshot digest covers request identity, input Dialogue digest, capture time, selection policy,
all admitted evidence, and acquisition gaps. Interpreter, Planner, Reviewer, and Repairer receive
the same snapshot during one plan/review/repair cycle. Review attempts persist its exact context
digest beside the MissionPlan revision and digest.

A user clarification answer invalidates the prior planning input and captures a new snapshot before
reinterpretation. Repair does not refresh context mid-cycle. An explicit later deliberation retry
may capture a new snapshot and produce a new draft revision. User Dialogue remains separate from
internal Draft/Review/Repair evidence.

Mission Request projection advances to v0.4. v0.1-v0.3 remain readable compatibility inputs and do
not receive fabricated historical grounding. Newly processed requests persist a non-null snapshot,
including an attributed empty snapshot when no evidence is available.

### 4. Acquisition is bounded and fail-soft

State and Memory reads have fixed configured origins, bounded response sizes, timeouts, evidence
counts, no redirects, and deterministic ordering. One unavailable or malformed source produces a
durable `GroundingGap`; it does not erase valid evidence from the other source or make an otherwise
valid Mission semantically invalid. Empty context is a supported bootstrap outcome.

All model stages treat strings embedded in grounding evidence as untrusted data, not provider
instructions. Snapshot construction detaches nested JSON containers and canonical digest
validation detects persisted content changes.

## Consequences

- Mission Intelligence can now consume attributed World evidence without regaining deployment
  feasibility or placement authority.
- Every model stage sees the same inspectable evidence, reducing Planner/Reviewer context drift.
- Persistence can explain which context supported a draft and Review.
- State and Memory may change after capture. The snapshot is deliberation evidence, not a live lock,
  reservation, or guarantee that the physical world remains unchanged.
- Global Memory metadata is discoverable context only. Useful content selection, authorization,
  retrieval, schema-aware decoding, relevance ranking, and prompt budgeting remain future slices.
- The first policy may include irrelevant Global records because no semantic query planner exists;
  bounded deterministic selection prevents unbounded input but does not claim optimal relevance.

## Not Implemented

- live Node/Resource inventory or Control decision input to Mission models;
- Memory Artifact content retrieval, prefetch, embedding, or vector search;
- cross-source fusion, conflict resolution, or belief generation;
- cross-process clock synchronization or timestamp comparison;
- grounding-driven scheduling, reservation, execution, or automatic recovery;
- dynamic grounding policy negotiation or per-schema relevance ranking.
