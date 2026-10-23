# Recovery v0.17：Controller 存储代际与原生 GC SQL 维护

显式 `roboguide.recovery-profile/v0.17` 保留完整 v0.16 的 Memory/Replica/Archive/
Catalog/GC 原引用、目录、内容、预算与缺失拒绝规则。bundle 保持 v0.4，restore receipt
为 v0.17；Node 合同、Controller wrapper v52、Artifact v5 与 GC SQL v0.1 保持原义。
本版本不包含尚未接入的新 Raft content roots 或 online 多数 ACK。

每个 `controller` component 必须增加 `storage_schema`，只能声明实际
`roboguide.controller-storage/legacy` 或 `roboguide.controller-storage/v1`。
Artifact component 继续必须声明实际 v3/v5。所有声明都与实际原文件核对，不能由 JSON
自身证明 authority。v1 以完整 mandatory tables/marker/user_version、私有 event 写表和
确切只读 legacy view 拒绝旧 writer，包括零事件。所有 Controller writer 的实际 SQL
inode 与 canonical sibling 租约共同持有到最后一个 SQLite connection 关闭。
legacy 不取得持久 downgrade fence，旧 profile 不能隐式读取 v1。

静止 backup 持有完整实际 Controller inode/sibling 与原先所有原 writer，完整核对原
Archive、Catalog、上传、已知 consumer 和实际内容；restore 不启动服务、不续原预算。
v1 普通 restart 不重复旧代际 DELETE 排空，独立只读 observer 不阻止正常重开；首次
legacy→v1 的迁移仍要求原生排空。见 [ADR-0161](../../../docs/decisions/0161-controller-inode-writer-and-storage-generation.md)。

新增本机 OS 管理命令 `recover-artifact-journal`，必须提供完整 standalone v0.17
profile、原 v5 Artifact component、实际 SQL/journal digest、独立 evidence directory
与原因。实际完整 writers 与全部已知 Controller 被持有/核对后，只有原生
`READONLY_ROLLBACK` 进入恢复。原 owner、GC identity、其它 stores、原输入 inode/hash
和 DELETE-only 边界继续完整验证，WAL、symlink、缺 source 和其它不可读状态不取得
隐含 repair 权限。

原随机 operation ID、完整 profile/owner/原输入、实际 OS caller/process/kernel 与
不可续 deadline 先保存为 immutable intent；原 SQL/journal 的完整 forensic copy 与
fsync 先于 writable SQLite。原生 exclusive/integrity 和全部原 GC 语义复核后才发布
完成。缺 committed table、head、marker、row 或 tombstone 不自动重建。
失败保留 Unknown/partial；同 evidence directory 只观察，不再次恢复、不续预算。
新的明确调用使用新的 operation ID，较早 Unknown 独立保留。

`observe-artifact-journal-recovery --evidence-directory <original-directory>` 只检查原
记录和已完成的 forensic bytes；不打开 source writable SQL，也不把当前可读补成原完成。
forensic 文件不授予应用、内容、GC 或恢复 authority。需要 cold 保留时，显式作为已有
`files` component 并依赖原 Artifact writer；恢复后仍只可作为证据观察。
见 [ADR-0162](../../../docs/decisions/0162-explicit-artifact-gc-sql-recovery.md)。

实际门禁为 `check_controller_storage_processes.py` 与
`check_artifact_sql_recovery_processes.py`。本地软件过程不建立 online quorum、设备
停止、跨主机 RTO/RPO、物理连续性或整体 SoftwareComplete。
