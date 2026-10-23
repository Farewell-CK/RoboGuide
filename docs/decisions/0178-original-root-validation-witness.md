# ADR-0178：原 Root 连接的完整校验复用

- 状态：Accepted，已实施并通过本包实际进程验收
- 日期：2026-10-24

Root4 的 `validate_connection` 在每次只读查询和有界操作前重复校验全部 head、
原 copy/reader/producer/stream 历史。实际运行的原入口回执显示随历史增长，完整
事务从约 3 秒增长至 20 秒；Prepared 恢复已经读取完整 bytes，却仍受后续提交拖延。

仅在同一实际持锁 SQLite connection、autocommit 状态下复用已完成的完整校验。
进程内 witness 保留原 `total_changes`、`data_version`、`schema_version`、
`user_version` 和 journal mode。先后完整相同且实际 footprint 仍在原 bound 内，
才可复用。新连接/重启没有 witness；任何自身写入（含 rollback）、外部提交、DDL、
版本或 journal 改变均清除复用并执行原完整校验。活动 transaction 不复用，也不
发布 witness。完整扫描期间发生变化拒绝；失败结果不缓存为成功。

它不缓存 bytes、当前权限、epoch、原预算、reader/current-use、实际 PID 或 quorum。
每个原操作的实际来源、完整 payload/hash、动态授权、原 FD/worker、原 intent/close
及 SQL Commit 顺序保持。witness 不持久化，不从恢复 receipt 或新 Root 导入；
原启动/storage/Node/Memory/Controller 合同不改变，旧 Unknown 不补造结果。

验收包括实际外部 SQL 修改、同连接写入、DDL/版本变化和重开拒绝；全部原 Root
故障回归，以及相同声明预算、真实三副本/Controller/Prepared 恢复。实测仅为软件
进程/文件观察，不建立长期 SLO、硬件或整体 SoftwareComplete。

本包结果与证据边界见[实际验收](../development/acceptance/2026-10-24-prepared-recovery-and-runtime-latency.md)；接续必需项不因本 ADR 状态关闭。
