# ADR-0046: Reset-state spatial admission for Habitat Local EAIOS

- Status: Proposed for review
- Date: 2026-09-27

## Context

The Habitat mobility deployment registers typed capability facts such as
whether a configured agent can transition between semantic floors. A Mission
Plan can be semantically valid while a particular reset places an agent on a
different floor from its assigned destination. The current shared-world
adapter receives committed assignments before the one official Habitat reset,
so it cannot prove that episode-specific start-state condition during Mission
Intelligence planning.

Failing to distinguish these facts allowed a single-floor deployment to spend
the full physical step budget on an assignment that the deployment itself
could not perform. Reassigning the Task at the adapter boundary would violate
Control ownership and changing the goal would violate benchmark authority.

## Decision

1. The launcher freezes the exact capability attributes from the Node config
   files used for registration into a versioned JSON profile. Each source file
   digest is checked again by the Habitat child before it serves the endpoints.
2. After the one official reset, and before Stage2 `AgentArguments`,
   `actor.act()`, or `gym_env.step()`, the shared-world adapter reads the
   agent base position and the destination entity position through existing
   read-only Habitat/PDDL APIs. It maps both positions to semantic floors and
   records a versioned decision artifact. Overlapping regions on one floor
   prove a floor without proving a unique region identity; overlaps across
   floors remain unknown.
3. A different-floor observation paired with an explicit registered
   `supports-floor-transition=false` is an incompatible local deployment
   assignment. The adapter records the reason, preserves terminal evidence,
   and fails the local executions. It never reassigns, edits the invocation,
   or fabricates `pddl_success`.
4. Same-floor, capable-agent, missing-profile, ambiguous-region, and other
   unreadable cases remain respectively admitted or explicitly `unknown`.
   Unknown evidence may continue execution, but the artifact distinguishes
   `execution_allowed=true` from `all_admitted=false`. A positive admission
   does not prove route reachability. A missing fact does not become a guessed
   capability.

## Consequences and limits

This closes a concrete negative feasibility gap without adding a Core or
MissionPlan field and without turning benchmark predicates into executor
identity constraints. It makes an impossible assignment fail early and
reviewably, but it cannot improve the success rate by itself: Control still
owns assignment, and the current pre-reset Mission grounding has no
episode-specific agent start floors. A future pre-assignment start-state
contract may let MI express a justified capability requirement before
matching; that is a separate change requiring identity, freshness, and
reproducibility review.

The check does not calculate paths, invoke navigation, read live Node
inventory, or alter Habitat RNG. Official benchmark truth remains Habitat's
PDDL outcome.

The initial Episode51 smoke exposed a concrete evidence limit: several actual
reset positions and PDDL entity positions belong to multiple loaded semantic
regions. When these regions share one floor, the adapter can retain that floor
without claiming one region; when they span floors, the floor remains `unknown`.
The adapter must not substitute a guessed height threshold or label a known
floor as a proven route. This gate is not sufficient readiness for the broad E1
comparison: it runs after Control commitment and cannot choose a different
capable executor.
