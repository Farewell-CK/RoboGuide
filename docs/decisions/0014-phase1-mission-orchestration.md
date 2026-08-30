# ADR-0014：Phase 1 Mission Orchestration Boundary

- 状态：Accepted for Phase 1
- 日期：2026-08-26

## Decision

Phase 1 以跨进程软件 MVP 为交付边界。Mission Intelligence 通过
`roboguide.mission-plan/v0.2` 提交确定性 MissionPlan；v0.2 必须包含 Context、ContextRole、
完整 Task DAG、Canonical ExecutionIntent，以及 Task/Context resource scope。

Mission 进入执行阶段时，`core/orchestration` 创建一个默认 Mission-level Execution Group，
并从完整 DAG 一次性建立所有 Pending TaskExecutions。依赖满足时 Orchestrator 将 Task 标为
Ready，再驱动 Control 的 Match -> Schedule -> Propose -> Commit -> Bind。Runtime 在 committed
execution 被 Node 接受或开始后驱动 TaskExecution 进入 Active。
TaskExecution 的绑定写回同一个 Group；Task 切换不会创建或销毁 Group。

Runtime 承接 committed execution、持续写入 observation/execution fact，并产出 Task lifecycle
transition；Integration 只负责协议与传输。Mission 完成必须由 Orchestrator 基于完整 MissionPlan 明确判断；
Runtime 不得通过“当前注册 Task 全部完成”推断 Mission 完成。只有显式完成、最终失败或取消后，
Group 才进入 Completed/Failed -> Released。

Control reservation 增加显式 `AllocationOwner`：Task-scoped 资源由 TaskExecution 持有，
Context-scoped 资源由 `(Mission, Context, ContextRole)` 持有，并在 Group `context_bindings`
中独立保存。Allocation State 只是这一权威的可观测投影。

Integration Server 提供 HTTP `POST/GET /v1/missions` 和 `POST /v1/missions/{id}/cancel`，
并将 Orchestrator 与 Integration checkpoint 原子保存。该决策首次采用外层 v7 与内层 v6；
ADR-0019 因 exact-contract readiness 将当前版本分别升级为外层 v8 与内层 v7。
恢复时必须在接受流量前交叉验证 Orchestrator 的完整 MissionPlan、Mission lifecycle 与
Control 中对应 Group 的 MissionId、TaskExecution DAG、continuity metadata 和 lifecycle；
两个独立恢复投影只要有一处不一致就 fail-closed。

## Consequences

- Phase 1 不依赖 LLM、数据库集群、仿真器或真机，可用固定 fixture 和 fake node 完成离线验收。
- Context continuity、partial release、rebind 和恢复状态可以跨多个 Task 进行验证。
- v0.1 MissionPlan 和 v4 以前的 controller checkpoint 不再自动兼容；旧数据必须显式迁移或清理。
- 未来复杂 Mission 可以拆分多个 Group，但这不是 v0.x 默认模型的反向约束。
