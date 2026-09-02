# ADR-0020：Execution Coordination Relations

- 状态：Accepted for v0.1
- 日期：2026-09-01

## Context

RoboGuide 已能用 Task DAG 表达完成前置关系，用 Control binding 表达 Role 到 Node/Resource 的
当前承诺，并用 Runtime execution status 表达单个远端执行的生命周期。这些对象不能表达一个
正在运行的 Role 持续依赖另一个正在运行的 Role，例如 safety observation 必须在 navigation
执行期间保持有效。

把该语义塞入 Task dependency 会强迫 source Task 先完成，失去并发约束；把关系绑定到 NodeId
会在 rebind 后失效；让 Integration 或 State 推断关系则会产生新的 execution authority。

## Decision

### Specification 与稳定端点

MissionPlan v0.3 在 `CoordinationContext` 中声明有向 `ExecutionRelationSpec`。v0.1 端点是
Mission 内的 `(TaskId, RoleId)` 逻辑执行槽，接受后由 Group/Mission identity 完整限定。端点
不包含 NodeId、ResourceId、transport session、central execution string 或 adapter-local handle。

ContextRole 继续表达 Actor 的跨 Task 语义连续性；它不是一个精确 live execution endpoint。
第一版使用 Task/Role 是因为同一 ContextRole 在未来可能同时对应多个 active Task，而关系必须
无歧义地解析到一个当前 logical execution。后续若引入 ContextRole-level fan-out，必须另行定义
cardinality 和 conflict 语义。

关系 specification 由 Mission Intelligence 生成，由 Orchestration 随完整 MissionPlan 持久化。
Control 不修改 specification；它继续独占 reservation、binding、rebind 和 recovery decision。

### v0.1 relation contract

v0.1 只支持 `requires-active`：当 target 的当前 attempt 为 Accepted/Running 时，source 的当前
attempt 必须为 Accepted/Running。关系两端必须属于同一 CoordinationContext，并且必须是同一
Task 或 Task DAG 中互无前置路径的两个 Task；有 DAG 前置路径的 Task 不可能同时 active，计划
在接受时直接拒绝。

Runtime 为每个 accepted relation 维护以下 live state：

- `Dormant`：target 尚未 active 或关系当前没有运行窗口；
- `Pending`：target 已 active，但 source 尚未产生可证明 active 的事实；
- `Satisfied`：source 与 target 当前均 Accepted/Running；
- `Violated`：target active，而 source 已 Completed/Failed/Cancelled；
- `Unknown`：target active，而 source 的物理状态不明。

`Pending` 用于吸收正常 dispatch/acceptance 竞态，不立即触发恢复。`Violated` 和 `Unknown` 会
产生持久化 `coordination required` evidence，并设置 reconciliation fence。关系重新变为
`Satisfied` 只代表当前端点事实恢复，不能自动清除已经观察到的违例；必须由 Control/应用
恢复流程显式确认 reconciliation（Runtime `acknowledge_relation_reconciliation`）后才解除
fence。`Dormant` 同样不得悄悄清除已经观察到的违例。

### Runtime、progression 与 recovery

Runtime 拥有 live relation registry、当前 attempt resolution、状态归约、去重事件和 checkpoint；
它没有 Matching、Scheduling、Commit、Rebind、Mission completion 或物理安全 authority。
Integration 只持有 facade 和 evidence conversion，不保存第二份 relation map。

存在未清除的 relation fence 时，Runtime 不向 Orchestration报告 target Task 成功。State Plane
的 durable Event Log 保存 relation registration、state transition 和 coordination-required
evidence；v0.1 不建立第二份 live State projection。v0.1 不自动把
relation violation 转成 Node failure，也不自动 release binding：

- source execution 自身 Unknown 或 Node unavailable 时，继续使用既有 Runtime
  `RecoveryRequired` 与 Control Assess -> Partial Release -> Match -> Propose -> Commit -> Rebind；
- source terminal failure 继续使用既有 Task/Mission policy；
- 仅有 relation violation、但物理执行事实仍明确时，Group 保持可检查的 coordination-required
  状态，等待应用/Control policy，而不是错误地替换健康节点。

该响应属于 supervisory coordination。硬实时 hazard 制动、接触丢失后的本地 stop 和最终安全
仍由 Local EAIOS 实现；RoboGuide v0.1 不宣称远程 pause 已经物理生效。

### Restart、rebind 与 identity

Runtime checkpoint 同时保存 relation specs、last live state 和 relation fence。恢复时所有依赖
非终态 attempt 的状态保守重算；无法证明 source active 时为 `Unknown`，不能沿用崩溃前的
`Satisfied`。Orchestration plan、Runtime relation registry 与 Control Group 在接受流量前必须
交叉验证，不一致即 fail closed。

`RuntimeExecutionManager.active_executions[(GroupId, TaskRef, RoleId)]` 是当前已有的 logical slot
到 current execution string 映射。新 attempt/rebind 更新该映射后，relation 自动解析新 attempt，
旧 attempt 的迟到 fact 不能改变当前 relation。当前 central `execution_id` 同时承担 logical dispatch
correlation 与 physical attempt identity，完整 supersede/history 仍是 RT-G3；v0.1 relation 不把
该字符串提升为稳定关系端点，也不声称 RT-G3 已关闭。

### Versioning

- Mission contract 新增 `roboguide.mission-plan/v0.3`；v0.2 只可作为无 relation 的兼容输入。
- Relation evidence 将 event payload codec 升级到 `domain.EventPayload.json/v5`，读取继续支持
  v2/v3/v4，但新 variants 不得伪装成旧 marker。
- Integration checkpoint 升级为 v8，外层 Controller checkpoint 升级为 v9。旧 checkpoint
  不得在缺失 relation registry/fence 时静默恢复。

## Consequences

- Task DAG 继续只决定 readiness；Execution Relation 只约束已经并发运行的 logical executions。
- Mission、Control、Runtime、State 和 Integration 的既有 authority 不发生横向复制。
- 第一版能够显式声明、维护、查询、持久化并对 execution-lifecycle cross-device dependency 作出
  progression fence 响应，同时保持 rebind 后的逻辑语义。
- hazard value、距离/速度窗口、双向 feedback、relation priority/composition、distributed clock、
  远程 pause/stop acknowledgement 和通用条件表达式不属于 v0.1；它们不能通过自由字符串或
  adapter-local字段绕过后续合同与安全决策。
