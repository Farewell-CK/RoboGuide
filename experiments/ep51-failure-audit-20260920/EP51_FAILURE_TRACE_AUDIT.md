# Episode51 / seed40 Failure Trace Audit

审计日期：2026-09-20。模式：只读离线分析；未调用模型，未启动 Habitat、MI、Stage2 或 Controller，未修改生产代码、协议或原始证据。SQLite 查询使用临时副本及只读连接。

运行基线：`edc773c9be19e951792c471c47d7c132b25178a6`。开始审计时工作树在 `codex/b1-event-archival@3724408e865a88a879f2644b8f0ab90fd4c5c20c`，本地 main/origin/main 已是 `937858f929d6e280c5c053ad9013d2a159f101c0`。本审计没有切换或更新分支。已逐字节核对本文使用的 `shared_world.py`、`emos_stage2.py`、`backend.py` 与运行基线相同。EMOS checkout HEAD 为 `e9501db45d634b087bf5d1a14228266685e8feeb`；其本地 model wrapper 有既有修改，不能声称整个外部环境具有冻结的完整代码身份。关键“立即写入 Success”行为也存在于该 SHA 的原版源码。

## Evidence Index

以下缩写均指原始文件；后文每项引用给出 sequence、step、JSON 字段或物理行号。行号按 LF 换行计数；不将进度条的 CR 当作换行。`SOURCE_INTEGRITY.json` 保存本次读取的重要文件的字节长度与摘要，便于核对未被覆盖。

| 标记 | 原始位置 |
| --- | --- |
| B | `/data/workspace/code/roboguide-stage2-provider-_hyz84nd/b1-preflight-episode51-seed40` |
| S | [B/evidence/shared-world-summary.json](/data/workspace/code/roboguide-stage2-provider-_hyz84nd/b1-preflight-episode51-seed40/evidence/shared-world-summary.json) |
| T | [B/evidence/action_trace.jsonl](/data/workspace/code/roboguide-stage2-provider-_hyz84nd/b1-preflight-episode51-seed40/evidence/action_trace.jsonl) |
| L | [B/shared-bridge.log](/data/workspace/code/roboguide-stage2-provider-_hyz84nd/b1-preflight-episode51-seed40/shared-bridge.log) |
| C | [完整 Controller 事件](/data/workspace/code/roboguide-stage2-provider-_hyz84nd/control-events-complete.json) |
| R | [B/b1-request-record.json](/data/workspace/code/roboguide-stage2-provider-_hyz84nd/b1-preflight-episode51-seed40/b1-request-record.json) |
| O | [B/b1-request-observations.json](/data/workspace/code/roboguide-stage2-provider-_hyz84nd/b1-preflight-episode51-seed40/b1-request-observations.json) |
| H0/H1 | B/evidence/chat-history/51/agent_0_action_history.json、agent_1_action_history.json |
| E | `/data/workspace/code/RoboGuide/evaluation/e1/paired-smoke/e1-habitat-mas-mobility/20260916T142636Z-52b4e640a10a` |
| ES/EE | [E/stdout.log](/data/workspace/code/RoboGuide/evaluation/e1/paired-smoke/e1-habitat-mas-mobility/20260916T142636Z-52b4e640a10a/stdout.log)、[E/stderr.log](/data/workspace/code/RoboGuide/evaluation/e1/paired-smoke/e1-habitat-mas-mobility/20260916T142636Z-52b4e640a10a/stderr.log) |
| EH | `/data/workspace/code/emos-baseline/chat_history_output/2026-09-16/llm_spot_fetch_mobility/FULL/51` |
| SW | [shared_world.py](/data/workspace/code/RoboGuide/integrations/habitat-local-eaios/habitat_local_eaios/shared_world.py)（运行基线源码） |
| EMOS | `/data/workspace/code/emos-baseline` |

## 1. Executive Summary

**A. node-b 的直接失败原因已确认。** shared-world adapter 在 simulator step 3000 收到 episode `done`，当时官方 `pddl_success=False`，且 agent_1 没有先前的本地导航完成 outcome，于是产生 `FAILED / episode-terminated-before-success`。原始原因是：`Habitat episode terminated before the assigned shared navigation completed`。同一原因贯穿 bridge 数据库、Node durable journal 和 Controller sequence 390；之后 sequence 391–394 推进任务/Group 失败及资源释放，`mission.json.status=Failed`。[S：`outcomes.1`；C：390–394；SW：207–270；B/bridge-b.sqlite3：`executions.detail`；B/node-state-b/execution-journal.sqlite3：`executions.reason`]

**B. 尚不能把这次失败归因于 RoboGuide 的任务组织。** 已验证 MI 的两个目标实体正确、没有任务依赖、没有 distinct-executor 约束，`space:1` 成功进入分配，Stage2 实际选择了匹配的两个导航目标。但 agent_1 没有完成导航，其更底层原因缺少连续位姿、路径、碰撞与完成传感器轨迹；历史 EMOS 对照的初始机器人状态也不同。[R：`plan`；C：137、143；L：4172、4209、4214、4251；T；B/evidence/scene_description.txt：7–8；ES：67–68]

**C. 与已找到的 EMOS 成功运行相比，最早可观察差异是执行前的初始场景状态，早于任务组织。** EMOS 的 Spot 位于 `bedroom_2 / 1 floor`，Fetch 位于 `living room_21 / 3 floor`；本次 RoboGuide 的 Spot 位于 `living room_21 / 3 floor`，Fetch 位于 `kitchen_23 / 3 floor`。之后组织输出也不同：EMOS Leader 把两个位置交给 Spot、Fetch “Nothing to do”；本次 MI 分为两个独立任务，Control 分配 Spot→object、Fetch→receptacle。[ES：60–94；B/evidence/scene_description.txt：7–8；R：`plan.tasks`；C：137、143]

两个 B1 endpoint 的**完成结果最早在 step 90 分化**：agent_0 捕获本地导航完成，agent_1 尚未完成；技能轨迹在 step 92 首次成为 `[wait, nav_to_obj]`。agent_1 在 step 1001 转为 wait，直到 step 3000 都没有再次导航。这个事实支持继续调查导航预算/完成反馈链，但不等于已经证明“卡墙”“下楼失败”或模型误判的唯一物理原因。[S：`outcomes.0.simulator_steps=90`；T：90–92、1000–1001、3000]

## 2. Official Goal Analysis

权威语义文件 `B/evidence/authoritative-semantic-evidence.json` 的 `goal` 是以下合取，`objective_scope=joint_terminal_state`：

```text
any_at(any_targets|0) AND any_at(TARGET_any_targets|0)
```

其身份为 episode 51、scene `data/scene_datasets/mp3d/pRbA3pwrgk9/pRbA3pwrgk9.glb`、dataset revision `mobility_episodes_1`，dataset SHA-256 `5d2c6aa6608d5611c73d8f6c688e17613a9898afa5f0f668e66db068598191ca`。两个谓词不要求两个不同 Physical Entity，也不绑定 Spot/Fetch。[上述文件：`identity`、`world_context`、`goal`；EMOS/habitat-lab/habitat/config/benchmark/multi_agent/pddl/pddl_spot_fetch_mobility.yaml：13–27]

| 官方目标 | 本次 B1 终态判定 | 已保存证据的限制 |
| --- | --- | --- |
| `any_at(any_targets\|0)` | **不可用 / 尚不能确定** | agent_0 的本地 Completed 不是官方逐谓词结果 |
| `any_at(TARGET_any_targets\|0)` | **不可用 / 尚不能确定** | agent_1 的 Failed 或聊天“已到达”都不是官方逐谓词结果 |
| 两者合取 | **False** | S：顶层 `official_pddl_success=false`，`identity.episode_terminated=true`、`simulator_steps=3000` |

T 的 3000 条记录只有 `simulator_step`、`skills`、`benchmark_task_achieved`，后者全为 False；没有逐目标值、位姿或朝向。SW：967–996 只从最终 `official_metrics` 中导出严格 bool 的 `pddl_success`，没有保存完整逐目标 metrics；两个 `outcomes.*.benchmark_task_achieved` 也读取联合值，不能解释为各自目标的 False。[SW：395–418、967–996；emos_stage2.py：554–563]

官方 `PddlSuccess.update_metric` 检查当前 goal 表达式；`PddlStageGoals` 则累计“episode 中任意时刻曾成立”。因此即使有 stage-goal success，也应区分历史达到与最终成立。本次没有可绑定到此 run 的逐目标日志，不能用 EMOS 全局 `episode_log/.../mobility_episodes_1_subgoals.json` 中历史 episode51 值填补。[EMOS/habitat-lab/habitat/tasks/rearrange/multi_task/pddl_sensors.py：209–222、225–255；该全局 episode log 的 steps 文件对应 130 steps]

## 3. Node Execution Trace

| 项目 | node-a / agent_0 / Spot | node-b / agent_1 / Fetch |
| --- | --- | --- |
| Task / Role | reach-object / object-move-role | reach-goal / goal-move-role |
| 目标 | any_targets\|0 | TARGET_any_targets\|0 |
| endpoint handle | habitat-4243438218e1ad84262071bb | habitat-b648f26ea30b63fa71d14865 |
| reset 后初始位置 | [-0.16678085923194885, 2.3214714527130127, 3.768552780151367] | [4.056283950805664, 2.8338050842285156, -1.6121662855148315] |
| outcome 中的 final_position | [-0.4552406668663025, 2.9140219688415527, -1.4644842147827148] | [1.3818612098693848, 2.833804130554199, -3.0569705963134766] |
| 该 position 的采样时点 | **step 90 的本地完成时点** | step 3000 的 episode 终止时点 |
| episode 最终实际位置 | **未单独记录**，不能把 step90 snapshot 当 step3000 pose | 以上 step3000 position |
| 初始/最终朝向 | 均未记录 | 均未记录 |
| 本地导航完成 | step90，oracle-nav-skill | 从未捕获本地完成；local_skill_completed=False |
| 最终 endpoint 状态 | COMPLETED | FAILED / episode-terminated-before-success |

位置来源：S：`identity.initial_agent_positions`、`outcomes.0/1.initial_position/final_position/simulator_steps`。`_pair_outcome()` 在 outcome 创建时读取位置；已经有 outcome 的 agent 会被后续循环跳过。因此 node-a 数值是冻结的早期完成位置，不是缺失的终局位姿的替代品。[SW：207–234、395–418]

**node-b 执行链：**

1. L：4214 记录实际 AgentArguments；L：4251、H1 `[2][1].tool_calls[0]` 记录 `nav_to_obj(target_obj=TARGET_any_targets|0, robot=agent_1)`。目标实体存在于权威 entity_catalog。
2. L：1739 首次观察 RUNNING 为本地日志时间 `2026-09-20 01:08:29.193`；外部观察记录 `runtime-observations.jsonl` 第6行在 Unix `1789837709.4096737` 再次观察 RUNNING。这是状态观测上界，不是精确的 actor.act 开始时间。
3. T：1–1000 始终为 agent_1 的 `nav_to_obj`；1001–3000 始终为 `wait`。未保存每步 action vector 或位姿，不能确定最后一次实际位移发生在哪步。
4. H1 的动作顺序为 `nav_to_obj` → `wait` → `send_request` → `wait` → `wait`（分别在外层索引 2、3、4、5、6）。消息内容为 “I navigated to TARGET_any_targets|0 and am positioned at the semantic goal receptacle.”。CrabAgent 将 send_request 本身映射为 `wait(['500'])`，所以 skill trace 中不会出现 send_request。[crab_agent.py：109–127；L：43635–43675]
5. S：`local_llm_calls=6`、`local_replans=4`、`invalid_outputs=0`、`send_request_count=1`。这里 replans 由聊天轮数推导，**不是四次重新导航**；只记录到一次导航选择。invalid_outputs 的实现统计也不是所有无效物理动作的检测器。[SW：420–459；H1]
6. 最后一次记录到的导航技能在 step1000，最后记录的技能为 step3000 wait；最后一个模型工具选择也是 wait。node-b 的具体“最后有效运动动作”和停滞起点不可用；初末位置不同只证明期间发生过位移。
7. S、bridge-b 数据库 `executions` 行及 Node journal `executions` 行一致保留终态失败原因；`cancel_requested/cancellation_requested=0`。C sequence390 的 NodeObservation.TaskFailed 保留相同原始文本，391 是 TaskExecutionFailed。

### 导航 timeout 与“Success”文本的证据强度

**已确认的源码行为：** 原始 `nav_to_obj` 配置 `max_skill_steps=1000`、`force_end_on_timeout=False`、`apply_postconds=False`。`SkillPolicy.should_terminate()` 会把 `over_max_len` OR 到 `is_skill_done`，使高层再次规划，而不要求完成传感器为真。高层下一轮通用提示写着 “You have completed your previous action.”。模型 wrapper 在拿到工具调用时就附加 `content=Success`，发生在物理 skill 执行之前。[EMOS/habitat-baselines/habitat_baselines/config/habitat_baselines/rl/policy/hierarchical_policy/defined_skills/oracle_skills_multi_agent.yaml：71–80；rl/hrl/skills/skill.py：181–205；rl/hrl/hl/llm_policy.py：98–123；EMOS/habitat-mas/habitat_mas/utils/models.py：222–250，原版 e9501db 的同文件196–213也有此行为]

**强代码支持的推断，但没有独立 runtime flag：** agent_1 在连续1000步导航后、未捕获本地完成的情况下切换到 wait，与技能预算终止路径高度一致。后续“到达”聊天与上述通用反馈机制一致；但未保存 `over_max_len`、原始 finished sensor、`hl_wants_skill_term` 逐步值，不能把具体选择分支或模型内部信念冒充直接观测。

**直接 FAILED 路径是另一层事实：** SW：235–270 的 `if done` 且 `pddl_success=False`、agent 尚无 outcome，才生成本次 Failed。SW：300–314 的 `step-budget-exhausted` 是另一条分支，本次没有走它。Habitat 配置 `max_episode_steps=3000`，`Env._past_limit/_update_step_stats` 会在步数到限时置 episode_over；这解释3000步终止，但原始轨迹没有单独保存 env termination reason，不能排除同一步同时存在别的结束条件。[EMOS/habitat-lab/habitat/config/benchmark/multi_agent/config_spot_fetch_mobility.yaml：100–104；habitat/core/env.py：232–240、285–288]

## 4. Stage2 Input Analysis

最终实际提交的是 R 的 revision1 production MI 计划：review `[0].approved=True`，`reviewed_at_ms=1789837690936`，draft digest 与 O 的 `submission_evidence.submitted_plan_digest` 相等；POST 时间 `1789837691071`，Controller HTTP 202。没有使用手工/B2计划。[R：`review_history[0]`、`plan`；O：`submission_evidence`]

两个任务均 `depends_on=[]`，同属 independent Context；`relations=[]`、`executor_constraints=[]`，各自的 `requirements.resources=[{kind:space, units:1}]`，`resource_scope=task`。没有意外先后依赖，也没有把两个谓词转换为物理实体互异约束。[R：`plan.contexts[0]`、`plan.tasks[*]`]

真实 Stage2 输入原文如下，来自 L 的4172、4214行；与 `B/evidence/subtask-agent-0.txt`、`subtask-agent-1.txt` 及 bridge invocation 对得上：

```python
AgentArguments(robot_id='agent_0', robot_type='SpotRobot',
    task_description='Reach the semantic object identified as any_targets|0.',
    subtask_description='Reach the semantic object identified as any_targets|0.',
    chat_history=[])

AgentArguments(robot_id='agent_1', robot_type='FetchRobot',
    task_description='Reach the semantic goal receptacle position identified as TARGET_any_targets|0.',
    subtask_description='Reach the semantic goal receptacle position identified as TARGET_any_targets|0.',
    chat_history=[])
```

Control assignments 的137/143及 commits139/145分别为 node-a/slot-a、node-b/slot-b。两个 assignment 到达后才启动一个联合 simulator，step1 两者都在 nav_to_obj；它们并发执行，不是等待另一个 Task Completed 后才进入 Stage2。[C；B/evidence/assignment-arrival.jsonl：1–2；T：1；SW：80–116、177–196]

`space:1` 用于资源选择/占用；bridge 收到相应 `resource_ids`，但 `_pair_arguments()` 只取 robot resume 与 invocation 的 objective/subtask，未将 space、slot或Node调度信息写入 AgentArguments。本次两目标在实际工具调用中仍精确匹配，未见资源字段改变局部语义的证据。[B/bridge-a.sqlite3、bridge-b.sqlite3：`executions.invocation_json`；SW：336–376；L：4209、4251]

可观察的组织信息差别是：R 的 Mission 总体目标包含联合终态，但本次每个 AgentArguments 的 task_description 只含本 agent 的子任务，chat_history为空。EMOS 的两个 AgentArguments 都携带完整联合 goal 的 task_description，且继承 Stage1 对话。这是实际输入差异，尚不是失败因果证明；当前实体名、目标覆盖、首个动作选择均正确。[R；L：4172/4214；ES：101/146]

## 5. Failure Timeline

Unix 时间转换为 Asia/Shanghai 仅便于阅读。Controller timestamp、adapter wall-clock、simulator step 属于不同来源；没有进行跨进程时钟校准。sequence 给出 Controller 内部顺序；无 wall-clock 的 step/对话不会被强行插入精确时间。

| 时间或 step | 可证实事件 | 原始证据 |
| --- | --- | --- |
| 01:02:57.335 | MI Request 创建 | R：created_at_ms |
| ≤01:08:10.936 | revision1 最终计划已经生成并获 Reviewer批准；精确生成时刻未记录 | R：review_history[0] |
| 01:08:11.071 | MI POST提交；HTTP202 | O：submission_evidence |
| Controller 01:08:11.074 | 创建Group、注册2 Task、Ready、两次资源选择与Commit/Bind | C：131–147；137/143为具体分配 |
| adapter 01:08:11.498 / .548 | a、b 两个 assignment 到达 | assignment-arrival.jsonl：1/2 |
| Controller 01:08:11.650 / .710 | reach-object、reach-goal 激活 | C：148/149；不是已完成 reset 或实际移动的证明 |
| adapter 01:08:27.160590 | reset 返回后记录 episode_started_unix；记录两个初始position | S：identity；SW：80–114 |
| 01:08:29.034 / .193 | 首次轮询看到 a/b RUNNING | L：1733/1739 |
| step1 | 两 agent 均选择正确目标的 nav_to_obj | T：1；L：4209/4251；不能由缓冲 stdout 精确推算选择时间 |
| step90 | agent_0 的本地完成 outcome 被捕获，agent_1 尚未完成 | S：outcomes.0；此时 Node 还未收到最终投影 |
| step92 | 首次技能组合分化为 wait/nav_to_obj | T：91–92 |
| step1000→1001 | agent_1 从 nav_to_obj 转 wait，后续未再导航；预算终止推断的最早明确轨迹依据 | T：1000/1001；未记录更早物理停滞时刻 |
| 导航之后，无法精确映射step | Fetch选择wait、发送“已到达”消息、再次wait | H1：[3]–[6]；send_request映射wait |
| step3000 | joint done，官方False；agent_1产生Failed | S；T：3000；SW：235–270 |
| adapter 01:18:02.808 / .937 | coordinator将 a COMPLETED、b FAILED分别持久化 | B/bridge-a.sqlite3、bridge-b.sqlite3：executions.updated_at_unix_ms |
| 01:18:02.939 / 01:18:03.123 | endpoint轮询读到Completed/Failed | L：43615/43617 |
| Controller 01:18:03.013 | a TaskCompleted、TaskSatisfied(execution-report)、释放slot-a | C：386–389 |
| Controller 01:18:03.155 | b TaskFailed，TaskExecutionFailed，Group失败并释放slot-b | C：390–394；mission.json最终为Failed |
| episode结束后的结果归档 | S保存official_pddl_success=False | SW：963–996；没有独立summary写入时间；不能宣称它早于Node终态 |

step90 的内部 local completion 与十分钟后才可见的 endpoint Completed 不矛盾：`run_pair()` 完整返回后 coordinator 才循环 `mark_terminal()`，没有在step90即时发布node-a终态。[SW：962–966] node-a在本地outcome之后仍参与原始联合policy循环，后续聊天记录保留wait和发消息；所以 summary 的node-a调用/Token计数只冻结到step90，不能当整个episode总计。[SW：207–234、395–459；H0；B/evidence/chat-history/51/token_usage.json：agent_0=16374，S：outcomes.0.local_tokens=2812]

## 6. EMOS Comparison

### 找到并核对的真实成功运行

Run `20260916T142636Z-52b4e640a10a`，system=emos，manifest命令显式 `habitat.seed=40`、单episode；ES：56、197确认实际episode51及130 steps；EE：69官方 `Average episode pddl_success:1.0000`。没有把 selector `seed-pinned-sample` 当作执行episode。manifest 的resolved scene与dataset digest同本次B1相符。[E/manifest.json：command、episode_selection.resolution、provenance；E/metrics.json：emos_episode_batch_size=1；ES/EE]

EE：63、68还分别保存receptacle/object stage-goal success=1；这些是“曾达到”指标。此run最终PDDL合取为True，才证明官方联合终态满足；不据此推断是哪个agent分别满足或是否存在distinct-entity关系。[PddlSuccess/PddlStageGoals源码；EE]

### Stage1、AgentArguments 与 Stage2轨迹

实际Leader输出（ES：81–94，EH/leader_group_chat_history.json：`[1][1].content`）：

```text
agent_0: Navigate to and be at the locations of objects 'TARGET_any_targets|0' and 'any_targets|0', traversing between floors as necessary.
agent_1: Nothing to do
```

两 AgentArguments 的完整 `task_description` 相同（ES：101/146）：

```text
The task requires multiple robots to collaboratively execute a sequence of actions to accomplish specific goals. Each robot may be responsible for interacting with multiple objects or, in some cases, none, depending on its capability.
The goals are defined by the following |goal conditions| :

Goal of this episode is the logical operation and of the following conditions:
0. Any robot is currently at the location of 'any_targets|0'.
1. Any robot is currently at the location of 'TARGET_any_targets|0'.
```

`robot_id/robot_type` 分别为agent_0/SpotRobot、agent_1/FetchRobot；subtask_description是上述Leader分配；两者chat_history均继承各自Stage1任务/scene输入和 `{{yes}}` 回复，而非空。完整参数repr含对话见ES：101/146；EH相应action_history的索引1保留该轮。没有把共享日期目录的存在单独当作run绑定：Leader文本、Stage2调用参数与此run持久stdout相符。

| 阶段 | EMOS成功运行 | RoboGuide本次失败运行 |
| --- | --- | --- |
| 初始scene描述（最早差异） | Spot bedroom_2/1楼；Fetch living room_21/3楼 | Spot living room_21/3楼；Fetch kitchen_23/3楼 |
| 组织输出 | Spot两个目标；Fetch无任务 | 两独立任务，Spot object、Fetch receptacle |
| 首个动作 | Spot nav_to_obj(any_targets\|0)；Fetch wait | Spot nav_to_obj(any_targets\|0)；Fetch nav_to_obj(TARGET_any_targets\|0) |
| 后续局部动作 | Spot改为nav_to_obj(TARGET_any_targets\|0)；Fetch只见wait | Spot改wait；Fetch1000步后改wait，并发“到达”消息 |
| 终态 | 130 steps，官方True | 3000 steps，官方False；a本地完成/b失败 |
| 各robot终局数字位姿/朝向 | 未找到绑定本run的结构化记录 | 见§3；a终局位姿仍缺失，两者朝向缺失 |

EMOS动作依据：ES：141、185、192；EH/agent_0_action_history.json：`[3][1].tool_calls`、`[4][1].tool_calls`；agent_1：`[3][1].tool_calls`。未找到对应3000行那样的EMOS逐step动作/位姿轨迹；不能给两个导航转换编造simulator step。stderr记录video生成路径，但本次查找没有找到该run的视频文件，不能从文件名反推位姿。130步成功也不能推出Fetch的本地导航Completed，它没有选择导航。

### Provider与公平性边界

EMOS manifest环境覆盖记录 `EMOS_LLM_MODEL=gpt-5.6-luna` 和 `OPENAI_BASE_URL=http://101.43.45.215:8080/v1`；ES：134/138/179/182/189的真实HTTP日志确认Chat Completions endpoint成功。该run归档的 `raw-evidence/token_usage_details-51.jsonl` **混有上午另一轮**：第1–8行是03:26–03:27 UTC，第9–16行才落在本run14:26:36–14:28:13 UTC窗口。第12–16行为当前Stage2调用，model字段均gpt-5.6-luna。不能把全部16行都算作本次模型调用。

本次B1的 `runtime-observations.jsonl` 第1行直接观察实际spawn child PID1938657的model/base URL；`stage2-proxy-routing.json` 给出logical endpoint；provider-diagnosis报告记录原始客户端的实际probe成功与配置来源。已保存真实Stage2对话可证其正常调用，**该probe不是每次B1调用的wire记录**；网络文件仅TCP观测，不能冒充完整HTTP/服务端model回执。两侧请求配置指向同一model名/endpoint，但没有冻结服务端模型版本与随机采样响应；manifest的reasoning_effort不等于原始Stage2客户端实际发送了此参数。[原始models.py：222–230；上述文件；E/manifest.json.llm.reported_model=null]

**本历史对照不满足完整配对公平性。** 不仅缺少EMOS精确reset pose/quaternion，已有scene文本已经显示初始状态不同；相同episode/seed不消除此差异。缺少两次运行的完整环境/随机状态/外部工作树digest及服务端模型版本快照。任务组织、全局任务上下文、Stage1历史、起始位置、启动路径和模型随机性相互混杂。可以报告描述性差异，不能报告“RoboGuide组织导致失败”或“EMOS组织更优”的因果结论。

## 7. Root-Cause Assessment

| 分类 | 结论 | 证据与限制 |
| --- | --- | --- |
| 已确认：终态传播 | episode终止且官方False、Fetch没有已捕获完成outcome → adapter Failed → Node Failed → Mission Failed | S.outcomes.1；C390–394；SW235–270；两个SQLite表 |
| 已确认：局部轨迹 | Fetch只有一次导航选择，连续至1000步，随后2000步均wait | T1–3000；H1 |
| 已确认：反馈文本不具到达权威 | wrapper立即写Success；后续高层提示称上一动作完成 | models.py222–250；llm_policy.py98–123；H1[2]–[6] |
| 强推断，未独立采集分支flag | 导航技能在1000步预算到限后返还高层；误导性完成提示可能促使模型转wait | max_skill_steps与轨迹吻合，但缺少over_max_len/finished sensor/原始action向量 |
| 待验证 | Fetch物理路径、碰撞、navmesh目标、跨层通行或完成阈值造成未完成导航 | 缺少每步pose/path/collision；不能直接宣称Fetch不能下楼或错误目标 |
| 待验证 | adapter错过完成脉冲或其完成判据与原始技能读数不同 | 已有False outcome不等于已保存原始finished sensor；需同step两处采样比对 |
| 待验证 | 子任务文字/缺失完整联合goal上下文影响后续重规划 | 实际输入不同，但首个目标正确；没有受控对照，不能归因为MI遗漏任务 |
| 已确认：对照混杂 | 初始场景状态不同，早于组织输出 | B scene_description7–8 vs ES67–68 |

尚缺：B1两谓词最终独立真假、完整3D pose/quaternion轨迹、step1000转换的终止flag、导航目标/最短路/碰撞/速度命令、EMOS初末数值状态、EMOS每step轨迹、模型响应版本与采样状态。未发现本run Provider401、目标entity不存在、资源锁互阻、取消或MI目标遗漏的直接证据；这不等于证明不存在所有未记录的物理/环境问题。

本报告不改变原始Formal admission或benchmark outcome。原始`events.json`仅100条导致的provenance问题，与本报告解释的官方False分开；本轮只使用同次完整C中的396条权威事件，不覆盖或重建历史归档。

## 8. Next Diagnostic Experiment（仅建议，本轮未执行）

建议预先固定 **一次 Episode51/seed40 的单臂诊断复现**，只增加只读观测，不改导航预算、原始策略、资源或目标。为隔离本次Fetch路径，应在明确标记的诊断环境中使用本次实际生产生成的两个AgentArguments作为保存输入；这属于历史输入回放，**不作为新的production-MI Formal B1或性能样本**，不生成手工计划。若下一轮要求完整production-MI入口，则仅当新MI输出与本次任务/assignment一致时用于本原因复现，否则归为不同轨迹。

预先确定记录：reset后的两个base_pos/base_rotation、对象/目标实际transform、实际seed与RNG状态；每步实际action向量、pose、goal逐谓词/联合官方真值；Fetch导航实体解析后的目标点、投影navmesh点、路径存在性/距离、碰撞与速度；`skill_done`、finished sensor、`cur_skill_step`、`over_max_len`、`hl_wants_skill_term`及Gym done/terminated/truncated来源。重点保留0、89–93、999–1002及终态，但连续pose/技能标志不可只采样这些点。

停止条件事先固定：原始episode done或3000步，先发生者；不追加“跑到成功”的重试。reset数值位置不匹配本次记录则标记未复现原初态；历史朝向/RNG状态缺失，不能声称严格重放。失败如未复现也保留为真实诊断结果，不换seed或补造响应。

这一次观测可区分：①位置/路径停滞而nav完成传感器始终False；②已到官方目标但本地完成判据未触发；③原始传感器有完成脉冲而adapter遗漏；④确为1000步技能timeout后通过通用完成反馈进入wait。若要检验任务组织的因果效果，之后仍需另行授权、固定相同完整初态与配置的配对设计；本次历史比较不能替代它。

---

交付：本文件、`EP51_FAILURE_SUMMARY.json`（事实/假设分开）及`SOURCE_INTEGRITY.json`。未创建commit或push，未修改正在验收的事件归档分支代码。
