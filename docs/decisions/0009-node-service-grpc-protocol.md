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

## v0.1 生命周期补充

同一个 `execution_id` 永久绑定首次 canonical invocation；不同 invocation 复用该 ID
必须拒绝。Server route 仅在当前 session 的 matching lease heartbeat 未过期时可用；新
session 注册会 fence 旧流，迟到旧消息不得更新 route、State 或 execution evidence。

第一版 Robonix Adapter 位于 Node Service 侧，通过本地安装的公开 Robonix Python SDK
使用 Atlas discovery/channel、Scene `goal_room` 与 Navigation `navigate/status/cancel`，
把 `mobility.reach_region@v1` 转成 Robonix 本地执行。该 helper 是 local-only backend，
其 Atlas/provider/stub 类型不进入 Node Protocol。

Integration Server composition root 将 validated registration/heartbeat/execution facts 交给
`IntegrationRuntimeBridge`：Registration/Heartbeat 复用 Control lease authority 并更新
Shared Node State；Execute/Cancel 按既有 NodeId route；terminal ExecutionEvent 转为已有
Runtime `NodeEvent` evidence。它不改变 Matching/Scheduler 算法。
