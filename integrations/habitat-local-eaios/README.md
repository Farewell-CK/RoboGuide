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
- `POST /v1/executions`
- `POST /v1/executions/status`
- `POST /v1/executions/cancel`

Execute durably returns one idempotent `habitat-*` local handle before dispatch. A dedicated
long-lived child process owns the loaded simulator so Habitat stepping cannot starve the loopback
HTTP facade. Status reports `ACCEPTED`, then `RUNNING`, then only a backend-observed
`COMPLETED`, `FAILED`, or `CANCELLED`. Cancel acknowledgement only persists a request; it does not
change the local state to `CANCELLED`. The simulator process reports that terminal state only after
it has actually stopped stepping the local execution.

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
the agent's own navmesh height, then uses bounded vertex search. It uses
bounded deterministic vertex and path queries, without simulator stepping or
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
retains observation faults. The runtime's full vertex search can still find a
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
`diagnostics-terminal.json` use `roboguide.e1.physical-diagnostics/v0.5`. The
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
The optional B1 geometry sidecar accepts both v0.4 and v0.5 terminal snapshots.

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
