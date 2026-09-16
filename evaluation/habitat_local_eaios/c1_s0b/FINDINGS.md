# C1-S0B + C1-S1 Overnight FINDINGS

> Historical evidence note: the wrapper-owned prompt/dispatch/retry
> implementation evaluated below was replaced during Formal E1 preflight.
> The current backend injects only the committed Stage1 assignment and runs
> the original EMOS `MultiLLMPolicy`/`HierarchicalPolicy` stack. See
> [`../../e1/FAIRNESS_LEDGER.md`](../../e1/FAIRNESS_LEDGER.md).

- Baseline: `c29f3840e610e6c60b5ab0d723bf5b8a4d067849`（本轮未改 Core；二进制
  `cargo clean -p` 强制重建）。`experiments/` 未触碰。

## Part A — C1-S0B implementation

1. **HEAD**：`c29f384`（origin/main 一致，无新 upstream commit）。
2. **新增**：`integrations/habitat-local-eaios/habitat_local_eaios/crabagent_backend.py`
   （唯一 production-integration 新模块）；`process_backend.py`/`__main__.py` 增加
   deployment-owned `backend_class`/`--backend`/`--subtask-mode`/`--evidence-dir`；
   测试 `tests/test_crabagent_backend.py`（10 个）；scenario
   `scenarios/habitat-local-eaios-c1-s0b/`（脚本+verifier+复用冻结 fixture）；
   evaluation 侧 `evaluation/habitat_local_eaios/c1_s0b/`。
3. **直接复用 EMOS CrabAgent**：是。`import habitat_mas.agents.crab_agent.CrabAgent`
   + EMOS 原生 action pool；无任何复制魔改；EMOS 路径经 deployment PYTHONPATH 注入。
4. **canonical operation**：仍 `mobility.navigate@v1`（model.py 未改）。
5. **natural objective 进入**：`CrabAgent.init_agent(subtask_description=invocation.objective)`
   （natural-objective 模式）；entity-grounded 模式为 `"Navigate to {destination}."`；
   `destination` 全程保留为 canonical parameter/evidence。
6. **scene_description 对齐**：直接调用 EMOS 同源函数
   `habitat_env.task.get_task_text_context()["scene_description"]`
   （rearrange_task.py:430 → habitat_mas_sensors.py:305）；prompt 模板逐字取自
   `llm_policy.py`，sha256 存于每 run `evidence/prompt_freeze.json`；完整样例已归档
   （e2e-*/evidence/scene_description.txt）。
7. **skill dispatcher**：复用 EMOS 语义——`nav_to_obj` 经 `pddl_problem.get_entity`
   解析 agent 自选目标后走与直接后端相同的 `OracleNavAction` step 循环（即
   OracleNavPolicy 的实体+finished-measure 语义）；`wait` 按 EMOS wait 步数执行；
   `pick/place/reset_arm/get_agents` fail-closed；None/畸形按 EMOS wait 同型处理
   并设 5 次连续上限；**无静默 fallback**。
8. **direct Oracle backend 不变**：`HabitatMobilityBackend` 零改动；
   frozen `run-happy-path.sh`/`run-cancel-sanity.sh` 双回归 PASS。
9. **可观测性**：每次 LLM 调用入 `action_trace.jsonl`（call_index/stage/token_delta/
   latency/pipe）+ EMOS 原生 `token_usage_details.jsonl`（逐调用 prompt/completion/
   reasoning tokens、模型名、latency）+ `token_summary.json`；message_pipe 逐 tick 快照。
10. **Core blocker**：无（C1-S0B 范围内）。

## Part B — C1-S0B validation

| 组 | 结果 | 关键数据 |
| --- | --- | --- |
| deterministic tests | 16/16 PASS | adapter 6 + crabagent 10（fake 决策驱动，不依赖 EMOS/网络） |
| isolated real smoke ×2 | 2/2 local COMPLETED | entity: nav→any_targets\|0, 101 steps；natural: 1873+tokens 决策 |
| E2E entity-grounded ×2 | 2/2 PASS | 1 决策/nav，101 steps，llm=2 calls，~2.7k tokens |
| E2E natural-objective ×3 | 3/3 PASS | 一致地选 `TARGET_any_targets\|0`（合法 PDDL 实体），223 steps，~2.9-3.5k tokens |
| direct Oracle regression | happy PASS + cancel PASS | 新 backend 未破坏 Protocol A native 路径 |

- **exactly-once**：全部 run node journal 1 execution + 1 authorization。
- **RUNNING**：全部经 ACCEPTED→RUNNING→terminal（bridge log）。
- **local/Mission outcomes**：全部 local COMPLETED 且 Mission Completed，双向一致。
- **message_pipe/send_request**：全部 run 从未触发（pipe 每 tick 空），已按 PHASE 8
  记录未禁用。
- **模型方差**：natural-objective 的实体选择（TARGET_ 前缀）与步数/ token 差异如实
  记录为 local-agent variance，非系统失败；未修 prompt 追 PASS。

**判定：C1-S0B = PASS**（entity 组硬判据全过；natural 组 3/3 全 Completed）。

## Part C — C1-S1（status 瞬时故障）

**复现（before-fix，全部为真实 production 链 + fault proxy 注入）**：

| Case | 注入 | 结果 |
| --- | --- | --- |
| F1 | 首个 status POST→500 | node journal `reconciliation_required`，attempt `Unknown`，1 recovery 事件，**bridge 实际 COMPLETED**，Mission 永久 Running |
| F2 | 前 3 个 status→500 | 同 F1 |
| F3 | status 超时（吞请求） | 同 F1（timeout 与 500 同路径） |
| F4 | status 畸形 200 | 同 F1（解码错误同路径） |
| F5/F5b | refuse 15s/40s | 窗口短于注册时延（~39s），fault 未命中，Completed——**无效 case，如实标注** |
| F5c | refuse 150s（覆盖 dispatch） | execute 被拒→**无 attempt/journal/物理执行**，task 停 Ready，无 recovery 风暴、无 overclaim（较优雅） |
| F8 | cancel during F2 | cancel 202 → Cancelling；**cancel 走独立路径成功到达 bridge（state=CANCELLED）**，但 terminal 事实无人观察 → Mission 永久 Cancelling |
| F6/F7/F9/F10 | 未单独构造 | F9/F10 已被 F1-F4 覆盖（bridge 先 terminal、事实迟到）；F6/F7 需额外 harness，标注未跑 |

**根因（唯一调用点，源码级）**：`core/node-service/src/engine/execution.rs:87-99`
——status 轮询循环对第一次 `run_steps`/mapping/record 失败即
`record_ambiguous()+return`，**永不重试**。A 类（瞬时传输）、B 类（handle 不明）、
C 类（进程不可用）在此混同。

**分类与权限判定（PHASE 17-18）**：
- A 类正确修复 = 轮询循环内有界重试/退避 → 位于 `core/node-service` →
  **C1-S1 CORE BLOCKER**（配置无 retry 构造；bridge 侧自重试无法覆盖 node↔bridge
  传输层失败）→ 未修，仅交证据。
- 物理派发 exactly-once 保持：F1-F4/F8 均 1 次物理执行；F5c 0 次（拒绝在派发层）。
- terminal 事实重获：现设计下不可（轮询已死）；F8 证明 cancel 也无法救回 terminal
  观察，Mission 永久 Cancelling。

**判定：C1-S1 = BLOCKED BY CORE**（修复点 `core/node-service/src/engine/
execution.rs::spawn_status_loop`；建议最小改动：class A 的有界指数退避重试 +
连续 exhaustion 后才 record_ambiguous；class B/C 仍走 ambiguity）。

## Part D — restart truth table（PHASE 21，源码级 audit）

`store.py:62-64`：bridge 启动时把 `ACCEPTED/RUNNING` 行一律置
`FAILED("Habitat bridge restarted before local execution terminated")`。

| Crash point | durable? | 物理任务可继续? | local handle 归属 | status 可重获? | RoboGuide 是否 overclaim |
| --- | --- | --- | --- | --- | --- |
| bridge 进程重启（sim 子进程随之死） | invocation+state 持久 | 否（sim 已死） | journal 仍在 | 不可 | **是**：物理可能已近完成，被标 FAILED 而非 ambiguity |
| sim 子进程单独死 | 同上 | 否 | 同上 | 不可（status 会失败→Core ambiguity） | 部分（Core 侧反 rather 正确进 Unknown） |
| roboguide-node 重启 | node journal 持久 | **是**（bridge/sim 独立运行） | node journal | **可**（重启后 snapshot 重放恢复轮询） | 否（2f19e8a 修复后 Dispatching 不再误报） |
| integration-server 重启 | controller checkpoint | 是 | 双侧 journal | 可（checkpoint 恢复+route 重建） | 否 |

结论：bridge restart 的 FAILED 粗分类是 **C1 reference adapter limitation**
（对 ephemeral reference adapter 可接受），但与 Claim 3（恢复语义）相关的正确
形态应为 `reconciliation_required` 语义而非 FAILED——列为未来 blocker 候选，
本轮不改。

## 交付物清单

- `integrations/habitat-local-eaios/`：crabagent backend + 选择参数 + 测试
- `scenarios/habitat-local-eaios-c1-s0b/`：受控 smoke 脚本 + verifier
- `evaluation/habitat_local_eaios/c1_s0b/`：设计 trace + isolated/E2E/回归证据 +
  summary.json（本 FINDINGS 同目录）
- `evaluation/habitat_local_eaios/c1_s1/`：fault proxy + runner + before-fix
  matrix 结论（本文件）
