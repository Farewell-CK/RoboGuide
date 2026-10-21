# Replica 升级排空与恢复格式校验

ADR-0135 补齐 storage v1 的旧连接排空与独立恢复校验。原 cluster/schema 在迁移前核对；
旧 WAL 连接未释放时，SQLite DELETE-journal 切换拒绝。完整迁移和只读旧表 fence 在
exclusive 事务提交，缺失原 identity、未知对象、改过的 view 和写入 trigger 拒绝。

`cargo test --locked -p controller-replication` 的 31 项回归通过。新成员加入测试现在
明确等待该 follower 实际应用完整成员集合；leader 的提交回执不等于每个 follower 已应用。
Recovery 的 29 项测试通过，包括修改 bundle SQL 后重新计算全部 byte/row/manifest pins，
仍拒绝缺失/重定向 view、writer trigger 和错误 marker，restore 不创建目标目录。
整仓 Rust 929 项通过、2 项保持原 ignored；fmt、all-target/all-feature Clippy 与私有
Rustdoc 的 warnings-as-errors 检查通过。变更 Python 的 Ruff、strict mypy、函数文档
检查和严格站点构建通过。

独立六进程结果保留于 `/tmp/rg-replica-quiescent-upgrade-20261021-a/RESULT.json`，
摘要 `sha256:a789fd1991722a37c853fc783f463e5745b5a24fa07e8bf13d64c646f9f7a046`。实际旧连接活跃时新 writer 拒绝；原连接退出后迁移；
hardlink 新 writer 拒绝；旧 writer 无法写只读 view；释放后重新打开成功。所有原 PID
已 wait/reap，Node、设备、模型和模拟器均为零。

新 probe 的维护源码为 `core/controller-replication/examples/storage_lease_probe.rs`，构建：
`cargo build --locked -p controller-replication --example storage_lease_probe`。验收脚本要求
显式新旧 executable、profile 和 application schema，readiness 有十秒期限，失败路径
回收原进程。实际旧 executable 摘要为
`03695d497fd69428db15409a77202bce23492cded99ca6c10bbd21a93c518347`。

Replica 冷恢复脚本按实际 SQL 版本声明 v0.2/v0.5；不把旧数据重标为新版本，不迁移原
证据源。Memory-only 的 v0.4 不变。该结果没有关闭 source-child drain、响应丢失、
完整三副本旧 binary 恢复、在线内容或跨故障域 HA；这些继续实施。
