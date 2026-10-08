# Generic COHERENT rolling-horizon experiment

This experiment replaces the task17-only phase macros with one generic boundary:

```text
current official partial observations + exact available actions
  -> RoboGuide Mission Intelligence (Planner / Reviewer / Repairer)
  -> one unedited MissionPlan
  -> Controller Matching / Commit / Bind
  -> one agent-specific roboguide-node
  -> coherent.agent-<id>-primitive@v1
  -> original COHERENT Get_env_info.step
  -> official task_goal check
```

The operation catalog and Nodes are generated from the agents in the selected official task.
The adapter contains no task-specific route or correct action sequence. It accepts an action only
when the exact string occurs in that agent's current official action list. Cancellation of an
already-committed synchronous graph primitive is explicitly unsupported; Mission cancellation is
owned by the Controller. Each planning decision, raw provider request/response, reviewed MissionPlan,
Controller event, primitive transition, final graph, and official goal verdict is retained.

Run one task from the repository root with the API credential already present in the environment:

```bash
export PYTHONPATH=integrations/coherent-local-eaios
export ROBOGUIDE_ALLOW_INSECURE_LLM_HTTP=1
uv run python tools/e2-generic/run_task.py \
  --repo . --coherent-root ../COHERENT --env env4 --task 5 \
  --config tools/e2-generic/mission-gpt-6-sol.toml --output /absolute/evidence/path
```

`sample-10-seed-20260929.json` freezes the deterministic ten-task sample. `run_sample.py` runs it
sequentially because every task owns the same loopback Controller and bridge ports. A failed task is
preserved and the batch continues; it is never repaired by editing the model's action or plan.

## Multi-task planning mode

`run_dag.py` submits an unedited multi-task MissionPlan to the same Controller and Nodes. Each
Task still executes one primitive through the existing generic adapter. The default `serial`
dependency profile preserves COHERENT's one-shared-graph step semantics and exercises multi-step
planning and cross-robot handoffs; it does not claim parallel scheduling performance. The separate
`partial-order` profile admits acyclic branches only when every ready action is currently available
and no ready tasks share a robot. It is reserved for controlled scheduler studies and must not be
mixed into the PEFA step-count comparison.

Planner and Reviewer run once per plan, with the existing bounded Repairer when needed. A completed
exploration segment may request a new plan using fresh observations. A failed Mission may request a
new plan only after both Controller Execution Attempts and local execution records for that Mission
are terminal; the next Planner, Reviewer, and Repairer receive their compact structured outcomes.
Ambiguous execution still ends the run.
The runner does not implement Core recovery or silently revise an accepted Mission. The first action
must be available at plan admission;
subsequent actions are checked by the unchanged adapter immediately before execution. No future
state is simulated by admission, and the planner receives only shared official partial observations.

After each primitive, the runner evaluates the official `task_goal`. If it becomes true before the
Mission ends, the runner requests the Controller's formal Mission cancellation. A second guard in
the adapter rechecks the same goal immediately before every primitive, so a cancellation race cannot
execute an overshoot action or increment the benchmark step counter. The cancellation request,
terminal Mission, guarded local outcome, final graph, and goal verdict remain separate evidence.

The default `fair` prompt profile contains only task-independent capability/action guidance already
present in the public PEFA prompts. It deliberately omits simulator-internal relation mutations.
`--prompt-profile informed` adds those implementation transitions for development diagnostics only;
an informed run is a different condition and cannot be used as the main fair comparison.

Defaults: at most three planning segments and `2*GT+1` total primitives. Every submitted plan must
fit the remaining primitive budget. The dedicated Sol configuration retains `xhigh` and review,
but raises the output-token ceiling to 24576 to allow multi-task JSON. This ceiling is an explicit
experimental difference from the single-step configuration; all raw calls remain recorded.

```bash
PYTHONPATH=integrations/coherent-local-eaios:mission/src \
python tools/e2-generic/run_dag.py \
  --repo . --coherent-root ../COHERENT --binary-root target/debug \
  --config tools/e2-generic/mission-dag-gpt-6-sol.toml \
  --env env4 --task 5 --prompt-profile fair --dag-profile serial \
  --output /absolute/new/dag-evidence
```

The default ports (25170, 28170, 28191, 28220) differ from the single-step experiment; `--port-offset`
can select another range. Use an isolated checkout while older batches are running. Binaries are
reused and hashed, provider requests/responses and submitted plans are retained, and checksums are
computed after child processes stop. Results distinguish Controller completion from official goal
success. Core, Mission Intelligence source and built-in system prompts, and contracts are unchanged.
The experiment objective is different: it asks for multiple tasks and supplies task-independent
action semantics. These instructions are not a fixed benchmark solution. Along with the larger
output ceiling and segment budget, this is a new experimental condition, not an identical-prompt
timing trial.

The initial development run on `env4/task5` generated five tasks and failed after three applied
primitives: the model requested `movetowards` to the floor it was already above. The adapter rejected
the action and the Controller reported failure. The run was not repaired or overwritten. Generic
room-entry/ABOVE semantics from COHERENT `get_env_info.py` were then documented in the objective;
follow-up runs start from the official initial state in separate directories. Development retries
must not be counted as independent held-out benchmark successes. Those historical runs predate the
profile field and are therefore classified as `informed`, not as `fair` comparison evidence.

On 2026-09-29 the follow-up development run `dev-env4-task5-20260929-b` passed both the official
goal check and Controller completion: four primitives, one planning segment, two provider calls,
250.73 seconds wall time (243.38 seconds inside recorded model calls). The prior single-step smoke
on the same task used four primitives, eight provider calls, and 688.97 seconds. These are single
development observations under different objective/configuration conditions, not a speedup claim
for all tasks. The failed first development attempt took 359.34 seconds and remains separate evidence.
