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
when the exact string occurs in that agent's current official action list. Cancellation is reported
as unsupported. Each planning decision, raw provider request/response, reviewed MissionPlan,
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
