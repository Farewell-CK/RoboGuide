# ADR-0021：Device Extension Boundary Consolidation & Conformance v0.1

> 后续清理见 ADR-0022：旧 HTTP/reference adapter 已退役，Artifact CAS 已迁移至
> `core/artifact-store`；本 ADR 的 Node Protocol、Node Service 和 deployment facade 边界继续有效。

- 状态：Accepted
- 日期：2026-09-01
- 范围：Node Protocol transport、Controller composition、Node Service Local Integration Engine 与 deployment-owned Local EAIOS facade

## Context

当前设备接入主链已经由 Node Protocol v0.2、单一 `roboguide-node` 和声明式 Local
Integration Engine 组成，但目录历史留下了容易混淆的边界：

1. `core/integration` 同时包含 formal gRPC session/router 和依赖 Control、State、Runtime
   的 `IntegrationRuntimeBridge`；
2. 旧 `core/adapters` 曾同时包含同步 HTTP reference、早期 configured backend 和 Artifact CAS，
   但正式的 HTTP/dynamic gRPC/MCP Local EAIOS 扩展已经位于 Node Service。

如果不明确 ownership，后续扩展可能把 Control reservation、Runtime lifecycle 或 Local How
重新塞回 transport/adapter，或者要求每个新 EAIOS 编译 RoboGuide 专用分支。

## Decision

### 责任边界

- `core/integration` 只提供 Node Protocol v0.2 wire、生成的 gRPC service、并发 session、
  lease/session fencing、NodeId router 和 wire 校验。它不依赖 Control、State、Runtime，
  不拥有执行生命周期。
- `core/orchestration` 是 Controller application/orchestration 组合层。
  `IntegrationRuntimeBridge` 在此消费 integration facts 并调用既有 Control/State/Runtime
  authority；它不选择 endpoint、Local EAIOS operation 或 replacement。
- 旧同步 HTTP NodeGateway、wire DTO 和 configured command backend 已由 ADR-0022 退役；
  `real-node-smoke` 现在直接验证正式 Node Protocol v0.2，不再依赖旧 adapter。
- `core/artifact-store` 是独立的 filesystem content-addressed storage infrastructure，
  只负责 bytes、digest、path safety、upload/read durability；不拥有 Map/Task/Group state
  或 Control commitment。
- `core/node-service` 是每台节点机器唯一的正式服务和 Local Integration Engine。它编译
  配置，驱动 HTTP、dynamic gRPC、MCP，维护 journal/local locks，并产生 Node Protocol
  facts；不拥有 Control reservation/recovery authority。
- `integrations/` 是 deployment-owned Local EAIOS facade，保留 vendor SDK、Immediate
  How、Local Planning、Hardware Control 和最终 Safety。新设备通过配置和受支持 driver 接入，
  不修改或重新编译 RoboGuide core。
- 旧 `core/adapters::bridge::ConfiguredCommandBackend` 没有生产调用，安全退役；固定
  command/Local How 不再作为 RoboGuide adapter API。

### Extension Conformance v0.1

`roboguide.extension-conformance/v0.1` 复用 Node Service 的生产 catalog compiler，并提供
离线 `--validate` 报告。Conformance 最低要求 `roboguide.node-config/v0.4`，每个 exact
canonical capability 必须有唯一 owner 和 readiness workflow。报告验证固定 connection、
operation、request mapping、execution-state mapping、required resources，以及共同的
execute/status/cancel lifecycle contract；它不会打开 endpoint、访问 Controller 或执行物理
动作。

所有 driver 共用以下安全不变量：

- Execute 只允许在已 Commit resource IDs 下发后发生，Node local lock 不是 Control commitment；
- status 是 terminal physical outcome 的唯一来源，cancel acknowledgement 不等于 `Cancelled`；
- unknown、timeout、重复 identity 冲突、journal/restart ambiguity 都 fail-closed 并进入
  reconciliation fence；
- restart 可以 status-poll 已持久化 handle，但禁止危险自动重放 execute；
- 网络 ExecutionIntent 不能选择 executable、endpoint、service、method、descriptor 或
  MCP tool。

### Compatibility and evidence

Node Protocol v0.2、Runtime/Controller checkpoint、Artifact data plane 和 Domain contracts
不因本 ADR 改版。旧 v0.2/v0.3 Node config 仍可启动以支持 legacy smoke，但不构成
Extension Conformance 或 Phase 1 hardware-readiness evidence。真实认证/TLS、descriptor 与
vendor 状态映射、取消和幂等、物理安全、Local Safety、map/localization frame 以及断电重启
演练必须由部署方在真实 facade/硬件上验证；离线报告不得宣称这些能力完成。

## Consequences

- transport crate 可独立测试和演进，不再携带 Controller authority 依赖；
- Controller composition 仍可复用同一个 bridge，不新增第二份 execution map；
- Artifact storage 保持独立数据平面；RoboGuide core 不提供 EAIOS 编译期插件目录；
- 新设备开发者获得可复制的真实配置样例和无网络 conformance 命令；
- 硬件接入仍需要 deployment-owned facade 和真实故障证据，不能由 CI 单独完成。

## Acceptance evidence

- `cargo run -p roboguide-node -- --validate scenarios/extension-conformance-v0.1/node.toml`
  成功输出三类 driver 的无密钥 conformance JSON；
- `cargo test -p node-service conformance --locked` 覆盖共享 workflow shape、唯一 owner、
  exact readiness 和失败诊断；
- `core/integration` 编译时不再依赖 Control、State 或 Runtime；
- 全仓 Rust/Python/文档质量检查通过；
- 真实部署另行保存 endpoint、认证、状态、cancel、重启和 Local Safety 证据。
