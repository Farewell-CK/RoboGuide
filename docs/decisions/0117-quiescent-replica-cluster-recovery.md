# ADR-0117：静止副本集群完整恢复

日期：2026-10-20。状态：Accepted；原证据见
[完整恢复验收](../development/acceptance/2026-10-20-replica-cold-recovery.md)。

ADR-0113 的完整存储包已经验证 standalone Controller、Node、Memory 与 CAS。Replica
还拥有独立 supervisor writer；只锁 replica.sqlite3 不足以阻止 supervisor 启动 child
或更新物化数据。复制 metadata 不代表 Artifact bytes 已在其他副本可用。

v0.2 的 `artifact_replica_sets` 是部署显式授权的同一基础设施集群 CAS 恢复集合，不从
发现或 hash 自动推断复制。每个 Artifact component 必须属于一个原 replica dependency；
取得全部 writer 后从实际 SQL 核对完整同一 cluster/schema/bootstrap 与成员数量。不同
集群、集合缺项、重复 owner 或不完整原 trust 拒绝，且不创建 partial output。

恢复只对 manifest 已核验的 finalized `blobs/sha256` 内容求并集，保留每份原 SQL/bytes，
向显式集合内缺少该 digest 的新 Artifact 目录复制。staging 不传播；原记录的缺失与
新恢复 placement 分别记录。v0.2 restore receipt 保留每个原 source/target/digest/size。
重试只核验，缺失或损坏不重新复制。原 tenant/content ACL、上传 receipt、来源/期限均
不重写；没有新增 Node Memory replica、provider availability 或 Task satisfaction 事实。

Recovery profile v0.2 显式承载 replica components，取得每个原 supervisor.writer.lock
和 replica database writer lock；Controller、Artifact、Memory、Node、配置与凭证仍声明为
独立真实存储及依赖。旧 v0.1 不再接受含 replica 的 profile；旧工具拒绝 v0.2，避免以旧
锁集合声称静止。所有原 lease 在读取任何 SQL/内容前取得，缺失/忙碌一律拒绝。

恢复到新目录，不原地覆盖、不启动、不重映射隐含路径。所有 Raft vote/log/commit/applied/
membership/snapshot、原 trust/receipt、应用 command/event/checkpoint，以及声明内容逐表/
逐字节保留。不同成员可能在原停机时有正常 applied lag；恢复由原 Raft 机制选举和追赶，
不能用编辑 SQL 或选择“看起来最新”的副本伪造接受状态。原执行/安全/Memory 预算不续期。

实际进程 gate 必须从原证书/CA 迁移后三副本生成完整备份、独立 SQL oracle、pinned manifest
验证和新目录恢复，重新配置明确的新路径后启动实际 supervisor/Controller，读取原 trust
和旧凭证拒绝证据；受控故障和正常停机全部留档。软件进程不建立设备或物理连续性。

该静止同版本恢复是完整 F5 的必要恢复路径。在线内容追赶、跨主机故障与受控版本升级
继续独立实施，不能把这项验收重命名为完整滚动升级或在线 Artifact 复制。
