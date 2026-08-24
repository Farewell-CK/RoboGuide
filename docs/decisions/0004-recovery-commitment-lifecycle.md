# ADR-0004：Recovery Commitment Lifecycle v0.3

- 状态：Proposed for Recovery Commitment Lifecycle v0.3
- 日期：2026-08-24
- 范围：Control Plane 中 committed-but-not-bound recovery ownership

## 背景

Recovery Reassignment Pipeline 已区分 Match、Proposal、Commit 和 Rebind。Commit 会将
replacement resources 写入唯一的 `ControlPlane.reservations` authority，但在 Rebind
之前，Execution Group assignment 尚未包含 replacement。如果 caller 中断、replacement
失效或 Group 终止，仅依靠 Group assignments 无法定位并释放这些资源。

ADR-0002 已规定 Proposal、Commit 与 Rebinding 的职责分离，但没有定义 Commit 与 Bind
之间的 ownership、撤销和 terminal cleanup，因此需要补充最小生命周期合同。

## 提议的决策

Control Plane 以 `(ExecutionGroupId, RoleId)` 为键维护至多一个
`CommittedRecoveryAssignment` pending commitment。该集合记录“已 Commit、尚未被 Group
Consume”的协作变化；资源 ownership 的唯一事实仍是 `ControlPlane.reservations`。

Recovery Commit 在完成全部验证后，语义原子地建立 replacement reservations 和 pending
commitment。Pending commitment 只有两个非终止出口：

1. **Consume through Rebind**：验证 authoritative pending value 与 reservations 后更新
   Group assignment、进入 Adapted，并删除 pending entry；replacement reservations 保留，
   因为它们已转为 active binding ownership。
2. **Abort**：验证全部 reservation ownership 后统一释放 replacement resources，并删除
   pending entry；Group 保持 Blocked，Role 保持 unbound，允许后续重新 Match/Propose/Commit。

`CommittedRecoveryAssignment` 是 handle/value evidence，不是 authority。Stale、forged 或
已 Abort 的 handle 不得 Rebind。`release_group` 是 terminal ownership cleanup：Released
之后不得存在该 Group 的 reservation 或 pending recovery commitment。

Pending commitment 不是 `GroupLifecycle` 状态，不进入 `ExecutionGroup.assignments`，也不
写入 Shared Node State。Failed 仍只表示显式 recovery exhaustion；Abort 不表示 Failed。

## 暂不解决

- 持久化、崩溃恢复和跨进程 transaction；
- commitment timeout、retry counter 和自动 Abort；
- distributed lock、2PC、Saga 或 Allocation State；
- Scheduler selection、multi-role recovery 和 recovery exhaustion policy。

## 接受证据

- 同一 Group/Role 第二次 Commit 被拒绝且不覆盖旧 commitment；
- Rebind Consume 后 pending entry 删除但 active reservation 保留；
- Abort 原子释放自身资源并拒绝 stale handle；
- Failed/Released terminal cleanup 不泄漏 committed-but-not-bound resources；
- Multi-Mission cleanup isolation 和 zero-resource commitment 均有 deterministic test。
