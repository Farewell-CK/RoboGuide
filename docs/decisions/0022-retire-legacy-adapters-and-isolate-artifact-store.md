# ADR-0022：Retire Legacy Adapters and Isolate Artifact Store

- 状态：Accepted
- 日期：2026-09-01
- 范围：旧 HTTP NodeGateway、real-node-smoke、Artifact CAS infrastructure

## Context

正式设备接入已经由 Node Protocol v0.2、单一 `roboguide-node` 和 Node Service 的
Local Integration Engine 承担。旧 `core/adapters` 中的同步 HTTP NodeGateway 不再是正式
生产路径；在迁移前，`core/adapters::artifact` 仍被 Integration Server 用作独立 Artifact
HTTP 数据平面。将两者继续放在同一泛化目录下，会让开发者误以为新设备需要编译期 adapter。

`apps/real-node-smoke` 原先也依赖旧 HTTP NodeGateway 和 v0.1 intent fixture，不能继续
作为当前 Node Protocol 的设备接入示例。

## Decision

1. 删除旧 `core/adapters` crate，包括同步 HTTP NodeGateway、HTTP wire DTO、旧 bridge
   残留和相关旧依赖。它们不再拥有生产调用方。
2. 保留 smoke 能力，但将 `apps/real-node-smoke` 改为正式 Node Protocol v0.2 的合成节点：
   默认验证 Hello、Welcome、Register、Registered、Heartbeat 和 Ack；显式
   `--simulate-execute` 只发送合成 Accepted/Started/Completed facts，绝不调用 Local EAIOS
   或执行物理动作。
3. 将仍在使用的 `FileSystemArtifactStore` 迁移到 `core/artifact-store` crate。该 crate
   是 `ports::ArtifactBlobStore` 的具体 infrastructure 实现，只负责 opaque bytes、digest、
   path safety、staging/upload 和 durability；不拥有 Map/Task/Group lifecycle 或 Control
   commitment。
4. `apps/integration-server` 直接依赖 `artifact-store`；Node Service 内部的
   `ArtifactClient`/`ArtifactStager` 仍是节点侧 Artifact data-plane client，不与服务器 CAS
   混为同一生命周期。
5. `core/ports::NodeGateway`、`core/runtime` 和 `core/testkit` 的 legacy synchronous
   contract 暂不在本 ADR 中删除。它们仍被既有 Runtime 单元测试覆盖，后续若迁移必须另行
   更新 Runtime ownership 和 ADR，不得把本次 crate 删除误称为 Runtime 完成迁移。

## Consequences

- 仓库不再有一个泛化的 `core/adapters` 目录，正式扩展入口只有 Node Service 配置和
  deployment-owned Local EAIOS facade。
- Artifact storage 仍保持可复用的 core infrastructure 位置，不污染 Node Protocol transport
  或 Node Service execution lifecycle。
- smoke 工具验证的是正式协议边界；它不提供真实设备 readiness、物理安全或硬件执行证据。
- 历史 ADR 中对 HTTP reference 的描述保留为历史记录，当前结构和依赖以本 ADR 为准。

## Acceptance evidence

- `rg` 不再发现生产代码对 `core/adapters`、`HttpNodeGateway` 或旧 HTTP DTO 的引用；
- `cargo tree` 显示 `integration-server -> artifact-store`，而非旧 `adapters` crate；
- Artifact HTTP 的 CAS 测试继续通过；
- `real-node-smoke` 编译并能在 Node Protocol v0.2 server 上完成合成握手；
- workspace Rust、Python 和文档质量检查全部通过。
