# ADR-0090：Checkpoint-bound append-only event archive

- 状态：Implemented for explicit local archive maintenance
- 日期：2026-10-13

## 决策

State 提供 `ArchiveSink` 插件边界和 `FileArchiveSink` 本地实现。一次 archive segment 是
newline-delimited immutable `PersistedEvent`，由 `ArchiveManifest` 绑定起止 sequence、条数、
segment SHA-256、完整 checkpoint sequence/digest。sink 成功写入并 fsync、内容复核后，
SQLite 才在同一事务中删除该 prefix，并记录 `history_floor` 与 manifest。重复相同 segment
幂等，内容不一致失败；sink 失败或 prefix 不连续时不删除任何行。

`SqliteEventLog::archive_and_gc` 只接受显式 cutoff，要求完整 checkpoint 覆盖 cutoff。应用
没有定时自动 GC，也不根据 Mission 终态猜测物理安全；Controller operator 通过
`POST /v1/maintenance/archive`、`recovery-admin` 角色和 deployment-owned
`ROBOGUIDE_EVENT_ARCHIVE_DIRECTORY` 发起维护。未来 S3/MinIO 等后端实现同一 `ArchiveSink`
合同即可接入。

带 replica writer 的 event log 会拒绝该本地 GC 请求，直到未来提供跨副本 archive
maintenance 协议；这避免 leader 单方面删除 follower 尚未锚定的 rows。

## 边界

归档不是完整 event reducer，也不替代 checkpoint/replica history anchor。当前 API 没有证明
跨节点 archive manifest 共识、远端保留策略、租户配额或长期容量曲线；cutoff 仍需人工确认
不包含 active/Unknown/Held authority。
