# Episode51 完整链路诊断预检报告

日期：2026-09-20  
性质：代码、配置与历史证据预检；本轮未调用模型 Provider，未创建 Mission Request，未启动
Control、Node 或 Habitat，也未改变任何历史运行目录。

## 1. 结论

**正式 `origin/main@28a3dcda5eb44d48df83700d13b4e0b1b601ea97` 的链路、冻结 workload、单请求生命周期、资源调度、
证据归档和 MI Prompt 均可追溯，但该 commit 不应直接用于开启物理诊断。** 原因是启用物理诊断时，
原实现每个 simulator step 都同步序列化并打开/写入文件；旧 `action_trace.jsonl` 的写失败还会传播到
shared-world 主循环。这既可能扰动 20 ms step 周期，也可能把证据存储失败误归因成物理执行失败。

独立分支 `codex/ep51-full-chain-readiness` 的代码提交
`db114d610fe89076ba4c3166752df3b01d85421f` 已修复这一阻塞：两条逐步证据流均以 32 条为固定批次，
序列化/写入失败只计入 evidence loss，terminal/exception 边界强制 flush，并记录采样、丢样、写失败和
观测耗时。诊断 schema 明确升级为 `roboguide.e1.physical-diagnostics/v0.2`。该提交没有改变
`actor.act()`、`gym_env.step()`、Stage2 动作、Control 决策、官方目标或 PDDL 判定。

因此本轮判定为：

- **main 原 SHA：NOT READY for enabled physical diagnostics**；
- **诊断修复提交 `db114d6`：CONDITIONAL READY**，下一次执行前还必须通过第 11 节的部署指纹、端口、
  GPU 和凭据存在性检查；
- 没有发现需要改变 MI、Control、Stage2 或 benchmark 语义的执行 blocker；
- 新诊断能直接验证此前最强的“1000-step skill budget / finished sensor / high-level feedback”假设，
  但不能直接观测每机器人场景碰撞接触、PathFinder 路径长度、完整 RNG 内部状态或原生持久化的
  skill-exit event。对此只能保留 `UNKNOWN`，不得根据停滞轨迹补造原因。

## 2. 基线、工作区与运行环境

| 项目 | 只读核对结果 |
|---|---|
| `origin/main` | `28a3dcda5eb44d48df83700d13b4e0b1b601ea97`，与任务预期一致 |
| 原用户工作区 | `/data/workspace/code/RoboGuide`，`codex/b1-event-archival@ad02d562...`，含未跟踪实验目录；未切换、清理或覆盖 |
| 本次独立 worktree | `/tmp/roboguide-ep51-full-chain-readiness` |
| 本次分支 | `codex/ep51-full-chain-readiness` |
| 诊断代码提交 | `db114d610fe89076ba4c3166752df3b01d85421f` |
| 外部 EMOS checkout | `/data/workspace/code/emos-baseline@e9501db45d634b087bf5d1a14228266685e8feeb` |
| EMOS tracked diff SHA256 | `172268dd4cf44e8e50ba1d51de2f9f7a4ff5377b32d7aa96bfa21f1b77c0ee78` |
| EMOS `models.py` SHA256 | `a63f6af9e98731d80ae578be59d98dc317b7614de36fc8f8de5ab538e1d22b74` |
| 预定 GPU | GPU 1，检查时 24,063 MiB free；GPU 0 正被其他任务使用 |
| 预定端口 | 8070、25060、28060、28090、28100、28102；检查时均空闲 |

EMOS checkout 不是干净 upstream：4 个 tracked 文件有部署/实验改动，另有未跟踪本地配置和历史日志。
已核对的关键差异包括 `EMOS_LLM_MODEL` 环境选择以及 Stage2 token/latency 记录。下一次运行必须以
完整 diff 指纹和关键文件摘要锁定这份部署，不得只核对 Git HEAD。`emos.env` 存在，但本轮没有读取或
输出任何值；当前 shell 中 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`EMOS_LLM_MODEL` 均未导出。

服务器上有一个 2026-09-08 启动的 ZCode `integration-server`（PID 2410339），使用
15051/18080/18090；它不占用本次端口。本轮未停止或修改该进程。

## 3. 冻结 Episode51 workload

来源：
`/tmp/roboguide-ep51-full-chain-readiness/scenarios/e1-shared-world-episode-51/b1-input.json`。

| 字段 | 冻结值 |
|---|---|
| input schema | `roboguide.e1.b1-input/v0.1` |
| input SHA256 | `1026761241f906ca2a35ab567082ab3d281d76524d5c8ab38a2c8522bbaf87c6` |
| episode | `51` |
| seed | `40` |
| scene | `data/scene_datasets/mp3d/pRbA3pwrgk9/pRbA3pwrgk9.glb` |
| dataset revision | `mobility_episodes_1` |
| dataset SHA256 | `5d2c6aa6608d5611c73d8f6c688e17613a9898afa5f0f668e66db068598191ca` |
| goal | `and(any_at(any_targets|0), any_at(TARGET_any_targets|0))` |

实际 dataset 文件
`/data/workspace/code/emos-baseline/data/datasets/mp3d/mobility_episodes_1.json.gz` 的摘要与冻结值完全一致。
Runner 从 frozen input 严格提取 episode、seed 和 dataset identity，并以 `habitat.seed=40` 覆盖配置。

Habitat 配置仍设置 `randomize_agent_start=1`、`min_distance_start_agents=5.0`、双 agent 和 3,000 个
episode steps。初始位姿由 reset 期间的实际 RNG 流和环境状态决定；**相同 seed 不等于已经证明两次
实际起点相同**。后续只能用 reset 后立即读取的 position/rotation 判断本次初态，不得以 seed 或历史
位姿代替。

## 4. 历史运行事实与 UNKNOWN

主要历史目录：

- 真实物理运行：
  `/data/workspace/code/roboguide-stage2-provider-_hyz84nd/b1-preflight-episode51-seed40`
- 补全 Controller 事件：
  `/data/workspace/code/roboguide-stage2-provider-_hyz84nd/complete-evidence/b1-preflight-episode51-seed40/events.json`
- 失败轨迹审计：
  `/data/workspace/code/RoboGuide/experiments/ep51-failure-audit-20260920/EP51_FAILURE_TRACE_AUDIT.md`
- 第二次 MI 提前失败：
  `/data/workspace/code/roboguide-ep51-diag2-20260920/b1-diag-ep51-seed40`
- Planner A/B：
  `/data/workspace/code/roboguide-ep51-mi-prompt-ab-20260920T124627Z/EP51_MI_PROMPT_AB_REPORT.md`
- Reviewer-only：
  `/data/workspace/code/roboguide-ep51-reviewer-only-20260920T144003Z/EP51_REVIEWER_ONLY_VALIDATION_REPORT.md`

历史物理运行可确认：

1. reset 后 agent0/Spot position 为
   `[-0.1667808592, 2.3214714527, 3.7685527802]`；agent1/Fetch position 为
   `[4.0562839508, 2.8338050842, -1.6121662855]`。该运行没有记录 rotation。
2. 生产 MI 提交 independent Context；两 Task 均 `depends_on=[]`、`relations=[]`，各自携带
   `space:1`，没有 distinct-physical-entity 约束。
3. Controller sequence 132/133 注册两 Task；137/143 分别选择 node-a/slot-a、node-b/slot-b；
   139/145 commit；148/149 activate。两个 assignment 到齐后，共享 Habitat 才开始 joint episode。
4. Stage2 输入分别为 `any_targets|0` 和 `TARGET_any_targets|0`，step 1 两个 agent 均为
   `nav_to_obj`。
5. step 90 agent0 捕获本地 navigation complete；step 92 首次看到 `[wait, nav_to_obj]`。
   agent1 持续导航到 step 1000，step 1001 转为 wait，之后直至 step 3000 未再导航。
6. step 3000 episode 终止，联合官方 `pddl_success=false`；agent1 尚无本地成功，因此 direct outcome
   是 `episode-terminated-before-success`。Controller sequence 390 保存原始失败原因，391–394 推进
   Task/Group failure 和资源释放。
7. node-a 的 summary `final_position` 是 step 90 本地完成时的快照，不是 step 3000 终态。

历史证据不能回答：

- 两个官方谓词各自在终态的独立真假；
- agent0 在 step 90 后是否一直留在目标位置；
- agent0/agent1 的历史 rotation；
- agent1 最后一次真实位移、是否场景碰撞、PathFinder 是否找到期望楼层路径；
- step 1000 的原生退出分支。其轨迹与 `max_skill_steps=1000` 高度一致，但旧运行没有保存
  `over_max_len`、finished sensor 和 high-level call flag，因此历史直接原因仍不能从“导航未完成”
  继续细化。

没有证据支持“Task Completed 后 agent0 已离开目标”。也不能从 `pddl_success=false` 推断两个谓词都
为 False。

## 5. 当前完整链路

实际入口为
`scenarios/e1-shared-world-episode-51/run-b1-roboguide.sh`，不是静态 fixture：

1. `roboguide_eval.b1_workload` 校验并提取 frozen input。
2. 启动 shared-world Habitat adapter；它在同一个 simulator reset 后维护 agent0/agent1 两个 endpoint，
   并先发布 authoritative semantic evidence。
3. 启动 `integration-server`、两个 `roboguide-node`，等 inventory 同时出现 node-a/node-b。
4. 启动 production Mission Service。`config/mission.toml` 实际加载当前 main 的 Interpreter、Planner、
   Reviewer、Repairer；Planner/Reviewer/Repairer Prompt SHA256 分别为：
   - Planner `f0cfd7f3394379c65a3913085cb43688d690d9a19613b07ea7576e977613b83d`
   - Reviewer `daa7238177c93aa54c903cc4411d3c0d041009fa7644cb987d88bb4007956c1e`
   - Repairer `0cac54f3aa567c363e953afba7ac0bf16892859efc5bc96b9e149b108333baed`
5. Interpreter → Planner → draft validation/regeneration → Reviewer/Repairer 使用同一冻结 grounding snapshot。
   Planner regeneration 使用同一 Planner Prompt，并收到前一份完整 raw draft 和 validation errors。
6. deployment execution profile（SHA256
   `2e0aaa5857cb208050241df0ccc04315c09bfdf2d97dab141e56c9e79535b504`）为
   `mobility.move@v1` / `mobility.navigate@v1` 注入 `space:1`；脚本不事后修改计划。
7. accepted plan 经现有 Controller Matching → Scheduler → Proposal → Commit → Group Bind → Node dispatch。
8. Node 的 canonical operation 经两个 deployment-owned endpoint 映射到原始 EMOS Stage2；Stage2 再调用
   Habitat skill stack。
9. Mission terminal 与 Habitat 官方 `pddl_success` 分别保存；`b1_artifacts` 汇集 request、events、
   attempts、semantic evidence、benchmark evidence，再计算 provenance 和 admission。

当前 production path 不读取 B2 authored plan，也没有 Episode51 hard-coded MissionPlan。

## 6. 单 Mission Request 生命周期

Runner 只执行一次 `b1_runner_wait --submit-and-wait`。Mission Service 同步 POST 超时或连接中断时，
helper 从本次独占 SQLite store 中恢复与 frozen instruction 匹配的唯一 `request_id`，随后继续轮询该 ID；
它不会重发 instruction。若无法唯一恢复 ID，结果是外部观察失败并停止，不会提交第二个 Request。

应在运行后复核：

- `mi-wait-log.jsonl` 只有一个 submit 起点；
- `mission-service.sqlite3` 只有一个属于本 run 的 Request；
- `b1-timing.txt`、`mi-wait-outcome.json`、`b1-request-record.json` 的 request_id 完全一致；
- Controller 只接收该 Request 最终绑定的一次 Mission submission（幂等 retry 不算新 Mission）。

Accepted 后的 Mission observation budget 固定为 1,800 秒。预算耗尽应归档为 harness observation
boundary，不能启动第二个 Request，也不能把未观察到终态改写成 MI/Control 失败。

## 7. Control 分配与资源证据

两个逻辑 Actor/Role 本身不保证使用不同 Physical Entity。当前冻结任务也不要求 distinct executors。
实际部署通过每个 node 的唯一 `space` resource 和 endpoint 容量表达互斥执行条件；Controller 以现有
resource commitment authority 选择资源并在 Group/Task lifecycle 中持有它。下一次运行应以事件而非
计划推断分配：

- `TaskExecutionRegistered`：两个 Task 均注册；
- `TaskSchedulingSelected`：逐 Role 的 node_id 与 resource_ids；
- `PlanCommitted` / `TaskExecutionActivated`；
- assignment-arrival 中的 Mission/Task/Role/Execution/Node/endpoint/agent id；
- terminal 前后的 bindings release。

历史序列 137/143 已证明现有 profile 能将两任务放到两个可用 slot。若下一次 MI 生成不同的合法任务
组织，应保存真实结果，不强制 Spot/Fetch assignment，不在实验脚本中 patch plan。

## 8. 初始化、轨迹和终态证据能力

显式设置 `ROBOGUIDE_B1_PHYSICAL_DIAGNOSTICS=1` 后，v0.2 诊断记录：

| 阶段 | 直接证据 |
|---|---|
| reset | 实际 episode/scene、传入 seed 与 Habitat config seed、两 agent post-reset position/rotation、官方 goal conjunct labels、goal entity world positions、初始逐谓词值、初始 PDDL metric |
| 每 step | simulator step、两个 agent position/rotation、当前/前一 skill、skill transition、skill counter/max、high-level-next flag、原始 finished sensor（policy input 与 post-step）、oracle `skill_done`、action argmax/nonzero、逐谓词值、PDDL metric、done/episode_over |
| terminal | 真正终态 position/rotation、逐谓词值、完整官方 metrics、termination reason、采样/写入/丢样/耗时统计 |
| Task/Control | final plan、context/relation/shared view、resource minima、Controller assignment/commit、Node execution ids、endpoint arrival、Node terminal reason |

由连续 position/rotation 与固定 goal entity position可以离线计算欧氏距离、停滞区间、往返和完成后
是否离开目标。场景描述能提供已有 room/floor 标签。诊断对 `any_at` 逐谓词调用官方
`Predicate.is_true(sim_info)`；它先 clone expression，并以 `dataclasses.replace(...,
pred_truth_cache=None)` 隔离 cache。该路径只读实体/agent transform，不替代或修改官方
`PddlSuccess` measure。

观测边界：

- 当前配置的 `did_agents_collide`/`num_agents_collide` 可在 terminal official metrics 中看到 agent-agent
  碰撞；没有逐 agent 的场景接触流。不能仅凭“位置不变”断言撞墙。
- PathFinder 内部每步路径/waypoint 和 geodesic distance 未归档；只有 goal entity position 与实际 pose。
- EMOS 没有持久化原生 skill-exit-cause event。诊断把 finished sensor、budget comparison 和
  high-level flag作为**原始字段**保存，但 `skill_exit_reason` 明确标记为 `inferred`。
- 完整 RNG state 没有只读、稳定的跨库契约；记录的是实际 reset 结果和配置 seed。
- Stage2 的 stdout/chat/tool evidence保存在 `shared-bridge.log` 和 usage files；当前 adapter 以
  `save_chat_history=False` 创建 actor，因此不会生成完整结构化 Provider request archive。

因此可独立判定“何时停滞、是否到达/离开、是否由 skill budget 切换、官方谓词变化与最终 benchmark
结果”，但若唯一原因依赖场景碰撞或 PathFinder 内部路径，结果应继续标为未确定。

## 9. 诊断非干预与证据损失边界

诊断默认关闭，只有环境变量严格等于 `1` 才启用。实现不额外调用 `actor.act()`、`gym_env.step()`，
不改变动作、技能状态、任务分配、RNG、episode step limit 或 PDDL 判定。

本次修复后的边界：

- 每条记录上限 65,536 bytes；每 step 一条，max records 由 shared-world `max_steps` 给出；
- 两条 JSONL 流均以 32 条为批次，最多约 2 MiB pending data；不再每 step open/write/close；
- 正常 terminal 和 `_pair_loop` finally flush；突发进程/主机死亡时，每条流最多丢失 31 条 pending rows；
- serialization/write failure只增加 dropped/write-failure counter，不抛回物理执行；
- reset/terminal 顶层读取失败会尽力写 `_status=unavailable`，写存储本身失败也不会改变 SUT outcome；
- terminal 保存累计/最大采集耗时、records accepted/written/dropped、pending、flushes、write failures；
  若这些统计显示丢样，就不能宣称轨迹完整。

## 10. Mission success 与 benchmark success

二者有独立 authority：

- Mission/Task terminal 来自 Control/Runtime/Node 执行链；local Completed 只证明本地 execution outcome。
- benchmark success 只读取 shared-world summary 中严格 bool 的 Habitat 官方
  `official_pddl_success`。
- 逐谓词诊断用于解释轨迹，不改变官方联合 metric。
- B1 provenance 验证 frozen episode/scene/dataset revision+digest、MI request/review context、Controller
  Task registrations/executions和真实 benchmark evidence。
- semantic goal coverage 仍是 diagnostic/model outcome，不是 Formal population exclusion 条件。

Controller events 使用 `/v1/events?after=<actual sequence>&limit=100` 有界分页。归档器允许合法 sequence
间隔，拒绝重复、乱序、无进展、非法响应和 budget exhaustion；terminal Mission 在采集前后保持一致，
同一 cursor 连续两次空 tail 后才原子发布 `events.json`。失败保留原始 page，并以
`failure_owner=EVIDENCE_COLLECTOR` 与 SUT failure 分开。

## 11. 下一次执行前的硬性门槛

全部通过后才能开始：

1. 从诊断代码 commit `db114d610fe89076ba4c3166752df3b01d85421f` 建立干净、专用 worktree；
2. frozen input、dataset、execution profile、runner 四个摘要与本报告一致；
3. EMOS HEAD、tracked diff SHA 和关键文件摘要一致；不得忽略 dirty deployment state；
4. `habitat` Conda 环境可 import Habitat、Habitat Baselines、Habitat MAS、Torch，CUDA 可用；
5. GPU 1 有足够显存，GPU 0 的其他任务不被触碰；
6. 六个端口均空闲；不得 kill 外部 listener；
7. 本地 source `emos.env` 后只检查三个必要变量非空，绝不打印值；
8. Rust binaries 从该 commit 构建成功；
9. 使用全新 run directory；存在 `b1-input-used.json` 或 `controller.sqlite3` 时 runner 必须拒绝覆盖；
10. diagnostics flag 显式为 `1`，MI observation budget 固定为 1,800 秒；
11. 执行期间绝不手工注入计划、重提 Request、换 seed、延长 steps 或改 goal。

## 12. 可执行方案（本轮未执行）

以下命令是下一次授权后使用的固定方案。它明确使用外部 EMOS root，避免独立 worktree 导致默认路径
解析到 `/tmp/emos-baseline`。

```bash
set -euo pipefail

REPO=/tmp/roboguide-ep51-full-chain-run
EMOS=/data/workspace/code/emos-baseline
RUN_ROOT=/data/workspace/code/roboguide-ep51-full-chain-diag-$(date -u +%Y%m%dT%H%M%SZ)
RUN="$RUN_ROOT/b1-diag-ep51-seed40"

git -C /data/workspace/code/RoboGuide worktree add --detach "$REPO" \
  db114d610fe89076ba4c3166752df3b01d85421f
test "$(git -C "$REPO" rev-parse HEAD)" = \
  db114d610fe89076ba4c3166752df3b01d85421f
test "$(git -C "$EMOS" rev-parse HEAD)" = \
  e9501db45d634b087bf5d1a14228266685e8feeb
test "$(git -C "$EMOS" diff --binary | sha256sum | cut -d' ' -f1)" = \
  172268dd4cf44e8e50ba1d51de2f9f7a4ff5377b32d7aa96bfa21f1b77c0ee78

test "$(sha256sum "$REPO/scenarios/e1-shared-world-episode-51/b1-input.json" | cut -d' ' -f1)" = \
  1026761241f906ca2a35ab567082ab3d281d76524d5c8ab38a2c8522bbaf87c6
test "$(sha256sum "$EMOS/data/datasets/mp3d/mobility_episodes_1.json.gz" | cut -d' ' -f1)" = \
  5d2c6aa6608d5611c73d8f6c688e17613a9898afa5f0f668e66db068598191ca
test "$(sha256sum "$REPO/scenarios/e1-shared-world-episode-51/execution-profile.json" | cut -d' ' -f1)" = \
  2e0aaa5857cb208050241df0ccc04315c09bfdf2d97dab141e56c9e79535b504
test "$(sha256sum "$REPO/scenarios/e1-shared-world-episode-51/run-b1-roboguide.sh" | cut -d' ' -f1)" = \
  1e326789fb1a0e113cc02676f3aa2839320523c0f9248eb2ae5027bf9b710f2a

for port in 8070 25060 28060 28090 28100 28102; do
  ! ss -ltn "sport = :$port" | tail -n +2 | grep -q .
done
nvidia-smi --query-gpu=index,memory.free --format=csv,noheader

set -a
. "$EMOS/emos.env"
set +a
: "${OPENAI_API_KEY:?OPENAI_API_KEY is required}"
: "${OPENAI_BASE_URL:?OPENAI_BASE_URL is required}"
: "${EMOS_LLM_MODEL:?EMOS_LLM_MODEL is required}"

export ROBOGUIDE_EMOS_ROOT="$EMOS"
export ROBOGUIDE_HABITAT_CONDA_ENV=habitat
export ROBOGUIDE_HABITAT_CUDA_DEVICE=1
export ROBOGUIDE_B1_PHYSICAL_DIAGNOSTICS=1
export ROBOGUIDE_B1_MI_OBSERVATION_BUDGET_SECONDS=1800

cd "$REPO"
cargo build -p integration-server -p roboguide-node
mkdir -p "$RUN"
scenarios/e1-shared-world-episode-51/run-b1-roboguide.sh \
  "$RUN" \
  scenarios/e1-shared-world-episode-51/b1-input.json \
  2>&1 | tee "$RUN/runner.log"
```

预计资源与时间：1 张 RTX 4090（固定 GPU 1），两个 Node/一个 Controller/一个 Mission Service/一个
Habitat child；3,000 × 20 ms 只是 step sleep 下界，Stage2 与初始化会显著增加总时长。MI 和 accepted
Mission 各有最多 1,800 秒观察边界。历史 run 约 5.2 MiB；v0.2 每条最多 64 KiB、每步一条，理论
诊断上界约 200 MiB，实际应明显更小。开始前保留至少 1 GiB run disk headroom。

预先固定停止条件：SHA/digest/diff 不符、端口占用、GPU 1 不足、凭据变量缺失、semantic identity
不符、二进制/服务启动失败、Request identity 不唯一、任何 evidence collector 不完整。物理运行中的
Provider/MI/Control/Node/Stage2/Habitat 真实失败不是改参或重跑理由；均归档后停止。

## 13. 验证结果

在 `db114d6` 上完成：

- targeted Habitat diagnostics/shared-world：37 passed；
- MI lifecycle、coordination、review 与 B1 runner/event/provenance targeted：全部通过；
- Ruff format/check：通过（175 files）；
- prescribed strict mypy：Mission/integrations 74 files、evaluation 51 files，分别通过；
- Python function-doc check：通过；
- 全量 `uv run pytest -q`：见本分支最终验证记录；
- `cargo fmt --all -- --check`：通过；
- `cargo test --workspace`：通过；
- `cargo clippy --workspace --all-targets -- -D warnings`：通过；
- `bash -n run-b1-roboguide.sh`、required Rust binary build、`git diff --check`：通过。

额外把两个 AGENTS.md mypy scope合成一次调用会在 `evaluation/mission_front` 报 20 个跨 package 类型解析
错误；完全相同现象在干净 `origin/main@28a3dcd` 可复现。仓库规定的是两个独立 mypy 命令，二者在
当前分支均通过，因此没有把这个 aggregate-only 结果归为本次回归。

## 14. Readiness 判定

当前没有证据支持在 `origin/main@28a3dcd` 上直接开启 enabled diagnostics。应使用经过验证的
`db114d6`，并在授权执行当日重新通过部署门槛。通过后，链路会真实运行 production MI、Control、
Node、原始 EMOS Stage2 和 Habitat；不会复用 A/B 历史计划，不会创建第二个 Request，也不会把本地
Task Completed 当作官方 benchmark success。

仍未经过真实 Habitat 验证的是 v0.2 诊断在实际 Episode51 的字段可用率和观测开销；这是下一次受控
诊断需要实际测量的风险。若 terminal `collection_stats` 出现 dropped/write failure，或关键字段为
unavailable，该次物理 outcome仍保持原值，但不得宣称诊断证据完整。

