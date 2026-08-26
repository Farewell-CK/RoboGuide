# ADR-0013：Mission-level Execution Group 与 Task Execution

- 状态：Accepted for the v0.x execution model
- 日期：2026-08-25

## Decision

RoboGuide 的 `ExecutionGroup` 是 Control/Runtime 持有的 Mission-level distributed execution
context，不是一个 Task 的临时容器。v0.x 默认一个 Mission 创建一个长期 Group，Group 内可以
连续或并发运行多个 Task。该默认创建策略不成为未来禁止一个 Mission 拆分多个 Group 的领域
不变量。

Mission Intelligence 的 Context 只描述跨 Task 的语义连续性、Actor 和 ContextRole 关系。它
不拥有 Node assignment、Reservation、Commit 或真实恢复状态。Task DAG 保留依赖、Task-local
requirements 和 ExecutionIntent，并通过 `context_id` 指向规划 Context。

每个 Task 在 Group 内是独立 `TaskExecution`。Task 完成只释放自己的临时 Node/Role、Compute、
Space 和 Time bindings/reservations；不会销毁 Group。显式 context-scoped 的持续资源在
Context 结束时释放。只有 Mission 完成或最终失败后，Group 才进入 Completed/Failed，然后
Released。

节点故障在同一 Group 内执行 partial release、rebind 和 `Adapted -> Active`。Recovery 必须
区分 ContextRole 级连续绑定与 Task-local role execution，且不得让一个 Task 的失败释放
不相关 Task 或 Context 的 ownership。

## Consequences

- Group lifecycle 与 Task lifecycle 分离，Task 切换不再创建和销毁 Group。
- Allocation projection 必须能区分 Task-scoped 与 context-scoped ownership。
- Runtime execution identity 至少包含 Group、Task 和 role，避免不同 Task 的同名 role 冲突。
- v0.x 的单 Group 策略可以在未来演化为多个 Group，而无需重新定义 Task DAG。
- Mission-level Group/TaskExecution 事件使用新的 `domain.EventPayload.json/v2`；Phase 1 的
  外层 Controller checkpoint 使用 `roboguide.controller-checkpoint/v5`，旧投影不得静默恢复。
