# ADR-0062: Scoped static navigation-region evidence

- Status: Proposed for review
- Date: 2026-10-02

## Context

Semantic floor compatibility does not establish a connected navigation route.
A bounded point search miss does not establish that the full goal region is
disjoint from an endpoint's starting component. Conversely, a static NavMesh
minimum cannot establish impossibility under every physical action, changing
geometry, or changing goal position. These are distinct evidence claims.

ADR-0052 deliberately limits the ordinary reset observer to two route candidates.
ADR-0053 consumes positive witnesses through optional initial candidate ordering.
Neither observation may silently acquire permanent Actor exclusion authority.

## Decision

1. Add an independently enabled, default-off `--reset-route-geometry` option.
   It requires the existing reset-route observer and its goal-region Local How
   configuration. It inspects the same detached mesh after the existing reset;
   it does not add a reset, model call, action, physical step, RNG sample, target
   selection, path query, or live action-cache mutation. Ordinary observation
   without this flag retains the v0.1 artifact and its original budget.
2. Read the complete starting component's vertices and triangle indices once
   per endpoint. Include triangle interiors and degenerate edges in the exact
   Euclidean distance calculation. Bind the result to the copied mesh profile,
   component content digest, actual start/reference positions, goal center,
   official radius and existing run/scene/dataset/source identities.
3. Bound retained component geometry to 25,000 vertices and 50,000 triangles;
   bound one reset to 200,000 triangle checks and check a one-second processing
   deadline for each record. A start snap exceeding 0.25 m is unresolved.
   Missing, oversized, malformed, interrupted or time-limited scans are unknown
   and publish no partial minimum. The native export API allocates before its
   size is known, and mesh recomputation has vendor-owned cost: these are not
   hard whole-process memory or wall-clock guarantees.
4. Emit reset-route-support v0.2 only when geometry was requested. Its separate
   `region_analysis` records `intersects`, `disjoint` or `unknown` for the frozen
   static component. A 1 mm margin leaves threshold contact unknown. A disjoint
   result additionally requires a conservative lower bound over rotations of
   the snapshot's base/reference offset; an orientation-specific miss alone is
   insufficient. This bound assumes the offset's length and geometry stay
   unchanged. An intersection is neither a route witness nor goal satisfaction.
5. The Local How profile becomes v0.3 for this flag; the execution resolver is
   unchanged. B1 explicitly requesting geometry requires the new artifact,
   exact source/profile identity and complete endpoint/goal coverage. It checks
   closest-triangle consistency, distances, scope and actual counters, without
   importing Habitat or authenticating the unarchived full mesh minimum.
   Missing or invalid requested evidence is a harness archival failure; honest
   unknown and static-negative observations leave Formal admission unchanged.
6. The separately enabled initial preference consumer may project v0.2 source
   evidence into neutral exact-operation records with `static_support`:
   `witnessed`, `unknown` or `static-disjoint`. Controller candidate combinations
   minimize scoped static misses, then missing positive witnesses, then witness
   cost, with stable ties. Control/Scheduler receive only their existing generic
   ordinals and retain all eligibility, resource and binding authority. Unknown
   **and static-disjoint** candidates remain eligible, including when all current
   choices lack support. Geometry never supplies a hard negative constraint.
7. Existing receive-time expiry, first-successful-Bind discard, first-Mission
   scope, Actor continuity and recovery/restore fences apply unchanged. Neither
   MI nor Core consumes Habitat mesh/PDDL types. No MissionPlan, Node Protocol,
   official benchmark, physical stop, recovery permission or population contract
   changes. Explicitly disclose the enabled geometry/ranking policy in paired
   experiments; initial-state identity and observed execution outcomes remain
   separate requirements.

## Validation and limits

Deterministic checks cover same-floor disconnected components, adjacent-floor
goal-ball overlap, interior witnesses missed by vertex-only checks, reference
rotation, numerical contact, incomplete scans, source substitution and requested
schema downgrade. Controller tests retain both candidates and actual Commit/Bind
paths even with all-negative observations; static data never fabricates success.

Offline checks cannot prove native exports are free of all vendor side effects,
physical feasibility, improved model decisions or benchmark success. A negative
NavMesh observation also does not justify increasing a robot's climb/slope or
moving its initial pose. Establish the registered physical/local-operation
contract before adding any stronger negative admission consumer.
