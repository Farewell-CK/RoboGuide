# ADR-0023：Application-Accepted Node Protocol Facts

- 状态：Accepted
- 日期：2026-09-01
- 范围：Node Protocol `Registered`/`Ack`、Controller composition、durable fact acceptance

## Context

Node Protocol transport 可以完成 session、lease 和 wire validation，但完整 Registration、
RegistrationUpdate、Heartbeat 与 Execution fact 仍可能被 Control、State 或 Runtime 拒绝。
此前 gRPC transport 在把 fact 放入异步队列后立即返回 `Registered` 或 `Ack`。如果后续
Controller composition 因全局 ResourceId 冲突、未知 ownership、execution identity 冲突或
持久化失败而拒绝 fact，Node 仍会误以为该 fact 已被系统接受，形成 Node 与 Controller 的
状态分歧。

把 Control 规则移入 `core/integration` 会破坏既有 authority 边界，因此 transport 必须能够
等待 application decision，但不能自行产生该 decision。

## Decision

1. `core/integration` 通过 transport-neutral delivery/completion envelope 把已完成 wire 与
   session validation 的 fact 交给 Controller composition。
2. Integration Server 仅在既有 Control/State/Runtime authority 接受 fact、同步 lifecycle
   transition 成功、controller checkpoint 写入并且 SQLite batch commit 成功后完成 acceptance。
3. gRPC transport 只在 application acceptance 成功后发送 `Registered` 或对应 sequence 的
   `Ack`。权威规则拒绝映射为 `FailedPrecondition`；持久化或 application service 故障映射为
   `Unavailable`；completion channel 丢失或 30 秒超时分别映射为 `Unavailable` 与
   `DeadlineExceeded`。所有失败均终止当前 session；Node Service 按既有 reconnect 机制重新
   协商，不把这些失败解释为 terminal execution outcome。
4. 新 session 在 application acceptance 期间只作为 pending route 存在，不能接收 Execute 或
   Cancel。旧 session 立即被 fence，避免 Control 已切换 lease 时仍向旧 route 下发命令。
5. Registration acceptance 不在同一 fact 内驱动 Ready Task。Node 收到 `Registered` 后立即发送
   的首个 Heartbeat 完成 route/liveness 证明，并触发既有 `drive_ready_tasks` 路径。
6. `Unavailable` 是 Integration 自身观察到的本地 transport fact，没有远端 Ack，因此不需要
   application completion response。

## Consequences

- `Registered` 与 `Ack` 现在表示 Controller durable acceptance，而不只是 transport receipt；
- `core/integration` 仍不依赖 Control、State 或 Runtime，也不获得 registration authority；
- application rejection 不再静默，Node 会看到 gRPC failure 并进入保守重连；
- 注册完成到首个任务 dispatch 至少需要一个已接受 Heartbeat，符合 lease/liveness 门槛；
- Node Protocol protobuf v0.2 wire shape 不变，但 acknowledgement 语义被收紧。

## Acceptance evidence

- transport 单元测试证明 completion 前不返回成功，application rejection 映射为
  `FailedPrecondition`；
- Node Service 正式协议闭环测试显式完成 application acceptance 后才允许 route；
- synthetic Node 使用 node-scoped ResourceId 和 session-unique capability contract，既不会因
  fixture 自身发生全局冲突，也不会把 smoke Mission 调度到已有同类 Node；
- self-triggered smoke 经 Controller Mission API 完成 Match -> Commit -> Execute -> facts。
