# ADR-0064: Spatial route arrival in differential-base Local How

- Status: Proposed for review
- Date: 2026-10-03

## Context

The deployed original Oracle measures arrival using only final-point X/Z and
entity heading. Its near-target branch can stop following the next route waypoint
when the horizontal projection is close while the robot is on a different level.
An actual archived navigation finished with 0.472 m horizontal separation but
2.591 m vertical separation, a successful remaining path, and a negative official
goal. Mesh resolution alone does not correct this local control mismatch.

The goal-region and step-aware profiles intentionally retain original Oracle
control. Changing them implicitly would hide an execution-arm difference and
invalidate historical comparisons. A local navigation completion also cannot
replace Habitat's official metric or Orchestration satisfaction evidence.

## Decision

1. Add separate default-off `--spatial-navigation-arrival`, requiring the explicit
   step-aware and goal-region profiles. B1 opts in through
   `ROBOGUIDE_B1_SPATIAL_NAVIGATION_ARRIVAL=1`. Disabled deployments retain their
   existing action class and semantics. Only the existing differential-base
   motion profiles are admitted; other motion profiles fail explicitly.
2. Preserve the model-selected entity, point selection, robot ability, simulator
   initialization, original velocity parameters, distance/turn thresholds, skill
   budget and total simulator budget. Do not edit external EMOS. A deployment-owned
   action uses one original base-motion dispatch per invocation; it does not call
   another actor, model or Gym step.
3. A successful bounded route must end within 5 cm of the selected point. There
   is no direct-line fallback on a missing active route. Require fewer than or
   equal to 4096 points, finite geometry and supported thresholds. Until the base
   is within the original distance threshold in three dimensions, follow the
   next usable planar waypoint. Only after spatial proximity may the action turn
   toward the original entity and set the existing skill_done flag. A vertical-only
   unusable segment fails rather than inventing a planar move or success.
4. This is a changed Local How controller, not read-only diagnostics and not a
   stricter official success rule. Official predicates and benchmark population
   retain their authorities. No plan, Actor count, resource, Control, recovery,
   MI input, prompt, goal or Formal admission changes.
5. Local How artifact v0.5 adds `navigation_arrival_profile=spatial-route-arrival/v0.1`.
   Action selection evidence v0.4 discloses it independently of selected point and
   copied mesh. Reset observation uses identical copied mesh settings but never
   runs the controller. B1 binds exact profile/source identity, rejects missing or
   resealed inconsistent sources, and continues accepting historical v0.2–v0.4.
6. Existing best-effort physical diagnostics may read the action's latest bounded
   pre-base-action decision: branch, 3D/horizontal/vertical distance and local
   invocation count. The count is not a simulator step, the snapshot is not a
   post-motion pose or goal truth, and absent observations remain unavailable.
   Reset clears it; diagnostic read failure cannot affect execution.

## Validation and limitations

Offline tests cover different-level horizontal proximity, genuine spatial arrival,
unchanged heading/threshold/velocities, waypoint continuation, unusable/missing
routes, one base dispatch, target integrity, idle/already-done behavior, default-off
selection, bounded observations and old/new archival identity. The actual installed
vendor imports must be checked separately without constructing a simulator.

Correct arrival is not proof that arbitrary robot starts have feasible routes,
that the original base controller traverses every stair, or that a joint Mission
will succeed. Real validation must freeze the new code and arm identity, retain
all failures and budgets, and compare actual starts. No historical outcome is
recomputed as a new benchmark result. The first controlled diagnostic is not a
success-rate estimate or a formal paired benchmark.
