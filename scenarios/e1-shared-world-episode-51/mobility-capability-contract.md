# Habitat mobility capability declarations

The `mobility.navigate@v1` and `mobility.move@v1` capability profiles are
registered by the deployment's Node Service. The shared-world configuration
maps `node-a` to Habitat `agent_0` (Spot) and `node-b` to `agent_1` (Fetch).
The actual simulator mapping comes from the deployed agent configuration and
adapter endpoint mapping, not from these logical Node IDs themselves. The
original EMOS mobility benchmark describes Spot's cross-floor navigation and
Fetch's single-floor mobility (`habitat-mas/README.md`, Mobility task description,
and `habitat_mas/agents/capabilities/parse_urdf.py` in the separately maintained
EMOS checkout); this deployment records those static capability
facts on the profiles each Node *actually* registers:

| Habitat agent | Source configuration | `base-type` | `supports-floor-transition` |
| --- | --- | --- | --- |
| `agent_0` / Spot | `node-a.toml` | `legged` | `true` |
| `agent_1` / Fetch | `node-b.toml` | `wheeled` | `false` |

The three maintained single-Spot smoke configurations register only
`mobility.navigate@v1`; they must not gain a fictitious `mobility.move@v1`
profile. Non-Habitat and non-mobility Node configs do not claim either fact.
The `base-type` value describes the configured robot class; it is diagnostic
metadata and has no canonical feasibility meaning. The boolean
`supports-floor-transition` is a typed, optional Catalog feasibility attribute.
`true` asserts deployment-supported inter-floor mobility, not episode-specific
path feasibility; `false` asserts that this deployment cannot traverse floors.
A missing attribute is unknown and never satisfies a MissionPlan requirement
for `true`. No constraint is added just because a destination name includes
`TARGET` or a scene has multiple levels.
These declarations are *configuration-owned capability claims*, not proof
that a particular path exists in an episode or that a specific local skill
can complete it. Changes to the robot configuration or adapter mapping
require a corresponding reviewed update to these claims and tests.

Control already compares a MissionPlan capability requirement with current
Node registration facts. It only applies this attribute if the Role declares
`{"attribute":"supports-floor-transition","operator":"equals","value":true}`.
An unconstrained `mobility.navigate@v1` Role matches both ready providers.
The deployment execution profile currently adds only `space:1`; it does not
know which semantic destination needs a floor transition. In particular,
**adding attributes to Node registration alone does not change the existing
Episode51 assignment or improve the benchmark success rate**. No tooling may
infer a floor requirement from an entity name or count of goals.

The Mission service reads the frozen joint-goal semantic evidence, the
canonical capability vocabulary, and the optional deployment-owned planning
profile at `planning-profile.json`. That profile is a versioned summary of
abstract mobility capability classes and the typed `supports-floor-transition`
fact; it contains no Node, Resource, Physical Entity, health, lease, reservation,
or current availability. It does not select an executor. The service still does
not read live Node Inventory. The separate pre-reset planning-world artifact
can include exact target-region/floor facts from the loaded episode and scene;
object instance locations with only a template handle remain explicit gaps.
The semantic goal artifact itself contains scene and entity identities but no
room-to-floor mapping or actual agent starting floor. In this deployment,
Habitat randomizes starts at `gym_env.reset()`, after the shared-world
coordinator has received both committed assignments. Only then does
`get_task_text_context()` supply the scene description, including room and
floor information, to the original EMOS Stage2. The frozen Mission Grounding
still lacks a per-agent initial floor. Before automatically
requiring `supports-floor-transition=true` for a Role, an admitted,
revision-bound episode topology/start-state source and an appropriate
pre-assignment observation sequence are necessary. Missing or stale
reachability evidence must remain explicit instead of silently choosing a
robot or altering the official goal.

The offline tests exercise Catalog typing, actual Node config compilation
and registration projection, and the existing Control capability filter. They
do not prove dynamic per-episode feasibility, Stage2 action correctness, or
improved official PDDL outcomes. Historical experimental evidence remains
under its existing local run directories and is not part of this change.

## Episode-start and topology review boundary

The current deployment already publishes a start-admission document from
`SharedWorldCoordinator.deployment_contract()` in
`integrations/habitat-local-eaios/habitat_local_eaios/shared_world.py`. Its
`required_distinct_endpoint_assignments=2` and
`sequential_endpoint_reuse_supported=false` describe an **adapter start
condition**, not a requirement that a benchmark's two predicates need
distinct physical executors. One Actor with two sequential Tasks remains a
semantically valid MissionPlan, but this fixed two-endpoint deployment cannot
start it: the first assignment waits for a distinct second endpoint until the
bounded pair-wait expires. `shared-world-start-admission.json` records admitted
or rejected assignments; the tests in `test_shared_world.py` cover both cases.
This limitation cannot be remedied by adding an unjustified distinct-entity
constraint to MI or by interpreting a ready Node as proof of an episode start.

The simulator supplies actual initial agent positions, rotations, scene, and
per-conjunct goal observations only after the shared-world barrier and
`gym_env.reset()`; see `PhysicalDiagnostics.record_reset()` in `diagnostics.py`
and the `initial_agent_positions` field in `shared-world-summary.json`. These
post-reset observations are evidence for the run and cannot retroactively
authorize a pre-assignment floor requirement. The existing `space:1` exclusive
resource declaration handles endpoint concurrency, but it does not claim an
agent can reach a specific destination from an unknown initial floor.

Before lifting this registration draft into production, separately review the
Node-to-agent mapping and the versioned source of floor-transition claims,
and decide whether the deployment admits a single-Actor sequential topology.
Any automatic cross-floor Role requirement additionally needs grounded
start/goal-floor facts available **before** matching, with freshness and
provenance; the current implementation does not fabricate them.
