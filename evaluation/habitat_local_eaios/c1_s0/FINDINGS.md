# C1-S0 Habitat Local EAIOS Bridge — Independent Revalidation FINDINGS

- Baseline: `727f5e4e5e938cf1c1907f131647a25d68974892`（`feat: add habitat local eaios bridge`，
  main HEAD，无后续 commit）。`experiments/` 保持未跟踪，未修改/未提交。
- 方法：使用**未修改的冻结脚本** `scenarios/habitat-local-eaios-c1-s0/run-happy-path.sh`
  与 `run-cancel-sanity.sh`（md5 见 `head.txt`），4 次完全 fresh 运行（脚本自身每 run
  `rm -rf` 运行目录；controller sqlite、node journal、bridge sqlite、全部进程均为新实例）。
  未复用 Codex /tmp 结果；未改 mission/destination/episode/scene/agent；未降低判据。

## 构建与环境取证

- 二进制：因 target 缓存 mtime 不可信（C0C-B1 教训），执行
  `cargo clean -p integration-server -p roboguide-node && cargo build -p integration-server -p roboguide-node`，
  产物 mtime `2026-09-15 23:03`（+0800），源码 = 727f5e4 工作树（`git status` 干净）。
- Habitat 环境（独立 conda env，未污染 RoboGuide uv env）：
  - `ROBOGUIDE_EMOS_ROOT` 默认 `../emos-baseline` → `/data/workspace/code/emos-baseline` 存在；
  - `ROBOGUIDE_HABITAT_CONDA_ENV=habitat` → `import habitat` OK（habitat-lab 来自 EMOS checkout）；
  - `torch.cuda.is_available()=True, device_count=2`（RTX 4090 ×2，脚本用 GPU 1）；
  - episode 51 / scene `mp3d/pRbA3pwrgk9` / agent 0 由冻结脚本原样启动并在两次 run 中成功加载。

## 四次运行结果

| Run | 窗口 (UTC) | 冻结 verifier | 关键证据 |
| --- | --- | --- | --- |
| happy-run-1 | 15:06:57–15:07:47 | **PASS 20/20** | 101 steps，位移 |Δz|=5.25 |
| happy-run-2 | 15:08:19–15:09:09 | **PASS 20/20** | 101 steps，与 run-1 轨迹逐位相同（同 episode/seed 的确定性 Oracle，属预期） |
| cancel-run-1 | 15:10:03–15:10:47 | **PASS 9/9** | RUNNING 中接受取消（accepted_marker 原文在案），1 step 后 simulator 停止 |
| cancel-run-2 | 15:11:10–15:11:52 | **PASS 9/9** | 同上 |

## 逐项验收（A6/A7）

- **A6.1 Production ingress**：`POST /v1/missions` → 202（`responses/post-status.txt`）；
  无任何绕过 HTTP 的直接 adapter/action 调用。
- **A6.2 Control path**：事件链 `CandidatesMatched → TaskSchedulingSelected →
  ProposalCreated → PlanCommitted → ExecutionGroupBound → MissionActorBound`（seq 6-11）。
- **A6.3 Canonical intent**：Bridge 收到的 invocation（bridge sqlite `invocation_json`）：
  `operation="mobility.navigate@v1"`、`mission_id/task_id/group_id/role_id/objective`
  与 MissionPlan 一致、`parameters={"destination":"any_targets|0"}`、
  `resource_ids=["habitat-navigation-slot"]`（Commit 资源）。destination 仅是 semantic
  parameter，由 Local EAIOS 映射为 Habitat PDDL entity；Habitat entity index、Oracle
  action 名、action_args、path/pose 均未进入任何 RoboGuide canonical schema（事件/
  invocation 中无此类字段）。
- **A6.4 Local handle**：`habitat-820baa2cba19b6d314f6465a`（happy）/ `habitat-007b…`（cancel），
  同 invocation 全部观测（281 次 ACCEPTED poll、HTTP 响应、sqlite 行）handle 恒定。
- **A6.5 真实 RUNNING**：bridge log status 观察序列 `281×ACCEPTED → 132×RUNNING →
  1×COMPLETED`，Node 侧 workflow 状态机经 RUNNING（frozen verifier `running_observed`）
  ——非 ACCEPTED→COMPLETED 直跳。
- **A6.6 物理执行**：`episode_id="51"`、`scene_id="data/scene_datasets/mp3d/pRbA3pwrgk9/pRbA3pwrgk9.glb"`、
  agent 0、`simulator_steps=101`，`initial=[-0.167,2.321,3.769] → final=[-0.419,2.917,-1.473]`，
  位移 |Δz|=5.24（>0.05 冻结阈值）。
- **A6.7 双重结果分离**：Habitat local outcome `state=COMPLETED`（bridge sqlite）；
  RoboGuide 侧 `TaskExecutionCompleted → TaskSatisfied` 且
  `basis={"basis":"execution-report"}`（事件 seq 18-19）→ `ExecutionGroupCompleted`。
- **A6.8 Exactly-once**：node journal `executions=1, local_dispatch_authorizations=1,
  status=completed`；bridge `executions` 表恰 1 行；重复 status poll / receipt /
  重连未产生第二次 dispatch（verifier `single_local_execution`）。
- **A6.9 无错误 recovery**：`RuntimeExecutionRecoveryRequired` 0、Group Blocked 0、
  false Unknown 0、duplicate attempt 0（四次 run 均成立）。
- **A6.10 进程存活**：冻结脚本在 verifier 前对全部 PIDs `kill -0`（line 144-146），
  且 Mission Completed 后仍拉取 events/attempts（生产 GET 成功）；server log 无
  `Error:`/`application timer stopped`，node log 无 `session ended`，bridge log 无
  `Traceback`/`BrokenPipeError`/simulator 异常退出。注：脚本自身的 `trap cleanup EXIT`
  在 verifier 之后按设计停止全部子进程，故 run 后存活以"收尾 GET + kill -0 + 零错误日志"
  为准（冻结语义，未改动）。
- **A7 Cancel**：`cancel_accepted_while_running=true`（bridge log 原文
  "cancellation request accepted for habitat-007b… while local state is RUNNING"，
  此刻 state 仍 RUNNING）→ `terminal_cancelled_later=true`（simulator 停止后
  "local execution … terminated: CANCELLED"）→ Mission `Cancelled`；无第二次
  Execute/handle/dispatch；cancel HTTP 202。

## 结论

```
Happy Path          2/2 PASS
Cancel Sanity       2/2 PASS
Exact operation     PASS（mobility.navigate@v1 + 全字段保持）
RUNNING observed    PASS（281 ACCEPTED → 132 RUNNING → 1 COMPLETED）
Physical movement   PASS（101 steps，|Δz|=5.24）
Exactly-once        PASS（auth=1，exec=1，EAIOS dispatch=1）
Cancel accepted≠terminal  PASS
三进程存活           PASS
New production blocker    NONE
```

**C1-S0 Habitat Local EAIOS Bridge Independent Revalidation = PASS**

已知限制（本规则确认，未顺手解决）：polling 不自动恢复（C1-S1）、verifier-evidence
ingress 未实现、cross-node rebind 未实现、future duration reservation 未完整生产验证、
当前 bridge 为 Oracle navigation（无 CrabAgent LLM loop）。

## 归档说明

每 run 目录含 mission/events/responses/attempts/controller/node/bridge/logs/verdict.json
与 head.txt（UTC 窗口、SHA、episode/agent）。原始 /tmp 运行目录已被后续 run 按冻结脚本
设计清除；本目录为唯一完整归档。`logs/` 受仓库 `.gitignore` 约束，按惯例 `git add -f`
提交（小于 1MB，无数据集/二进制/conda env）。
