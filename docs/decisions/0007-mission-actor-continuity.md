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

### 2026-08-26 补充：Deployment Placement Constraint

Mission Actor 仍是逻辑身份，不是物理节点选择器。部署或实验需要预先指定物理节点时，
使用独立的 Control-owned `(MissionId, ActorId) -> NodeId` placement constraint。该约束：

- 不进入 MissionPlan，不改变 Context/ContextRole 语义；
- 在 Actor 首次绑定前把 Candidate Set 收窄到指定节点，但仍检查当前 State eligibility 和
  整张 MissionPlan 的 actor requirements；
- 不创建 reservation、commitment 或 ActorBinding；只有正常 Proposal -> Commit -> Group
  Bind 成功后才产生 authoritative binding；
- 在 Group Bind 再次校验，防止调用方绕过 mission-aware matching；
- 进入 Control checkpoint，恢复时与既有 ActorBinding 冲突则 fail-closed。

未配置 placement 时，继续使用原有 Matching + Scheduler policy。该机制不是新的 Scheduler，
也不允许 Runtime、Integration 或 State 选择物理 Actor 实现。

### 2026-08-26 补充：Recovery 不隐式迁移 Actor

当前 v0 的 Recovery Rebind 不拥有 Actor migration authority。带 Actor 的 Role 在 recovery
matching 时必须服从已有 `ActorBinding`，尚未绑定时服从 deployment placement；Commit
再次校验相同 authority。若权威 Node 本身就是不可用节点，Candidate Set 为空，Group 保持
Blocked/RecoveryPending，不得把该逻辑 Actor 静默换绑到另一物理 Node。

未来若要支持 Actor migration，必须定义显式的 Control `ActorRebind` decision、审计事件、
与旧 binding/placement 的原子更新和失败语义，并单独形成 ADR；普通 Role Rebind 不能承担
这一语义。

## Contract 版本

本轮没有改变 MissionPlan v0.1 的 wire shape 或字段语义，只把既有 actor/contract
字段接入 Control 执行语义，因此不允许继续进行未记录的 v0.1 breaking evolution，
继续使用 `roboguide.mission-plan/v0.1`。任何后续跨语言字段或语义破坏必须新建版本
（例如 v0.2）并保留旧合同。

2026-08-26 的 placement 补充同样不改变当前 `roboguide.mission-plan/v0.2`：物理部署
关系由独立、版本化的 composition 配置提供，不能反向塞入 Mission Intelligence 合同。

2026-09-01：ADR-0020 将当前合同升级为 `roboguide.mission-plan/v0.3`，新增的 Execution
Relation 仍只引用逻辑 Task/Role，并未把 Actor 或 placement 变成物理 binding authority。

## 后果

首次选择可能因未来 Task 的要求而排除当前 Task 看似可行的节点；这是连续 Actor
身份的必要代价。恢复仍由既有 reconciliation pipeline 负责，Actor continuity 不
引入第二套 Scheduler。权威 Actor Node 不可恢复时，当前 v0 会明确停在 Blocked，而不是
牺牲身份连续性换取自动迁移。
