# ADR-0130：Replica 数据库本体 writer 与旧格式写入隔离

状态：Accepted；实现与独立进程验收完成前不视为 F5 完成。

## 问题

原 Replica 只锁定 canonical 文件名旁的 `.writer.lock`。实际两个独立进程通过不同
硬链接同时打开同一数据库，获得相同 consensus authority；路径锁不覆盖数据库 inode。
仅新增本体锁又不能阻止不认识该锁的旧程序，原 recovery profile 也没有取得本体锁。

## 决定

Replica Store 同时持有原路径锁与实际数据库本体的排他 advisory lock。它们共享实际
Store 生命周期；最后一个 Store 释放时显式 unlock，非 writer 子进程继承 descriptor
不能延长 authority。拒绝非普通文件与 lease symlink，数据库路径别名仍共享本体锁。

独立 storage format v1 保持全部原 profile、投票、日志、应用 checkpoint、命令、事件、
snapshot 和证书历史。旧数据库必须在原 writer 全部退出、SQLite 实际排空后，才可在
单一事务里迁移。新表使用独立名称，旧表名保留只读 view；旧二进制即使绕过路径锁，
也无法写入新 authority。`user_version` 与独立 marker、完整表/view 集合共同校验；缺失
或未知格式拒绝，不能用 `CREATE IF NOT EXISTS` 自动重建丢失 authority。只读旧 view
可供原诊断使用，不授予写入。该迁移不修改 network/profile 或 application policy pin，
不允许因 compiler、schema 相同而假定二进制可滚动替换。

显式 recovery profile v0.5 才可获取新 Replica 本体锁并备份、校验和恢复该格式；它
继承 v0.4 的完整 Memory 四 SQL writer 合同。旧 profile 遇新 Replica 格式必须拒绝。
恢复 receipt 保留所有原数据 identity 与预算，不自动启动服务，不续期任何执行权。

独立进程门禁必须重放原硬链接反例、原 writer 活跃时拒绝迁移、旧二进制冷写拒绝、
非 writer 继承 descriptor、完整原数据迁移与恢复，再执行实际多副本共识回归。
这关闭本地持久化唯一 writer 缺口；durable leader drain、在线 CAS、一致升级、独立故障
域及其余 F5 项仍必须另行完成，不能据此宣称机器人连续性或 SoftwareComplete。
