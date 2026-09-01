# 文档索引

## 当前架构

- [`architecture/v2/RoboGuide_Architecture_Baseline_V2.docx`](architecture/v2/RoboGuide_Architecture_Baseline_V2.docx)：当前架构权威源文档，包含 V2 总体架构图；
- [`architecture/v2/README.md`](architecture/v2/README.md)：V2 仓库内结构化摘要；
- [`images/roboguide-v2-overall-architecture.png`](images/roboguide-v2-overall-architecture.png)：V2 总体架构独立原图。

## 项目范围

- [`project-goals-and-mvp.md`](project-goals-and-mvp.md)：通用 OS 目标、多机异构 MVP 和应用演进；
- [`mvp-definition.md`](mvp-definition.md)：完整 MVP 草案、已批准的 Slice v0.1、候选场景和冻结清单；
- [`implementation-backlog.md`](implementation-backlog.md)：V2 开放架构问题、待定 MVP 决策和延后实现项。

## 开发

- [`development/README.md`](development/README.md)：`Proposed` 开发基线、目标目录、模块职责、依赖方向和变更门槛；
- [`development/coding-standards.md`](development/coding-standards.md)：Rust/Python 函数文档、类型、错误处理、测试和质量门槛；
- [`extensions/device-extension-conformance-v0.1.md`](extensions/device-extension-conformance-v0.1.md)：不修改 RoboGuide core 接入新 Local EAIOS 的配置、离线 conformance 与真机验证边界；
- [`decisions/0001-rust-core-python-edges.md`](decisions/0001-rust-core-python-edges.md)：`Proposed` Rust 核心与 Python 边缘职责 ADR。
- [`decisions/0002-deaios-node-contract.md`](decisions/0002-deaios-node-contract.md)：DEAIOS 与本地 EAIOS/厂商运行时之间的 Node Contract v0。
- [`decisions/0003-mission-plan-contract.md`](decisions/0003-mission-plan-contract.md)：Mission Intelligence 向 Rust 核心交付 Task Graph 的 MissionPlan v0 合同。
- [`decisions/0004-recovery-commitment-lifecycle.md`](decisions/0004-recovery-commitment-lifecycle.md)：Recovery Commit 到 Rebind 之间的 Pending、Consume、Abort 和 terminal cleanup ownership。
- [`decisions/0005-allocation-state-projection-authority.md`](decisions/0005-allocation-state-projection-authority.md)：Control reservation authority 到非权威 Allocation State projection 的单向边界。
- [`decisions/0007-mission-actor-continuity.md`](decisions/0007-mission-actor-continuity.md)：Mission Actor 跨 Task 连续绑定与版本策略。
- [`decisions/0008-integration-server-node-connector.md`](decisions/0008-integration-server-node-connector.md)：Integration Server 与 Node Connector 长连接协议。
- [`decisions/0009-node-service-grpc-protocol.md`](decisions/0009-node-service-grpc-protocol.md)：正式 gRPC Node Protocol 与节点侧 Node Service/Adapter 边界。
- [`decisions/0010-single-node-service-local-integration-engine.md`](decisions/0010-single-node-service-local-integration-engine.md)：单一 Node Service、声明式 Local Integration Engine 与本地执行日志。
- [`decisions/0011-event-evidence-codec.md`](decisions/0011-event-evidence-codec.md)：可持久化、可重放的结构化事件 evidence codec。
- [`decisions/0012-controller-checkpoint-recovery.md`](decisions/0012-controller-checkpoint-recovery.md)：Controller projection checkpoint 与保守恢复。
- [`decisions/0013-mission-level-execution-group.md`](decisions/0013-mission-level-execution-group.md)：Mission-level Group 与多 TaskExecution 生命周期。
- [`decisions/0014-phase1-mission-orchestration.md`](decisions/0014-phase1-mission-orchestration.md)：完整 MissionPlan/DAG 编排与显式 Mission completion。
- [`decisions/0015-runtime-execution-boundary.md`](decisions/0015-runtime-execution-boundary.md)：Runtime live execution authority 与 Integration 边界。
- [`decisions/0016-distributed-spatial-memory.md`](decisions/0016-distributed-spatial-memory.md)：不可变地图 Artifact、Catalog 与双节点共享切片。
- [`decisions/0017-canonical-capability-contract-identity.md`](decisions/0017-canonical-capability-contract-identity.md)：可逆的 canonical capability 字符串与结构化身份规则。
- [`decisions/0018-mission-intent-loop.md`](decisions/0018-mission-intent-loop.md)：自然语言 Mission Request 的澄清、草案、风险审批与内部计划提交边界。
- [`decisions/0019-capability-readiness-and-localization-evidence.md`](decisions/0019-capability-readiness-and-localization-evidence.md)：真机 capability readiness 与结构化 localization verification evidence 边界。
- [`decisions/0020-execution-coordination-relations.md`](decisions/0020-execution-coordination-relations.md)：并发 logical executions 之间持续约束的 specification、Runtime state、evidence 与 recovery 边界。
- [`decisions/0021-device-extension-boundary-conformance.md`](decisions/0021-device-extension-boundary-conformance.md)：Node Protocol transport、Controller composition、Local Integration Engine 与 device-extension conformance ownership。
- [`decisions/0022-retire-legacy-adapters-and-isolate-artifact-store.md`](decisions/0022-retire-legacy-adapters-and-isolate-artifact-store.md)：退役旧 HTTP adapter，迁移 smoke 到 Node Protocol，并隔离 Artifact Store 基础设施。

## 历史架构

- [`architecture/v1.1/README.md`](architecture/v1.1/README.md)：V1.1 历史摘要；
- [`architecture/v1.1/Distributed_Embodied_AI_OS_总体架构详细设计说明书_V1.1.docx`](architecture/v1.1/Distributed_Embodied_AI_OS_总体架构详细设计说明书_V1.1.docx)：V1.1 原始文档；
- [`images/distributed-embodied-ai-os-architecture-v1.1.png`](images/distributed-embodied-ai-os-architecture-v1.1.png)：V1.1 历史架构图。

新的外部架构资料应先完整阅读，再归入 `architecture/<version>/`。不要把 DOCX、PDF
或临时图片长期留在仓库根目录。
