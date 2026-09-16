# E1-I Unified Paired Smoke — Pre-Pilot Closure

The first paired run driven entirely by the unified Eval Harness
(`roboguide-eval run --spec evaluation/specs/e1/mobility-smoke.yaml`):
same frozen workload, two independently launched and collected arms.

## Result

```
UNIFIED PAIRED SMOKE = PASS   (pipeline)
EMOS       pddl_success = true   (130 sim steps, 97 s wall)
RoboGuide  pddl_success = false  (3000 sim steps budget, 336 s wall)
   failure_category = LOCAL_AGENT_REASONING/model
   (agent_1 replan churn exhausted the official episode budget before its
    goal predicate; infrastructure_failure=false; correctly recorded —
    pipeline PASS is independent of benchmark success by design)
```

Machine summary: `pair-summary.json`. Raw fresh evidence:
`e1-habitat-mas-mobility/20260916T141956Z-*` (RoboGuide) and
`.../20260916T142636Z-*` (EMOS) — controller DB/events, node journals,
bridge stores, shared-world summary, EMOS stdout/token evidence, harness
manifests/metrics/trace.

## Identity gates (all pass)

dataset digest equal (`5d2c6aa…`) · dataset file equal (mobility_episodes_1) ·
episode id equal (51) · scene equal (mp3d/pRbA3pwrgk9) · seed equal (40) ·
goal predicate set equal (`any_at(any_targets|0)`, `any_at(TARGET_any_targets|0)`) ·
robot/agent set equal (Spot+Fetch) · max-step policy recorded (3000) · local model
recorded (`EMOS_LLM_MODEL` relay) · local policy stack recorded (original EMOS
Stage2 both sides) · benchmark authority = `habitat.pddl_success` · metrics
schema = `roboguide-eval.metrics/v0.3`.

Assignment wording differs by design (different global organization: EMOS
Stage1 Leader discussion vs RoboGuide committed MissionPlan subtasks); both
full texts are retained as evidence.

## Timing boundaries (recorded, no performance conclusions)

Both manifests carry `started_at`/`ended_at`; RoboGuide additionally records
per-assignment arrival timestamps and the shared-world
`episode_execution_start` (unix) — Mission/Control overhead is included in
the RoboGuide wall time, never hidden. EMOS organization time completes
inside the first policy act (chat history retained).

## Harness notes

`roboguide-eval run` selected both arms from one spec; the RoboGuide arm ran
the E1-I shared-world production scenario (`POST /v1/missions`, two Nodes,
one Habitat world, original EMOS Stage2). The runner supports the
`roboguide.e1-shared-world-verdict/v0.1` schema alongside the C1-S0B
controlled verdict; benchmark success remains the official shared
`pddl_success`, never Mission state.

## E1-I PRE-PILOT = READY

Formal episode selection / repetition policy is deliberately out of scope of
this closure and belongs to the experiment owners.
