# ADR-0050: Distance-goal-aware deployment spatial feasibility

- Status: Proposed for review
- Date: 2026-09-29

## Context

ADR-0046 and ADR-0047 treated an agent on a different semantic floor from a
PDDL destination, together with `supports-floor-transition=false`, as proof
that its canonical mobility intent was infeasible. Episode 3 exposed a false
negative: Habitat's official `any_at` checks the full 3D distance against
`robot_at_thresh`, not equality of semantic floor ids. The archived Fetch
start and target differ vertically by about 1.624 m, below the deployed 2 m
threshold. A read-only navmesh probe found a reachable same-floor point inside
the official target radius. That geometric witness does not prove the original
Stage2 skill will select or traverse that route.

## Decision

The Habitat adapter reads the existing official goal expression and threshold
without stepping the simulator or invoking a second path query. Its v0.2
spatial record reports whether the exact destination is an `any_at` goal,
another goal predicate, absent from the goal, or unavailable. It also records
the positive finite `any_at` threshold when available. The v0.3 reset-state
matrix retains the same source identity and content digest rules.

A different-floor observation and explicit false transition capability retain
the current deployment's `incompatible` decision only for a destination absent
from the official goal. This is its existing exact-entity destination profile,
not a general inference that every other mobility task requires a floor change.
An `any_at` destination, an unrecognized predicate, or unreadable goal metadata
remains `unknown`; no cross-floor route or predicate truth is inferred. The
same classifier applies before assignment and at local execution admission.
The B1 preflight cross-checks each classification against the frozen neutral
semantic goal. Controller rejects malformed records and decisions that
contradict their registered floor, capability, and goal-occupancy facts. Old
matrix versions cannot silently pass as the new decision rule.

Control still owns Matching, commitment, and physical Actor placement. Mission
Intelligence receives neither live Node inventory nor an adapter-selected
executor. The official Habitat predicate remains the sole benchmark success
authority. A subsequent Stage2 or navigation failure is retained as a real
execution outcome, not converted to pre-assignment infeasibility.

## Consequences and limits

This removes one unsound early rejection without promising that a particular
navigation skill will reach a valid pose. Static navmesh connectivity omits
dynamic collisions, local policy choices, and robot-specific motion limits.
Explicitly non-goal destinations retain the old floor requirement. A future
operation whose contract permits a cross-floor proximity outcome outside the
official goal needs its own declared success semantics before this negative
rule can be generalized further.

Controller deployment rejection is not automatically fed to an LLM. A future
bounded planning-feedback route must distinguish immutable topology evidence
from transient resource shortage, carry typed and source-bound feedback, and
revalidate any revised plan through the full MI review chain. Repeating a
409 or inventing Actor constraints cannot repair physical infeasibility.
