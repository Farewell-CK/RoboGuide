# ADR-0065: Prepare selected joint navigation before physical dispatch

- Status: Proposed for review
- Date: 2026-10-04

## Context

Habitat dispatches actions inside one Gym step sequentially. A bounded target
resolution miss in the second navigation action can occur after the first
robot has moved, while no complete Gym step or post-step observation is returned.
The opt-in spatial arrival controller must expose this expected failure without
partially dispatching its joint navigation command or losing the failing attempt.
This is a Local EAIOS failure boundary, not a scheduling resource violation.

## Decision

1. With the existing default-off spatial arrival profile enabled, use the same
   vendor vector decoder as Gym and preserve its actual selected action order.
   Prepare all navigation targets, routes and velocity decisions before entering
   the original Gym step. Preserve Stage2 tool choice and canonical destination;
   no additional actor, Provider, Gym step, target query or route query is added.
2. Each prepared command is bound to the exact episode, entity index, position
   and orientation. The original action consumes it once and performs its one
   original base dispatch. Stale commands fail explicitly. Discard unconsumed
   commands on failure and after dispatch; do not clear original mesh/target caches.
3. Preparation is Local How execution. It initializes existing caches and may
   invoke the original target helper, which can use RNG and temporary restored
   agent state. Hoisting these calls before motion is an explicit arm difference,
   not a claim of read-only observation or identical successful trajectories.
4. Catch only expected goal-region/route preparation failures as local failures.
   Retain action/endpoint, complete canonical invocation/attempt, original error,
   bounded search counters, completed simulator-step count and a content digest.
   A bounded miss never proves physical impossibility. Unexpected exceptions keep
   their original exception path. Evidence writes are best effort and cannot
   authorize a Gym step or mask the execution failure.
5. Stop the shared segment after such failure, preserving already Completed peers.
   No automatic reassignment, new MissionRequest, goal rewrite or simulator reset
   is authorized. A preparation failure before the first completed physical step
   has no official execution outcome: reset metrics remain diagnostic, and the
   summary records an explicit benchmark-unavailable reason. After actual physical
   execution the existing official metric path remains in force.
6. Local How v0.6 adds `navigation_preparation_profile=joint-navigation-preparation/v0.1`.
   Source provenance includes the original Gym decoder and preparation module;
   opted-in archive preflight checks their exact identities. Historical v0.2–v0.5
   remains supported. Action-selection v0.4 retains its existing meaning; failure
   evidence uses `roboguide.habitat-navigation-preparation-failure/v0.1`.
7. Disabled deployments retain the original path. Core, MissionPlan, MI, resource
   authority, Formal admission, official goals/thresholds, robot ability, skill
   and simulator budgets, and external EMOS source are unchanged.

## Validation and limitations

Deterministic checks reproduce the original partial-motion failure and prove
that preparation stops all motion on an expected miss. They cover one/two/four
configured actions, unchanged command parameters, one query and dispatch,
stale-command rejection, cleanup/storage faults, serial and shared policy loops,
Completed-peer preservation, default-off behavior and historical/new archive
identity. Real controlled execution is separate evidence, not a success-rate gate.

This boundary does not provide transactional rollback of arbitrary simulator
faults, multi-robot collision safety or proof that bounded route search finds
every feasible goal point. A robot pose changing between preparation and dispatch
is a stale-command error, not an implicit permission to recompute or move it.
Deployment capacity and physical path evidence remain independent.
