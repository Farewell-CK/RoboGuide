# Recovery profile v0.3

设计：[ADR-0122](../../../docs/decisions/0122-runtime-mi-recovery-writer-closure.md)。
显式增加 `runtime-mi` kind，其数据库直接位于声明 root；原 `database.worker.lock`
和数据库 inode 两把实际 lease 必须同时取得。格式须为唯一实际
`roboguide.runtime-mi-work/v0.1` instance，并保留原 provider nonce schema。

完整保存 Controller、Node、Request、MI work 和 CAS 依赖。实际活动 worker、硬链接别名、
未知 marker 与旧 profile 拒绝；新目录恢复保留完整 schema/rows、原 budget/nonce/bytes/
uncertain/独立 observation。旧工具必须拒绝 v0.3，不得误用无关的 `.writer.lock`。

v0.2 的 replica supervisor/数据库 lease、原 cluster/schema/bootstrap 验证和完整
`artifact_replica_sets` 语义保持。v0.3 restore receipt 单独保留 CAS placement；相同重试
只读核验现有结果。服务启动后 SQL journal mode 等字节变化不能被重试覆盖。

工具不启动服务，不重映射原权限/配置/时钟，不重做模型、设备操作或原 Unknown 结果。
数据可读不代表在线 HA、当前证据、设备安全、硬件身份或物理连续性。
