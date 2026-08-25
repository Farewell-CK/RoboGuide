# ADR-0006：Heterogeneous EAIOS Integration Contract v0.1

> 历史记录：同步 NodeGateway/HTTP reference binding 保留；节点侧异步接入与 Local Integration 边界已由 ADR-0010 和 Node Protocol v0.2 取代。

- 状态：Proposed for Integration Contract v0.1
- 日期：2026-08-24
- 范围：RoboGuide Runtime 与异构 Local EAIOS / Vendor Runtime 的通用执行边界

## 背景

现有 `NodeGateway` 已能表达 Node identity、status 和 invocation routing，但旧
`ExecutionCommand` 只说明 Task/Group/Role/Node，无法告诉真实 Local EAIOS 需要执行什么。
同时，真实远程 status 可能 timeout 或协议失败，不能继续假设查询总能返回 `NodeStatus`。

RoboGuide 的目标是协调多个相同或不同 EAIOS，而不是要求设备运行 RoboGuide-owned Agent，
也不是以全局 Control 替代节点的 Immediate How 和最终 Safety。

## 提议的决策

1. Node Contract 是 semantic contract，不是 HTTP、ROS 2、gRPC 或某个厂商 SDK。
2. Registration 显式携带 `roboguide.node.v0.1`；未知版本必须拒绝，不能静默兼容。
3. `ExecutionCommand` 携带 canonical `ExecutionIntent`：可扩展 `OperationRef` 和
   transport-neutral scalar parameters。Operation version 与 Node Contract version 独立。
4. `OperationRef` 不是 Rust enum。Adapter 将 canonical operation 映射为 Local EAIOS Skill、
   Service、Primitive 或 vendor API；Local Skill name 不进入 Core contract。
5. Mission Intelligence 描述 canonical What，Matching/Scheduler 不解析 intent，Runtime 只
   路由，Adapter/Local EAIOS 保留翻译、Local Planning、Hardware Control 与 Safety。
6. `NodeGateway::status` 返回 fallible result。transport failure 保留旧 reported health，
   Runtime 只记录 RoboGuide-observed liveness `Unreachable`，不得伪造本地 `Offline`。
7. HTTP/JSON 是第一份 reference adapter；serde DTO 和 endpoint 只存在于 `core/adapters`。
8. Reference configured backend 只允许 canonical operation 查找本地预配置 fixed argv；
   网络输入不能指定 executable，不使用 shell 拼接 parameters。

## 当前限制

v0.1 invocation 仍同步返回 `TaskCompleted`、`TaskFailed` 或 `SafeStopped`。真实长动作所需的
Accepted/Started/job identity/callback/stream lifecycle、operation catalog/discovery、认证、
retry/idempotency、payload size policy 和真实 EAIOS SDK mapping 均延后，必须通过真实 endpoint
证据后再决定。

## 影响与接受证据

- Core 不含 Lite3、Robonix 或 ROS 2 专用字段；新增 backend 不修改 Control/Scheduler/Runtime；
- 相同 canonical intent 可由两个 configured backend 翻译为不同 local invocation；
- HTTP registration/status/execute DTO 显式转换为 domain，版本和 identity mismatch 被拒绝；
- timeout 作为 gateway error 进入 Runtime，并只影响 liveness；
- `real-node-smoke` probe 默认不执行动作，显式 `--execute` 才发送 fixture intent；
- 当前证据仅为 deterministic offline tests，不声明 real-device verified。
