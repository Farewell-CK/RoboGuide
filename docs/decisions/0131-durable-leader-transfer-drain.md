# ADR-0131：持久化 leader transfer drain 与重启保持

状态：由 [ADR-0136](0136-member-local-leader-transfer-evidence.md)修正 snapshot、完成证明和
重复操作边界。原三成员测试不证明 source-child drain 或物理连续性。

## 问题

旧机制只在内存里串行 transfer，临时关闭 source heartbeat/election，成功或超时后由
guard 恢复。进程在目标选举期间退出时，重启无法知道是否存在未决 handoff；超时也可能
重新启动 owned Controller child，造成 source epoch 与物理执行边界之间的歧义。

## 决定

source 在关闭 ticker 前，把完整 `transfer_id`、source epoch、target、required last log
index 和 `prepared` 写入 Replica SQL。目标在触发 election 前写入同一 identity 的
`accepted`；source 看到目标新 epoch 后写入 `target-elected`。目标成为新 leader 后可
reconcile 为 `completed`。`unknown`、`prepared`、`accepted` 在重启后保持 hold，不能
自动再次发送、续期或启动 owned Controller；操作员必须观察和处理原 receipt。状态只可
单调前进，identity/target/epoch/log 不可改写。已确认 `target-elected` 可作为下一次
独立 transfer 的前置完成状态。

本决策最初把 handoff record 放入 Raft snapshot。复查确认，这是成员本地运维状态，
会污染其他成员或阻止正常 snapshot 安装；ADR-0136 已移除这种传递。本地完整 SQL 恢复
保留所有原 ID，不能用其他成员的 snapshot 赋予或抹除 hold。Controller 子进程的实际
stop/wait 仍须独立证明；运维记录不拥有 Node/Actor 或设备停止 authority。

## 边界

原实现仅补齐 intent/hold，不能单凭 role 变化证明 durable source drain 或 target quorum。
当前独立 quorum、Unknown 不重发与成员本地历史由 ADR-0136 定义。
它仍不等于跨故障域在线 HA、CAS/Archive 一致恢复、硬实时 stop/handoff 或物理机器人连续性。
