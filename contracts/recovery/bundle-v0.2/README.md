# 完整副本静止恢复 v0.2

[ADR-0117](../../../docs/decisions/0117-quiescent-replica-cluster-recovery.md)明确新 profile。
`replica` 同时要求实际 supervisor 和 Raft writer lease；Controller、Artifact、凭证和配置
仍独立声明。旧工具拒绝 `roboguide.recovery-profile/v0.2`；当前工具拒绝 v0.1 的 replica
component，避免以不完整旧锁集合声称静止。

`artifact_replica_sets` 必须显式存在，空数组表示不授予额外 CAS placement。非空集合
包含 3–9 个完整同一集群的 Artifact components；它们各自被一个原 replica dependency
引用。实际锁内检查 cluster/schema/bootstrap 与完整成员数量；不从 catalog 或内容 hash
猜测复制权限。只有 manifest 已核验的 finalized CAS bytes 可以补到声明的新目录，
staging 保留原位置。原应用 ACL 和来源不变，不能声称 Node/provider Memory placement。

`roboguide.restore-receipt/v0.2` 单独记录实际 source/target/digest/size；同一 command 重试
只核验，缺少已完成副本会报错，不产生隐式写入。源文件、manifest、原所有 writer、
全部原 SQL 及 byte/time/file bounds 延续 v0.1。CLI 不启动服务，不改源地址/epoch/预算。

完整配置见 `profile.schema.json`，执行工具见
[recovery-tool](../../../apps/recovery-tool/README.md)。旧 profile 原历史说明保留；含 replica
的新调用必须使用 v0.2。
