# ADR-0076：Continuous Node Liveness and Decision Time

- 状态：Accepted
- 日期：2026-10-12
- 范围：Control Node lease 与 transport-neutral 连续接收证据、双时间语义

## Decision

1. Integration reader 持有每个当前 session 的有界连续心跳摘要：精确 Node/session/lease、
   activation receive anchor、first/last heartbeat sequence/time 及最大相邻间隔。只累积已经
   通过现有身份、lease、management sequence 和 health shape 校验的心跳。超过静默界限
   后的消息不能重新激活 route。摘要是当前进程的证据，不是 Control lease authority。
2. Control 记录 registration 建立的 process-local session/lease anchor、duration、accepted
   evidence watermark 和 expiry/route-loss fence。这些与 live lease 一样不进入 checkpoint；
   restore 清空并要求新 registration。Integration 不修改 Control 或 State。
3. application consumer/timer 在持有统一 writer gate 的 candidate 事务内读取摘要，Control
   验证身份、顺序、anchor、连续性及当前时效。只有从已登记 interval 连续接上的合法证据
   才能把 lease 延伸到 last_received + duration；已提交 expiry 或 route-loss fence 永远
   拒绝同 lease 续约。不能仅凭最新心跳延伸跨真实静默的 lease。
4. 此 liveness-only acceptance 写入原始 lease evidence trace 后与现有 checkpoint 一起
   commit；不提前 ACK 尚未消费的 raw heartbeat，也不记录/更新其 reported health。
   health、State、peer、receipt、terminal 的原有有序 application acceptance 仍必需。
5. bridge 新增双时间 ingestion：`received_at` 保留 actual ingress，`decision_at` 从同一
   process Clock 在拿到 writer/Controller lock 后读取。旧 `consume` 入口兼容地令两者相同。
   State、health、peer/progress 原始 receive-relative TTL 不因排队/restore 变新；Runtime
   deadline 刷新与所有后续 Match/Commit/recovery 使用 fresh decision time。
   Peer ack reducer 入库后再次按该 decision time 刷新过期 fence，覆盖延迟新 sequence
   和 exact replay；先前过期的 peer 不因另一个 peer 更新而变新。
6. Registration 的 process lease grant 使用 decision time，initial reported Offline 事实保留
   ingress time；transport route 仍等待 durable Registered。第一份正常 health heartbeat
   仍是实际 dispatch 的门槛。短时间残留且 session 已 superseded 的 registration 被拒绝。
7. timer 在拿到 writer gate 后读取 decision time，先应用连续证据再 expire。命令 outbox
   保持 commit 后 fresh Clock 检查。没有当前 session 或持续证据时依旧 expire/Unknown；
   无物理 stop 证明不 release、takeover 或自动重复 Execute。
8. 队列容量/丢弃政策在此不变；bounded evidence summary 不携带 payload，也不替代 raw facts
   的 durable sequence。更改无界队列或 batching 留给独立性能 ADR。

## Acceptance

FixedClock/scripted ingress 验证连续心跳在 application 暂缓时仍保留 lease，而旧 health
保持 stale；真实静默、边界 gap、wrong lease、old session、倒序序列、已提交 expiry、
route loss、restart 拒绝复活；延迟 State/peer/progress 保留 receive time。实际 loopback
验证 timer 事务看到 reader evidence，ACK 仍等待原始事实持久接受。Control-only、
合成网络与物理停止证据分别报告；当前历史 pilot 因果仍为 NOT PROVEN。
延迟 peer 回归同时检查入库后的当前状态、下一 tick 前的 checkpoint、receive-relative
expiry 原值、exact replay、部分 peer 更新及全部 peer 新鲜后的正常恢复。
