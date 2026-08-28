# ADR-0018：Mission Intent Grounding Loop

- 状态：Accepted for Mission Request v0.1
- 日期：2026-08-28

## Context

现有 `POST /v1/missions` 接受完整 MissionPlan，适合作为 Mission Intelligence 到
Orchestration 的内部合同，却不是用户入口。真实双机器狗实验要求调用者预先提供 Mission、
Task、Context、Role、Actor 和 Artifact 等内部信息，绕过了 RoboGuide 应承担的意图理解、
澄清和任务拆解职责。含糊指令如果直接进入执行，会把模型假设错误提升为物理副作用。

## Decision

新增 Mission Intelligence-owned Mission Request v0.1。外部用户只提交自然语言 instruction；
Mission Service 生成 RequestId/MissionId，持久化 dialogue、解释结果和 MissionPlan 草案。其
生命周期为：

```text
Received -> Interpreting -> NeedsClarification
                         -> Drafted -> Reviewing
                         -> AwaitingApproval -> Submitting -> Accepted
                         -> Blocked / Failed / Cancelled
```

存在 open questions 时不得创建 Execution Group。无歧义且未命中部署配置的
`approval_required_contracts` 时可以自动提交；命中风险策略时，用户必须按当前 draft
revision 和 digest 审批，过期审批被拒绝。

Mission Service 是独立 Python composition root。它使用自己的 SQLite 保存 deliberation
evidence，但不复制 Node、Control、Runtime 或 Mission execution lifecycle。`Accepted` 只表示
现有 Rust Controller 接受完整 MissionPlan；Running/Completed 继续由 Orchestration 查询。

Integration Server 提供只读 `GET /v1/inventory`，返回 Shared Node State 当前注册、reported
health、RoboGuide-observed liveness、canonical contracts 和 resources。该 snapshot 仅用于
规划预检且允许滞后；Control 在 Match/Commit 时仍是唯一资格与资源 authority。

`POST /v1/missions` 保持内部兼容接口。完全相同的 MissionId、MissionPlan 和 GroupId 重复
提交是幂等接受；同 MissionId 的不同计划仍返回 conflict。这使 Mission Service 在响应丢失后
可以安全 retry，而不会产生第二个 Group。

## Consequences

- 用户不再提供 NodeId、ResourceId、TaskId 或 ExecutionGroupId；物理设备身份只来自注册。
- 模型只解释目标并生成 canonical What，不选择 Node、不 Commit 资源、不执行 local How。
- process health 仍不等于 capability readiness；v0 inventory 明确暴露当前证据而不夸大它。
- 动态 MapId/Revision output binding、执行中重规划和自动 recovery 不在本 ADR 范围。
