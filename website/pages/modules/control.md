# 模块地图：Control Plane

对应 crate：[`core/control`](https://github.com/Farewell-CK/RoboGuide/tree/main/core/control)
（约 8,200 行产品代码 + 8,600 行测试；18 个产品模块、123 个测试函数）。
模块地图首页 · 上一篇：[Domain 与端口](domain-ports.md) · 下一篇：[Orchestration](orchestration.md)

**定位**（lib.rs）：Control Plane 门面与组合根。进程内权威 `ControlPlane` 持有：
节点租约权威、唯一资源预约权威、未来任务调度日历、Mission 级 Actor 绑定权威、
带防回滚 provenance 的部署实体注册表、Execution Group 与待定恢复承诺。

## 权威边界

| Control 拥有 | Control 绝不做 |
| --- | --- |
| 资源 Commit/Revoke（唯一预约权威） | 不解释 ExecutionIntent 的 objective/参数 |
| Group 生命周期与 Rebind 决策 | 不做传输（属 integration） |
| 恢复承诺（pending commitment） | 不选择替换单元的"人选"（消费外部提案） |
| 调度日历与版本化快照 | 不持有 State 真相（Allocation 只是投影） |
| Actor/物理实体绑定持久化 | 不代替 Runtime 归约执行事实 |

## 模块地图

| 模块 | 职责 |
| --- | --- |
| `lib.rs` | `ControlPlane` 门面、`ControlCheckpoint`、`ControlError` 全部拒绝原因 |
| `coordination.rs` | 共享资源协调、预约权威、normal Commit |
| `matching.rs` | 能力候选集模型与匹配策略 |
| `proposal.rs` | 分配提案模型与校验边界 |
| `calendar.rs` | 未来预约日历与版本化调度快照 |
| `allocation.rs` | 权威预约 → 可观察 Allocation View 投影 |
| `node.rs` | 节点准入、心跳/租约、共享 eligibility 谓词 |
| `registry_provenance.rs` | 无路由表的持久防回滚证据水位 |
| `group/`（4 文件） | Group 生命周期、TaskExecution/Context 绑定、Mission 绑定、checkpoint 校验 |
| `reconciliation/`（3 文件） | 指派节点失联评估（只检测分歧，从不选节点）与恢复管道 |
| `scheduler/` | 有界联合调度策略（纯选择，不校验/不提交/不变更） |

## 主流程 API

**正常路径**（Mission-level Group 前半程）：

```text
create_mission_group          从完整 DAG 创建默认 Mission Group 与全部 TaskExecution
ready_task_execution          DAG 依赖满足后置 Ready
match_capabilities_for_mission → CandidateSet（首用 Actor 约束到整计划可用节点）
BoundedJointScheduler::schedule_task → SelectedNow | SelectedFuture | Deferred | WindowMissed
propose                       → AssignmentProposal（校验选择，零资源副作用）
commit_for_group_with_state   → CommittedPlan（重验每个 ResourceId 当前身份后原子提交）
bind_task_execution           写入既有 Ready TaskExecution 并记录 Actor 连续性
activate_task_execution → record_task_execution_completed → satisfy_task_execution
release_task_bindings / release_context_bindings / complete_group
```

**恢复路径**（L2，五步显式管道）：

```text
assess_group                  → NoAction | RoleRecoveryRequired（对比 Shared Node State）
begin_role_recovery           阻塞 Group，仅部分释放受影响角色（→ RecoveryOutcome::Pending）
match_recovery_candidates     只匹配未绑定角色 → RecoveryCandidateSet
propose_role_recovery         校验外部选择（不预约、不绑定）→ RecoveryAssignmentProposal
commit_role_recovery          重验后原子提交 → CommittedRecoveryAssignment（可 abort）
rebind_role                   消费已提交资源 → Recovered（Group 进入 Adapted）
```

## 关键类型

`ControlPlane` / `ControlCheckpoint` / `ControlError`（枚举每种拒绝：
`NoCandidate`、`ResourceConflict`、`PendingRecoveryCommitmentExists`…）；
`CandidateSet`、`AssignmentProposal`、`CommittedPlan`；
`ExecutionGroup` + `GroupLifecycle`（`Bound/Active/Adapted/Blocked/Failed/Completed/Released`）；
`ReconciliationAssessment`、`CommittedRecoveryAssignment`、`RecoveryOutcome`；
`BoundedJointScheduler`（无状态、`DEFAULT_SEARCH_EXPANSIONS = 10_000` 有界回溯）；
`TaskSchedulingOutcome`、`SchedulerError::NoFeasibleSelection/SearchLimited`；
`ScheduledTaskReservation` + `SchedulingReservationPhase`（`Scheduled/Activated/Invalidated`）。

## 测试覆盖（15 个测试文件的主题）

正常 match→propose→commit 与候选拒绝规则；v0.8 Actor 绑定语义与注册表水位；
Actor 跨 Task 连续性；Allocation 投影相位；Commit 时资源身份重验
（[ADR-0042](../docs/decisions/0042-resource-identity-revalidation.md)）；
Group 生命周期保绑定；Mission 级 Group 创建/绑定流；节点注册/心跳/TTL 拒绝；
操作感知匹配（[ADR-0036](../docs/decisions/0036-operation-support-and-integrated-capability-baseline.md)）；
对账评估与恢复管道；恢复承诺生命周期（单待定/abort/mismatch）；注册表防回滚；
调度确定性选择、有界联合搜索预算、恢复调度共用日历。

## 实现状态

- 对账 slice v0.1 明确只处理**单个**不可用角色（`pipeline.rs` 显式拒绝多角色）。
- MissionPlan v0.8 绑定要求物理实体注册表已安装，否则拒绝。
- 租约字段标注"所有权评审待定"（`lib.rs`）。
- 旧单 Task Group 创建 API `create_group_with_actor_bindings` 已降为 `#[cfg(test)]`，
  新执行必须走 `create_mission_group`。

## 相关 ADR

[0004 恢复承诺生命周期](../docs/decisions/0004-recovery-commitment-lifecycle.md) ·
[0005 Allocation 投影权威](../docs/decisions/0005-allocation-state-projection-authority.md) ·
[0007 Actor 连续性](../docs/decisions/0007-mission-actor-continuity.md) ·
[0012 Controller checkpoint](../docs/decisions/0012-controller-checkpoint-recovery.md) ·
[0013 Mission 级 Execution Group](../docs/decisions/0013-mission-level-execution-group.md) ·
[0029 有界联合调度与未来预约](../docs/decisions/0029-bounded-joint-scheduling-and-future-reservations.md) ·
[0036 操作支持基线](../docs/decisions/0036-operation-support-and-integrated-capability-baseline.md) ·
[0040 Actor 与物理实体绑定](../docs/decisions/0040-mission-actor-binding-semantics.md) ·
[0041 类型化调度 disposition](../docs/decisions/0041-core-scheduling-disposition.md) ·
[0042 Commit 资源身份重验](../docs/decisions/0042-resource-identity-revalidation.md) ·
[0043 注册表防回滚](../docs/decisions/0043-registry-anti-rollback-provenance.md) ·
[0044 部署规划证据](../docs/decisions/0044-deployment-planning-evidence.md)

---

模块地图：[Domain 与端口](domain-ports.md) · [Control](control.md) ·
[Orchestration](orchestration.md) · [Runtime](runtime.md) ·
[State & Memory](state.md) · [Integration 与 Node](integration-node-service.md) ·
[Mission Intelligence](mission.md) · [Eval 与集成](evaluation.md)
