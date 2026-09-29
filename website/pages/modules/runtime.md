# 模块地图：Distributed Runtime

对应 crate：[`core/runtime`](https://github.com/Farewell-CK/RoboGuide/tree/main/core/runtime)
（约 3,200 行产品代码 + 1,400 行测试）。
模块地图首页 · 上一篇：[Orchestration](orchestration.md) · 下一篇：[State & Memory](state.md)

**定位**（lib.rs）：Control 与本地节点适配器之间的运行时执行语义层。持有两个权威：

1. `Runtime<C,E>` —— 本地 `NodeGateway` 适配器注册表，执行 Node Contract 版本检查、
   健康/活性观察与事件证据追加；
2. `RuntimeExecutionManager` —— 传输中立的活执行权威：派发意图、尝试代际、
   协同上下文与 peer channel、执行关系、强定位证据、durable checkpoint。

Runtime **不做** Matching、Scheduling、Reservation、Commit 或替换单元选择——
这些属于 Control；也不判定 Task/Mission 语义满足——那属于 Orchestration。

## 模块地图

| 模块 | 职责 |
| --- | --- |
| `lib.rs` | `Runtime` 适配器注册表：注册、状态观察、命令执行 |
| `clock.rs` | `FixedClock`（测试）与 `SystemMonotonicClock`（真实进程） |
| `execution/` | 活执行上下文：逻辑槽、派发意图、事实归约、checkpoint |
| `execution/dispatch.rs` | 派发校验、attempt 分配、取消请求 |
| `execution/observation.rs` | 执行事实归约与节点不可用观察 |
| `coordination.rs` | 协同上下文与 peer channel 生命周期（readiness 证据与 fencing） |
| `relation/` | 执行关系注册、证据归约、checkpoint 校验 |

## 核心概念

**逻辑槽与物理尝试分离**。`ExecutionSlot = (ExecutionGroupId, TaskRef, RoleId)`
是 Mission 语义的稳定端点；同一槽位在恢复/重派发时由新的物理 attempt 占据，
`attempt_history()` 保留不可变历史。执行关系（ADR-0020）因此从不因 rebind 失效。

**事实归约**。`observe_execution` 有序消费节点执行事实推进 `ExecutionStatus`；
`observe_node_unavailable` 只记录证据。`task_execution_result` 从角色事实导出
终态本地结果——这是"本地执行完成"的事实，不是 Task 满足。

**Unknown 与恢复栅栏**。重启或路由丢失后非终态 attempt 进入 `Unknown`（物理
歧义待恢复），`ExecutionRuntimeError::ReconciliationRequired` 阻止恢复态执行被
直接路由。关系恢复为 `Unknown` 时保持 reconciliation fence；新 attempt 重新占据
同一逻辑槽即可重新解析关系，无需修改规格。

**peer channel**。`PeerChannelReadinessEvidence` 驱动
`Planned → Ready → Fenced → Closed` 生命周期；两端非过期、receive 相对的确认
齐备才 Ready，过期/丢路由/重启都会 fence——等待中的 Task 保持 durable Ready。

## 关键类型

`RuntimeExecutionManager`、`RuntimeExecutionCheckpoint`（create/restore）、
`DispatchIntent`（durable 派发意图，含 `command_id()` 与投递代数）、
`ExecutionStatus` / `ExecutionEvent` / `ExecutionRuntimeError`（`is_terminal()`）、
`ExecutionAttemptSnapshot`、`ObservedTaskExecutionResult`、
`RuntimePeerChannel` / `PeerChannelLifecycle` / `CoordinationReadiness`、
`RuntimeExecutionRelation` / `SharedSpatialEvidence`。

## 测试覆盖

协同生命周期；活执行权威（派发/取消/尝试历史/checkpoint）；执行关系（注册、
证据归约、共享空间证据校验）；健康观察写入 Shared State、网关失败与活性分离、
未知契约版本拒绝。

## 实现状态

- Node Contract 固定在 `NODE_CONTRACT_VERSION_V0_1`（runtime 侧适配器注册拒收其他版本）。
- checkpoint 为 serde 序列化的持久投影；恢复路径 `restore_coordination_maps` /
  `restore_relation_maps` 显式存在，无 TODO 标记。

## 相关 ADR

[0015 Runtime 执行边界](../docs/decisions/0015-runtime-execution-boundary.md) ·
[0020 执行协同关系](../docs/decisions/0020-execution-coordination-relations.md) ·
[0026 执行耦合与组视图](../docs/decisions/0026-execution-coupling-and-group-views.md) ·
[0027 协同证据补全](../docs/decisions/0027-runtime-coordination-evidence-completion.md) ·
[0028 持久命令、恢复与物理尝试](../docs/decisions/0028-durable-command-recovery-and-attempts.md)

---

模块地图：[Domain 与端口](domain-ports.md) · [Control](control.md) ·
[Orchestration](orchestration.md) · [Runtime](runtime.md) ·
[State & Memory](state.md) · [Integration 与 Node](integration-node-service.md) ·
[Mission Intelligence](mission.md) · [Eval 与集成](evaluation.md)
