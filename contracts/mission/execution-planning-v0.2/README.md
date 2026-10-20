# 发布的执行拓扑与草案校验 v0.2

本合同描述 deployment-owned planning evidence 与只读草案反馈，不是 MissionPlan、
Node Inventory、资源承诺或物理成功证明。决策背景见
[ADR-0109](../../../docs/decisions/0109-published-plan-validation-and-bounded-draft-recovery.md)。

## 执行声明

`roboguide.execution-planning-evidence/v0.2` 保留 v0.1 的闭合字段
`authority`、`identity`、`verifier_source`、`execution_lifetime` 和 `digest`，增加必需的
`execution_topology`。整个 JSON 按既有有限、排序键、紧凑 UTF-8 编码计算 SHA256；
新字段参与摘要和请求 provenance，不允许通过重算摘要接受非法结构。

| 字段 | 含义 |
|---|---|
| `supported_coupling_modes` | 非空、排序且唯一的合法 mode 集合；不改变任务真正需要的 mode |
| `supports_task_dependencies` | 是否执行已声明的 DAG 依赖；不允许模型删除真实先后要求 |
| `supports_context_relations` | 是否支持 Context execution relations；不授予虚构或移除 relation 的权力 |
| `actor_binding` | 当前值 `session-stable`；一个逻辑 Actor 的 endpoint 在该执行 session 中稳定 |

[execution-topology.schema.json](execution-topology.schema.json) 定义新对象的闭合结构。
当前 Habitat live producer 从已验证的 `independent-live/v0.1` registry 发布：
`[independent]`、支持 DAG、不支持 Context relations、session-stable Actor binding。
它不向 MI 提供 NodeId、PhysicalEntityId、ResourceId、候选列表或当前资源可用性。

`execution_lifetime` 的 final verifier barrier 和 Control release 条件保持原有含义。
总 Task 数不等于终局保留槽位数；前置动作可以按其自己的完成合同释放资源，再执行
后续阶段。需要完整联合目标的末端 Task 仍使用完整 frozen predicate；普通本地完成
不能替代终局真值。等待终局 verdict 的 Task 若阻塞同 Actor 后续工作或 DAG 依赖，
必须拒绝，不能通过加边或提前释放占用解决。

## 只读反馈

Controller `POST /v1/missions/validate-plan` 返回 `roboguide.plan-validation/v0.2`。
它保留 v0.1 的 exact-plan digest、Mission、source/deployment digests、world identity、
lifetime、decision、issues 及截断标记，并增加 `execution_topology`，无 live 声明时为 null。
每条 issue 仍为闭合的 `{code,path,message}`，路径指向实际 canonical draft。

新增具体 code 包括 `deployment.coupling_mode_unsupported`、
`deployment.context_relations_unsupported`、`deployment.operation_unsupported`、
`deployment.intent_invalid`、`deployment.resource_requirement_invalid`、
`deployment.actor_placement_unavailable`、`deployment.concurrent_actor_reuse`、
`deployment.concurrent_placement_unavailable`、`deployment.object_relocation_conflict`、
`deployment.topology_unsupported`、`deployment.slot_limit_exceeded`、
`verifier.retained_capacity_exceeded` 与 `verifier.retained_placement_unavailable`。
既有 `verifier.blocks_dependent_work`、`verifier.blocks_actor_reuse` 继续有效。
MI 的声明检查还区分 `deployment.dependencies_unsupported`、
`deployment.endpoint_capacity_exceeded`、`deployment.concurrent_capacity_exceeded` 和
`deployment.context_capacity_exceeded`。无法确定更细原因时保留原 generic rejection，
禁止从消息文本猜测重试或恢复策略。

当前实现收集独立的模式/relations 冲突，并保留候选合同检查的第一个具体失败。
这不是穷举所有部署错误；结果至多32条，超出时保留 `issues_truncated`。正常提交和只读
查询复用这些判断；前检不创建 Group，不持久化事件/承诺，也不授权未来提交。
恢复保持既有次数，接收真实原始草案和具体问题，不自动修改 mode、Actors 或目标。

## 兼容性

- 历史 execution evidence v0.1 保持原字段与摘要，不推断其未发布的拓扑能力。
- 历史 plan-validation v0.1 仍可读取；其缺失的 topology 不能满足显式配置的 v0.2 evidence。
- v0.2 声明与 Controller 返回的 topology、lifetime、world/source 必须精确一致。
  缺失、版本不支持、来源变化、HTTP 失败均是配置/系统错误，不消耗模型草案恢复次数。
- 真正需要合作、共享位姿或 handoff 的 Mission 保留需求；部署不支持时明确拒绝，
  不把所有任务强制变为 independent。MissionPlan 与 Core/Node 合同没有变化。

## 离线验收

[fixtures/regression-cases.json](fixtures/regression-cases.json) 是纯合成的中性合同测试，
不是历史模型输出或 benchmark 结果。Python 与 Rust 使用同一计划、变更与预期 code/path，
覆盖 independent 阶段复用、Context/Task mode 不支持、过多终局占用与终局前置循环。
其通过仅证明结构/部署合同一致，不证明真实模型遵循率或物理可见性。
