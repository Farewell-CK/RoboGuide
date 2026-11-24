# RoboGuide 文档总索引（Documentation Hub）

欢迎进入 RoboGuide（DEAIOS）仓库文档体系。本索引按**读者角色与权威层级**组织，覆盖 V2 架构基线、核心系统数据流、257 份架构决策记录（ADR）、开发与设备接入规范、运维手册及全部历史验收归档。

> - 返回项目主页：[`README.md`](../README.md) · 在线双语站点：[RoboGuide Technical Docs](https://roboguide-docs.chaokedu.workers.dev)
> - 阅读边界：历史验收、审计记录与实验快照保留原时点与原语言，**软件进程验收不代表真机物理连续性或长期 SLO 证明**。

## 按角色快速进入

| 你想做什么 | 推荐阅读顺序 |
| --- | --- |
| **理解整体设计与数据流** | [V2 架构手册](architecture/v2/architecture-handbook.md) → [核心系统数据流图解](architecture/v2/system-dataflows.md) → [V2 现行架构](architecture/v2/README.md) |
| **参与核心 Rust / Python 开发** | [开发基线](development/README.md) → [编码规范](development/coding-standards.md) → [ADR 核心主题](#二架构决策记录decisions-00010259共-257-份) |
| **接入新机器人 / Local EAIOS** | [设备扩展规范 v0.2](extensions/device-extension-conformance-v0.2.md) → [跨语言契约 `contracts/`](../contracts/) → [接入经验清单](development/local-eaios-integration-lessons.md) |
| **生产部署、复制高可用与排障** | [可执行部署 Profile](development/deployment-profile-v0.1.md) → [观测与告警](development/observability-v0.1.md) → [故障与可靠矩阵](development/runtime-reliability-fault-matrix.md) |
| **查证实验与历史验收证据** | [验收记录总索引](development/acceptance/README.md) → [原 README 研发日志归档](development/readme-development-log-archive-2026-11.md) → [Eval Harness](../evaluation/README.md) |

---

## 一、权威架构基线（`architecture/`）

### 现行 V2 基线（Source of Truth）

- [`architecture/v2/README.md`](architecture/v2/README.md)（[English](architecture/v2/README.en.md)）：V2 现行核心语义、职责分工、不变量与模块权威边界。
- [`architecture/v2/system-dataflows.md`](architecture/v2/system-dataflows.md)：**五类核心协议与系统数据流图解**（部署拓扑、任务全生命周期控制流、遥测瘦身与空间 CAS 流、四种协作耦合模式分流、L0–L4 分级恢复与安全停机流）。
- [`architecture/v2/architecture-handbook.md`](architecture/v2/architecture-handbook.md)（[English](architecture/v2/architecture-handbook.en.md)）：面向新读者的 V2 架构导读手册。
- [`architecture/v2/current-architecture.md`](architecture/v2/current-architecture.md)（[English](architecture/v2/current-architecture.en.md)）：当前主线代码的 4 张矢量架构图与源码对应表。
- [`architecture/v2/implementation-topology.md`](architecture/v2/implementation-topology.md)：逐次软件切片的详细接线与拓扑演进记录。
- [`architecture/v2/RoboGuide_Architecture_Baseline_V2.docx`](architecture/v2/RoboGuide_Architecture_Baseline_V2.docx) & [`images/roboguide-v2-overall-architecture.png`](images/roboguide-v2-overall-architecture.png)：最初 V2 概念快照（历史留档，演进以正文与 ADR 为准）。

### 历史架构快照（V1.1）

- [`architecture/v1.1/README.md`](architecture/v1.1/README.md) · [`Distributed_Embodied_AI_OS_总体架构详细设计说明书_V1.1.docx`](architecture/v1.1/Distributed_Embodied_AI_OS_总体架构详细设计说明书_V1.1.docx) · [`images/distributed-embodied-ai-os-architecture-v1.1.png`](images/distributed-embodied-ai-os-architecture-v1.1.png)

> 新增架构参考材料请先完整核对再归入 `architecture/<version>/`，勿将临时 DOCX/PDF 或大图留在仓库根目录。

---

## 二、架构决策记录（`decisions/`，`0001`–`0259`，共 257 份）

全量 ADR 原件保留在 [`decisions/`](decisions/)（由文档站构建脚本自动生成完整按号索引，见 [`website/pages/decisions.md`](../website/pages/decisions.md)）。
> **历史编号说明**：仓库因并行分支历史，保留了两组相同编号的独立文件——`0255`（`0255-host-owned-mission-objective.md` 与 `0255-private-node-plan-preflight.md`）与 `0256`（`0256-complete-node-preflight-sources.md` 与 `0256-native-chat-task-agent.md`），均原样留存不改名。

下表按 8 大核心主题精选关键 ADR 入口：

### 1. 核心边界与 Node 协议

- [`ADR-0001`](decisions/0001-rust-core-python-edges.md)：Rust 核心与 Python 边缘职责边界（`Proposed`）
- [`ADR-0002`](decisions/0002-deaios-node-contract.md)：DEAIOS Node Contract v0
- [`ADR-0006`](decisions/0006-heterogeneous-eaios-integration-contract.md)：异构 EAIOS 接入与 Canonical Capability 合同
- [`ADR-0009`](decisions/0009-node-service-grpc-protocol.md) & [`ADR-0010`](decisions/0010-single-node-service-local-integration-engine.md)：gRPC Node Protocol、单一 Node Service 与 Local Integration Engine
- [`ADR-0021`](decisions/0021-device-extension-boundary-conformance.md) & [`ADR-0023`](decisions/0023-application-accepted-node-protocol-facts.md)：设备扩展合规边界与 Controller 持久接纳 ACK 原则

### 2. Mission 语义、规划审查与时间窗前检

- [`ADR-0003`](decisions/0003-mission-plan-contract.md) & [`ADR-0034`](decisions/0034-mission-semantic-contract-normalization.md)：MissionPlan 跨语言合同与语义规范化
- [`ADR-0018`](decisions/0018-mission-intent-loop.md)、[`ADR-0030`](decisions/0030-mission-semantic-admission-and-deployability.md) & [`ADR-0032`](decisions/0032-mission-review-and-repair-loop.md)：意图澄清、语义接纳分离、独立 Review 与有界 Repair
- [`ADR-0031`](decisions/0031-canonical-capability-catalog.md)、[`ADR-0037`](decisions/0037-mission-grounding-context.md) & [`ADR-0038`](decisions/0038-blocking-clarification-and-grounding-acquisition.md)：Canonical Capability Catalog 与受控 Grounding Snapshot
- [`ADR-0033`](decisions/0033-task-satisfaction-boundary.md)、[`ADR-0039`](decisions/0039-mission-satisfaction-freshness-policy.md) & [`ADR-0093`](decisions/0093-task-satisfaction-rejection-and-evidence-projection.md)：Task 满足判定、Freshness 策略与语义拒绝
- [`ADR-0109`](decisions/0109-published-plan-validation-and-bounded-draft-recovery.md)、[`ADR-0255`](decisions/0255-host-owned-mission-objective.md) & [`ADR-0259`](decisions/0259-dependency-window-preflight.md)：只读发布前检、宿主目标与依赖时间窗诊断（`?timing=v0.1`）

### 3. 调度、资源承诺与 Execution Group

- [`ADR-0004`](decisions/0004-recovery-commitment-lifecycle.md) & [`ADR-0005`](decisions/0005-allocation-state-projection-authority.md)：恢复承诺生命周期与 Allocation State 单向投影
- [`ADR-0007`](decisions/0007-mission-actor-continuity.md) & [`ADR-0040`](decisions/0040-mission-actor-binding-semantics.md)：Actor 连续性与 PhysicalEntity 绑定语义
- [`ADR-0013`](decisions/0013-mission-level-execution-group.md) & [`ADR-0029`](decisions/0029-bounded-joint-scheduling-and-future-reservations.md)：Mission-level Group、有界联合调度与未来预订日历
- [`ADR-0041`](decisions/0041-core-scheduling-disposition.md)–[`ADR-0043`](decisions/0043-registry-anti-rollback-provenance.md)：调度反馈分类、Commit 身份重验与防回滚水位
- [`ADR-0140`](decisions/0140-control-resource-quota-commitment.md)、[`ADR-0206`](decisions/0206-quantitative-resource-allocation.md) & [`ADR-0209`](decisions/0209-spatiotemporal-resource-authority.md)：租户配额、定量 Compute 与时空资源边界

### 4. 四种协作耦合模式与运行时边界

- [`ADR-0015`](decisions/0015-runtime-execution-boundary.md) & [`ADR-0020`](decisions/0020-execution-coordination-relations.md)：Runtime 权威边界与 Execution Coordination Relations
- [`ADR-0026`](decisions/0026-execution-coupling-and-group-views.md) & [`ADR-0027`](decisions/0027-runtime-coordination-evidence-completion.md)：**四种协作耦合模式（`Independent` / `SequentialHandoff` / `ConcurrentCooperation` / `TightlyCoupledCooperation`）、Group 共享视图、`SharedSpatialReference` 与双端 `PeerChannel` 就绪门禁**
- [`ADR-0028`](decisions/0028-durable-command-recovery-and-attempts.md)：Durable Command Outbox、Physical Attempt 代次与恢复组合

### 5. State、Memory 与空间记忆（Spatial）

- [`ADR-0016`](decisions/0016-distributed-spatial-memory.md)、[`ADR-0024`](decisions/0024-federated-state-and-selective-memory.md) & [`ADR-0025`](decisions/0025-memory-provider-backend-and-workflow.md)：分布式空间地图 CAS、联邦 State 与选择性 Memory 目录
- [`ADR-0104`](decisions/0104-state-consumers-belief-and-semantic-verifier.md)–[`ADR-0108`](decisions/0108-durable-group-memory-handoff.md)：Belief 消费策略、Memory 准备/使用租约、部分撤销对账与 Group 交接
- [`ADR-0148`](decisions/0148-artifact-owner-identity-and-retained-layout.md)、[`ADR-0157`](decisions/0157-source-artifact-retention-release-and-reclamation.md) & [`ADR-0208`](decisions/0208-scoped-typed-map-lifecycle.md)：Artifact CAS 所有权、离线源回收与 Scoped Typed-Map 生命周期
- [`ADR-0234`](decisions/0234-controller-reported-position-inspection.md) & [`ADR-0235`](decisions/0235-origin-memory-grounding.md)：Controller 报告位置只读检查与起点 Memory Grounding

### 6. 故障恢复、安全停机与取消结案

- [`ADR-0012`](decisions/0012-controller-checkpoint-recovery.md)、[`ADR-0057`](decisions/0057-confirmed-stop-role-recovery.md) & [`ADR-0061`](decisions/0061-confirmed-stop-group-continuation.md)：Checkpoint 保守恢复、确认停止后角色恢复与整组续跑
- [`ADR-0110`](decisions/0110-execution-generation-and-process-stop-evidence.md) & [`ADR-0120`](decisions/0120-versioned-runtime-plan-revision.md)：Attempt 代次、进程退出证据与运行期计划修订
- [`ADR-0232`](decisions/0232-task-cancellation-settlement.md)、[`ADR-0233`](decisions/0233-local-administrator-cancel-settlement.md) & [`ADR-0250`](decisions/0250-explicit-local-execution-unknown.md)：任务取消人工结案与显式 `Unknown` 非终态映射

### 7. 节点接入、复制 HA、存储治理与事件瘦身

- [`ADR-0083`](decisions/0083-quorum-controller-replication.md) & [`ADR-0159`](decisions/0159-original-content-quorum-and-raft-reference-lifecycle.md)：OpenRaft 三成员共识与原内容多数派边界
- [`ADR-0239`](decisions/0239-online-static-node-identity-adoption.md) & [`ADR-0242`](decisions/0242-online-deployment-publications.md)：静态 Node 证书在线采用与在线实体发布
- [`ADR-0243`](decisions/0243-catalog-recovery-checkpoint.md) & [`ADR-0244`](decisions/0244-state-retention-and-deduplicated-storage.md)：Catalog 事务恢复投影与 Storage v3 无损遥测正文去重
- [`ADR-0255`](decisions/0255-private-node-plan-preflight.md) & [`ADR-0256`](decisions/0256-complete-node-preflight-sources.md)：Node 私有配置只读计划预检与完整来源证明
- [`ADR-0257`](decisions/0257-process-local-node-liveness.md) & [`ADR-0258`](decisions/0258-periodic-node-telemetry-persistence.md)：进程内 Liveness 续期与周期遥测语义去重/间隔落盘

### 8. 对话工作台与真机任务委派

- [`ADR-0231`](decisions/0231-local-eaios-and-device-integration.md)：本机 EAIOS 任务委派与设备接入两类模型
- [`ADR-0251`](decisions/0251-readonly-conversation-workbench.md)–[`ADR-0254`](decisions/0254-installed-chat-workbench.md) & [`ADR-0256`](decisions/0256-native-chat-task-agent.md)：`roboguide chat` 对话工作台、任务提交意图、流式终端与原生任务 Agent

---

## 三、开发、接入与运维指南

### 工程开发与规范

- [`development/README.md`](development/README.md)：仓库模块职责、允许依赖方向与工程基线
- [`development/coding-standards.md`](development/coding-standards.md)：Rust/Python 函数文档注释、类型安全、错误处理与门禁要求
- [`development/controller-persistence-performance.md`](development/controller-persistence-performance.md) & [`development/deployment-evidence-boundaries.md`](development/deployment-evidence-boundaries.md)：事务批处理、增量 Checkpoint 与部署来源边界

### 设备、仿真与 Local EAIOS 接入

- [`extensions/device-extension-conformance-v0.2.md`](extensions/device-extension-conformance-v0.2.md)（历史初版：[`v0.1`](extensions/device-extension-conformance-v0.1.md)）：不修改核心接入 Local EAIOS 的配置与离线校验规范
- [`extensions/bounded-observation-and-evidence-closure.md`](extensions/bounded-observation-and-evidence-closure.md)：有界主动观测与证据闭环
- [`extensions/habitat-relocation-profile.md`](extensions/habitat-relocation-profile.md) & [`extensions/habitat-independent-live-profile.md`](extensions/habitat-independent-live-profile.md)：Habitat 搬运与多机独立部署 Profile
- [`development/local-eaios-integration-lessons.md`](development/local-eaios-integration-lessons.md)：EMOS/Robonix 接入经验与可复用验收清单
- [`contracts/`](../contracts/)：14 类跨语言契约（MissionPlan, Node Protocol/Catalog, State, Memory, Spatial, Recovery, Federation 等）

### 对话查询工作台（`roboguide chat`）

- [`development/conversation-workbench-v0.1.md`](development/conversation-workbench-v0.1.md) → [`v0.2`](development/conversation-workbench-v0.2.md) → [`v0.3`](development/conversation-workbench-v0.3.md) → [`v0.4`](development/conversation-workbench-v0.4.md)
- [`development/conversation-workbench-agent-loop-design-2026-11-13.md`](development/conversation-workbench-agent-loop-design-2026-11-13.md) · [`conversation-workbench-state-v0.1.md`](development/conversation-workbench-state-v0.1.md) · [`chat-real-use-remediation-20261116.md`](development/chat-real-use-remediation-20261116.md) · [`chat-terminal-ux-20261117.md`](development/chat-terminal-ux-20261117.md)

### 生产部署、运维、观测与容灾

- [`development/deployment-profile-v0.1.md`](development/deployment-profile-v0.1.md) · [`tools/deployment/README.md`](../tools/deployment/README.md)：可执行部署 Profile 与零 Node 检查器
- [`development/observability-v0.1.md`](development/observability-v0.1.md) · [`listener-capacity-v0.1.md`](development/listener-capacity-v0.1.md) · [`operator-network-bounds-v0.1.md`](development/operator-network-bounds-v0.1.md)：Prometheus 指标、服务入口容量与网络期限边界
- [`development/node-protocol-diagnostics.md`](development/node-protocol-diagnostics.md) · [`hot-node-enrollment-v0.1.md`](development/hot-node-enrollment-v0.1.md) · [`online-deployment-publications-v0.1.md`](development/online-deployment-publications-v0.1.md)：协议诊断、热接入与在线实体发布
- [`development/controller-replication.md`](development/controller-replication.md) · [`replicated-federation-deployment-v0.1.md`](development/replicated-federation-deployment-v0.1.md) · [`runtime-reliability-fault-matrix.md`](development/runtime-reliability-fault-matrix.md)：三成员复制高可用、跨域 HA 与故障矩阵

---

## 四、项目范围、路线与治理清单

- [`project-goals-and-mvp.md`](project-goals-and-mvp.md)：通用分布式具身 OS 目标与多机异构 MVP 演进
- [`mvp-definition.md`](mvp-definition.md)：完整 MVP 草案、Slice v0.1 与冻结清单
- [`implementation-backlog.md`](implementation-backlog.md)：V2 开放架构问题与延后实现项
- [`development/adr-follow-up-register.md`](development/adr-follow-up-register.md)：全 ADR 遗留项核验与依赖顺序
- [`development/future-research-ideas.md`](development/future-research-ideas.md)：后续版本研究想法（不纳入当前基线）
- [`development/release-preparation-audit.md`](development/release-preparation-audit.md)：历史凭据扫描、锁定依赖许可证盘点与开源发布技术准备审计

---

## 五、历史验收、审计与实验归档（零丢失）

- **核心归档入口**：
  - [`development/acceptance/README.md`](development/acceptance/README.md)：**全部 143 份历史验收记录分主题总索引**（覆盖 2026-10 非发布整改与 2026-11 真机/取消结案/热接入验收）
  - [`development/readme-development-log-archive-2026-11.md`](development/readme-development-log-archive-2026-11.md)：原根目录 `README.md`（`e4c03a55`）10~11 月研发与验收流水账无损归档
- **阶段对账与基线审计**：
  - [`development/roboguide-software-completion-plan-2026-10-15.md`](development/roboguide-software-completion-plan-2026-10-15.md) · [`nonrelease-acceptance-matrix-2026-10-26.md`](development/nonrelease-acceptance-matrix-2026-10-26.md)
  - [`development/code-and-e1-baseline-2026-10-30.md`](development/code-and-e1-baseline-2026-10-30.md) · [`selective-main-integration-plan-20261112.md`](development/selective-main-integration-plan-20261112.md) · [`rg-data-governance-implementation.md`](development/rg-data-governance-implementation.md)
  - [`development/pre-c1-readiness-audit-2026-09-15.md`](development/pre-c1-readiness-audit-2026-09-15.md) · [`roboguide-branch-audit-2026-10-22.md`](development/roboguide-branch-audit-2026-10-22.md)
- **历史实验与外部交接快照**：
  - [`experiments/e2-protocol.md`](experiments/e2-protocol.md) · [`experiments/e2-dog-b-takeover.md`](experiments/e2-dog-b-takeover.md)
  - [`codex-sync/STATUS.md`](codex-sync/STATUS.md) · [`codex-sync/DECISIONS.md`](codex-sync/DECISIONS.md)
  - [`../experiments/`](../experiments/)（2026-09-20 EP51 审计快照：`ep51-failure-audit-20260920`、`ep51-full-chain-readiness-20260920`、`ep51-physical-diagnostic-20260920`）
  - [`../mission/POSTURE_VERIFIER_ACCEPTANCE.md`](../mission/POSTURE_VERIFIER_ACCEPTANCE.md)（MI 姿态观测独立软件验收）
