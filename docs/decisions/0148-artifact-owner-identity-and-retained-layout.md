# ADR-0148：Artifact owner identity 与显式 retained layout

- 状态：Accepted
- 日期：2026-10-22

## 问题

跨 owner 引用不能用同一个 digest 混同 Controller Artifact CAS 与 Node provider CAS。
Artifact 需有稳定的实际 backend identity，以及旧 binary/恢复工具无法忽略的新格式 fence。

## 决策

Artifact Store 只管理 opaque bytes、实际 storage identity 和本地格式，不接管语义 Memory
或 Control 权限。默认 `new` 保持 legacy 布局；显式 `new_with_retention` 在原 root writer
lease 下迁移。实际 OS 随机 nonce 只标识软件存储，不产生 Node 或设备 inventory。

迁移先写有界 `.retention-intent.json` 并 fsync，固定同一个 owner 和原 blobs 目录 inode。
原 `blobs` 原子改名为 `objects-v1` 后，旧路径变为同一身份的 regular-file fence；最后
把原 intent 原子发布为 `.retention-owner.json`，逐步 fsync parent。旧 binary 创建
`blobs` 目录失败，旧 backup 布局要求目录也失败。中断只能核对原 intent/inode 后继续；
已开始迁移或已发布 identity 缺失、冲突、symlink、陌生目标目录均拒绝，不另造 owner。
原 staging 和所有内容 bytes 不变。新格式必须显式选择，不能回落到 legacy。

新格式的恢复合同须显式保存 owner/fence 文件与 `objects-v1` 内容；恢复后的 inode
是新的，原 inode 只作为历史迁移证明，不声称 physical continuity。后续 Catalog/
lineage 引用绑定此 owner、原 tenant/principal、内容 digest/size 与原权威命令。
storage identity 本身不授予读取、使用、退休或删除权限。

## 验证要求与边界

验证真实旧目录、实际 bytes、重启、迁移窗口、缺失 identity/冲突/符号链接拒绝、旧
binary 和旧 profile 拒绝，以及新格式完整恢复。不得仅以随机 ID 或目录创建关闭 N1。
本决策是 Catalog/lineage/Archive 和在线内容引用的依赖；它们及其实际组合仍须完成。
