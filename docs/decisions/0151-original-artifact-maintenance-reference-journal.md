# ADR-0151：原 Artifact 维护引用日志

- 状态：Accepted
- 日期：2026-10-22

## 决策

Snapshot、Archive 和在线 Replica 的 Artifact 内容责任由实际 Artifact owner 保留，
不复用 Local Memory provider 的同 hash 引用。显式 storage v2 在原 root writer 内
保留 v1 原 backend/目录身份，先发布原升级 intent，再初始化有界原维护日志，最后
原子替换 owner/fence；重启只恢复同一次升级。旧 v1 reader/writer 因 schema 拒绝，
缺 mandatory journal/原 owner 不补造。默认 legacy/v1 不隐含升级。

维护日志使用原 owner 下独立 immutable operation 目录：完整原 intent 在副作用前
持久化，绑定原 command/profile/source/purpose、实际 content digest/size、generation、
实际 kernel/UID 和不续期 deadline。只读观察不复制，不发明 Completed；同原 command
重复调用只能查询/对账，不能开始另一份副作用。历史容量满时拒绝，不移除旧 ID。
不同 command 的相同 bytes 独立保留；source provider 与实际 store owner 不合并。

Snapshot 在所有原 root/inode writer leases 内取得原引用后才复制；发布完整 bundle 并
独立验证实际 bytes 后才持久化原 release receipt。恢复保留拍摄时 active intent，
不能把后来的 source release 合成到旧 bundle。显式 abort 须同样拥有所有原 leases，
证明原同步 copier 已不能继续，保留 partial 和原失败 evidence；不是分布式 abort。
Archive 的发布副作用和 Raft Snapshot/online 内容副本分别通过自己的原 producer
实现相同 acquire→实际 effect→精确 proof retirement，不能以 Snapshot 测试关闭其它项。

GC 只有原 owner 可实施，在原 writer 内重查所有 Catalog/lineage、维护引用、原 upload
和 application-use 证明；tombstone 持久化先于 unlink。Unknown、遗漏 mandatory evidence、
缺少消费者退休证明都阻止回收。已退休历史不要求被删除的 bytes；读权仍由原身份/
tenant/Memory policy 授予。冷恢复 profile 显式升级，不让旧工具遗漏维护日志。

## 验证

实际 v1→v2 migration 保留 owner/inode/bytes，旧 binary 拒绝新格式；注入每个原迁移
窗口、SQL/文件及响应中断。实际 CLI producer 须覆盖 acquire、GC 拒绝、copy 中断/
重启、readonly reconcile、原 budget 不变、完整发布后精确释放、abort 和真实 unlink。
复读原 journal 与 CAS，恢复保持原 active intent。零 Node 的维护 gate 不创建设备或
物理连续性；Archive/online majority/完整 P1–P7 必须分别验收。
