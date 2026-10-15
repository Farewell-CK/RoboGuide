# F7 停止、恢复与运行期协调软件边界

状态：软件停止/恢复 authority 已通过现有生产路径回归；物理 stop certificate、硬实时 hazard、
运行中物理抢占、耦合多 Role 迁移和异构 regrasp 保持 `HardwareEvidencePending`/后续部署 gate。

当前调用链是：relation/recovery/explicit cancel → Mission safety hold → operation-owned
`SafetyStopPurpose` → durable Cancel outbox/Node receipt → exact current-session State safe-state
evidence + original terminal receive time → `StopConfirmed` → release/resume/recovery gate。Cancel
receipt 只表示 Node journal 接纳，不表示机器人停止；`SoftwareProcess` assurance 也不升级为
设备观察。旧 stop 字符串恢复为 `ManualEscalation`，启动事务建立缺失 Mission hold；Unknown、
旧 session、冲突/过期/迟到 evidence 和 deadline expiry 都保留 ownership 并进入人工/对账路径。

Recovery API 仍区分 exact execution 和 whole Group：请求带当前 owner、完整 attempt 集、不可续
deadline、repeat authorization 和最大 replacement 次数；Match/Propose/Commit/Rebind 由 Control
执行，Node/Runtime 不选择替代节点。恢复视图把当前 Unknown、等待停止、历史完成 recovery 和
人工介入分开，自动 Execute replay、自动 resource release、自动 Node replacement 均关闭。

## 回归证据

```bash
cargo test --locked -p integration-server
```

结果：220 passed。覆盖安全停止 receipt、静默 timer deadline、冲突/过期/错误 assurance、旧
证据 migration、Mission hold、取消/失败/否定 verifier 竞态、Unknown 不重放、replacement
Commit/Rebind、Group recovery、SQL failure rollback、restart/restore 和 late evidence。完整
workspace tests、Clippy `-D warnings`、private Rustdoc 也通过。

## 未关闭边界

软件证据不能证明物理停止、硬实时 deadline、设备 hazard isolation、跨站点物理连续性或真实
regrasp/handoff。运行中 preemption 目前只允许严格 future、未激活/未绑定/未 Commit 的计划
窗口；已运行工作必须先经过本停止合同和独立部署安全证明。
