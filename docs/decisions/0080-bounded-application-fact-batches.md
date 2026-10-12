# ADR-0080：有界 Application Fact 批处理

- 状态：Implemented; deterministic protocol checks and isolated process comparison complete
- 日期：2026-10-12

## Decision

Controller 在现有单 writer gate 下按原接收顺序处理已经排队的 Node facts。每批最多
32 项，部署配置 `ROBOGUIDE_FACT_BATCH_LIMIT=1..32`，当前默认 8；可配置
`ROBOGUIDE_FACT_BATCH_WAIT_MS=0..5` 的单一合批窗口，当前默认 2ms，必须小于
application acceptance timeout，不按每项续期。Registration、RegistrationUpdate、Unavailable 是独立事务 barrier，
不会把 pending route 或新 session 当作已经 application accepted 的路由。
limit=1 的默认 wait 为 0；很短的自定义 acceptance timeout 会缩短默认窗口。

每项输入有独立 private candidate 和 input SQL/sequence savepoint；ADR-0077 的 Mission
savepoint 使用不同名字并嵌套在里面。输入被既有 bridge authority 拒绝时丢弃该项变更，
其他 session 继续；同一 session 后续项一并拒绝。共享/lifecycle/SQL 错误中止整批。
每项仍执行完整派生 transition，使用原始 ingress time 与 fresh decision clock。只合并
最终 checkpoint serialization 与 durable Commit，不删除 fact、receipt 或 lifecycle evidence。

Commit 前不暴露 candidate、完成 Registered/Ack 或 flush Execute/Cancel。Commit 成功后
安装唯一 live authority，使用新时钟 flush 已持久 outbox，再按输入顺序完成 acceptance。
原时限、Control ownership、Mission hold、Unknown/no-blind-replay 和 timer 错误规则保留。

## Validation gate

通过真实 loopback Node Protocol 验证多 session 1/N 的最终 checkpoint/event trace 等价、
Commit 前无 Ack/Execute、拒绝只回滚自己的输入、同 session 后续被 fence、嵌套 savepoint、
checkpoint/SQL 故障整批回滚和 queue/barrier 边界。再用隔离合成工程 probe 比较实际
commit/serialization 数量和完成分母；证据不属于 E6 或物理实验。扩大默认 batch 前必须
通过上述门禁；未证明的性能收益不得报告成已完成。
