# ADR-0135：Replica 升级前实际排空与恢复格式校验

状态：Accepted，原始进程证据与完整数据恢复通过后再关闭对应验收项。

## 问题与决定

ADR-0130 的 inode lease 只约束认识它的新 writer。旧进程通过 hardlink 保持 SQLite 连接时，
新程序仍须通过 SQLite 的 `journal_mode=DELETE` 和 `BEGIN EXCLUSIVE` 排空实际旧连接，
再把完整表迁移与旧表只读 fence 提交到主数据库。完整原 profile/schema identity 必须在
迁移前核对，错误 profile 不得改变原 authority。现有 v1 的完整表、原 marker、只读 view
definition 与无写入 trigger 也须在 reopen/backup/verify/restore 校验。

实际数据库与 sibling lease 必须为普通文件；lease symlink/FIFO 等拒绝。新数据库为原
声明之外的未知 SQL 格式时，不能当作空 Store 修复。备份验证除比较 pinned bytes/rows，
还须重新检查该 profile 的 Replica writer fence，不能让改过 manifest 的缺失 view 获得
restore authority。

显式 recovery v0.5 保留此前完整 Memory 合同。Replica 实际进程验收使用 v0.5；只有
Memory 的既有 v0.4 仍保持原语义，不能无理由改成新版本。旧 profile 读取新 Replica
格式应拒绝，这种拒绝是 writer 合同保护，不能描述为旧 profile 绕过锁。

ADR-0131 新增的 transfer 记录仍须补响应丢失、成员本地证据与 Raft snapshot 分离、source
child drain 和旧 writer fence；其原 28 项回归不能单独关闭完整 HA。所有暂未证实的范围
继续为必需工作，不能把本地进程结果当成多主机或设备连续性。
