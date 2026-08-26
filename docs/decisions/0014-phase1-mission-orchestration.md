# ADR-0014：Phase 1 Mission Orchestration Boundary

- 状态：Accepted for Phase 1
- 日期：2026-08-26

## Decision

Phase 1 以跨进程软件 MVP 为交付边界。Mission Intelligence 通过
`roboguide.mission-plan/v0.2` 提交确定性 MissionPlan；v0.2 必须包含 Context、ContextRole、
完整 Task DAG、Canonical ExecutionIntent，以及 Task/Context resource scope。

Mission 进入执行阶段时，`core/orchestration` 创建一个默认 Mission-level Execution Group，
并从完整 DAG 一次性建立所有 Pending TaskExecutions。依赖满足时 Orchestrator 将 Task 标为
Ready，再驱动 Control 的 Match -> Schedule -> Propose -> Commit -> Bind -> Activate。
TaskExecution 的绑定写回同一个 Group；Task 切换不会创建或销毁 Group。

Runtime/Integration 只承接 committed execution、持续写入 observation/execution fact，并产出
只读 terminal Task outcome。Mission 完成必须由 Orchestrator 基于完整 MissionPlan 明确判断；
Runtime 不得通过“当前注册 Task 全部完成”推断 Mission 完成。只有显式完成、最终失败或取消后，
Group 才进入 Completed/Failed -> Released。

Control reservation 增加显式 `AllocationOwner`：Task-scoped 资源由 TaskExecution 持有，
Context-scoped 资源由 `(Mission, Context, ContextRole)` 持有，并在 Group `context_bindings`
中独立保存。Allocation State 只是这一权威的可观测投影。

Integration Server 提供 HTTP `POST/GET /v1/missions` 和 `POST /v1/missions/{id}/cancel`，
并将 Orchestrator 与 Integration checkpoint 以 `roboguide.controller-checkpoint/v4` 原子保存。

## Consequences

- Phase 1 不依赖 LLM、数据库集群、仿真器或真机，可用固定 fixture 和 fake node 完成离线验收。
- Context continuity、partial release、rebind 和恢复状态可以跨多个 Task 进行验证。
- v0.1 MissionPlan 和 v3 controller checkpoint 不再自动兼容；旧数据必须显式迁移或清理。
- 未来复杂 Mission 可以拆分多个 Group，但这不是 v0.x 默认模型的反向约束。
