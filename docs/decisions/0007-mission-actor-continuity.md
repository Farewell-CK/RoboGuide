# ADR-0007：Mission Actor Continuity 闭环

## 状态

已接受（2026-08-24）。

## 决策

MissionPlan 中同一 `actor` 的所有 Task role capability 与
`CapabilityContractRef` 是一次首次选择的整体约束。Control matching 将整张
MissionPlan 的 `actor_requirements()` 应用于未绑定 Actor 的 Candidate Set，随后
继续使用既有 `DeterministicBootstrapScheduler` 完成选择。

Actor Binding 是 Mission/Actor 作用域的 Control authority。它只在对应 Task 的
Proposal 成功、Commit 成功且 Group Bind 成功之后写入，并产生
`MissionActorBound` 审计事件。Binding 不是长期 Resource Reservation；后续 Task
只能使用该 Node。若该 Node 不再满足当前 role 的 eligibility，matching 返回需要
reconciliation 的明确错误，不得静默换绑。

绑定按 MissionId 隔离，同名 Actor 不跨 Mission 共享。失败的 Proposal、Commit 或
Group Bind 不产生 binding。

## Contract 版本

本轮没有改变 MissionPlan v0.1 的 wire shape 或字段语义，只把既有 actor/contract
字段接入 Control 执行语义，因此不允许继续进行未记录的 v0.1 breaking evolution，
继续使用 `roboguide.mission-plan/v0.1`。任何后续跨语言字段或语义破坏必须新建版本
（例如 v0.2）并保留旧合同。

## 后果

首次选择可能因未来 Task 的要求而排除当前 Task 看似可行的节点；这是连续 Actor
身份的必要代价。恢复仍由既有 reconciliation pipeline 负责，Actor continuity 不
引入第二套 Scheduler。
