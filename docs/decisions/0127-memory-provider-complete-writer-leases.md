# ADR-0127：Memory provider 的完整数据库 writer 与静止恢复

状态：Accepted，当前参考实现及实际进程验收已完成。

## 已确认问题

实际旧 provider 在持有 `writer.lock` 时，另一目录通过 `memory.sqlite3` 硬链接仍能打开
同一 instance。主 `MemoryStore.close()` 也会释放根 writer，而 `UseLeases` 连接仍可写。
只冻结根目录锁无法覆盖这两种实际 writer；反例保留在
`/tmp/rg-provider-writer-audit-20261021-a/RESULT.json`，源码基线 `d964bc8`。

## 决定

文件参考 backend 共享一个存储 writer 生命周期。打开任何 SQLite 前，取得实际根锁和
全部四个固定 managed SQL 文件 inode 锁；拒绝 symlink、非普通文件、重复 inode 和并发
writer。每个实际 connection 持有同一租约，关闭主 store 后仍有子连接时不释放 writer。
最后一个 connection 关闭才显式解锁，不能由另一个父对象的 close 提前释放。
同一连接类型只能有一个 writer。启动时 SQLite 的完整排空检查要求旧连接全部关闭；
正常服务期间允许独立 readonly SQL 检查。

Memory SQL 升为 user_version 3，记录确切 storage contract 和 managed filenames。
旧 v2 writer 拒绝新版本。旧数据升级先通过 SQLite 自己的 DELETE journal/exclusive
切换检查，拒绝仍打开的 legacy 别名；完整 marker/原始行提交到主文件后才启用 WAL。
实际旧 instance、import/Unknown/revoke/GC、原预算和进程退出历史不重建或续期。
Use/Jobs/Active-map 各自原 schema 保留；Root 实现不授予 Node、设备或执行权限。

显式 recovery-profile v0.4 冻结这些实际 inode 和原 root/use 锁，验证真实 v3 marker；
包含完整四类 SQL、内容、原 Node/Controller、引用和声明的配置/导出文件。
旧工具拒绝 v0.4，旧 profile 不获得新 writer 保证。备份与恢复保持原 rows/schema/
UUID/预算/provenance；不启动服务，恢复原不确定性不能变成成功或停止证明。

## 验收

实际进程验证硬链接/路径别名拒绝、主 close 后仍存活的各子连接、失败初始化、独立旧
writer、活动与 legacy 连接的恢复拒绝，以及最后关闭后可正常恢复。结合实际 scoped
Memory 修订/CAS/退出证明和全 SQL 静止恢复，不只验证空 catalog。
完整全局引用/在线 HA 和其他 F0/F4–F10 工作继续；这些存储事实不证明设备安全。
实际命令、原结果 digest 和完整范围见
[Memory writer 与完整恢复验收](../development/acceptance/2026-10-21-memory-provider-writer-recovery.md)。
