# ADR-0015：Runtime Execution Boundary

- 状态：Accepted for Phase 1
- 日期：2026-08-26

## Context

Phase 1 已建立 Mission-level `ExecutionGroup` 和 Group 内 `TaskExecution`，但 live execution
identity、远端状态归约和 checkpoint/resume 实际保存在 `IntegrationRuntimeBridge`，而
`core/runtime` 只封装同步 NodeGateway。与此同时，Control 的 Group projection 同时保存
committed binding 与 Task lifecycle，导致 Integration、Runtime 和 Control 的职责边界不清。

## Decision

RoboGuide Runtime 是持续驱动已经 Commit 的分布式具身执行运行下去的执行环境，不是
Adapter 或协议转换层。

- Control 持有 committed Group configuration、reservation、binding、release、rebind 和
  recovery decision authority。
- Runtime 持有 live `ExecutionContext`、稳定 execution identity、Node ownership、事件序列、
  Task runtime lifecycle、取消和 checkpoint/resume 状态。
- Integration 持有 Node Protocol、transport、session、router 和 wire/domain conversion，
  并将 execution facts 交给 Runtime 归约。
- State & Memory 保存 observation、execution fact、evidence 和 projection，不主动推进执行。
- Node Service / Adapter 持有节点侧 durable execution continuity，并把 canonical intent 映射为
  Local EAIOS How；它不拥有 Mission/Group lifecycle 或 replacement decision。

Phase 1 采用渐进迁移：`ExecutionGroup` 和 `TaskExecution` 的 Control projection 暂时保留，
新增 Runtime-owned live execution registry。Integration bridge 作为 transport facade 委托
Runtime，并且不保留第二套 live execution maps。Runtime 产生 Task activation/terminal outcome，Control 仅验证并更新 committed
projection，Mission Orchestration 继续根据完整 DAG 判断 Mission completion。

`CoordinationContext` 继续属于 Mission Intelligence。Runtime `ExecutionContext` 是另一种对象，
通过 Group/Task/Role identity 与前者关联，但不表达 Actor/ContextRole 语义。

Reconciliation 分为两段：Runtime 检测 timeout、unknown execution、session loss 或 ambiguous
outcome 并 fence execution；Control 读取 State/evidence 后进行 assessment、replacement
proposal、commit 和 rebind。Runtime 不选择 replacement，State 不触发 recovery。

## Consequences

- `core/runtime` 可以在不依赖 Control 或具体 transport 的情况下测试 execution lifecycle、
  identity conflict、event ordering 和 checkpoint invariants。
- `core/integration` 不再直接拥有 live execution maps，但保留现有 transport facade API。
- Control projection 与 Runtime live state 在 Phase 1 会并存；其同步通过显式 transition 完成，
  后续可再拆分 `TaskExecutionSpec` 与 `TaskExecutionRuntime`。
- 本 ADR 不授权 Runtime 执行 Matching、Scheduling、Reservation、Commit、Rebind 或 Mission
  completion inference。
- Phase 1 当前实现 execution identity、ordered fact reduction、checkpoint fencing、
  recovery-required evidence 与 lifecycle transition；timer、Runtime-owned cancellation state
  和显式 resume protocol 仍是后续 Runtime slice，不得描述为当前已完成能力。
