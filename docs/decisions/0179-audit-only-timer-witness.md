# ADR-0179：独立审计行不触发无变化的权威提交

- 状态：Accepted，已实施并通过本包实际进程验收
- 日期：2026-10-24

Artifact 的真实 HTTP admission 会持久化 service audit。它改变 SQLite 的
`total_changes`，使 ADR-0173 的保守 timer witness 失效，即使原 checkpoint、
事件和权威命令完全未变。实际 Prepared 恢复成功后的 GET 仍等待了约 21 秒，
另一次纯输入冲突等了约 40 秒；不能把已持久化审计行当成新的权威状态变化。

仅 `record_service_access` 的实际 autocommit 单行 INSERT 可延续原完整提交的
witness：写前 `total_changes` / `data_version`、schema/user version 与 journal mode 必须精确
等于原已验证 witness；
写后实际受影响行必须为 1，`total_changes` 恰好增加 1，外部 `data_version`
及上述 headers 保持。任一条件不满足不延续。Trigger/cascade 等额外写入因此不能借用旧 witness。
实际审计 SQL 先成功，才延续本进程 witness；失败不补成功、不清写入禁用。

原完整 checkpoint/事件/command 校验、外部/其他自身 SQL 变化、rollback、原 writer
与动态授权规则保持。审计仍实际持久化，不跳过、不另建未验证的后台队列。
本优化没有持久 schema、Node 或物理 authority 变化；验收需真实 SQL 的 trigger、
额外写入及外部修改反例，以及相同原预算的实际恢复与只读重试。

本包结果与证据边界见[实际验收](../development/acceptance/2026-10-24-prepared-recovery-and-runtime-latency.md)；接续必需项不因本 ADR 状态关闭。
