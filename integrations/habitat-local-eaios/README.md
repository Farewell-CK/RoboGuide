# Habitat Local EAIOS Bridge

This deployment-owned bridge is the C1-S0 reference path from the generic
`roboguide-node` Local Integration Engine into an existing EMOS/Habitat environment. It is not a
RoboGuide Core module and does not add Habitat, EMOS, Gym, Torch, or simulator dependencies to the
RoboGuide Python environment.

The bridge supports exactly the existing canonical operation `mobility.navigate@v1`. It accepts the
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
implementations. Consequently invalid output, wait, peer requests, skill entry/termination,
replanning, and skill step budgets remain the EMOS Stage2 implementation. The direct-Oracle backend
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
