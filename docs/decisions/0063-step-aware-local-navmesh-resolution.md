# ADR-0063: Step-aware Local How NavMesh resolution

- Status: Proposed for review
- Date: 2026-10-02

## Context

Habitat-Sim v0.3.1 converts declared climb height into Recast voxel units using
`floor(agent_max_climb / cell_height)`. A positive climb smaller than the vertical
cell size is represented as zero climb. A robot's configured physical abilities
and their numerical mesh representation are separate facts. A disconnected mesh
does not by itself justify enlarging those abilities or moving a reset pose.

The original Oracle agent-mesh helper also changes the shared mesh-settings
object. The isolated reset observer already copies those settings. An optional
execution profile needs the same copied construction in both paths, with explicit
identity, rather than an external EMOS source patch or a diagnostic-only route
that the actual action cannot use.

## Decision

1. Add default-off `--step-aware-navmesh`, requiring the existing goal-region
   Local How. B1 enables it only through `ROBOGUIDE_B1_STEP_AWARE_NAVMESH=1` with
   `ROBOGUIDE_B1_GOAL_REGION_NAVIGATION=1`. It selects the maintained
   `StepAwareGoalRegionOracleNavDiffBaseAction` subclass before environment
   construction. Disabled execution retains the existing action choice.
2. Copy the closed supported scalar layout, exact configured radius, height,
   climb and slope, the existing 5 cm collision buffer and static-object inclusion.
   For positive climb, choose `min(original_cell_height, agent_max_climb / 2)`;
   zero climb retains the original resolution. Preserve horizontal cell size and
   all other mesh fields. Never increase climb, slope, radius or height to create
   a route. The minimum supported vertical cell size is 5 mm and refinement is
   limited to 32 times. Exceeding those limits or an unknown layout fails explicitly
   before native construction; there is no undisclosed fallback.
   A 1 nm cell-height and 1 ppm refinement-ratio allowance cover native float32
   storage at the supported boundaries, not smaller physical ability declarations.
3. Use this same pure settings builder for the active action and detached reset
   observation. Neither modifies the shared settings or replaces the simulator's
   global pathfinder. Habitat retains mesh-construction and physical collision
   behavior. Original Oracle control, model-selected entity, skill budget,
   finished sensor and official PDDL truth remain unchanged owners. This is an
   intentional Local How change, not a read-only diagnostic feature.
4. Local How v0.4 declares `step-preserving-cell-height/v0.1`, independently of
   the geometry flag. Action selection v0.3 archives actual active mesh settings.
   Reset-route artifacts retain their v0.1/v0.2 envelope, whose Local How digest
   binds the new version. Preflight checks exact declaration, source identity
   and supported observed vertical resolution. A declared profile cannot silently
   observe an action using another profile. Unknown observations remain unknown.
5. No MI, MissionPlan, capability/resource declaration, Core scheduling, recovery
   authorization or population protocol change. A static path is not physical
   completion. Any controlled comparison discloses this profile as a difference
   from original EMOS, and compares actual starts and official outcomes separately.

## Validation and limits

Deterministic tests cover default-off selection, original controller inheritance,
zero climb, already-fine resolution, capability preservation, independent identical
active/observer settings, failed construction, invalid scalar facts, refinement
limits and resealed archive inconsistency. Native builds and physics require
separate controlled validation. This profile does not fix all disconnected scenes,
prove passage through moving obstacles or guarantee benchmark success.

The numerical refinement limit is not a whole-process memory/time guarantee;
native mesh construction has vendor-owned cost. Experiments retain an external
wall-clock limit and shared-host GPU checks. The exact installed source build
identity must be recorded separately from a public version-tag reference.

Reference: [Habitat-Sim v0.3.1 mesh construction](https://github.com/facebookresearch/habitat-sim/blob/v0.3.1/src/esp/nav/PathFinder.cpp#L573).
