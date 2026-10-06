# Habitat Local EAIOS Bridge

This deployment-owned bridge is the C1-S0 reference path from the generic
`roboguide-node` Local Integration Engine into an existing EMOS/Habitat environment. It is not a
RoboGuide Core module and does not add Habitat, EMOS, Gym, Torch, or simulator dependencies to the
RoboGuide Python environment.

The bridge accepts the canonical operations `mobility.navigate@v1` and `mobility.move@v1`. It accepts the
intact canonical invocation produced by Node Service, retains Mission/Task/Group/Role identity,
objective, typed scalar parameters, and committed resource IDs, then interprets only the semantic
`destination` inside Local EAIOS. The current C1-S0 backend maps that destination to the same Habitat
Oracle navigation action used below the EMOS/CrabAgent high-level policy boundary. PDDL entity
resolution, agent selection, path planning, action arguments, simulator stepping, and local safety
remain Local How.

## Local workflow

The loopback-only HTTP facade implements the existing declarative Node workflow routes:

- `GET /v1/health`
- `GET /v1/capabilities/mobility.navigate`
- `GET /v1/executions/progress` (empty unless explicitly configured)
- `GET /v1/executions/recovery-support` (read-only deployment declaration)
- `POST /v1/executions`
- `POST /v1/executions/status`
- `POST /v1/executions/cancel`

Execute durably returns one idempotent `habitat-*` local handle before dispatch. A dedicated
long-lived child process owns the loaded simulator so Habitat stepping cannot starve the loopback
HTTP facade. Status reports `ACCEPTED`, then `RUNNING`, then only a backend-observed
`COMPLETED`, `FAILED`, or `CANCELLED`. Cancel acknowledgement only persists a request; it does not
change the local state to `CANCELLED`. The simulator process reports that terminal state only after
it has actually stopped stepping the local execution.

## Optional operation progress

`--progress-directory <run-local-path>` enables a read-only navigation observer;
the default does not sample or write files. B1 exposes the same opt-in as
`ROBOGUIDE_B1_EXECUTION_PROGRESS=1`. Current B1 Node templates register a fixed
`habitat-local-eaios`-owned `roboguide.execution-progress/v0.1` State export
polling `GET /v1/executions/progress` every 500 ms with a 1500 ms TTL.

The existing reset/step boundary samples at most four times a second. Navigation
units are whole centimetres of best observed 3D distance improvement to the
committed entity, within one skill/target-position stage. They are not simulator
steps, path feasibility, arrival or official success. A detour can have no
improvement; an operator-supplied stall interval is an observation, not an
automatic cancellation policy. Wait has no counter. Unknown skills or unavailable
geometry do not produce measured work. Files use
`roboguide.habitat-navigation-progress/v0.1`; each endpoint retains only its latest
snapshot, at most 4096 bytes. Observation, encoding and storage faults leave
physical execution unchanged.

Projection requires the exact invocation `attempt_id`, operation, agent and
destination; a local `habitat-*` handle is never a Runtime attempt. Missing,
malformed, oversized, future or 2-second-old producer snapshots return an empty
batch. Polling cannot renew that source age; existing State TTL remains separate.
No action, extra reset/step, predicate computation, path query or RNG call is
performed by the observer. Terminal execution has no active progress sample.

Cancel acknowledgement remains an intent. Only the original backend's actual
return proves it has exited the stepping loop. In the shared deployment, cancelling
one endpoint ends the **whole** joint segment; it does not provide independent
Role stopping, and consumed sessions cannot generally be retried. This observer
does not enable automatic recovery. See [ADR-0058](../../docs/decisions/0058-local-navigation-progress-observer.md).

The existing Node LocalSystem metadata and read-only support route both declare
`roboguide.local-execution-recovery/v0.1`. Shared endpoints report
`stop_scope=execution-group`, `continuation=unsupported`; standalone reset-based
backends report `execution` / `unsupported`. A normal next Task after completion
does not imply retained-context retry after cancellation. Support reads never
query the simulator, model, RNG or execution lifecycle. Controller individual
Role recovery is rejected before Cancel for these deployments; ordinary joint
cancel remains available. See [ADR-0059](../../docs/decisions/0059-deployment-stop-continuation-contract.md).

Optional `--retain-stopped-session` adds **local Group continuation**, not independent
Role stopping or Controller recovery. It is disabled by default; Node templates retain the
unsupported-continuation declaration. The production B1 launcher accepts
`ROBOGUIDE_B1_RETAIN_STOPPED_SESSION=1`: it derives only recovery metadata in copied run-local
Node configs, validates them with the actual Node binary and freezes their byte digests in
`recovery-deployment.json`. Before Controller/Node registration it compares both read-only
adapter support responses with that frozen declaration; missing, changed or oversized evidence
aborts deployment startup. The launcher never issues a recovery command automatically.
When explicitly enabled, the
fixed read-only support route reports `execution-group` / `repeat-after-stop`; deployment
registration must use the same declaration. Either scope still fails the current Role
`/recover` gate. See [ADR-0060](../../docs/decisions/0060-retained-shared-world-continuation.md).

After the original joint loop actually returns Cancelled, this mode retains the same child,
Habitat world, observations, effects and total simulator-step budget. Only a complete set of
new attempts for the cancelled slots, with the same accepted-plan session, exact intent and
endpoint, can continue. Completed peers keep their original outcomes and select only the
original wait skill through the existing model-free passive policy. No retry is generated by
the adapter; resource ownership and dispatch remain external Control/Runtime responsibilities.
Normal single-Actor next-Task execution remains supported; a stopped Task must resume before
any later Task. Original Stage2 policies and tool guards handle the fresh assignments.

At most 16 continuations are admitted across the **whole world**, even across serial Tasks;
the opted-in profile accepts at most 32 slots and keeps at most 48 segment records. The fixed
`--pair-wait-s` stop window is not renewed by a partial assignment. Failed/ended/exhausted
worlds cannot continue. A fsynced `retained-world-owner.json` is created before initialization;
reuse of its run directory or loss of the child cannot trigger a replacement reset. Use a new
directory only for a genuinely new run.

`diagnostics-stop-N.json` and `shared-world-segment-N.json` are stop/segment evidence, not a
final benchmark result. Pauses flush diagnostics without closing probes or video. The final
`shared-world-summary.json` retains segment history and reports only actual official metrics;
optional segment/state storage failures are explicit `continuation_archival` gaps and never
rewrite local outcomes. No terminal verifier verdict is published for a resumable pause.
`stage2-assignment-segment-N.json` identifies active and retained/passive agents directly.

This implementation has deterministic policy-loop, Pipe and loopback HTTP coverage, actual
Controller/Node process conformance, and a real single-Actor retained-world continuation check.
Multi-Actor physical continuation remains unproven. The separate Controller Group
recovery API is implemented in [ADR-0061](../../docs/decisions/0061-confirmed-stop-group-continuation.md).
It requires the exact complete original set, explicit repeat permissions, actual terminal facts
and current Control revalidation of retained resources. It prepares complete new attempts before
application checkpoint Commit and delivery; it does not enable isolated Role recovery or automatically retry
a stall. Deployment metadata and `--retain-stopped-session` must both be enabled consistently.

Actual Controller and `roboguide-node` processes can be checked without a simulator or Provider:

```bash
cargo build -p integration-server -p roboguide-node
uv run python tools/quality/check_group_continuation_processes.py \
  --output results/group-continuation-process-check
```

The output directory must be new. This is synthetic process conformance, using generic authored
tasks and explicitly controlled local outcomes; it is not B1 evidence or physical validation.
It checks that Cancel acknowledgement and partial stopping do not admit new attempts or release
resources, same-owner continuation preserves intent/session, Completed peers are not repeated,
and actual Node completion permits normal Mission/resource closure. Only its own processes and
ephemeral listeners are stopped on exit. Process logs, journal databases and checkpoints remain
available on failures as well as success.

Run the bridge from the independently managed EMOS checkout and Habitat Conda environment:

```bash
cd "$ROBOGUIDE_EMOS_ROOT"
CUDA_VISIBLE_DEVICES=1 \
PYTHONPATH="$ROBOGUIDE_ROOT/integrations/habitat-local-eaios" \
conda run --no-capture-output -n habitat python -m habitat_local_eaios \
  --state-db /tmp/roboguide-habitat-c1-s0/habitat-bridge.sqlite3 \
  --habitat-config \
    habitat-baselines/habitat_baselines/config/multi_rearrange/llm_spot_fetch_mobility.yaml \
  --episode-id 51
```

The `emos-crabagent` backend does not carry a copied RoboGuide decision loop. It injects the
Control-committed assignment at the output boundary of EMOS Stage1, then calls the original EMOS
`MultiLLMPolicy`, `LLMHighLevelPolicy`, `CrabAgent`, `HierarchicalPolicy`, and configured skill
implementations. Wait, peer requests, skill entry/termination, replanning, and skill step budgets
remain the EMOS Stage2 implementation. Selected model actions pass the local execution contract
below before they can reach a skill. The direct-Oracle backend
remains a separate native protocol path.

The optional `--goal-region-navigation` mode is an explicit deployment-owned
Local How variant for `shared-emos-stage2`. For exact
entities in direct conjunctive official `any_at` goals, it retains the model's
selected `nav_to_obj` entity and the original Oracle control loop, but may
choose a different physical navigation point inside the official 3D tolerance
when the original point is not reachable or leaves too little margin for the
original Oracle's stopping radius. It first tests the official entity X/Z on
the agent's own navmesh height, then uses bounded vertex and triangle-interior
search. Selection evidence v0.5 records the candidate policy and geometry
limits (100,000 vertices, 200,000 triangles, two seconds of Python work),
alongside the unchanged 256-path-query ceiling. Native array export allocation
and time are not hard process bounds. Every accepted interior point still
requires an actual agent-specific path and the unchanged stopping envelope.
It uses deterministic geometry and path queries, without simulator stepping or
random sampling. Unsupported action types fail at initialization; a qualifying
goal with no proven route fails locally rather than using the original
straight-line fallback. `evidence/local-how-profile.json` records the selected
mode, and `evidence/goal-region-navigation-selections.json` records the original
and selected points at terminal. The official PDDL metric alone decides goal
success; a static route or local skill completion does not. This mode changes
RoboGuide's Local How relative to native EMOS and must be disclosed in paired
comparison. It is off by default, including in B1 unless
`ROBOGUIDE_B1_GOAL_REGION_NAVIGATION=1` is explicitly set.

The separate `--reset-route-support` option is also off by default and requires
that Local How mode plus the shared-world spatial profile. It writes
`evidence/reset-route-support.json` once after the existing reset. An isolated
agent PathFinder uses copied settings; neither the live action cache nor the
simulator's active mesh is changed. Only deterministic original/projected-center
candidates are tested, without the random safe-snap fallback or vertex scans.
At most 128 records and 256 path queries are allowed. `supported` is a static
witness; `not_found` covers only those initial candidates, and `unavailable`
retains observation faults. The runtime's fuller bounded search can still find a
route that this probe missed. Total probe elapsed time and source/native-library
digests are recorded; vendor navmesh build cost is not a hard time/memory cap.

For B1, explicitly set both `ROBOGUIDE_B1_GOAL_REGION_NAVIGATION=1` and
`ROBOGUIDE_B1_RESET_ROUTE_SUPPORT=1`. The launcher checks the diagnostic archive
before Controller startup. A missing/invalid archive is a harness failure;
honest unavailable/miss records do not change Formal admission or Node
candidates. These records currently do not enter MI or Control decisions.
They must not become permanent Actor exclusions across later navigation Tasks.
The Local How profile is v0.2; per-action selection records are v0.2 and add
actual progress on bounded runtime misses. See
[ADR-0052](../../docs/decisions/0052-reset-route-support-observations.md).

The independently enabled `--reset-route-geometry` flag requires the above
observer. It emits reset-route-support v0.2 with complete static starting-component
triangle analysis; without it, v0.1 and the original point-only observation are
unchanged. B1 uses `ROBOGUIDE_B1_RESET_ROUTE_GEOMETRY=1` and requires that exact
version rather than silently falling back. Local How v0.3 declares the observer;
the navigation resolver and original controller remain unchanged.

The retained geometry limits are 25,000 vertices / 50,000 triangles per endpoint,
200,000 total triangle checks, and a checked one-second analysis deadline per
record. Native mesh export/recomputation cost is outside those Python limits.
`intersects` is not a path; `disjoint` concerns only the static component and
assumed reference geometry; incomplete or boundary cases remain `unknown`.
No path query, RNG sample or physical step is added. When the separate initial
preference consumer is enabled, neutral v0.2 support grades only order current
eligible candidates; even static-disjoint endpoints remain valid fallbacks.

`--step-aware-navmesh` is a separate default-off **execution** profile requiring
`--goal-region-navigation` (B1: `ROBOGUIDE_B1_STEP_AWARE_NAVMESH=1`). It refines only
the copied vertical cell height to `min(original, declared_climb / 2)` for positive
climb; zero climb retains the original. Robot radius/height/climb/slope and the
existing collision buffer stay unchanged. Resolution below 5 mm or refinement
above 32 times fails explicitly. The action and observer build independent meshes
from identical copied settings, leaving global settings untouched. Original
Oracle control, exact destination and official PDDL retain their owners.

Local How v0.4 declares `step-preserving-cell-height/v0.1`; action selection v0.3
records actual active settings. Existing reset-route versions bind this profile
through their Local How digest and source fingerprint. Native construction cost
is outside Python observation bounds. This can change actual routes and motion:
disclose it as an arm difference from original EMOS, and never infer completion
from mesh connectivity. See [ADR-0063](../../docs/decisions/0063-step-aware-local-navmesh-resolution.md).
First Bind, expiry and restore fences remain unchanged. See
[ADR-0062](../../docs/decisions/0062-scoped-static-navigation-region-evidence.md).

`--spatial-navigation-arrival` is a separate default-off differential-base control
profile requiring step-aware and goal-region navigation. B1 uses
`ROBOGUIDE_B1_SPATIAL_NAVIGATION_ARRIVAL=1`. Until full 3D proximity to the selected
point, follow the next planar route waypoint; only then face the original entity
and set the existing finished flag. Thresholds, velocities and skill/simulator
budgets remain unchanged. An unavailable/unusable bounded route fails without a
straight-line fallback. Each invocation dispatches exactly one original base action.

This changes Local How control; the original/goal-region/step-aware classes retain
their existing behavior when this flag is off. External EMOS and official PDDL are
untouched. Local How v0.6 and selection v0.4 disclose the profile; B1 verifies exact
sources and continues accepting older archives. Optional diagnostics expose a bounded
pre-base-action decision, not physical arrival or benchmark truth. See
[ADR-0064](../../docs/decisions/0064-spatial-route-arrival-local-navigation.md).

The enabled spatial controller now prepares every selected navigation command
before the original joint Gym step, using the vendor's exact vector decoder and
action order. Commands retain their episode, entity index and starting pose;
dispatch consumes the prepared command once without repeating target/path queries.
An expected bounded miss prevents that step entirely and records
`navigation-preparation-failure-<step>.json` with canonical attempt attribution,
original cause, search counters and a content digest. Already Completed peers are
preserved. A failure before the first physical step leaves the official benchmark
outcome unavailable; a reset predicate reading is diagnostic, not a final outcome.

Preparation initializes the existing target/mesh caches and can run the original
target helper; it is not RNG-neutral observation. Moving these calls ahead of
motion is a disclosed Local How difference (`joint-navigation-preparation/v0.1`).
It does not promise rollback for arbitrary Gym/physics faults or infer global
unreachability. When the spatial flag is off, the legacy step path is unchanged.
Historical profiles remain accepted. See
[ADR-0065](../../docs/decisions/0065-joint-navigation-preparation.md).

`--goal-aware-navigation-arrival` is another default-off Local How profile,
requiring all three navigation flags above. B1 uses
`ROBOGUIDE_B1_GOAL_AWARE_NAVIGATION_ARRIVAL=1`. The resolver admits routed points
inside the exact goal region without requiring the full legacy stop envelope.
The controller then requires the actual PDDL reference position to be inside the
same local margin before its original selected-point/heading checks can finish.
When that position is still outside, it continues the actual route using original
velocities and budgets. Missing geometry, changed goals or unusable routes fail
before motion; the model-selected entity remains exact.

Local How v0.7 and action selection v0.6 explicitly disclose this arm difference.
The reset probe remains conservative under its declared
`conservative-stop-envelope/v0.1` policy; a bounded probe miss is not an exclusion
or an impossibility claim. Diagnostics record actual reference/center/distance and
local bound before the base action. These are not official verdicts or evidence
of subsequent residence. B1 verifies new profile/source identity and retains
historical schemas. See [ADR-0068](../../docs/decisions/0068-live-reference-goal-region-arrival.md).

The backend reports local skill completion, Habitat PDDL benchmark success, episode termination,
and RoboGuide execution state as separate evidence. `COMPLETED` retains an explicit
`terminal_basis`: either the Oracle navigation skill reached its own terminal measure, or Habitat
reported semantic task success and ended the assigned operation first. Mission completion never
fabricates either local fact, and local skill completion never implies benchmark success.

The deployment-selected episode is resolved uniquely from the configured Habitat dataset before
the Gym environment constructs Habitat-Sim. This keeps the simulator's initial scene and the first
seed-controlled reset bound to the same frozen episode. Pinning an episode only after simulator
construction is invalid: Habitat may already have initialized a different scene, and equal
`habitat.seed` values would then not produce a comparable initial state.

The bridge currently supports one active simulator execution and one deployment-selected episode
and robot. That is an intentional C1 scope bound, not a canonical operation constraint. Node Service
performs bounded status reacquisition for transient observation failures without redispatching the
physical attempt.

## Shared-world deployment topology

The `shared-emos-stage2` profile has a stricter episode-start contract than the single-agent
backend. One process owns one official Habitat episode and exactly two configured agent endpoints.
It resets once before MI freezes environment evidence. Two independent Actors require
Control-committed assignments on distinct endpoints before joint execution. One independent
Actor may run successive Control-dispatched Tasks on its retained endpoint in that same reset
world. Other topologies fail closed. These are deployment limits, not claims that a joint goal
semantically requires distinct Physical Entities.

The bounded `--pair-wait-s` barrier prevents a half-populated two-Actor execution from running. Expiry
fails the arrived local execution and writes `evidence/shared-world-start-admission.json` with the
fixed topology, arrived assignment, and rejection reason. The coordinator also appends every
admission decision to `shared-world-start-admission.jsonl`, so a later decision cannot silently
overwrite an earlier rejection. Before reset it verifies that both assignments belong to the same
Mission and execution Group, target distinct logical Task/Role slots, and map to distinct configured
agent endpoints. A mismatched pair is rejected before Stage2 starts. A successful pair writes the
same artifact with `state: ADMITTED` before execution. Evidence write failures are logged and never
change the local execution result. Generic Control remains free to run a true one-Actor DAG
sequentially on one resource after satisfaction releases it; such a plan must use a deployment that
implements sequential endpoint reuse.

After the single shared-world reset, the adapter writes
`evidence/authoritative-planning-world-evidence.json`. Its v0.2 schema binds the loaded episode
and dataset identity to static object/goal region and floor facts, and may add conservative
single-location witnesses for direct official geometric terminal conjunctions. A witness is a
reset snapshot, not a route or success result. Missing exact geometry remains an explicit gap.
The builder performs no additional reset or simulator step, and MI receives no agent start pose or
Node selection authority; the separate Control preassignment evidence retains that information.

## Stage2 execution contract guard

For the `emos-crabagent` deployment profile, the bridge derives a temporary
`Stage2ExecutionContract` from the committed canonical invocation. The guard
observes the raw `(action_name, arguments)` returned by the original EMOS
`OpenAIModel` before `CrabAgent` maps it to a Habitat skill or sends an outgoing peer
request. A navigation assignment may use the deployment's navigation action,
normal wait, or a well-formed peer request, but its `nav_to_obj.target_obj`
must equal the committed `parameters.destination`; an unassigned sibling may
only wait or communicate. Peer messages do not transfer authority: the receiving
agent retains its own invocation-bound destination. Pick, place, reset-arm and
unknown tools are outside this implemented navigation profile. A future local
integration that legitimately requires another action must provide an explicit
operation profile; broad robot capability alone does not authorize it.

The `natural-objective` Stage2 assignment preserves the canonical objective and states the exact
committed `parameters.destination` as the target for this execution's `nav_to_obj`. A joint
Mission objective can mention other entities without making them alternate destinations for this
one invocation. The assigned endpoint's original EMOS Stage2 model, tool implementations, skills
and selected action remain untouched. The adapter changes assignment text and one execution-scoped
tool argument
schema as described below. The guard still
rejects a wrong target instead of silently correcting it.

For each assigned execution, the adapter also narrows only the original `nav_to_obj.target_obj`
Provider schema to an enum containing the committed destination. Other tool names, descriptions,
and schemas stay as supplied by EMOS. The original tool declaration is restored after the call,
including Provider exceptions, and the raw returned target still goes through the independent
Contract Guard. An incompatible vendor navigation schema fails closed before any model call.
This is a B1 Local EAIOS contract affordance and must be recorded when comparing against an EMOS
arm with an unconstrained tool schema.

The guard installs only on the execution's agent instances and looks up each
model after its normal lazy initialization. It preserves the original Prompt,
model response, planning calls, accepted action mapping and skill
implementation. A rejected action returns `local-contract-failure`, without a
retry or substitute action. The shared episode stops before the next Gym step;
unfinished sibling work reports `sibling-local-contract-failure`, and already
observed local completions remain intact. Existing official Habitat metrics are
read unchanged. Guard rejection is not an assertion about PDDL truth.

In a one-Actor shared-world segment, the unassigned endpoint has no task-level
model authority. The adapter temporarily supplies a model-free idle agent to
EMOS' original `MultiLLMPolicy`/`HierarchicalPolicy`, which selects the existing
`WaitSkillPolicy`; it restores the original `CrabAgent` instance on every exit.
Admission requires the exact agent assignment and original wait skill mapping.
The assigned endpoint keeps its original model and canonical action guard; a
wrong assigned destination still fails before `gym_env.step()`. Paired
two-Actor execution still uses both original model agents. Per-Task
`idle-endpoint-<invocation-digest-prefix>.json` records this deployment policy,
idle agent names, local wait selections, and zero Provider calls. These local
wait decisions are separate from model-selected `stage2-actions.jsonl` records.
This is a RoboGuide arm strategy difference from native EMOS, where an idle
agent may ask its model to select `wait`; comparative evidence must retain it.

`evidence/stage2-actions.jsonl` contains `roboguide.stage2-action/v0.1` decisions
for every **selected** execution tool returned by EMOS: raw action, immutable
invocation digest/destination, agent, local time and completed simulator step.
The guard also verifies that the current raw Provider response contains exactly
one tool call; zero or multiple calls fail closed before CrabAgent dispatch.
EMOS still selects the first tool when a response contains several; ignored tool
proposals remain in the original chat history. `allowed` means admitted at the
tool boundary, not physically executed or successful: another agent can reject
the joint step. EMOS's existing synthetic tool-history `Success` receipt also
does not prove physical success.

Records stream at model-call frequency, one bounded record at a time (64 KiB
maximum), rather than at simulator-step frequency. The existing episode budget
bounds the synchronous actor loop and therefore the number of selected actions.
`stage2-action-audit.json` records written/dropped/unavailable counts and explicit
completeness; serialization and I/O failure never bypass enforcement or replace
the local failure cause. The schema marks unavailable raw evidence instead of
claiming a truncated action is complete. This profile is an admission boundary,
not a model correctness or navigation-convergence guarantee. It adds no global
RoboGuide authority, MissionPlan fields or new Habitat success rules.

When `ROBOGUIDE_B1_PHYSICAL_DIAGNOSTICS=1`, `diagnostics-steps.jsonl` and
`diagnostics-terminal.json` use `roboguide.e1.physical-diagnostics/v0.6`. The
initial and terminal snapshots include `goal_entity_positions`, read through
Habitat's authoritative PDDL entity mapping at the corresponding world state.
Each target is recorded independently; an unreadable target is marked
`unavailable` and never replaced with a guessed pose. These positions are
diagnostic evidence only and do not alter PDDL evaluation or execution.
Agent records also distinguish the navigation `position` (articulated base
ground point) from `pddl_reference_position` (the articulated base transform
origin used by Habitat's `any_at` predicate). This avoids comparing a goal
entity against the wrong robot point when the embodiment has a base offset.
Both snapshots read the active PDDL `robot_at_thresh`; the terminal snapshot
also records episode and scene identity for the optional run-local geometry
comparison. Missing fields remain unavailable rather than changing execution.
The v0.5 observer also records a floor from the loaded Habitat semantic region's
read-only `contains` method, when it uniquely contains the observed point,
for physical agent bases and PDDL target entities. A probe around the existing
Oracle target selection and pathfinder call records the actual selected navigation
point and `find_path` result once per original call. It does not issue a new path
query or change the selected point, movement, or official metric. Ambiguous floors,
unsupported action layouts and steps without an Oracle call remain unavailable.
The v0.6 observer additionally taps the original step_filter and update_base
methods. agents[agent_id].local_motion retains copied pre-call requested
positions, the exact filter-returned position before caller mutation, and actual
base positions before/after the original update. Each original method and
PathFinder query still executes once, with unchanged arguments, return identity
and exceptions. It does not add collision queries or infer that an unchanged
base position proves collision or arrival. Differences between requested and
returned endpoints are explicitly derived comparisons.

The shared policy loop binds these records to the supplied canonical
Mission/Task/Group/Role/attempt and destination. A new serial Task or continuation
attempt clears previous-segment motion attribution. Native or legacy calls without
that identity remain unavailable. Actual action and active-NavMesh capability
scalars are read once per segment; they describe the deployment navigation model,
not certified hardware limits. See [local navigation evidence guidance](../../docs/development/local-navigation-evidence.md).

At most eight original method-call records per agent and post-step interval are
retained. Extra calls still execute; dropped counts and call_records_complete
expose evidence limits. No per-call serialization or disk write is added; the
existing bounded 32-record JSONL batch, step limit and 64 KiB document cap remain.
Observer overhead and loss appear in collection_stats.motion_observation.
Stop/terminal snapshots retain both the last post-step sample and pending calls
since that sample. A failed Gym step is not assigned an invented simulator step;
pending motion remains available even when final world reads fail, storage permitting.
Final cleanup restores the original instance overrides.

The optional B1 geometry sidecar accepts v0.4, v0.5 and v0.6 terminal snapshots.
Motion observations have no benchmark or admission authority.

The shared-world deployment also accepts `roboguide.execution-session/v0.1`
metadata derived from an accepted MissionPlan. Two independent Actors retain
the distinct-endpoint start barrier. One independent Actor can run successive
Control-dispatched Tasks on the same endpoint without resetting Habitat between
them. The adapter verifies Group, Task/Role slot, session digest, prerequisites,
and retained endpoint; it does not release resources or decide Task readiness.
Action traces and selected-tool audit counts retain continuous episode step
and sequence identities across those Task segments. The final official
`pddl_success` remains separate from each local Task outcome. Unsupported
topologies and a missing follow-on assignment produce explicit failure or
INCOMPLETE admission evidence. A Node route must advertise this session schema
in registration metadata; the Controller rejects a non-advertising route before
dispatch rather than silently losing the topology. Deploy Controller and Node
updates together.
