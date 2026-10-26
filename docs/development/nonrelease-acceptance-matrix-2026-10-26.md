# 全部非发布整改最终验收矩阵

状态：R01–R20 软件实现与本地完整组合验收通过；`SoftwareComplete` 交付同时要求下述
当前 CI/main 门禁。本文对照原 14 层与 R01–R20，不建立新的缩减范围。
唯一开发分支为 `codex/nonrelease-remediation-closure`；[原计划](roboguide-software-completion-plan-2026-10-15.md)
的 A1–F 是执行队列。各项先以公开入口完成实际软件链路，再集中运行全模块检查。

## 固定验收范围

最终软件 profile 使用显式配置的单机独立进程、原文件、SQLite、CAS、真实 mTLS 和原 OS
wait。每条 native gate 在启动前固定实际二进制、配置与源码 identity；端口、Node 和成员
标识由各次明确的部署输入生成，不成为 Core 中的设备清单。相同原端点的恢复步骤顺序执行。
新的恢复目录不自动启动服务。测试中的软件 Node、CPU worker、协议 fixture 和独立文件
真值均保留标签，不能作为设备、外部模型或物理世界证明。

维护 CI 的 Rust/Python 工作流是可重复执行的总入口；空间 native SDK 另使用其真实 Python
3.11/Open3D 环境，标准库桥接兼容另使用真实 Python 3.9。站点严格构建和浏览器检查只验证
文档，不能关闭运行功能。正式发版、公开分发、生产上线、许可证决策和认证办理不在本轮内。

## 原审计逐项对账

| 原项 / 层 | 软件权威与实际路径 | 必需验收与记录 |
| --- | --- | --- |
| R01 / Runtime、Orchestration | 原 attempt/session/export 的 stop、receipt、fresh safe-state、Mission hold、独立原进程退出 | [停止与恢复](acceptance/2026-10-16-f7-stop-recovery-software.md)、[真实 worker wait](acceptance/2026-10-20-file-process-memory-ownership.md)、[whole Group](acceptance/2026-10-21-relation-whole-group-stop.md)；确认前 release/resume/repeat 仍受 fence |
| R02 / Control、Replication 接线 | 同一 application schema、真正 owned child、完整 batch/辅助权威与零事件写入 | [当前合同集成](acceptance/2026-10-20-current-execution-contract-integration.md)、[runnable 内容](acceptance/2026-10-23-runnable-controller-content.md)；当前 CI 与实际三副本链路 |
| R03 / 部署拓扑 | 闭合配置声明 host/domain/process/store owner、TLS、故障目标与退出条件 | [零 Node 部署检查](acceptance/2026-10-21-deployment-profile.md)；软件 profile 与尚未冻结的物理 MVP 分开 |
| R04 / Mission 入口 | 原 HTTP 幂等 journal、同 caller/tenant/key、只读 observe/reconcile | [F2 身份入口](acceptance/2026-10-15-f2.md)；丢失响应不重跑模型或 Controller 提交 |
| R05 / 服务授权 | 稳定 principal、immutable tenant、原 TLS witness、Memory/Artifact/Group 权限 | [身份](identity-lifecycle-v0.1.md)、[scoped typed Memory](acceptance/2026-10-25-scoped-typed-map-lifecycle.md)、当前跨域 mTLS |
| R06 / MI 来源 | 原 Request、代码/模型配置/Catalog/Prompt/依赖、provider nonce/raw response、独立 Review | [非空运行期 MI](acceptance/2026-10-21-runtime-mi-scoped-memory.md)、[typed-map 修订](acceptance/2026-10-25-typed-map-runtime-revision.md)；fixture 不冒充模型推理 |
| R07 / verifier | 版本化 source、原 Task/attempt/plan、freshness/finality、持续 gate 与独立原目标 | [State/MI](acceptance/2026-10-15-f3.md)、[执行协调](acceptance/2026-10-25-execution-coordination.md)、双域独立目标 |
| R08 / State、Belief | source/schema/TTL/conflict/condition registry、Match/Commit/Bind/Rebind/dispatch/satisfaction 消费、显式融合 | [State/MI](acceptance/2026-10-15-f3.md)与当前全部 Control 回归；缺失协方差和矛盾来源保留 unknown |
| R09 / 持久化故障 | 原 writer generation 的 write-disabled latch、最后提交查询、授权零事件恢复探针 | [实际 SQL 恢复](acceptance/2026-10-20-authority-write-recovery.md)、[告警](acceptance/2026-10-20-observability.md)、完整实际 SQL 故障组 |
| R10 / Memory Unknown | 原 import/use/revoke 操作、独立 provider GET、首次预算与只读对账 | [逐 provider 撤销](acceptance/2026-10-19-f4-original-revoke-reconciliation.md)、当前完整文件/typed-map 生命周期；不换 ID 重导入 |
| R11 / Memory、Spatial | 完整 typed root/lineage、active-map、显式引用、tombstone-before-unlink、原 GC/producer | [scoped typed map](acceptance/2026-10-25-scoped-typed-map-lifecycle.md)、[全 producer](acceptance/2026-10-25-complete-producer-retirement.md)、[在线 Archive](acceptance/2026-10-26-coordinated-content-archive.md) |
| R12 / Group 授权 | 每 Task 原 preparation/use、成员代次、完整停止/撤权、immutable handoff child | [Group handoff](acceptance/2026-10-19-f4-group-memory-handoff.md)、[typed-map 完整交接](acceptance/2026-10-25-scoped-typed-map-lifecycle.md) |
| R13 / 动态 HA | 原 learner prefix/内容准入、joint、唯一 transfer、source spawn/PID/wait、有限代次升级/拒绝回退 | [动态内容 voter](acceptance/2026-10-24-content-voter-admission.md)、[真实滚动升级](acceptance/2026-10-25-application-generation-upgrade.md)、当前跨域 HA |
| R14 / 身份生命周期 | 注册/退役/轮换/撤销、原 session/证书 fence、实际签名/nonce/PCR policy | [身份](identity-lifecycle-v0.1.md)、[副本换证](acceptance/2026-10-20-replica-certificates.md)、当前 HA 中 Stage/Retire；物理来源另列 |
| R15 / Archive、内容恢复 | 原 private Archive、Catalog/Snapshot/Log 引用、所有当前 qualified bytes、完整包验证与新 inode restore | [协调归档](acceptance/2026-10-26-coordinated-content-archive.md)、[部署恢复合同](replicated-federation-deployment-v0.1.md)；同编号跨域成员不能替代缺失 Root |
| R16 / Scheduler | 来源成本/TTL/unavailable、独立资源资格、有界确定性排序 | [真实来源成本](acceptance/2026-10-25-current-state-scheduling-costs.md)、[量化 Compute](acceptance/2026-10-25-quantitative-resources.md)、[时空合同](acceptance/2026-10-25-resource-topology.md) |
| R17 / Local EAIOS 安全 | operation-owned stop/safe-state/hazard/continuation 合同、部署 assurance 与实际支持声明 | [原 OS 资源限制](acceptance/2026-10-21-local-process-os-quota.md)、[运行协调](acceptance/2026-10-25-execution-coordination.md)；无设备证明拒绝设备级 regrasp |
| R18 / 运维 | 所有 listener/本地 adapter 的容量与有限等待、metrics-only 身份、原生告警与明确分母 | [连接与延迟](acceptance/2026-10-26-listener-capacity.md)、[SLO/runbook](production-candidate-slo-and-drills-v0.1.md)；短采样不计 30 天达标 |
| R19 / 非发布工程交付 | 当前 schema/ADR/双语架构/实现图、质量检查、完整复现输入、人类 Git identity 与 main 同步 | 当前 Rust/Python/文档 CI 与最终合并后的远端 SHA；发布和许可证部分继续排除 |
| R20 / 后续能力 | 自动 MI、原目标/Review/scoped Memory；divisible/time/space；运行中抢占/耦合恢复/handoff；独立域原资源事务 | A1–F 完整组合，不用单个 metadata receipt、库测试或软件 stop 推断全局执行/物理事实 |

## 最终签署门禁

必须同时有当前整包质量检查、各原功能的实际正/负向/故障与恢复、完整内容和跨域组合、
默认容量与有限等待、当前完整 CI、主分支合并及远端精确 SHA。历史已通过且调用链未受影响的
证据保留原二进制/配置 identity；受变化影响的入口在当前二进制上重新运行。失败、忽略、
Unavailable、Unknown、样本数与未实测部署条件均独立列出。

双域 HA/完整包已由 HA16 的真实进程、分区、停止对账、证书拒绝、backup/verify/restore、
重复恢复和两域各三 leader 读取通过。当前 typed-map/MI 整链及完整 Archive/producer GC/
成员故障/最终冷恢复复查通过，原失败和各自 frozen identity 分别保留。

交付必须核对[完整 CI](https://github.com/Farewell-CK/RoboGuide/actions/workflows/ci.yml)与
[文档 CI](https://github.com/Farewell-CK/RoboGuide/actions/workflows/docs.yml)均对应当前提交，
且远端 `main` 与该受检提交一致、工作树干净。CI 记录和 Git refs 是这两项外部状态的原证据，
不能从本文状态、单个 JSON `passed` 或 API 存在推断它们。

| 当前复查 | 原证据 |
| --- | --- |
| 双独立三副本域正常/分区/换证/transfer/完整恢复 | `/tmp/rg-federation-ha-16/result.json`；[完整说明](acceptance/2026-10-26-replicated-federation-ha.md) |
| typed-map/每 Task import/use/自动 MI/独立原目标/恢复 | `/tmp/rgs-current-final-1/result.json`，真实 Python 3.11/Open3D，无设备或外部模型 |
| 在线 Archive/原 Unknown/跨 owner/首次冷恢复/旧 writer 与负向拒绝 | `/tmp/rg-current-final-archive-1/online/result.json`、`original-cold/result.json`、`refusals/result.json` |
| producer GC/Snapshot/learner/joint/最终三 leader 冷恢复/原 GC 独立核对 | `/tmp/rg-current-final-archive-followup-1/result.json`；保留上一行原数据，原失败不改写 |
| 私有 Archive 退休/故障/GC/完整冷恢复与实际旧 profile 拒绝 | `/tmp/rg-archive-recipient-local-7/result.json`，使用当前选定 recovery profile 验证全部原 leases/存量 |
| genuine v55 → 当前 v60 与完整冷恢复 | `/tmp/rg-upgrade-v60-current-1/result.json`、`/tmp/rg-upgrade-v60-current-cold-1/result.json`；从完整原 activation receipts 选择 v0.29，三个恢复后 leader 读取通过 |

设备停止、物理交接、硬实时、硬件身份、跨主机连续性、长期 SLO 和人工响应保留独立证据
要求；软件合同完整后可标为 HardwareEvidencePending，不能用它掩盖软件 TEST GAP。
