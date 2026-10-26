# ADR-0224: Bounded initial observation geometry preferences

- Status: Implemented; physical validation pending
- Date: 2026-10-27

## Context

A capability name establishes implemented operation support, not compatibility
between an exact terminal condition and a calibrated observer. Actual E1 evidence
shows detection and own-reference arrival can require different platform geometry.
Logical Actor names and Task order cannot supply this information to Matching.
Giving MI live inventory or a benchmark-specific allocation answer would cross the
existing boundary and would not repair it.

## Decision

1. An explicit deployment-feasibility v0.7 source may contain initial observation
   geometry preferences, under Local How v0.18. Earlier sources acquire no new
   policy. The producer reads only the same reset, exact configured endpoint
   digests, loaded calibrated fixed-camera attributes and actual base/reference
   geometry. No sensor refresh, route mutation, new reset, action, predicate call,
   provider request or RNG sample is used.
2. A finite analytic candidate stencil checks whether a stance on the original
   reset base plane places the exact target center within the calibrated primary
   camera frustum and, if requested, the own-reference arrival sphere. This is
   hypothetical static geometry, not a route, visibility, achievable-height,
   detector-pixel or official-goal certificate. Secondary/articulated optics,
   occlusion, other support surfaces and dynamic state remain unknown.
3. A found geometric candidate is advisory positive evidence. No candidate,
   missing calibration and unsupported motion models remain unknown. They never
   exclude a Node or prove physical impossibility. Actual capability/readiness,
   resource, binding and safety checks remain independent Control authority.
4. The source binds exact run/episode/scene/dataset/reset, start positions and
   configured Node bytes. Control rechecks bounded coverage and the analytic
   projection, then may rank only current eligible candidates for the first
   Mission's initial unbound work. At most four initial slots/endpoints and 256
   combinations are inspected; unsupported scope uses normal scheduling without
   a preference. Equal support stays tied. No concrete executor is supplied to MI.
5. Initial advisory input has the existing ten-minute, receive-relative lifetime.
   Restore disables new use and cannot renew the original source. Published
   orders confer no reservation, admission, future availability or physical fact.
   Explicit route and observation preference profiles must not silently replace
   one another. Existing normal scheduling and serial Actor reuse remain valid.
6. MI Prompts, task semantics, exact tool arguments, original EMOS code, simulator
   budgets, Formal admission and official final evaluation remain unchanged. Each
   new physical validation freezes its own code/configuration and retains all
   earlier outcomes; a small selected-case diagnostic is not an E1 success rate.
