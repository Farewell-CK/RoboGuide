# ADR-0131：持久化 leader transfer drain 与重启保持

状态：Accepted；实际 three-member transfer 回归继续保留为软件证据，不代表物理连续性。

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

handoff record 随完整 Replica snapshot 保留，安装 snapshot 时较新的本地状态优先，避免
旧 source snapshot 抹掉目标已经完成的 hold。transfer record 是软件共识运维证据，不是
Node/Actor、设备停止证书或 Mission 语义；Controller 子进程由 supervisor 独立 stop/reap。

## 边界

该项补齐 durable source drain、跨 restart 的 uncertain hold 和 target reconciliation。
它仍不等于跨故障域在线 HA、CAS/Archive 一致恢复、硬实时 stop/handoff 或物理机器人连续性。
