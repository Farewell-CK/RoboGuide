# ADR 主题与决策摘要

从实际问题进入决策。摘要用于阅读导引，原 ADR 的编号、状态、兼容性和历史保留。完整目录自动从仓库生成，不手写数量。

[全部 ADR](docs/decisions/index.md) · [原始验收档案](archives.md)

## 对象与身份

Actor 是逻辑连续性，PhysicalEntity 是部署执行身份，Node 承担路由与能力边界。

[ADR-0040 原文](docs/decisions/0040-mission-actor-binding-semantics.md)。

## 承诺与恢复

Proposal 不持有资源，Commit 形成原子承诺，Rebind 只消费已提交恢复分配。

[ADR-0004 原文](docs/decisions/0004-recovery-commitment-lifecycle.md)。

## 执行与满足

本地失败和负向 final verifier 是不同结果；TaskSatisfactionRejected 不伪装成执行失败。

[ADR-0093 原文](docs/decisions/0093-task-satisfaction-rejection-and-evidence-projection.md)。

## State 与证据

显式来源策略、原 lineage、TTL 和 exact Task verifier 进入真实消费门禁。

[ADR-0104 原文](docs/decisions/0104-state-consumers-belief-and-semantic-verifier.md)。

## Memory 准备

原 context、provider 与预算先持久化，Prepare/import/query/availability 必须先于 Execute。

[ADR-0105 原文](docs/decisions/0105-memory-reconciliation-authorization-and-spatial-provider.md)。

## Memory 使用与撤销

当前使用租约和 scoped release 独立于历史 placement，原 backend proof 不能替换。

[ADR-0106 原文](docs/decisions/0106-group-memory-use-leases-and-active-map.md)。

## Unknown 只读恢复

原 revoke 预算与 Unknown 不变，独立有界 query 只追加原 provider 证据，不重复副作用。

[ADR-0107 原文](docs/decisions/0107-original-revoke-observation-and-provider-progress.md)。

## Group 内容交接

原完整 stop/revoke 后发布带 lineage 的 child，新接收准备与显式 commit 后执行。

[ADR-0108 原文](docs/decisions/0108-durable-group-memory-handoff.md)。

## 分支与版本 fence

保留已发布 Memory 字段，独立 attempt generation，歧义 fork authority 拒绝自动迁移。

[ADR-0111 原文](docs/decisions/0111-branch-integration-and-authority-version-fences.md)。

## GC 与静止恢复

真实 writer leases 下备份声明 SQL/内容；恢复到新根，不启动服务、不更新预算。

[ADR-0113 原文](docs/decisions/0113-quiescent-recovery-bundles-and-node-writer-lease.md)。

## 运行期修订

原 Mission goal 与停止/撤销门禁先于新 Group，保留原完成事实和所有权历史。

[ADR-0120 原文](docs/decisions/0120-versioned-runtime-plan-revision.md)。

## MI work 与 Memory

原 scoped Memory 声明、独立 Review、完整 stop/revoke 和实际 CAS 先于新 scope 发布。

[ADR-0126 原文](docs/decisions/0126-scoped-memory-for-runtime-plan-revision.md)。

## 可执行部署输入

闭合 profile 声明真实 component/store/TLS，inspector 不 Register、不 Execute。

[ADR-0129 原文](docs/decisions/0129-executable-deployment-profile.md)。

## 副本原 child 排空

真实 spawn/PID/boot/start tick 与 OS wait journal 构成 transfer 原 incarnation 的退出依据。

[ADR-0138 原文](docs/decisions/0138-original-controller-child-drain-before-transfer.md)。

## 逻辑 quota

原 Role units 与 Mission tenant 在 Commit/release 原子记账，不声称 GPU/OS 资源切分。

[ADR-0140 原文](docs/decisions/0140-control-resource-quota-commitment.md)。

## 活动引用与 CAS

同一恢复包中的活动 Memory/map/handoff 引用必须对应实际 bytes，缺失或 tombstone 冲突拒绝。

[ADR-0145 原文](docs/decisions/0145-recovery-active-content-reference-binding.md)。
