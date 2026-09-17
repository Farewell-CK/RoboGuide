# ADR-0040: Mission Actor and Physical Entity Binding

- Status: Proposed for review
- Date: 2026-09-17

## Context

ADR-0007 defines Actor as mission-local continuity, with a Node binding established only
after Commit and Group Bind. A joint physical goal can additionally require two independent
executors. Actor identity, Role cardinality, Physical Entity, Node, Physical Host, and Local
EAIOS are different concepts. Neither a second Actor name nor a second Node automatically
establishes physical distinctness. Mission Intelligence cannot infer entity identity from live
deployment inventory. The B1 application currently posts only instruction text; its stored
goal predicates and world context do not yet enter Mission grounding.

## Decision

MissionPlan v0.8 adds an optional `mission.actors[].physical_entity` reference and a
Context-scoped `executor_constraints[]` declaration over ContextRole identities. The only
implemented constraint kind is `distinct-physical-entities`: its referenced Actors must bind
pairwise distinct PhysicalEntityIds within that collaboration Context. Unconstrained Actors
may share an executor; multiple Roles of one Actor express continuous participation, not
executor cardinality. Actors remain logical mission identities, Roles remain Task slots,
PhysicalEntities are deployment identities, and Nodes are independent routing, capability,
and liveness authorities. Physical Hosts and Local EAIOS remain separate from both.

An Actor has zero physical bindings before first successful Commit+Bind and exactly one
while bound. Distinct ActorIds alone do not require distinct physical executors. A physical
entity routes through one Node at a given registry revision; that association may change in
deployment, but never silently changes an already committed ActorBinding. A physical Host
may run multiple Nodes; a Node may aggregate multiple Local EAIOS. The present
`one-routable-entity-per-node` profile rejects multiple entities behind one Node because
the current Execute protocol has no entity-addressed target, not because Node is an entity.

Grounding authority is admitted attributed World/semantic evidence, not model text or live
inventory. Binding and resource authority reside in Control. Reconciliation may block and
propose role recovery but cannot change an Actor's committed entity; explicit Actor migration
needs its own Control decision. A deployment operator owns registry identity, revision and
registration updates. A Node reconnection is not a registry update or proof of entity migration.

MI may name a PhysicalEntity only when an exact, fresh entity reference was admitted into its
immutable Grounding Context. Planner and Repairer cannot create deployment identity strings.
The mission semantics are immutable. Control uses a deployment-owned, mission-independent,
versioned PhysicalEntityRegistrySnapshot for current entity-to-Node routing. This registry is
not a MissionPlan field and is not a substitute for live Node eligibility. The current Node
Protocol cannot address multiple entities behind one Node, so executable deployment snapshots
explicitly declare `one-routable-entity-per-node`. A future entity-addressable Node profile
can lift this limitation without changing Mission distinctness semantics.

Control resolves grounding in Matching, carries candidate entity identity through Scheduler
selection, Proposal, and CommittedPlan, and revalidates mapping, actor continuity, Context
distinctness, operation support, and resources before Commit and Bind. Scheduler only enforces
declared hard constraints; it never spreads actors without one. ActorBinding records the exact
PhysicalEntity, Node, registry ID, and bind-time revision only after Group Bind succeeds. Bind
validates every assignment before modifying Group/reservations and cannot fail after mutation.
Same entity and Node at a newer registry revision preserve the original bind-time provenance.

Control checkpoints persist Mission semantics, ActorBindings, Groups, and reservations; they
do not persist the registry as current deployment truth. On restart the deployment supplies a
fresh registry snapshot; absent, changed, mismatched, or rolled-back routing fails closed rather
than migrating a bound Actor. Node reconnect does not change PhysicalEntity identity. Ordinary
Role recovery may not silently replace the Actor's entity or migrate its Node; no replacement
keeps the Role Blocked/Pending. Explicit Actor migration and entity-addressed multi-executor
Nodes require separate decisions and protocol evidence.

## Compatibility and limits

v0.7 plans remain accepted inputs without physical binding constraints; v0.8 fields are rejected
in earlier versions. An explicitly admitted v0.8 plan retains its version through checkpoint
restore even when no Actor is grounded and every Context constraint list is empty. The
deployment's legacy mission-scoped Node placement remains available
for authored plans but is not PhysicalEntity grounding. This decision does not translate
Habitat PDDL to semantic goal evidence or claim that B1 is closed. An application adapter must
provide admitted, canonical goal/world context to MI separately. Current registry revisions
are deployment-owned configuration evidence; no automatic discovery, topology migration, or
physical-host inference is implied.
