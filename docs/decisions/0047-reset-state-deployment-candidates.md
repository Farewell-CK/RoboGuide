# ADR-0047: Reset-state deployment candidates for a shared Habitat world

- Status: Proposed for review
- Date: 2026-09-27

## Context

ADR-0046 rejects a known cross-floor assignment after Control has committed it.
In the current shared-world deployment, the reset may place a single-floor
endpoint on another floor from a goal. The adapter can then reject the
assignment before Stage2 acts, but Control had no access to that negative fact
when matching. Reassigning inside the adapter would create a second placement
authority. Treating two benchmark predicates as a requirement for two distinct
robots would invent Mission semantics.

The initial position is observable only after Habitat reset. The old launch
order reset the world after assignments arrived. It could not supply both an
actual reset observation and pre-assignment matching evidence for the same
world.

## Decision

1. The shared-world child performs its one official episode reset before it
   advertises ONLINE. It retains the returned observations and uses them for
   the first Stage2 segment; neither the pair nor the serial path resets again.
   A failed or empty reset prevents readiness. The run records actual initial
   positions, the consumed seed, and the reset count. This changes reset
   timing, not the frozen episode, seed, goal, step budget, Stage2 policy, or
   Habitat success authority.
2. The child reads the reset positions, authoritative PDDL destination
   entities, semantic floors, and the Node-config-derived floor-transition
   profile. It writes a versioned v0.2, content-digested, run-local matrix for exact
   canonical operation/destination/endpoint triples. `incompatible` requires
   distinct known floors and an explicit `supports-floor-transition=false`.
   `compatible` means only that this particular negative constraint was not
   violated; it is not a route or task-success proof. Ambiguous or missing
   spatial evidence remains `unknown` and the existing local execution gate
   remains in force.
   Its digest maps each finite floating value to its exact IEEE-754 bits before
   canonical JSON encoding, so Python and Rust agree even when they render a
   small decimal exponent differently.
3. The B1 launcher binds the matrix to its frozen run, episode, scene, dataset,
   semantic evidence, Node-profile digest, and seed before starting Control.
   The Controller loads the bounded matrix as an optional deployment-owned
   source and checks its schema, content digest, endpoint coverage, reset
   positions, and decision consistency. The source is not Mission Grounding,
   live Node Inventory, or a MissionPlan field. Other deployments keep their
   existing Controller behavior when no source is configured.
4. For an accepted plan, Controller composition extracts each exact mobility
   destination and intersects its non-incompatible Node set across every Task
   of the same logical Actor. It rejects an Actor with no candidate and rejects
   a two-Actor session without two distinct feasible endpoints before Mission
   submission. The deployment admits only the execution-session topologies in
   ADR-0045 and requires the configured exclusive `space:1` slot on each Task
   Role. The current Node configuration has one such reservable endpoint slot
   per Node. Control still intersects these restrictions with current
   registration, operation support, health, calendar, and resource evidence;
   only Control selects, reserves, commits, binds, and recovers Nodes.
5. The Mission/Actor candidate restriction and matrix digest survive Control
   checkpoint restore. A restored Controller must be started with the same
   matrix digest. Matching, recovery candidates, placement constraints, and
   binding recheck the restriction. The restriction is neither a reservation
   nor an ActorBinding and never grants a currently unavailable Node.

## Consequences and limits

Known reset-state incompatibility is visible before a Task enters the physical
world, and the single-Actor serial topology can reuse one feasible endpoint.
If a generated two-Actor plan has only one feasible endpoint, submission fails
as a deployment infeasibility; it must not be relabeled as a Habitat
`pddl_success=false` or repaired by fabricating a distinct-executor Mission
constraint. This gate cannot by itself make the MI model choose a different
valid task organization or prove a route. It also cannot replace current
resource capacity checks or the local post-assignment guard.

The fixed matrix describes one reset world. It is valid for the configured
mobility operations and their no-teleport, fixed-floor-transition capability
contract; a deployment that can externally move agents between floors needs a
new observation/reconciliation protocol before reusing this restriction.
The two-Actor deployment additionally relies on each endpoint advertising one
exclusive space slot. Adding more simultaneous slots per endpoint requires an
explicit topology/anti-colocation review, rather than assuming this policy
will keep assignments apart.

Reset-before-MI can leave an idle simulator world while the one request is
deliberated. Paired EMOS/RoboGuide comparisons must compare the recorded actual
initial states, not infer equality from an equal seed. This decision does not
change Formal population admission or the official benchmark metric.
