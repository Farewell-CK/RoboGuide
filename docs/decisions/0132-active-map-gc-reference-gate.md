# ADR-0132：Active Map GC 使用实际引用权威

状态：Accepted；仅关闭 active-map 的本地引用门禁，不宣称全局 Memory 删除或跨站点 GC。

## 问题

旧 `active-map-gc/v0.1` 接口接收八类 caller boolean。即使全部为 `true`，它也不能证明
任何实际 execution/import/use/lineage/replica/snapshot/archive 引用已释放，因此只能永久
返回 blocked。

## 决定

新增闭合 `active-map-gc/v0.2`，只接收原始 content digest 与 selector。实现从同一配置
Provider 的 `content_references`、Active Map heads/refs、pending handoff 及 tombstone
SQL 读取实际状态；caller 不再提供 all-clear。仍由 `ContentReclamation.collect` 拥有最终
inode unlink authority，GC 只返回 `blocked` 的完整原因或 `eligible` 的委托证明。

旧 v0.1 保持拒绝授权的兼容行为。map release、revoke 和 handoff 继续保留 history、CAS
字节与 Unknown 状态；本项不扩大 Scope/Visibility/Placement，也不建立设备或物理定位事实。
