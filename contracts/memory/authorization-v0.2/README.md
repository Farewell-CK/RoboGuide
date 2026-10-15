# Memory Authorization v0.2

`memory-registry.schema.json` 是部署管理 API 的完整资源策略合同。`manifest_digest` 绑定
通用 manifest；typed-map 资源另外必须指定 `typed_map_manifest_digest`，其值为原始
MapArtifactManifest 的完整 canonical JSON SHA-256。JSON 对象键按字典序排列，无空白；
以 Rust 实际编码为权威，内容 SHA-256 独立核对，不得代替 metadata digest。

v0.1 输入只允许旧字段；显式升级可增加 typed binding，已有 typed binding 不可改变、
移除或通过通用 Memory 投影绕过。策略 revision/history、tenant 和撤销规则保持完整。
