# E2 protocol: long-horizon heterogeneous collaboration

Status: frozen for controlled development slices E2-S0 and E2-S1. Autonomous
planning and formal comparison remain pending.

## Question and claims

E2 tests whether RoboGuide can preserve a long-horizon heterogeneous task across
planning, matching, scheduling, commitment, node dispatch, local execution,
feedback, and final goal verification.

The experiment separates three claims:

1. `E2-S0 physical`: one validated Dog–Drone–Arm plan traverses the production
   RoboGuide control and Node Protocol path and establishes the physical goal.
2. `E2-S1 graph controlled`: one public COHERENT task is split into tasks assigned
   to distinct embodiment nodes with dependency-gated handoffs.
3. `E2-Full` (pending): natural language is converted to a MissionPlan without a
   hand-authored plan and compared with PEFA under the same task and goal rules.

S0 and S1 are controlled execution results. They must not be reported as autonomous
planning or as evidence that RoboGuide outperforms PEFA.

## Frozen task identity

Each run records repository commit, dirty-worktree patch, runtime versions, exact
MissionPlan, task identity, and result checksums. A run with different initial graph,
scene, task index, or goal is a different trial.

| Slice | Backend | Frozen task | Robots | Plan length |
| --- | --- | --- | --- | --- |
| E2-S0 | OmniGibson / Isaac Sim physical | `Merom_1_int_Task1` | `aliengo_0`, `quadrotor_0`, `franka_0` | 9 text actions / 21 physical skills |
| E2-S1 | Original COHERENT graph environment | public `env4/task17` | robot arm 23, robot dog 24, quadrotor 25 | 13 graph actions (paper GT) |

Development tasks and repetitions are labeled separately from frozen test tasks.

## Observation boundary

At graph level, each robot may consume only the observation produced by the original
COHERENT graph rules: the current room, visible nodes and relations, carried or held
objects, reachable objects, connected rooms, and embodiment-specific state. Hidden
objects in closed containers are not visible. RoboGuide receives execution lifecycle
facts and expected-effect evidence; it does not receive private local controller
state as global truth.

At physical level, observations and safety remain owned by COHERENT, OmniGibson,
ROS, and the existing robot controllers. The adapter may retain task identity,
canonical intent, local handle, lifecycle state, result paths, and final goal
evidence. It must not fabricate perception or physical completion.

## Action boundary

Graph operations are semantic phase operations. In E2-S1 they expand to these frozen
original COHERENT actions:

| Phase | Node | Original graph actions | Cumulative steps |
| --- | --- | --- | --- |
| `arm-load-milk` | `coherent-arm-e2-s1` | grab milk; put milk into basket | 2 |
| `drone-lower-basket` | `coherent-drone-e2-s1` | take off; move to coffee table; land | 5 |
| `dog-load-book` | `coherent-dog-e2-s1` | approach book; grab; approach basket; put into basket | 9 |
| `drone-deliver` | `coherent-drone-e2-s1` | take off; enter garden; approach swing table; land | 13 |

Every action is checked against embodiment-specific preconditions before calling the
original `Get_env_info.step`. Phase order is durable and fail-closed.

Physical E2-S0 maps only `coherent.execute-plan@v1` to the startup-approved existing
runner. Paths, poses, low-level motion, grasp control, flight control, and immediate
safety remain Local How. Unsupported cancellation returns an explicit rejection.

## Completion semantics

The following facts are never interchangeable:

- command accepted: a durable local handle exists;
- action or skill finished: the local transition returned terminal success;
- task satisfied: the phase expected effect is present;
- mission successful: every task is satisfied and the final benchmark goal passes.

E2-S1 succeeds only when all four Controller tasks are Completed and Satisfied, the
attempt history assigns them to the intended three nodes, the shared graph commits
four phases and exactly 13 steps, and these original goal relations exist:

- `INSIDE(book 34, basket 29)`;
- `INSIDE(bottle of milk 35, basket 29)`;
- `ON(quadrotor 25, swing table 16)`.

E2-S0 succeeds only when all 9 text actions and all 21 required physical skills
finish, all three physical agents execute, the runner exits successfully, and the
independent final pose/container goal checker passes without manual correction.

## Budgets

| Budget | Controlled graph | Controlled physical |
| --- | --- | --- |
| Action steps | exactly 13 | fixed 9 text actions; measured physical skills |
| Task deadline | 60 s per graph phase | 1,200 s Mission deadline |
| Local execution timeout | synchronous bounded graph phase | 900 s runner plus 180 s process margin |
| Retries | transport/workflow idempotency only | transport/workflow idempotency only |
| Model calls | 0 | 0 |
| Manual correction | forbidden | forbidden |

E2-Full will separately freeze model name, model version, temperature, top-p, token
budget, maximum calls, repair budget, and wall-clock timeout before any test run.

## Evidence contract

Every accepted result keeps:

- raw MissionPlan and rendered node configuration;
- Controller Mission, events, inventory, and execution-attempt history;
- adapter SQLite state and exact canonical invocations;
- local action/skill records and phase evidence;
- final graph or physical state and goal decision;
- repository provenance, dirty-worktree status, checksums, and automatic verdict.

Historical failures are retained. A verifier fix creates a new run; it does not edit
an old verdict. Development results are not promoted to unseen test results.

## Comparison boundary

The graph-level main comparison is PEFA versus RoboGuide-Full on the same public task
index, initial graph, observation rules, action rules, goal checker, step limit, and
model budget. The controlled comparison uses the same correct plan to compare a
reference executor with RoboGuide-Controlled and measures orchestration correctness
and overhead. Physical validation shares the same low-level controllers and reports
success, completion time, and failure cause separately from graph step metrics.

Primary metrics are Success Rate, Action Steps, Steps/GT, model calls, tokens, wall
time, and RoboGuide control latency. Results are also stratified by plan length and
cross-robot handoff count.
