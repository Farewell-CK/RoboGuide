# ADR-0097：通用 Physical Stop Contract v0.1

- 状态：Generic contract implemented; deployment adapter integration pending
- 日期：2026-10-14
- 衔接：ADR-0057、ADR-0059、ADR-0095、ADR-0096

## 决策

1. RoboGuide 使用 `roboguide.physical-stop-contract/v0.1` 保存一次 exact physical attempt 的停止证据。
   identity 固定绑定 `GroupId`、`TaskRef`、`RoleId`、`execution_id`、`execution_generation`、
   `controller_epoch` 和 `safety_policy_digest`。
2. 通用阶段只能按以下顺序推进：
   `StopRequested → StopCommandAccepted → SafeStateObserved → StopConfirmed →
   ContinuationPrepared | GroupStopped | ManualEscalation`。
3. `StopConfirmed` 必须在 exclusive deadline 之前由绑定 attempt 的 Local EAIOS evidence 抵达。
   `CancelAccepted`、Node journal receipt、route loss、lease expiry、checkpoint restore 和心跳不能替代
   `SafeStateObserved` 或 `StopConfirmed`。
4. 只有当前记录包含及时 `StopConfirmed`，Control 才能通过 `may_release_ownership()` 释放或准备继续；
   其他阶段维持 ownership、Unknown/Blocked 或 ManualEscalation。
5. generic domain record 不调用 vendor SDK、不宣称物理证明，也不替代 Node Protocol。部署适配器必须把
   本地停止接口返回的 evidence digest 和来源映射到该记录，并在后续应用事务中提交。

## 实现与边界

- `core/domain/src/physical_safety.rs` 提供 schema、exact identity、阶段机、deadline 和 release gate。
- 当前 Runtime 已有 recovery-stop/group-recovery 的取消和真实 `Cancelled` fence；下一步将其状态视图与本
  ADR 的通用阶段机对齐，并由 Node Protocol/Local EAIOS adapter 提供 `SafeStateObserved` 证据。
- 当前代码没有真实设备 safe-state、停止证书、硬实时保证或物理连续性证明；这些必须在 deployment gate
  和受控设备验收中单独报告。
