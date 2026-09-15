# C1-S0 Habitat Local EAIOS Happy Path

This scenario sends one deterministic MissionPlan v0.7 through the production Controller HTTP API,
Control Match/Schedule/Proposal/Commit/Bind path, Node Protocol v0.4, real `roboguide-node`, and the
deployment-owned Habitat Local EAIOS bridge.

The scenario uses existing canonical operation `mobility.navigate@v1`, Habitat mobility dataset
episode `51`, EMOS agent `0` (Spot), and semantic destination `any_targets|0`. The destination value
is scenario evidence interpreted by Local EAIOS; no Habitat action, PDDL entity index, path, pose, or
Oracle-skill field enters a RoboGuide canonical schema.

Prerequisites:

- RoboGuide `integration-server` and `roboguide-node` debug binaries are built;
- the EMOS checkout is available at `$ROBOGUIDE_EMOS_ROOT` (default sibling checkout
  `../emos-baseline` relative to RoboGuide);
- its Conda environment is `$ROBOGUIDE_HABITAT_CONDA_ENV` (default `habitat`);
- `$ROBOGUIDE_HABITAT_CUDA_DEVICE` identifies the available physical GPU (default `1`).

Run:

```bash
bash scenarios/habitat-local-eaios-c1-s0/run-happy-path.sh
```

All generated databases and evidence are written to `/tmp/roboguide-habitat-c1-s0`. The verifier
requires HTTP 202, exact intent preservation, one dispatch authorization, at least one Node workflow
observation of real `RUNNING`, a moved simulated base position, local `COMPLETED`,
`TaskExecutionCompleted`, execution-report `TaskSatisfied`, Mission `Completed`, and live Controller
and Node processes without recovery evidence.

The bounded cancellation sanity uses a separate State directory and the same production path:

```bash
bash scenarios/habitat-local-eaios-c1-s0/run-cancel-sanity.sh
```

It requires the bridge to accept cancellation while the durable local state is still `RUNNING`,
then waits for the simulator process to stop and report terminal `CANCELLED` before RoboGuide marks
the Mission `Cancelled`.
