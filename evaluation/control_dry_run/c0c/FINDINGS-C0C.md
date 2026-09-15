# C0-C Production HTTP Submission Smoke — FINDINGS

- Baseline: `main@31bc9f6071a7d427affc48f1854e9137827a546d`
- Method: 真实启动 production `integration-server` 与 `roboguide-node`（未改任何
  Core/apps 生产代码），fake Local EAIOS 仅作为 evaluation 侧 HTTP responder；
  Mission 全部由 `curl POST /v1/missions` 经生产入口提交。
- 结果: **C0-C1 PASS、C0-C2 PASS、事务边界探针 PASS、C0-C3 BLOCKED**
  （Control 层语义 PASS，runtime 层存在确定性生产缺陷 → **C0 Control Dry-run 不能关闭**）。

## 0. 运行环境与证据

| 项 | 值 |
| --- | --- |
| server 启动 | `target/debug/integration-server <grpc> <sqlite> <http> <artifact> <artifact_root>` |
| node 启动 | `target/debug/roboguide-node <config.toml>`（config 为 evaluation 侧 v0.7 副本） |
| fake EAIOS | `fake-eaios/fake_eaios.py`，仅实现 health/readiness/executions HTTP 路由并 JSONL 记录请求 |
| 端口组 | 每 run 独立（25051-25054 / 28080-28097），未触碰常驻 dev server（15051/18080） |
| 证据目录 | `responses/`（原始 HTTP）、`events/`（事件日志快照）、`logs/`（进程日志+git 头）、`run/*/controller.sqlite3`（durable 事件库）、node journal（`node-configs/node-state/**/execution-journal.sqlite3`）、`summary.json` |

## 1. C0-C1 Exact Operation HTTP Smoke — PASS

- 提交物：R2 `n4-edge-inference-plan-v0.7.json`（原样，未改一个字节）
- Node：`c0c-node-infer`，声明 `capability_profiles: compute.infer@v1`（readiness
  HTTP 观察 READY）+ `operations: compute.infer@v1`（execute/status/cancel HTTP workflow）
- 结果：`POST /v1/missions` → **202**，`mission_id`/`group_id` 正确；
  GET mission → `status: Completed`，`task-infer-recording: Completed`（约 2 秒收敛）
- **exact operation 未退化为 capability** 的三重证据：
  1. Matching 事件链正常（exact-operation 门通过，否则 409/no-candidate）；
  2. Node 侧 invocation（fake EAIOS `POST /v1/executions` 请求体）：
     `"operation": "compute.infer@v1"`、`"parameters": {"model": "pointcloud-analysis-v1"}`、
     完整 `mission_id/task_id/group_id/role_id/objective/resource_ids`；
  3. 事件链 `TaskExecutionActivated → NodeObservation(TaskCompleted) →
     TaskExecutionCompleted → TaskSatisfied(basis=execution-report) →
     ExecutionGroupCompleted`。
- 数据身份观测（如实记录）：`lidar-run-017` 在该 fixture 中只存在于
  objective/description 文本，**不是结构化参数**；因此 Node command 证据中它随
  objective 字符串保留（上述请求体可见），不存在独立的 `data` 参数字段。这是
  fixture 的构造属性，不是链路丢失。
- Node durable journal：单条 execution `attempt-32:...role-infer-1`，
  `status=completed, reason=c0c-fake-done`（dispatch 前持久化语义成立）。

## 2. C0-C2 Future Timing HTTP Smoke — PASS

- 提交物：R2 `t2-start-window-plan-v0.7.json`，提交前脚本断言
  `earliest=latest=3600000` 原样保留（guard 输出 `t2 timing guard passed`）。
- 结果：**202 接受**；即时 GET mission → `Running` / `task-move-gate: Ready`；
  fake EAIOS dispatch 调用 **0 次**；execution-attempts **空**——**没有提前激活**。
- **timing 进入 scheduler 的证明**：`TaskSchedulingDeferred
  {reason: "no-feasible-interval"}`。这是生产策略（`core/control/src/scheduler/policy.rs:186`）：
  `starts_at > now && ends_at.is_none() → Deferred`——未来起点在无 duration evidence
  时拒绝无界预留。同机制 A/B 对照：timing=0 的 C0-C1 立即 SelectedNow 并完成执行，
  timing=+3600000 的 t2 拒绝立即执行——差异只能来自 timing 被调度器读取，未丢失。
- `GET /v1/scheduling-reservations` 为空：与 deferral 一致（没有 reservation 被
  创建，而非 timing 丢失）。当前生产 HTTP 路径无任何 duration evidence 供给方
  （`prepare_task_with_duration_estimate` 无生产调用点，grep 验证），因此未来窗口
  任务在现网始终走 durable deferral，等待调度条件。**这是设计行为，不是缺陷**；
  若未来接入 duration evidence（State 侧源感知证据），将形成有界 future reservation。
- PASS 判定依据（按本轮标准）：HTTP 路径没有丢 timing，且系统没有提前激活 Task。✓

## 3. C0-C3 Same-Mission Independent Parallel — BLOCKED

- 提交物：evaluation-only fixture `c0c3-independent-parallel-plan-v0.7.json`
  （1 mission / 2 actors / 2 independent contexts / 2 tasks `depends_on=[]` /
  `compute.infer@v1` + `mobility.move@v1`，均为 Catalog 既有 operation，未新增能力）
- Nodes：`c0c-node-a`（infer）、`c0c-node-b`（move）。

### 3a. Control 层语义 — PASS（事件证据 run/c0c3/controller.sqlite3 seq 5-21）

- `POST /v1/missions` → **202**；两个 task 进入**同一个** mission-level group。
- 两个 task 各自独立完成完整链：`CandidatesMatched → TaskSchedulingSelected →
  ProposalCreated → PlanCommitted → ExecutionGroupBound → MissionActorBound`。
- 绑定正确：`role-infer → c0c-node-a`、`role-move → c0c-node-b`（seq 11/17）。
- `relations: []`、`peer_channels: []`、无 coupling 升级、TaskRef/Role/Binding
  无冲突。task-move-gate 全链完成：
  `Activated → NodeObservation(TaskCompleted) → TaskExecutionCompleted →
  TaskSatisfied → BindingsReleased`（seq 34-42）。

### 3b. Runtime 层 — 确定性生产缺陷（2/2 复现），C0-C BLOCKED

崩溃签名（两次运行完全一致）：

```
Error: "Mission orchestration failed after fact acceptance:
control rejected orchestration: invalid lifecycle: Blocked"
```

完整因果链（全部有代码/事件/journal 证据）：

1. node-a 的执行处于 journal `Dispatching` 窗口时，一条执行快照被回放给
   server（node 侧 broadcast `Lagged → replay_snapshots`，
   `core/node-service/src/service/mod.rs:152`；快照 phase 映射
   `core/node-service/src/engine/validation.rs:528-531`：
   `Dispatching → ExecutionPhase::Unknown`，reason 为空）。
   （journal 中 `local_dispatch_authorizations` 仅一行，排除了重复 Execute 投递
   的 `Existing` 快照路径。）
2. server `consume_execution`（`core/orchestration/src/integration_bridge/
   execution_facts.rs:23`）把 Unknown/Unspecified phase 归约为
   `ExecutionStatus::Unknown`；runtime（`core/runtime/src/execution/observation.rs:194`）
   发出 `RuntimeExecutionRecoveryRequired{reason:""}`（事件 seq 22）。
3. reconciliation 将 node-a 判为 unavailable → 释放 role 绑定 → **Group Blocked**
   （seq 23-25）；recovery 无候选（node-b 不支持 infer）→ `RecoverySchedulingNoSelection`
   循环（seq 26-49）。
4. 后续 `drive_ready_tasks` 重新 Match→…→Bind task A 并 `TaskExecutionActivated`
   （seq 28-33, 47；第二个 attempt 的 Execute 从未投递到 node——node journal 无第二行）。
5. node-a 上**原始 attempt-32 实际执行成功**（journal：`completed/c0c-fake-done`），
   其完成事实到达 server 后，`apply_runtime_outcomes` 对 Blocked group 应用
   完成转移被 Control 拒绝（`invalid lifecycle: Blocked`）；
   `apps/integration-server/src/main.rs:629-641` 将该错误作为 fatal 发送 →
   **main 返回 Err，整个 controller 进程退出**。两个 node 进入重连循环
   （node 日志：`h2 broken pipe` + 连续 `transport error`）。

### 3c. 后果与判定

- 后果：Mission 永久停留 `Running`，task-infer-analysis 停留 `Ready`，controller
  进程死亡，全部已注册 node 失联。**一条良性 node 事实杀死整个 controller。**
- 判定：C0-C3 的语义断言（§3a）全部成立，但 smoke 不能标记 PASS——运行时层
  崩溃违反任何可接受的 smoke 标准。按本轮权限边界，未做任何生产修改。

## 4. HTTP Acceptance 事务边界 — PASS

| 探针 | 结果 | 说明 |
| --- | --- | --- |
| 畸形 JSON | **400** | JSON/decode 层拒绝 |
| v0.7 缺 timing | **400** | decode 门（"MissionPlan v0.5+ Task must declare timing"） |
| 无 provider 节点 | **202** | **provider 缺失是调度条件而非语义拒绝**（与 Mission Service 语义一致）；mission 存在（GET 200）、事件已持久化、task 留待调度 |
| 不可执行 relation（relative-pose） | **409** | "valid contract syntax but is not executable by this Controller build"；mission GET **404**、事件零痕迹（candidate controller 未换入 live） |
| checkpoint 持久化失败 503 | 源码验证 | `server.rs` save_checkpoint 分支（217-236）；不破坏存储无法在线触发，未实测 |

**command-delivery 边界（按本轮要求如实表述）**：`live.bridge.flush_command_outboxes()`
属于 durable commit **之后**的投递步骤；其失败只 eprintln
`durable command outbox delivery deferred` 并继续——Mission 已接受、HTTP 已 202、
command 投递延后。**delivery 失败不回滚 Mission**（`main.rs:242-244`）。

## 5. 结论回答

1. **Production POST /v1/missions 是否真实接受 v0.7 MissionPlan？**
   是——三个 smoke 全部 202 接受，decode 门（400）与 preflight 门（409）行为正确。
2. **Mission 是否真实走到 orchestration + Control？** 是——事件链证明
   submit → group → ready → match/schedule/propose/commit/bind → dispatch，不是停在 decode。
3. **exact operation / timing 是否在 runtime 中保持？** 是——operation+model 参数
   完整到达 Node 侧 Local How invocation；timing=3600000 驱动调度决策且未提前激活
   （A/B 对照证明）。注意：未来窗口在当前生产路径下因无 duration evidence 必然
   durable deferral（设计行为）。
4. **Same-Mission Independent Parallel 是否真实成立？** Control 层成立（独立
   全链 + 正确分派 + 无 relation/无 coupling 升级）；runtime 层被 C0C-B1 阻断。
5. **authority commit 与 command-delivery failure 边界是否符合设计？** 符合——
   400/409 在 acceptance 前回滚、不换入 live；delivery 失败不回滚已接受 Mission。
6. **是否存在需要 Codex 修复的 production blocker？** **存在 1 个（C0C-B1）**，
   见 §3b 与 summary.json `core_blockers`。

## 6. 判定

```
C0-C Runtime HTTP smoke      BLOCKED (C0C-B1)
C0-A Internal invariants      PASS
C0-B Production path trace    PASS
C0 Control Dry-run            未关闭（等待 C0C-B1 修复后重跑 C0-C3）
```

## Core blocker list（交 Codex，ZCode 未做任何生产修改）

**C0C-B1** — same-mission 并行任务下 controller 因 Blocked-lifecycle fatal 退出

- failing stage: C0-C3 runtime-level 任务完成（group recovery 之后的 node 完成事实应用）
- exact source path / function:
  - `core/node-service/src/engine/validation.rs::snapshot_from_record`（Dispatching→Unknown+空 reason）
  - `core/node-service/src/service/mod.rs` session loop（`Lagged → replay_snapshots`）
  - `core/orchestration/src/integration_bridge/execution_facts.rs::consume_execution`（Unknown phase 归约）
  - `core/runtime/src/execution/observation.rs::observe_execution`（Unknown → RecoveryRequired("")）
  - `core/control/src/reconciliation`（node unavailable → group Blocked）
  - `apps/integration-server/src/main.rs` receiver loop（`apply_runtime_outcomes` Err → fatal → 进程退出）
- actual behavior: Dispatching 窗口的执行快照被当作物理歧义触发 recovery；Group Blocked 后，
  原尝试的真实完成事实应用被拒；fact 处理链把该错误升级为 fatal，controller 进程退出。
- expected behavior: 刚下发的 Dispatching 快照不得触发物理歧义 recovery（或 recovery 不得
  永久 Block 同一 node 仍在心跳的 group）；被 fence 尝试的完成事实应消解歧义而不是使
  controller 退出；单条 node 事实不应有进程级杀伤力。
- minimal production change（供 Codex 决策，二选一或组合）:
  a) `snapshot_from_record` 不把 `Dispatching` 映射为 `Unknown`（如映射为
     Accepted 或带显式 reason 的非歧义 phase），或 runtime 对"本控制器刚下发、
     receipt 尚未到期"的 attempt 的 Unknown 快照不触发 RecoveryRequired；
  b) `apply_runtime_outcomes` 对 fenced/Blocked group 的完成转移改为有界 deferral
     （保留事实、等待 recovery 决议）而不是 fatal 进程退出。
- 复现：`evaluation/control_dry_run/c0c/commands/run-c0c3.sh`（2/2 确定性）。

## 7. 约束遵守

- 未修改任何 production 代码（core/ apps/ contracts/ mission/）；全部改动位于
  `evaluation/control_dry_run/c0c/`。
- 三个 smoke 均经真实 `POST /v1/missions` 提交；未使用 smoke tool 的内部提交路径
  （real-node-smoke 未使用，因其 contract 会话唯一、无法声明固定 operation）。
- 未调用 LLM、未跑 Mission regression、未接 Habitat/EMOS。
- 常驻 dev server（15051/18080）与 `experiments/` 未触碰。
