# ADR-0051: Deployment-owned goal-region navigation for distance predicates

- Status: Proposed for review
- Date: 2026-09-29

## Context

The controlled Episode 3 run sent `TARGET_any_targets|0` to Fetch. The original
Oracle selected a point on a different floor, and its agent-specific pathfinder
reported no path. A separate static probe found a same-floor point inside the
official `any_at` radius, but did not establish robot-specific reachability.
The model-selected entity and Control assignment were correct. A new MissionPlan
constraint or an LLM retry cannot repair this Local How target-point choice.

## Decision

An explicitly selected Habitat Local EAIOS profile may replace only the Oracle
navigation point resolver with an adapter-owned subclass of the original action.
It retains the original EMOS Stage2 model, selected `nav_to_obj` entity,
navigation `step`, skills, local terminal conditions, simulator steps, and
official PDDL evaluation. The adapter reads the reset-bound official goal and
its positive finite `robot_at_thresh`. It applies this resolver only to exact
entities named by direct conjunctive `any_at` predicates. Other destinations
continue through the original action.

For a qualifying destination, selection first tests the original point against
the three-dimensional goal region, the original Oracle's local stopping radius,
and the active agent-specific pathfinder. If that point is unsuitable, it
projects the official entity's X/Z onto the agent's current navmesh height and
tests the agent-specific snapped point and route. A bounded, deterministic
navmesh-vertex search remains a fallback. Candidates are ranked by their
estimated distance after the original Oracle could stop short of the physical
navigation point. The current base-to-PDDL-reference offset is included in
this estimate. A first controlled run found that a point inside the official
radius could still stop just outside it; the stop envelope prevents that point
from being accepted solely because its center is inside the goal region.
No simulator step, reset, collision command, random sampling, or
physical reassignment is added. Unsupported action layouts, unavailable
authoritative geometry, and exhausted search fail explicitly; no straight-line
fallback is accepted as a route. Selection evidence records both the original
and selected point, path status, search bounds, and the exact deployment mode.

The stop envelope assumes the local controller approaches the selected point
on the indicated navmesh surface; it is a conservative planning estimate, not
a proof of dynamic terminal pose. The resolver is a Local How choice, not a benchmark verifier. A pathfinder
route is static evidence and can fail under dynamic obstacles or local control.
The official `Predicate.is_true` and `pddl_success` remain authoritative. No
Core, MissionPlan, formal admission, or generic capability contract changes.

## Consequences and limits

This mode changes RoboGuide's physical action implementation relative to native
EMOS. Comparative runs must pin and disclose it. It is opt-in for controlled
diagnostics until its compatibility and physical behavior are established.
The bounded vertex search can report no supported point despite another
unexamined reachable point; that is an explicit Local How failure, not evidence
that the mission is semantically impossible. An official distance predicate may
still be false after the local skill declares completion.

Selection record v0.2 adds structured counters on bounded misses and checks
the remaining query budget before a projected-center query. Exception messages
and fail-closed propagation remain intact.
