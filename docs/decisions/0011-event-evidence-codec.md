# ADR-0011：Event Evidence JSON Codec

- 状态：Accepted for State & Memory evidence bootstrap
- 日期：2026-08-25

## Context

Integration Server 需要在进程重启后保留不可变事件证据。原有 `EventSink` 只规定了领域
payload 的接收边界，没有规定持久化格式；直接把 Rust `Debug` 文本写入 SQLite 无法为
后续 State/Control projection replay 提供稳定输入。

## Decision

`domain::EventPayload` 使用版本化 JSON codec 写入
`core/state::SqliteEventLog`。事件 envelope 保留 `event_id`、RoboGuide-local timestamp、
correlation/causation identity 和 payload schema marker。`SqliteEventLog::decoded_events`
只负责 codec 解码，不负责应用 Control mutation 或授予 reservation authority。

Mission-level Group/TaskExecution evidence 最初使用 `domain.EventPayload.json/v2`。加入
Distributed Spatial Memory manifest/replica evidence variant 后，新事件升级为
`domain.EventPayload.json/v3`；读取路径继续接受 v2，不能把新增 variant 伪装成旧 marker。
ADR-0019 加入 strong localization evidence variant 后，新写入升级为 v4；读取路径继续接受
v2/v3，旧 marker 仍不得承载新 variant。

ADR-0020 的 execution relation evidence 将新写入升级为 v5；ADR-0024 的 source-aware State
与 generic Memory evidence 升级为 v6。generic Memory replica 最初在 v6 只有 Node identity，
ADR-0025 completion pass 后由 v7 增加必需的 `consumer_provider_id`，durable key 成为
`(MemorySelector, NodeId, ConsumerProviderId)`。读取继续支持 v2-v6：缺少 provider identity 的
v6 replica 只能归入不可与合法 provider 冲突的 `~legacy-v6-unknown` bucket，不能猜测历史归属；
带 provider identity 的 payload 不能伪装成 v6，缺少该字段的 v7 payload 必须 fail-closed。

JSON codec 版本升级必须使用新的 schema marker，并保留旧版本读取路径，直到已有数据库完成
迁移。完整 event-sourced projection replay 必须额外定义 event ordering、idempotency 和
非法状态转换策略；当前 controller checkpoint recovery 的边界见
[`ADR-0012`](0012-controller-checkpoint-recovery.md)。

单个 Integration fact 及其同步 Group lifecycle evidence 采用一个 SQLite batch。批次只保证
evidence 的原子可见性；当前 `ControlPlane` 仍不是可回滚事务对象，因此 append/commit 失败
必须触发进程级 fail-stop，不能继续提供看似健康的控制服务。

## Consequences

- Evidence 可以被工具和未来 projection 读取，而不依赖 Rust `Debug` 输出。
- SQLite WAL 仍然只是单控制器持久化，不提供复制、HA 或跨进程 Control authority。
- 删除或重建 projection 不会改变 Control reservation 的权威归属。
- 事件 codec 兼容性成为 State & Memory 后续实现的测试要求。
