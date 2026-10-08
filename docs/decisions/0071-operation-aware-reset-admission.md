# ADR-0071: Operation-aware reset admission for shared-world relocation

- Status: Accepted
- Scope: Deployment adapter, Controller composition and B1 source preflight; no Core or MissionPlan change

## Context

The shared-world Controller adapter previously required one `destination` parameter
for every accepted operation. This navigation-only contract rejected a valid
`object.relocate@v1` intent with its exact `object`, `source` and `destination`.
Navigation floor exclusions also cannot prove that an integrated manipulation
operation is feasible or infeasible. Disabling deployment admission would discard
the existing topology, reset identity and resource protections.

## Decision

Navigation-only deployments continue producing and consuming
`roboguide.deployment-intent-feasibility/v0.3`. An explicitly enabled relocation
deployment publishes v0.4, containing the unchanged navigation records and a
separate `roboguide.deployment-operation-admission/v0.1` projection. This projection
binds observed object/source pairs, destination entities, exact Node configuration
digests and capacity-one `space` endpoints to the existing reset and registration
artifacts. Its route reachability is explicitly `unknown`.

The producer uses existing immutable observations: no extra reset, predicate,
action, model, RNG or environment read. Relocation preflight reconstructs the
projection from its original sources. Independent B1 preflight cross-checks source
schema/digests, run/episode/scene/dataset/seed and step-zero identity. Missing,
malformed or contradictory evidence fails before MI and preserves original files.

Controller validates the version and exact operation parameters, then applies
the existing shared-world topology and `space:1` requirements. Relocation requires
an observed exact object/source and an observed destination; it does not inherit
navigation-only candidate exclusions. Two Actors cannot concurrently manipulate
the same object. One independent Actor may reuse its endpoint after Control-owned
Task release; two independent Actors require separate feasible endpoints. Current
Node operation support/readiness and actual resource reservations remain Control
responsibilities, independent of the configured endpoint projection.

The existing portable content digest covers both navigation and operation sources.
Persisted Actor candidate restrictions therefore retain the combined source
watermark, and existing restore checks reject a changed source. A v0.3 snapshot
never implicitly enables relocation. No new environment variable, Core resource
kind, public HTTP contract or MissionPlan schema is introduced.

## Consequences and validation

The canonical intent and generated plan are never rewritten. Operation admission
is not proof of a route, manipulation success, Task satisfaction or official PDDL
truth. Formal population admission and benchmark rules are unchanged.

Synthetic cross-language evidence and real Controller HTTP tests cover distinct-
object parallel allocation, exclusive slots, single-Actor sequential reuse after
actual reducer completion, current capability shortages, atomic invalid-input
rejection, navigation compatibility and combined source digests. These offline
tests do not prove robot capability or model reliability; a fresh real preflight
must establish physical execution and archive closure before batch dispatch.
