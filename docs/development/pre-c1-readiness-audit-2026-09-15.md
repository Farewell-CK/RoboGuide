# Pre-C1 Readiness Audit

> 状态：Point-in-time audit；记录日期：2026-09-15；审计基线：
> `main@0e3e5c6`。本文不改变架构、合同、authority 或生命周期语义。

## 1. 结论

RoboGuide 已具备开始 Habitat Local EAIOS Bridge 实现的生产基础，但准入范围是有边界的：

- 可以接入一个使用现有 canonical operation、即时调度且以 `execution-report` 为合法
  satisfaction basis 的 Habitat execution；
- 必须继续经过真实 Controller HTTP admission、Match、Schedule、Proposal、Commit、Bind、
  Node Protocol、`roboguide-node` 和 Local Integration Engine；
- 不能据此宣称长任务中断恢复、generic verifier evidence、Actor 跨 Node migration、完整
  future scheduling 或跨节点 relation/peer-channel 已经闭环。

C0 Control Dry-run 可以按已有证据关闭。C1 Bridge 可以开始实现，但真实长任务的终态取证
需要单独验收，不能从 C0 的即时完成 smoke 推定。

## 2. 已验证的生产路径

| 环节 | 当前结论 | 证据边界 |
| --- | --- | --- |
| MissionPlan v0.7 到 Controller HTTP | 已贯通 | 真实 HTTP admission、字段保留和非法输入拒绝 |
| Match 到 Schedule 到 Proposal 到 Commit 到 Bind | 已贯通 | Core deterministic tests 与生产事件链 |
| Capability Profile 与 Operation Support | 已进入 Control feasibility | Operation Binding 仍为 Node-private Local How |
| ExecutionIntent 到 Local Integration Engine | 已贯通 | exact operation、objective、parameters 和 identity 到达本地 workflow |
| Node terminal fact 到 Mission completion | 已贯通但有条件 | 已验证场景使用 `execution-report` satisfaction basis |
| 同 Mission 双 Node 独立并行 Task | C0C-B1 已关闭 | 修后两次 fresh production smoke 均单次 dispatch、完整完成且 controller 存活 |

C0C-B1 的修复语义为：同进程 replay 不把仍在 `Dispatching` 的 journal row 投影成
`Unknown`；Blocked Group 不消费迟到 terminal outcome；正常 Ready-Task 路径不绕过 recovery
重新绑定。修后证据位于
`evaluation/control_dry_run/c0c/postfix/`，原始失败证据保留在相邻 pre-fix 路径。

## 3. C1 前必须保留的风险边界

### 3.1 Local status polling 中断后不会在同一进程自动恢复

Local Integration Engine 的 status workflow 一次失败或状态映射失败后会记录
`ReconciliationRequired / Unknown` 并结束该 execution 的 polling task。`NodeService::run()`
只在进程启动时调用一次 `recover()`；Node Protocol session 重连只重放快照，不会重新启动
已经退出的 polling task。

因此可能发生：Local EAIOS execution 继续运行并最终终止，但 RoboGuide 不再主动获取该终态。
记录物理结果不确定性是正确的；缺口是后续如何恢复 status acquisition。C1 长任务验收必须
覆盖 `Running -> transient status failure -> terminal fact`，不能通过自动重发 Execute、伪造
Offline/Completed 或放宽 Blocked lifecycle 规避。

### 3.2 Verifier satisfaction 只有内部消费 API，没有生产 ingress

Runtime completion 与 Task satisfaction 已经分离。生产 outcome handler 会自动应用
`execution-report`；`MissionOrchestrator::satisfy_task_from_verifier()` 目前没有 application ingress，
只有 deterministic test 调用。

因此一个选择 `verifier-evidence` basis 的 Task 可以完成 Local execution，但会继续停在
`AwaitingSatisfaction`。ADR-0039 的 freshness policy 解决 Planner、Reviewer、Repairer 对 bound
来源的一致性，不代表 generic evidence 已能进入生产链。C1 首个 Bridge 应选择语义上确实允许
`execution-report` 的 operation；不得把需要独立验证的 Task 降级来绕过此限制。

### 3.3 Actor continuity 不等于 Actor migration

带 Actor 的 recovery matching 必须服从已有 `ActorBinding`，同时排除故障 Node。若 Actor 的
authority Node 就是故障 Node，candidate set 合法地为空；Recovery Commit 也拒绝未经显式
Actor rebind authority 的 replacement。

这是 ADR-0007 的既有边界，不是应当隐式解除的过滤。C1 可以验证同一 Actor 在既有 Node 上的
连续执行，但不能宣称故障后会自动迁移到另一物理 Node。

### 3.4 Future timing 已保留，但 production duration evidence 未接入

C0-C2 证明未来时间约束没有在 HTTP 到 Scheduler 路径中丢失，也没有提前 dispatch。该场景因
缺少 duration evidence 而进入 durable deferral，并未证明到点形成有界 reservation 后激活。
`prepare_task_with_duration_estimate()` 已存在，production application 当前仍调用无 estimate 的
入口。

这不阻塞即时 C1 execution，但 deadline、candidate-specific duration 和 future activation 需要
后续独立证据。

### 3.5 独立并行不证明 cross-node coordination data plane

C0C-B1 的两个 Task 彼此独立，`relations` 与 `peer_channels` 均为空。它没有验证 shared spatial
reference、peer readiness、地图传递或 relation recovery。C1 若只做单节点 Habitat operation，
不应扩大范围；若使用跨 Node relation，则必须增加对应真实验收。

### 3.6 Mission Front-half 可用于集成，但不是全量稳定结论

最新 Mission Front-half R2 evidence 为 14/20 invariant pass。`a5`、`c1` 出现了隔离的模型结构
输出失败，`m2`、`m4` 受 provider 502 影响；该轮 Repairer 调用数为零，不能据此证明真实 Repair
路径已经回归完成。Evaluation ceiling 为 300 秒，而 production timeout 为 90 秒；两者结果
必须分开解释。

这些事实不阻塞使用一份经过 canonical validation 的冻结 MissionPlan 做 C1，但不支持声称任意
自然语言输入都能稳定产生可执行计划。

## 4. Habitat Local EAIOS Bridge 最小验收合同

首个 C1 Bridge 应保持现有 Node Protocol v0.4、Node Contract v0.6、Node Config v0.7，并验证：

1. 接收完整 canonical `ExecutionIntent`，包括 objective、operation、typed parameters 和
   Mission/Task/Group/Role identity；
2. 将 canonical intent 映射到 Habitat/CrabAgent Local How，Core 不出现 benchmark-specific
   primitive；
3. Execute 快速返回可持久查询的 local handle，随后真实经历 Accepted/Running/terminal；
4. cancel acknowledgement 仅代表取消请求已提交，只有本地状态真正 terminal 后才上报
   Cancelled；
5. 重复 command、Node session reconnect 和 status failure 不得重复物理执行；
6. Local workflow completion 与 Habitat episode/task success 分开记录，不把函数返回自动当作
   物理目标满足；
7. 实验 runner 必须驱动真实 Controller/Node/Runtime 路径，不能直接调用 Habitat skill 绕过
   RoboGuide。

## 5. 建议的 C1 准入顺序

1. 实现单 Node、单 Task、即时执行、`execution-report` basis 的 Bridge happy path；
2. 验证真实 Running 生命周期和一次 terminal fact；
3. 注入重复 command、session reconnect、cancel race 和 transient status failure；
4. 根据第 3 步证据修复 status acquisition/recovery，再宣称长任务闭环；
5. verifier ingress、Actor migration、future duration evidence 和 cross-node relation 分别进入
   后续独立 slice，不与首个 Bridge 一次实现。

## 6. 本次审计证据与限制

本次审计直接阅读当前生产代码、相关 ADR、Node Contract、C0-A/B/C 报告、C0C-B1 两次修后
复验 artifact 和 Mission Front-half R2 evidence，并重新运行离线质量门禁。审计没有运行真实
模型、Habitat、CrabAgent、真实机器人或新的 production HTTP smoke。

审计时重新执行：

- `cargo test --workspace`：通过；
- `uv run pytest -q`：通过；
- `cargo fmt --all -- --check`：通过；
- `cargo clippy --workspace --all-targets --all-features -- -D warnings`：通过；
- `git diff --check`：通过。

修复提交对应的保存日志记录 431 个 Rust tests；当前 Python suite 通过。历史 smoke 的通过范围
以其冻结脚本、原始 response、event、journal 和 verdict 为准，不把文档结论本身当作新的执行
证据。
