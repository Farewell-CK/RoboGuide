# ADR-0005：Allocation State Projection Authority

- 状态：Proposed for Allocation State v0.1
- 日期：2026-08-24
- 范围：Control resource commitment authority 到 State observable view 的单向边界

## 背景

Control Plane 已通过唯一的 `ControlPlane.reservations` 维护正常 Commit、Execution Group
Binding 和 Recovery Pending commitments。V2 要求 State & Memory Plane 提供 Allocation /
Reservation State 的共享可观察视图，但若 Control 与 State 同时被解释为 reservation
authority，将产生双权威、同步双写和不清晰的失败语义。

## 提议的决策

`ControlPlane.reservations` 保持 resource commitment 的唯一 authority。Control 根据
reservations、Execution Group assignments 和 pending recovery commitments 构建完整
`AllocationViewSnapshot`；State 只能 whole-view replace 并向 Reader 提供查询。

Allocation View 包含三种最小 phase：

- `Committed`：正常资源已经 Commit，但 `group_id=None`，尚未 Bind；
- `Bound`：资源属于当前 Execution Group assignment；
- `RecoveryPending`：replacement 已 Commit 到 existing Group，但 unbound Role 尚未 Rebind。

Projection refresh 在 authority mutation 之后独立发生，可以暂时滞后。State write 失败
不回滚 Control Commit，State 内容也不能授予、拒绝或释放 Control ownership。Control
projection builder 必须拒绝 orphan、重复或同时 Bound/RecoveryPending 的 reservation，
State implementation 只存已经规范化的 snapshot，不重新验证 Group/Reservation authority。

`projected_at` 使用 RoboGuide-local bootstrap time。State 拒绝更旧 snapshot 覆盖更新
view；相同 timestamp ordering 暂不定义。没有 allocation record 表示当前 view 中没有该
resource commitment，不创建 `Free` record。

## 影响

- Allocation View 可作为未来 Scheduler advisory evidence，但 Commit 始终重新检查 authority；
- Controller 当前同步调用 snapshot/replace 仅作为 composition bootstrap，不是 transaction；
- Allocation State 不进入 Control Commit API，不要求每次 mutation 双写；
- Allocation State 不投影完整 GroupLifecycle、capacity、load、contention 或历史。

## 暂不解决

- background projector、event sourcing、persistence、replication 和 crash recovery；
- Allocation revision/CAS、distributed ordering 和 stale-view SLO；
- partial/fractional capacity、GPU/CPU quota、contention score；
- Scheduler v0.2 allocation-aware policy。

## 接受证据

- Committed/Bound/RecoveryPending、Partial Release、Abort、Rebind 和 Release projection tests；
- orphan reservation/inconsistent ownership rejection；
- deterministic ordering 与 stale snapshot rejection；
- projection lag 和 State mutation 不改变 Control reservation authority；
- Multi-Mission allocation isolation 和完整 Controller refresh path。
