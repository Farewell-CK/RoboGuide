# ADR-0180：写入禁用后的原上传恢复观察

- 状态：Accepted，已实施并通过本包实际进程验收
- 日期：2026-10-24

新增只读 `GET /v1/artifact-uploads/<upload>/recoveries/<command>`，仅返回该原
stable uploader / tenant 的已提交本地 record。实际当前 TLS/grant/epoch/quorum
仍须通过；输入不授予内容、当前 use 或重试副作用。它不调用 writable-upload
permission，不创建 read/import/copy/Execute 或新的命令。

健康时保持原实际 admission audit。已知本地 SQL 故障使写入禁用时，该精确
metadata-only 入口从原 committed cache 观察，并明确返回
`audit_persisted: false`、原 write generation/status 和 `committed_local: true`。
这与现有 Controller 的只读故障观察面一致；不补造持久化审计，也不宣布 cache 是
新的全局结果。其它 Artifact bytes、write 入口、未知路径和权限不足不使用此例外。

原 POST 重复命令在可写时可查询原业务结果，正常记录审计；写入禁用后使用上述
GET。缺完成 receipt 保持 Unknown，不能因实际 bytes 存在而补 Completed 或重做。
要求 restart 的副本故障或 Catalog outcome fence 必须额外通过原 parent 的 fresh
complete-quorum View，精确匹配本地已提交 checkpoint identity。缺该实际 port、
不一致或无法确认则拒绝并要求 startup replay，不能用旧权限借用新 authority。
这只允许读取原记录，不清除 restart 要求；内部 quorum 完整性读取不授予应用 bytes
读取/导入/use。缺 cache、跨主体/tenant、过期/撤销
TLS、失去原当前 authority 都拒绝。观察不清
failure latch，不更改原预算或 Owner，不证明设备停止或总体恢复完成。

本包结果与证据边界见[实际验收](../development/acceptance/2026-10-24-prepared-recovery-and-runtime-latency.md)；接续必需项不因本 ADR 状态关闭。
