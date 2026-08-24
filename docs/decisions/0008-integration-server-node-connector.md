# ADR-0008：Integration Server 与 Node Connector 长连接

## 决策

Node Connector 主动连接固定的 RoboGuide Integration Server。第一版使用长度受限的
newline-delimited JSON framed stream，语义上保持 gRPC bidirectional streaming 的
单连接双向 ordered message 模型：Hello/HelloAck、Register/RegistrationAccepted、
Heartbeat、RegistrationUpdate、Execute、Cancel 与 ExecutionEvent。framing 被隔离在
`core/integration`，未来可替换为 tonic service，不把 transport 泄漏到 Domain/Control。

Server 为每次连接生成新的 `session_id` 与 `lease_id`；它们只表示接入和当前控制租约，
不能代表 Task、Group 或 Mission 完成。Execute 必须携带 caller-owned `execution_id`，
Connector/backend 在重试和重连中按该 ID 做幂等，网络 timeout 不自动生成新的物理动作。
ExecutionEvent 只报告 Accepted、Started、Completed、Failed、Cancelled 等事实，业务
状态仍由 RoboGuide Control 根据事实推进。

断线后 Connector 可按退避重连并重新 Hello/Register；旧 session/lease 不复活，旧
execution_id 由上层重用以避免把重连误认为新任务。Local EAIOS/backend 保留 Immediate
How 与 final safety；Connector 不包含 Robonix 专用逻辑。

Connector 的 Execution Registry 生命周期属于 daemon，不属于单次 session。backend
执行在独立 blocking task 中，通过 channel 渐进发送 lifecycle facts；网络 read、write、
heartbeat 与 Cancel 始终独立运行。重连后 Connector 主动 replay 已知 execution 的
Running/Completed/Failed/Cancelled 状态；重复 Execute 只返回当前状态，不再次调用
backend。Unknown 只表示该 Connector daemon 没有该 identity 的记录。

Integration Server 的 accept loop 只负责协商新连接，每个已注册 Node session 进入独立
Tokio task，并通过共享 event channel 汇聚事实。因此多个 Node 可以同时在线，任一 Node
的慢消息或断线不会阻塞其他 Node 的 accept、heartbeat 或 execution stream。

## 范围

本轮提供 generic Tokio TCP 实现、deterministic backend、server/connector binaries 和
offline round-trip/idempotency tests。wire schema 当前仍为 `roboguide.node.v0.1` 的
Node Contract 注册语义；后续若增加跨语言字段，需新建版本而不是静默修改。
