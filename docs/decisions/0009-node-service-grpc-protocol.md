# ADR-0009：RoboGuide Node Service 与正式 gRPC Node Protocol v0.1

## 状态

已接受（2026-08-24）。

## 决策

节点侧常驻程序命名为 `roboguide-node`。它读取 `config/node.toml`，按配置选择一个
`LocalEaiosAdapter`，主动建立 RoboGuide Server gRPC bidirectional stream，并维护
heartbeat、lease、reconnect、Execute、Cancel 和 execution snapshot replay。

`contracts/node/v0.1/roboguide-node.proto` 是正式 Node Protocol v0.1 合同。连接严格为
Hello -> Welcome -> Register -> Registered；只有完成协商与注册后才进入长期消息阶段。
Server 只选择双方共同支持的 Protocol 与 Node Contract version。

Local EAIOS Adapter 负责 discovery、health、canonical invocation、cancellation 和 execution
facts/snapshots。本边界不包含 Robonix、Atlas、Pilot、ROS topic 或厂商 SDK 类型。
`FakeAdapter` 仅提供离线和 composition evidence。

Node Service 的 execution registry 独立于 session/lease。新连接生成新 session/lease，
已知 execution_id 不会重新调用 Adapter；重连后主动 replay ExecutionSnapshot，供
RoboGuide 后续 reconciliation 使用。

现有 NDJSON/TCP `NodeConnector` 与 `IntegrationServer` 保留为 reference/debug transport，
不得被描述为正式 Node Protocol。生产入口 `apps/integration-server` 改用 tonic gRPC。
