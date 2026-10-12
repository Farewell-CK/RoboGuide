# ADR-0081：有界增量 checkpoint 存储

- 状态：Implemented and offline/loopback checked; throughput benefit is not established
- 日期：2026-10-12

## Decision

State SQLite store 可配置 full 或 incremental 模式；完整 Controller authority snapshot
仍由其既有拥有者编码。存储层不解释 Mission/Runtime/Memory 语义，不从不完整事件
reducer 推定恢复状态。incremental 使用前一完整快照的 bounded byte Copy/Literal delta；
滚动指纹只帮助发现完全相同的片段，每次 Copy 必须比对原始 bytes，SHA256 绑定 schema、
event sequence、revision 和完整结果。连续 revision、前后 digest 和原 event ordering 必须
一致；schema 改变、收益不足、最多 32 个 deltas 或累计大小达到基线时原子 compaction。

基线、delta/head 更新与事件使用同一 FULL-synchronous SQLite transaction。缓存只在
Commit 后发布，input/Mission savepoint 及 outer rollback 恢复 pending cache；拒绝不留下
较新 cache。恢复重建并逐项校验精确 snapshot，丢段、错序、坏 digest、越界 Copy、未知
codec 或 head 不一致均 fail closed。原 Controller startup tail/schema 校验、Unknown restore、
无盲目 Execute 重放、原 received_at、Actor registry watermark 与唯一 Control ownership 保留。

旧的 plain controller_checkpoint 可直接读取并在首个 incremental write 内迁移。采用链
存储后，旧表 schema 使用独立 storage marker，旧 binary 即使遇到零新事件也不能误读
旧基线；完整 application schema 保存在链 head。full 模式可对已迁移库作 compaction，
但不会隐式移除该降级 fence。提供 read-only snapshot inspection 以便工程工具读取最新
重建结果，而不是把旧表 baseline 当作最新 authority。

该优化减少重复磁盘 bytes；每个 application batch 仍有完整 snapshot serialization，
不能宣称消除其 CPU 成本、实现 event-complete reducers 或获得 control replication。
最多 64MiB snapshot，bounded chain/指纹桶/输出长度防止恢复工作无限增长。

## Validation gate

精确 round trip、legacy migration、同 event sequence 的 revision、真实 event/checkpoint
atomic rollback、嵌套 pending-cache rollback、schema change、compaction、截断/错序/篡改
和 durable reopen；应用恢复仍需重新注册并保留物理 ambiguity/ownership。隔离进程比较
full/incremental 的完成分母、写入 bytes、持久化与编码耗时；未证明收益前默认 full。
检查不启动 E6、模型、Habitat 或硬件。

## 当前软件证据

`core/state/src/checkpoint_store.rs` 已实现以上存储链。8 项存储专项检查覆盖 UTF-8 与
多位置/长度变化、迁移、nested pending cache、事件原子性、compaction 和十种损坏；
生产 application restore 回归验证 hold/ownership/Unknown/no-replay。workspace 检查
通过，当前默认仍 full，可显式配置 `ROBOGUIDE_CHECKPOINT_STORAGE=incremental`。
`integration-server --inspect-checkpoint <database>` 使用 readonly connection 重建最新快照。

两轮 debug 复核分别完成 64/64；编码写入量下降，但计算成本和 Commit 波动使吞吐收益
尚未建立。首次比较首轮完成 32/32，第二轮首个启动达到 10 秒 harness 限制；保留失败
日志、计划/实际分母和后续未到达范围，原因未证明。复核未复现该启动超时，不能把它
从原始证据删除。细节见事务性能记录，release 复核单独记录。

release 两轮再完成 64/64，增量 Save 的计算成本明显低于 debug；端到端结果仍双向
波动，默认 full 的决定保留。缓存复用 exact current digest 与 bytes-equal prefix/suffix，
避免重复计算已验证的前一摘要；新结果仍计算完整摘要，恢复时逐段验证不省略。
