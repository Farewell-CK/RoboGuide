# Episode51 / seed40 完整链路诊断报告

日期：2026-09-21  
运行代码：`c4258266d9f9bd4cd1bb1b2dba52f97816b81181`  
运行目录：`/data/workspace/code/roboguide-ep51-full-chain-diag-20260921T025132Z/b1-diag-ep51-seed40`

## 结论

本次运行真实执行了 production Interpreter → Planner → Reviewer → Control → 两个 Node → 原始 EMOS
Stage2 → Habitat，且只创建了一个 Mission Request。新版 MI 首稿直接生成合法的 `independent` 计划，
Reviewer 一次批准，没有 rejected draft、regenerate 或 Repairer 调用。此前阻塞完整执行的 MI executor
constraint 问题已经消除。

Mission 最终为 `Failed`，官方 `pddl_success=false`。最早获得直接证据支持的任务偏离发生在 Fetch 的
Stage2 action generation：它收到的任务是到 `TARGET_any_targets|0`，首个工具调用却是
`nav_to_obj(any_targets|0)`。随后 Fetch 的技能序列转成
`nav_to_obj → pick → nav_to_obj → place → reset_arm → wait`，执行了原任务没有要求的 manipulation
序列。两个官方谓词在 1–3000 的每个采样 step 均为 False。

这次结果不支持把失败归因于 MI 的任务组织、Control 分配或资源调度。它直接支持“Stage2 没有忠实执行
已分配 canonical destination”这一结论。它也暴露了另一个独立事实：Spot 在 step 90 得到本地 Oracle
navigation completion，但对应官方谓词从未为 True；本地技能完成和 Habitat 官方目标满足仍然不是同一
事实。

本次 provenance、Formal population 和 benchmark population 判定全部有效，benchmark outcome 是合法的
`BENCHMARK_FALSE`。这是一条有效失败样本，不应改计划或重跑来制造成功。

## 冻结基线与单请求约束

运行使用 Episode51、seed40、场景
`data/scene_datasets/mp3d/pRbA3pwrgk9/pRbA3pwrgk9.glb`，dataset revision 为
`mobility_episodes_1`，摘要为
`5d2c6aa6608d5611c73d8f6c688e17613a9898afa5f0f668e66db068598191ca`。冻结输入、runner、execution
profile 和 Prompt 摘要见 [RUN_MANIFEST.json](RUN_MANIFEST.json)。

Mission Service 日志只有一次 `POST /v1/mission-requests`，request id 为
`request-9b361cdb7fea41dd8e25cb33555f48fd`。SQLite 在运行期间只有一条 Mission Request；runner 没有因长
Provider 延迟重提任务。证据为 [mission-service.log](evidence/mission-service.log)、
[b1-timing.txt](evidence/b1-timing.txt) 和 [mi-wait-outcome.json](evidence/mi-wait-outcome.json)。

本次真实执行使用的代码是 `c425826`。运行后发现并修复物理诊断兼容缺陷，最终 `origin/main` 更新到
`9ea39c2077308291569e67fd84c3fa40b7640599`。后一个提交没有用于本次物理结果，也没有再次运行 Habitat；
它的真实 Habitat 验证状态必须保持为未验证。

## Mission Intelligence

最终计划具有以下结构：

- Context `ctx_reach_conditions` 为 `independent`；`relations=[]`、`executor_constraints=[]`，没有
  `shared_view`。
- `task_reach_object` 的 destination 是 `any_targets|0`。
- `task_reach_target` 的 destination 是 `TARGET_any_targets|0`。
- 两个任务均 `depends_on=[]`，各自声明 `space:1`。
- 两个 Task 和两个官方目标均完整保留。

Reviewer 返回 `approved=true, issues=[]`；draft revision 为 1，rejected draft 数为 0，Repairer 次数为
0。完整计划、review history 和 frozen grounding context 在
[b1-request-record.json](evidence/b1-request-record.json)；提交证据在
[b1-request-observations.json](evidence/b1-request-observations.json)。

因此，新加入的确定性 semantic admission 没有自动改写本次计划。模型首次就选择了与权威联合终态相容
的组织结构，然后通过了相同的结构、Catalog、Grounding 和 Review 链。

## Control、Node 与资源分配

Controller 完整归档 324 条事件，最后 sequence 为 324。关键事件如下：

| sequence | 事件 | 结果 |
|---:|---|---|
| 154/155 | `TaskExecutionRegistered` | 两个真实 Task 均注册 |
| 159 | `TaskSchedulingSelected` | object task → `e1-shared-node-a` / `habitat-navigation-slot-a` |
| 161 | `PlanCommitted` | object task 资源提交 |
| 165 | `TaskSchedulingSelected` | target task → `e1-shared-node-b` / `habitat-navigation-slot-b` |
| 167 | `PlanCommitted` | target task 资源提交 |
| 172/173 | `TaskExecutionActivated` | 两个任务均进入执行 |
| 315/316 | Completed / Satisfied | object task 依据 local execution-report 完成 |
| 319 | `TaskExecutionFailed` | target task 失败 |
| 321 | `ExecutionGroupFailed` | Mission execution policy 宣告最终失败 |

完整事件见 [events.json](evidence/events.json)，提取后的核对结构见
[TRACE_ANALYSIS.json](TRACE_ANALYSIS.json) 的 `controller_events`。assignment arrival 进一步证明 node-a
收到 `any_targets|0`，node-b 收到 `TARGET_any_targets|0`，见
[assignment-arrival.jsonl](evidence/assignment-arrival.jsonl)。

资源调度达到了预期：两个 `space:1` 被不同 slot 承诺，没有再次出现两个 execution 被塞入同一 endpoint
的阻塞。没有证据显示 Control 重启、覆盖或重新分配已运行技能。

## Stage2 输入与第一个执行偏离

原始 Stage2 证据同时保存了正确输入和错误首个动作：

- agent0/Spot 收到 `Move a robot to the semantic object any_targets|0.`，并调用
  `nav_to_obj(any_targets|0)`。
- agent1/Fetch 收到 `Move a robot to the goal receptacle position TARGET_any_targets|0.`，却调用
  `nav_to_obj(any_targets|0)`。

这两个事实在 [stage2-initial-output.log](evidence/stage2-initial-output.log) 第 1、38、43、80 行可直接
复核。错误动作发生在任何物理导航结果之前，因此是本次最早可确认的任务偏离。

Fetch 后续共发生 8 次本地 LLM 调用、6 次 replan。3,000-step 技能段为：

| step | Spot | Fetch |
|---|---|---|
| 1–91 | `nav_to_obj` | `nav_to_obj` |
| 92–1000 | `wait` | `nav_to_obj` |
| 1001–1100 | `wait` | `pick` |
| 1101–2100 | `wait` | `nav_to_obj` |
| 2101–2200 | `wait` | `place` |
| 2201 | `wait` | `reset_arm` |
| 2202–3000 | `wait` | `wait` |

这个序列与仅导航到 `TARGET_any_targets|0` 的任务不相符。1000-step navigation 和 100-step manipulation
边界与当前 EMOS skill budget 配置一致；同时 Fetch 的 finished sensor 和 `oracle_skill_done` 在所有 step
都为 False。因此“budget 触发高层切换”得到很强的间接支持，但本次 skill-state 读取因字段兼容缺陷全
部 unavailable，不能把 `over_max_len=true` 写成直接观测。原始 EMOS 没有持久化后续每次完整 tool-call
body，所以不能进一步证明每次 replan 的文字理由。

## 物理轨迹与官方谓词

reset 初始位置来自 shared-world identity：

- Spot：`[-0.1667808592, 2.3214714527, 3.7685527802]`。
- Fetch：`[4.0562839508, 2.8338050842, -1.6121662855]`。

reset rotation 因初始快照序列化失败而不可用。step 1 rotation 分别为 2.7435 和 1.1779 radians，不能
冒充 reset 时刻 rotation。

Spot 在 step 15–85 之间发生 63 个可见位移 step，轨迹长度约 5.56 m；step 85 后一直保持在
`[-0.4552406669, 2.9140219688, -1.4644842148]`，直到 step 3000。post-step finished sensor 在 step 90
为 True，policy-input finished sensor 在 step 91 为 True，step 92 切到 `wait`。因此 Spot 在本地完成后
没有离开其停止位置。然而官方 `any_at(any_targets|0)` 在包括 step 90 和终态在内的全部 3,000 个 step
均为 False，所以“保持原地”不等于“官方目标已满足”。

Fetch 从 step 24 到 2100 发生 1,044 个可见位移 step，轨迹长度约 6.65 m，净位移约 3.08 m；最后位置
为 `[1.3818612099, 2.8338041306, -3.1388485432]`。它的 Oracle finished sensor 与
`oracle_skill_done` 从未为 True。Fetch 没有满足本地导航完成条件，episode 终止后由 node-b 报告
`Habitat episode terminated before the assigned shared navigation completed`。

两个官方谓词和联合 `pddl_success` 的 true-step count 都是 0。该结论来自 1–3000 连续、无缺号的官方
predicate 采样，而不是由 Node Completed/Failed 反推。完整压缩轨迹见
[diagnostics-steps.jsonl.gz](evidence/diagnostics-steps.jsonl.gz)，核对摘要见
[TRACE_ANALYSIS.json](TRACE_ANALYSIS.json)。

## 失败责任边界

**已确认：**

- MI 生成了覆盖两个目标的合法 independent 计划，Reviewer 批准。
- Control 正确注册、承诺并激活两个任务，分配了两个独占 slot。
- Stage2 收到的 Fetch destination 正确，但首个工具调用使用了另一个实体。
- Fetch 随后执行了任务未要求的 pick/place 序列，最终未完成导航。
- Spot 本地完成与官方谓词为 False 同时成立。
- Habitat 官方联合结果为 False，Mission 因 node-b 终态失败而 Failed。

**强证据推断：**

- Fetch 在 step 1000 和 2100 后切换技能很可能由 1000-step navigation budget 触发，因为 finished
  sensor 始终为 False，切换点与配置及 `SkillPolicy.should_terminate` 的阈值一致。本次没有成功记录内部
  counter，仍不能把它提升为直接观测。

**尚不能确定：**

- Stage2 模型为什么把 `TARGET_any_targets|0` 改成 `any_targets|0`；完整推理和后续 tool-call body 未归档。
- Fetch 的具体 PathFinder 路径、碰撞或跨楼层可达性；本次没有 geodesic/path 和逐 agent 场景接触流。
- Spot 本地 finished sensor 与官方谓词不一致的精确几何阈值差异；初始快照中的 goal entity position
  丢失，不能离线计算目标距离。
- 单个诊断样本不能估计新版 MI 或 Stage2 的成功率，也不能证明 Prompt 是唯一因果因素。

按组件划分，MI/Reviewer 和 Control/Node 完成了各自本轮职责。最早直接偏离属于 Stage2 action
generation。Habitat/skill 层还暴露了 local completion 与 official predicate 不一致，但它不是 Fetch
错误首个目标的原因。诊断层丢失 reset/terminal 完整快照及 skill counters，是独立 evidence defect，
没有改变物理动作或官方结果。

## 诊断完整性与运行后修复

`diagnostics-steps.jsonl` 有完整的 3,000 行、step 1–3000 连续，position、rotation、skills 和两个官方
谓词均可用。action trace 也写入 3,000 行，`records_dropped=0`、`write_failures=0`，写入累计耗时约
0.0469 秒。

以下字段不完整：

- reset 与 terminal 快照因 NumPy `float32` 不能直接 JSON 序列化而降级为 unavailable；
- 3,000 行 action summary 因把 flat joint action 误读成 per-agent matrix 而 unavailable；
- 两个 agent 的 3,000 行 skill state 因当前 `HierarchicalPolicy` 使用 `_skills` 而非
  `defined_skills` 全部 unavailable；
- terminal 快照失败同时使 physical diagnostics 自身 capture overhead 统计不可用。

提交 `9ea39c2077308291569e67fd84c3fa40b7640599` 已修复这三类兼容问题：转换 array-library 标量、读取
当前 index-keyed `_skills`、并对 flat action 记录明确的 joint-scope 摘要。修复保持 diagnostics 默认关闭，
没有增加 `actor.act()` 或 `gym_env.step()`，不改变 Stage2、动作、RNG 或官方判定。targeted tests 和全量
Python 门禁通过。由于本轮禁止重跑，这个修复尚未获得真实 Habitat 再验证。

实际质量检查：diagnostics/shared-world targeted 33 tests 通过；全量 Python 836 tests 通过；Ruff
format/check、仓库规定的 strict mypy scope、Python function-doc check 和 `git diff --check` 均通过。

## Provenance 与 admission

[b1-verdict.json](evidence/b1-verdict.json) 给出：

- `protocol_provenance=VALID`；
- `provenance_valid=true`；
- `valid_for_formal_population=true`；
- `valid_for_benchmark_population=true`；
- `benchmark_outcome=BENCHMARK_FALSE`；
- `system_outcome=FAILURE`，failure owner 为 `SUT_SYSTEM`。

Controller event archive 状态为 complete，324 条事件、最后 sequence 324、两次 terminal tail confirmation。
诊断快照不完整没有被伪装成 provenance invalid，也没有改变 Habitat 官方 benchmark authority。

## 下一步建议

当前已经不存在“完整 B1 路径跑不起来”的 blocker；这次链路从 MI 到 Habitat 跑完并产生了有效
benchmark False。下一步应集中在 Stage2 对 canonical execution intent 的忠实度，而不是继续修改 MI
协作 Prompt。

建议先做一个小的、确定性的 Local EAIOS contract-adherence 层：完整归档每次 Stage2 tool call，并验证
工具名称和实体参数是否属于当前 canonical operation。发现 destination 改写或无授权 manipulation 时，
应记录明确的 local execution contract failure，不能继续执行错误动作，也不能由适配器偷偷替模型改成
正确动作。该层应先离线测试，不需要调用 Provider 或 Habitat。

这一步能把“模型选错动作”和“导航器执行失败”分开，也能避免再花 3,000 steps 才看到任务早已偏离。
如果未来要加入模型自纠重试，需要先作为 Stage2 policy 变更单独设计和登记，因为它会改变 benchmark
arm，不能作为诊断修复顺带引入。

在完成 tool-call 归档和 contract guard 后，可做一次预注册的受控诊断来验证证据链。若目标是推进 E1
测量，本次已是有效样本；不应继续反复运行 Episode51 直到出现成功，而应进入预先冻结的 paired harness
与公平性流程。

## 证据索引

- 运行 Manifest：[RUN_MANIFEST.json](RUN_MANIFEST.json)
- 机器可读轨迹分析：[TRACE_ANALYSIS.json](TRACE_ANALYSIS.json)
- 最终计划与 Review：[b1-request-record.json](evidence/b1-request-record.json)
- Controller events：[events.json](evidence/events.json)
- Stage2 初始输入/输出：[stage2-initial-output.log](evidence/stage2-initial-output.log)
- Shared-world 终态摘要：[shared-world-summary.json](evidence/shared-world-summary.json)
- 连续物理轨迹：[diagnostics-steps.jsonl.gz](evidence/diagnostics-steps.jsonl.gz)
- B1 判定：[b1-verdict.json](evidence/b1-verdict.json)
- 文件摘要：[SHA256SUMS](SHA256SUMS)
