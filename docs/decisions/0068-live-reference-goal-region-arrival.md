# ADR-0068: Live reference arrival for goal-region navigation

- Status: Implemented, physical validation pending
- Date: 2026-10-05

## Context

The goal-region resolver requires a selected point and its estimated full Oracle
stop envelope to fit inside the official distance goal with a local safety margin.
A reachable component may intersect a goal in a narrow region even though no point
can contain that complete envelope. Additional triangle search cannot resolve this
control mismatch. Dropping the envelope for the existing controller would allow
local completion while the actual robot reference remains outside the goal.

## Decision

1. Add a separate default-off `--goal-aware-navigation-arrival`, requiring the
   goal-region, step-aware and spatial-arrival profiles. B1 opts in with
   `ROBOGUIDE_B1_GOAL_AWARE_NAVIGATION_ARRIVAL=1`. The registered
   `LiveGoalArrivalGoalRegionOracleNavDiffBaseAction` is deployment-owned Local How;
   MissionPlan and provider tool calls cannot enable it.
2. For an exact direct conjunctive `any_at` destination, select a point with an
   actual agent-specific path and an estimated PDDL reference inside the unchanged
   radius minus the existing local margin. The full legacy stop envelope is still
   recorded, but is not an admission requirement for this controller. Vertex,
   triangle, time and path-query bounds remain intact; a bounded miss is not a
   physical impossibility proof. Legacy classes retain their envelope requirement.
3. Before each original base dispatch, read the actual robot
   `base_transformation.translation` and exact entity position from the existing
   PDDL simulator information. Local completion requires both original selected-point
   proximity and actual full 3D reference proximity inside that local margin, plus
   the original heading condition. Otherwise continue the bounded route. This
   reads geometry, not `Predicate.is_true` or official metric evaluation.
4. Keep the exact entity, original base velocity parameters, one original dispatch,
   one existing joint Gym step, robot abilities, reset process, skill budget and
   simulator budget. No extra actor/model/reset/RNG call or external EMOS edit is
   introduced. A successful degenerate path is admitted only at the actual selected
   point. Read the current position of the same entity so ordinary settling does
   not substitute a stale center for the local check; the cached navigation point
   remains unchanged. Missing/unusable routes, unsupported goals, unavailable
   reference reads, changed cache/radius or stale prepared geometry fail before motion.
5. Local How v0.7 declares `official-any-at-live-arrival/v0.1` and
   `live-reference-goal-region/v0.1`; action evidence v0.6 discloses the policy.
   Pre-base diagnostics retain actual reference, target geometry, local bound,
   measured distance and branch. These are local observations, not post-motion
   official truth. Completed peers remain governed by existing lifecycle handling.
6. Reset-route support deliberately retains its conservative, two-candidate,
   stop-envelope probe. The v0.7 profile declares
   `reset_route_probe_policy=conservative-stop-envelope/v0.1`. Its positive witness
   is still useful; a miss cannot exclude an endpoint or prove the new controller
   cannot arrive. The separate component analysis and admission authorities are
   unchanged. B1 binds the exact new profile and sources; old archives remain readable.
7. Habitat alone evaluates official predicate truth and joint benchmark success.
   Orchestration retains Mission satisfaction authority. This profile changes the
   RoboGuide execution arm relative to native EMOS and must be disclosed. It does
   not repair invalid plans, invent routes, move goals or change Formal admission.

## Validation and limitations

Offline coverage distinguishes a routed narrow intersection from an unchanged
legacy miss; selected-point proximity from actual reference arrival; different
levels and rotated/reference offsets; missing geometry, route and query budgets;
stale prepared state; default-off selection; conservative observation; source
identity and unchanged B1 population decisions. Existing joint-preparation tests
retain Completed peers and prevent any joint step on an attributable preparation
failure. Actual installed vendor import conformance is separate from physical runs.

Static point estimates may differ from actual dynamics. Original speed and heading
control can still stall, overshoot or exhaust the unchanged budget. A local gate
does not prove post-step residence, later predicate persistence or a joint goal.
Controlled physical validation must freeze code, workload, source and input identity,
compare actual reset states, retain all outcomes and disclose this arm difference.
Historical outcomes are never recomputed as new benchmark results.
