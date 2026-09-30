# ADR-0052: Reset-bound route support observations

- Status: Proposed for review
- Date: 2026-09-30

## Context

The deployment's existing floor matrix deliberately keeps distance-based goals
unknown across floors (ADR-0050). Even same-floor compatibility does not prove
that the active Local How has a path. Controlled runs observed correct exact
assignments followed by goal-region resolution failures before the first
completed simulator step. Neither semantic floor labels nor a bounded search
miss proves that an endpoint can or cannot complete a Mission.

Calling the original Oracle's target resolver before Stage2 is unsafe as an
observer: it fills action caches, and its safe-snap fallback can sample random
points. Its lazy navmesh builder also modifies shared NavMeshSettings. An
observer must not reuse those mutating helpers.

## Decision

1. Add a separate, default-off `--reset-route-support` deployment option. It
   requires the shared-world backend, its spatial profile, and the explicitly
   selected goal-region Local How. The observation runs once after the existing
   reset and before endpoint readiness. Stage2 reuses exactly that reset's
   observations; no reset, policy call, action, or simulator step is added.
2. Build at most one detached PathFinder per configured endpoint. Copy the
   installed version's supported scalar NavMeshSettings and apply the same
   agent radius/height/climb/slope and static-object buffer as the current
   Oracle. Refuse unsupported layouts. Do not assign the new mesh to the live
   simulator or action, call `_create_pathfinder`, prime `_targets`, use
   `safe_snap_point`, or invoke a random sampling API. Native navmesh building
   remains a vendor operation on a supplied isolated mesh, not a physics step.
3. Reuse the current goal-region selector's official 3D radius, current
   base-to-PDDL-reference offset, and Oracle stop envelope. Probe only the
   deterministic original snap and agent-floor projected center. Do not scan
   navmesh vertices: this avoids the existing vendor API's unbounded vertex
   array allocation during diagnostics. The bound is 128 endpoint/goal records,
   two path queries per record, and 256 path queries in total. Record actual
   counters and observation elapsed time. Navmesh recomputation has vendor-owned
   cost, so these bounds are not a hard wall-clock or whole-simulator memory cap.
4. Emit `roboguide.deployment-reset-route-support/v0.1` as a separate artifact
   with three distinct states:
   - `supported`: a static path witness to one stop-compatible goal-region
     point; dynamic execution and official goal truth remain unproven.
   - `not_found`: neither initial candidate produced a witness within this
     probe. The runtime resolver's vertex fallback was not evaluated. This is
     neither its full verdict nor physical impossibility.
   - `unavailable`: unsupported layout, failed data read/build/query, missing
     deterministic geometry, or a changed reset start. Do not invent state.
5. Bind the artifact to the existing run/episode/scene/dataset/seed/reset
   identity, preassignment digest, actual starts, exact semantic goal, Local How
   profile, runtime module digests, and native Habitat-Sim extension digest.
   The Local How profile becomes v0.2 to declare this option explicitly. The B1
   preflight checks identity, source bytes, geometry, budgets, and complete
   endpoint/goal coverage. These are consistency checks, not simulator truth
   authentication. Use a fresh B1 run directory as already required by the runner.
6. Observation faults become unavailable records. Optional serialization/write
   faults are logged without replacing the SUT exception or changing physical
   execution. If B1 explicitly requested this evidence, an absent/invalid
   archive stops before Controller/Mission Request startup and is attributed to
   the existing external `harness` category. Honest unavailable and bounded-miss
   records pass the archive check; they do not exclude a Formal sample.

## Authority and integration limits

This is an observation artifact, not a new Core capability, MissionPlan field,
resource promise, executor choice, Runtime outcome, or benchmark authority. The
existing negative floor matrix and Control candidate restrictions are unchanged.
MI receives no Node IDs, reset agent inventory, or route-based selector through
this addition. Formal and benchmark admission are unchanged.

The reset observation must not become a permanent Actor-wide negative
restriction: a later Task may start after another navigation changes pose or
geometry, and bounded misses may have unexamined valid routes. A future consumer
must define freshness against the current attempt/world and distinguish a
preference for positive evidence from authoritative exclusion. Any deliberation
feedback must preserve the original task semantics and Control's placement
authority; the adapter cannot redistribute tasks or force single-Actor plans.

Offline tests cover isolation, budgets, serialization failures, three-state
classification, source binding, and unchanged admission. They do not prove all
native-library side effects, dynamic navigability, improved model organization,
or benchmark success. Those require a separately frozen controlled validation.
