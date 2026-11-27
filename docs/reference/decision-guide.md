# ADR 主题与决策摘要

从实际问题进入决策。摘要用于阅读导引，原 ADR 的编号、状态、兼容性和历史保留。完整目录自动从仓库生成，不手写数量。

[全部 ADR](../decisions/README.md) · [原始验收档案](../archive/README.md)

## 对象与身份

Actor 是逻辑连续性，PhysicalEntity 是部署执行身份，Node 承担路由与能力边界。

[ADR-0040 原文](../decisions/0040-mission-actor-binding-semantics.md)。

## 承诺与恢复

Proposal 不持有资源，Commit 形成原子承诺，Rebind 只消费已提交恢复分配。

[ADR-0004 原文](../decisions/0004-recovery-commitment-lifecycle.md)。

## 执行与满足

本地失败和负向 final verifier 是不同结果；TaskSatisfactionRejected 不伪装成执行失败。

[ADR-0093 原文](../decisions/0093-task-satisfaction-rejection-and-evidence-projection.md)。

## State 与证据

显式来源策略、原 lineage、TTL 和 exact Task verifier 进入真实消费门禁。

[ADR-0104 原文](../decisions/0104-state-consumers-belief-and-semantic-verifier.md)。

## Memory 准备

原 context、provider 与预算先持久化，Prepare/import/query/availability 必须先于 Execute。

[ADR-0105 原文](../decisions/0105-memory-reconciliation-authorization-and-spatial-provider.md)。

## Memory 使用与撤销

当前使用租约和 scoped release 独立于历史 placement，原 backend proof 不能替换。

[ADR-0106 原文](../decisions/0106-group-memory-use-leases-and-active-map.md)。

## Unknown 只读恢复

原 revoke 预算与 Unknown 不变，独立有界 query 只追加原 provider 证据，不重复副作用。

[ADR-0107 原文](../decisions/0107-original-revoke-observation-and-provider-progress.md)。

## Group 内容交接

原完整 stop/revoke 后发布带 lineage 的 child，新接收准备与显式 commit 后执行。

[ADR-0108 原文](../decisions/0108-durable-group-memory-handoff.md)。

## 分支与版本 fence

保留已发布 Memory 字段，独立 attempt generation，歧义 fork authority 拒绝自动迁移。

[ADR-0111 原文](../decisions/0111-branch-integration-and-authority-version-fences.md)。

## GC 与静止恢复

真实 writer leases 下备份声明 SQL/内容；恢复到新根，不启动服务、不更新预算。

[ADR-0113 原文](../decisions/0113-quiescent-recovery-bundles-and-node-writer-lease.md)。

## 运行期修订

原 Mission goal 与停止/撤销门禁先于新 Group，保留原完成事实和所有权历史。

[ADR-0120 原文](../decisions/0120-versioned-runtime-plan-revision.md)。

## MI work 与 Memory

原 scoped Memory 声明、独立 Review、完整 stop/revoke 和实际 CAS 先于新 scope 发布。

[ADR-0126 原文](../decisions/0126-scoped-memory-for-runtime-plan-revision.md)。

## 可执行部署输入

闭合 profile 声明真实 component/store/TLS，inspector 不 Register、不 Execute。

[ADR-0129 原文](../decisions/0129-executable-deployment-profile.md)。

## 副本原 child 排空

真实 spawn/PID/boot/start tick 与 OS wait journal 构成 transfer 原 incarnation 的退出依据。

[ADR-0138 原文](../decisions/0138-original-controller-child-drain-before-transfer.md)。

## 逻辑 quota

原 Role units 与 Mission tenant 在 Commit/release 原子记账，不声称 GPU/OS 资源切分。

[ADR-0140 原文](../decisions/0140-control-resource-quota-commitment.md)。

## 活动引用与 CAS

同一恢复包中的活动 Memory/map/handoff 引用必须对应实际 bytes，缺失或 tombstone 冲突拒绝。

[ADR-0145 原文](../decisions/0145-recovery-active-content-reference-binding.md)。

## 协作耦合模式与通道就绪

MissionPlan 区分 `Independent`、`SequentialHandoff`、`ConcurrentCooperation` 与 `TightlyCoupledCooperation` 四种协作模式；Runtime 管理 `SharedSpatialReference`、共享视图与双端 `PeerChannelReadiness` 围栏，高频相对控制留在 Local EAIOS。

[ADR-0026](../decisions/0026-execution-coupling-and-group-views.md) · [ADR-0027](../decisions/0027-runtime-coordination-evidence-completion.md)。

## 取消结案与 Unknown 语义

无副作用取消结案与管理员人工对账（`reconcile_manual`）不将决策等价为物理停止证明；本地执行缺失终态时保守保留 `Unknown` 非终态。

[ADR-0232](../decisions/0232-task-cancellation-settlement.md) · [ADR-0233](../decisions/0233-local-administrator-cancel-settlement.md) · [ADR-0250](../decisions/0250-explicit-local-execution-unknown.md)。

## 节点热接入、在线发布与存储治理

支持静态 Node 证书显式在线采用、实体与可行性来源在线发布，以及流式 Catalog 恢复投影（存储 v2）与无损正文去重（存储 v3）。

[ADR-0239](../decisions/0239-online-static-node-identity-adoption.md) · [ADR-0242](../decisions/0242-online-deployment-publications.md) · [ADR-0243](../decisions/0243-catalog-recovery-checkpoint.md) · [ADR-0244](../decisions/0244-state-retention-and-deduplicated-storage.md)。

## 对话工作台与前检扩展

`roboguide chat` 提供只读状态/证据查询、任务提交意图与原生 Agent Loop；Node 私有配置前检与依赖时间窗前检分别在提交前校验映射完整来源与截止时间冲突。

[ADR-0251](../decisions/0251-readonly-conversation-workbench.md) · [ADR-0254](../decisions/0254-installed-chat-workbench.md) · [ADR-0255](../decisions/0255-private-node-plan-preflight.md) · [ADR-0256](../decisions/0256-complete-node-preflight-sources.md) · [ADR-0259](../decisions/0259-dependency-window-preflight.md)。

## 遥测事件瘦身

稳态心跳仅更新进程内租约而跳过冗余 SQL 提交；周期诊断样本按 `ROBOGUIDE_TELEMETRY_PERSISTENCE_POLICY` 在内容变更或最大间隔时落盘，安全停机与决策证据始终持久化。

[ADR-0257](../decisions/0257-process-local-node-liveness.md) · [ADR-0258](../decisions/0258-periodic-node-telemetry-persistence.md)。
