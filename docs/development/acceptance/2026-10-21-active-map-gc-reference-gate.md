# Active Map GC 实际引用门禁验收

新增 `active-map-gc/v0.2` 后，GC 不再接受 caller 提供的八类 boolean。它从实际 Provider
`content_references`、Active Map 当前 head/ref、pending handoff 和 tombstone 状态读取本地
引用；存在 active map 时返回 `blocked` 与具体 selector，释放后才返回 `eligible`，并明确
最终删除权属于 `ContentReclamation.collect`。

旧 v0.1 仍保持 blocked 兼容路径。Active Map 测试 8 项通过，17 个 Memory provider
strict mypy 源文件和 Ruff 通过。该门禁只覆盖本地软件引用和 inode 删除前置条件，跨节点
replica/archive/catalog 一致性、全局 GC 和物理连续性仍待 F4/F5。
