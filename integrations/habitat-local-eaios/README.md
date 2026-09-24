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
It starts the single reset only after two Control-committed assignments arrive through distinct
endpoints. It does not support running one endpoint and later reusing that endpoint for a second
assignment inside the same official episode. This is a deployment topology limit, not a statement
that every two-goal Mission semantically requires distinct Physical Entities.

The bounded `--pair-wait-s` barrier prevents a half-populated joint episode from running. Expiry
fails the arrived local execution and writes `evidence/shared-world-start-admission.json` with the
fixed topology, arrived assignment, and rejection reason. The coordinator also appends every
admission decision to `shared-world-start-admission.jsonl`, so a later decision cannot silently
overwrite an earlier rejection. Before reset it verifies that both assignments belong to the same
Mission and execution Group, target distinct logical Task/Role slots, and map to distinct configured
agent endpoints. A mismatched pair is rejected before Habitat starts. A successful pair writes the
same artifact with `state: ADMITTED` before reset. Evidence write failures are logged and never
change the local execution result. Generic Control remains free to run a true one-Actor DAG
sequentially on one resource after satisfaction releases it; such a plan must use a deployment that
implements sequential endpoint reuse.

Before the single shared-world reset, the adapter also writes
`evidence/authoritative-planning-world-evidence.json` when static scene metadata is available.
This versioned artifact binds the loaded episode and dataset identity to object/goal region, floor,
and explicitly supplied same/different-floor relations when the raw episode exposes exact mappings.
It reads no PDDL `sim_info`,
object manager, or simulator target index before their reset-time binding. Missing exact object
instance handles and ambiguous regions remain explicit gaps. It never calls reset, samples agent
poses, advances the simulator, or selects a Node. Start poses therefore remain an explicit
`agent_start_state_pending_reset` gap until execution evidence is recorded after reset.

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

The guard installs only on the execution's agent instances and looks up each
model after its normal lazy initialization. It preserves the original Prompt,
tool schema, model response, planning calls, accepted action mapping and skill
implementation. A rejected action returns `local-contract-failure`, without a
retry or substitute action. The shared episode stops before the next Gym step;
unfinished sibling work reports `sibling-local-contract-failure`, and already
observed local completions remain intact. Existing official Habitat metrics are
read unchanged. Guard rejection is not an assertion about PDDL truth.

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
