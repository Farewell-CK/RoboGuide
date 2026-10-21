# Replica storage v1 与数据库本体 writer 验收

本项关闭了一个已确认的本地持久化缺口：旧 Replica 只锁定数据库文件名旁的
`.writer.lock`，两个独立进程可以通过不同硬链接同时打开同一 SQLite inode。

实现内容：

- `SqliteRaftStore` 同时持有 sibling lease 与实际数据库 inode 的 advisory lock；最后一个
  Store owner 释放两者，继承的子进程 descriptor 不延长 authority。
- 完整原表在一个迁移事务中改名为 `replica_v1_*`；`user_version=1` 和
  `roboguide.replica-storage/v0.1` marker 固定格式。旧表名保留为只读 view，旧 writer
  在写入前失败；未知/不完整格式拒绝。
- Recovery profile v0.5 继承 Memory v0.4 的全部 writer 合同，增加 Replica 数据库本体锁、
  v1 表/view/marker 校验和 restore receipt v0.5。旧 profile 不会读取新格式。

单元回归在 Rust 1.99.1 下通过 27 个 Replica 测试，包含 hardlink、legacy migration、
old-table write refusal、完整 snapshot/reopen 和 OpenRaft storage conformance。

独立进程证据位于 `/tmp/rg-replica-storage-v1-process-final-20261021/RESULT.json`：
真实新 probe、真实旧 probe 和 hardlink alias 均由本次检查创建并回收。结果为 `passed=true`，
`nodes=0`、`devices=0`、`models=0`、`simulators=0`。旧 probe 返回
`cannot modify replica_meta because it is a view`；新 probe 在释放后可重新打开。
可复验入口为 `tools/quality/check_replica_storage_processes.py`，需要显式传入新/旧 probe
二进制；脚本不会创建 Node 或设备事实。

该项仍不等于完整 F5：durable leader drain/quorum barrier、在线 CAS/archive 一致性、
跨故障域恢复、旧 binary 的正式 v0.5 bundle restore 和物理连续性仍保持待办。
