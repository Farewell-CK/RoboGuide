# 架构与实现待决事项

2026-10-26 复核：下文按日期保留历史决策缺口，后续实现不回写为当时已经具备。
当前 [A1–F 队列](development/roboguide-software-completion-plan-2026-10-15.md)与
[R01–R20 验收矩阵](development/nonrelease-acceptance-matrix-2026-10-26.md)是整改进度入口。
完整 scoped typed-map/MI、原 producer/GC、在线 Archive、升级/回退、divisible/time/space、
运行中抢占/耦合/hazard 和 standalone Federation 已有实际软件验收。双域 HA/完整恢复已由
HA16 实际门禁通过；
当前 CI/main 尚在最后验收；物理与跨主机/长期证据独立，不重复把已实现模块列作待开发。

2026-10-21 [ADR-0141](decisions/0141-unconfigured-resource-quota-usage-view.md)修复 quota 运维视图
只枚举已配置限额而隐藏既有 commitment 的问题；v0.2 汇总 configured limits 与原 claims 并集，
无上限/剩余量明确返回 `null`。零 Node 的实际 API/SQLite 重启门禁持续验收。

2026-10-21 [ADR-0140](decisions/0140-control-resource-quota-commitment.md)把 revisioned quota policy
接入 recovery-admin `/v1/resource-quotas`、原 operator authority journal 与幂等 receipt；wrapper v47
对账 ResourceId claim、tenant、policy history 和 reservation，并将旧需求不可用显式 fence。真实
双向 TLS/SQLite/重启进程验收通过且 Node inventory 为零，见[验收](development/acceptance/2026-10-21-control-resource-quotas.md)。逻辑账本
约束 Control 承诺，不等于 divisible allocation、GPU/traffic cost 或 OS/物理强制，完整 F6 仍开放。

2026-10-20 [运行期修订验收](development/acceptance/2026-10-20-runtime-plan-revision.md)
补齐原目标 gate、版本历史、原预算、精确 Review 与完整 stop/revoke 后的新 Group；普通
resume/recovery 不能越过待完成修订。ADR-0121 的 wrapper v44 / inner v23 / codec v28
保留真实 v43 历史并增加显式 MI generation/claim/provider nonce 与原调用 SQL/只读对账；
自动发现、scoped Memory 迁移及完整组合仍是必需软件，不能据此关闭整个 F8。

本文件跟踪 [`RoboGuide V2 架构基线`](architecture/v2/README.md) 尚未冻结的问题，以及应由 MVP 或实现证据决定的工程选择。当前架构语义以 V2 README 和已接受 ADR 为准；历史 DOCX 不是后续演进的 source of truth。

2026-10-12 全 ADR 遗留项核验与当前实施顺序见
[ADR 遗留项清单](development/adr-follow-up-register.md)。已由后续 ADR 实现的子集回写原
文档；Software implemented、默认关闭、物理验证待补与一般研究扩展保持不同状态。

2026-10-14 原分层审计的当前工作树复核见
[剩余整改清单](development/roboguide-remediation-remaining-2026-10-14.md)。selective import 已有
ADR-0100 的 exact-binding 软件路径，但 Unknown reconciliation 和完整生命周期仍未关闭；
RT-G9 只是部分接入，且 HA composition 还有 Controller v27/supervisor v26 不一致。
这些是软件剩余项，不能归为仅等待物理验收。

2026-10-15 的当前执行状态以[全部非发布整改计划](development/roboguide-software-completion-plan-2026-10-15.md)
为准。F1/F2 已统一 supervisor/Controller schema 并补 stop、身份与入口门禁；
[ADR-0104](decisions/0104-state-consumers-belief-and-semantic-verifier.md) 实现显式 State consumer、
Belief、MI provenance 与通用 verifier。实际 POSIX/正式 Node/CAS/独立复算及重启链路见
[F3 验收](development/acceptance/2026-10-15-f3.md)。Memory/Spatial、动态 HA、时空/quota、运行期
协调/计划、Federation 及总验收仍为必需软件工作，不能视为只等设备。

[ADR-0105](decisions/0105-memory-reconciliation-authorization-and-spatial-provider.md) 的首个当前
切片补齐原实例/合同绑定的只读 import query、持久预算、迟到历史、完整 result 校验与旧二进制
Node journal 写入拒绝。实际文件、SQL 提交失败、正式查询、重启及独立复算见
[Memory 连续性验收](development/acceptance/2026-10-15-f4-memory-continuity.md)。完整 Group 内容
授权、active-map、retention/GC/revocation 与空间计算仍需继续完成 F4。

2026-10-16 Active Map 软件切片已完成：实际 CAS 文件验证、当前使用租约绑定、选择/查询/释放、
重启失活、软件 stop barrier handoff、历史代数和八类引用 GC 门禁均有实现与实际进程证据，见
[F4 Active Map 验收](development/acceptance/2026-10-16-f4-active-map.md)。这不关闭物理定位、
硬件连续性、完整 Group 生命周期、map fusion 或跨站点数据面恢复。

2026-10-16 Scheduler/运维软件切片已完成：`SchedulingCostEvidence` 的来源/TTL/边界校验与
确定性排序、checkpointed logical `QuotaLedger`、以及 Mission/Artifact Prometheus 文本入口
均已接入；ADR-0140 又将 revisioned tenant quota 与原 ResourceId Commit/release 接通。它们不提供
divisible allocation、真实 GPU/交通成本、OS quota enforcement、物理执行时限或告警平台 SLO 达标证据。

2026-10-16 Control 已增加持久 Federation prepare/commit session：epoch、plan/ownership digest、
deadline 与 abort/dispatch-ready gate 均有 checkpoint 和回归，见
[F9 Federation 验收](development/acceptance/2026-10-16-f9-federation-session.md)。ADR-0201/0202
增加 participant HTTP、版本化 mTLS/tenant/domain grant 和原始阶段回执；真实双服务外部调用、
SQL 回滚/响应丢失/重启等已有软件证据。持久 coordinator、真实 peer 请求、各域资源/epoch
证明、跨主机 clock translation 和组合恢复仍是未完成的软件，global dispatch 保持 false。

2026-10-16 F7 软件停止/恢复边界已由 integration-server 220 项生产回归重新核对，见
[F7 停止与恢复验收](development/acceptance/2026-10-16-f7-stop-recovery-software.md)。物理
stop certificate、硬实时 hazard、运行中抢占和异构 regrasp 仍保持独立部署 gate。

2026-10-19 Memory 的 Task/整组 release scope、原 context/provider revoke ACK、原 backend
身份核验和每次发送的最早期限门禁已接入生产路径，见
[多 Node 生命周期验收](development/acceptance/2026-10-19-f4-memory-group-lifecycle.md)。
两软件 Node 的真实 CAS/计算/独立验证/重启检查与旧 v37 writer 拒绝分别保留证据；它不关闭
跨主机部署、多 provider 部分 Unknown 的完整对账、跨 Group handoff 或 F5–F10。

2026-10-19 原 revoke 的多 provider 部分 Unknown 对账已由
[ADR-0107](decisions/0107-original-revoke-observation-and-provider-progress.md) 与
[专项验收](development/acceptance/2026-10-19-f4-original-revoke-reconciliation.md) 补齐。
原回执/预算保留，自动恢复和独立有界 GET 各有实际进程证据；无法确认仍保留 ownership，
内容确认不绕过 stop gate。跨 Group handoff、跨主机、HA 数据面及其余阶段继续为必需项。

2026-10-21 [ADR-0139](decisions/0139-original-file-worker-quota-and-kernel-proof.md)补齐 Local
EAIOS 文件 worker 的原 quota SQL、子进程实际 `getrlimit` proof 与只读查询，并由真实进程
检查独立核对原 PID/SQL receipt。它关闭本地 software CPU/address-space envelope 的证据缺口；
Control 的 divisible quota、真实 GPU/交通成本、设备调度、物理时间/安全与 F6/F7 总验收仍开放。

## V2 开放架构问题

2026-10-19 双 Group Memory 交接与后续成员生命周期由
[ADR-0108](decisions/0108-durable-group-memory-handoff.md) 与
[实际软件验收](development/acceptance/2026-10-19-f4-group-memory-handoff.md) 接通：原停止/
context/provider revoke、不可变 child lineage、原接收准备、显式 commit、后续新代际/新许可，
并覆盖 SQL 回滚、超时、策略变化与重启。它不关闭完整 F4、跨主机、HA 内容恢复或物理交接。

| ID | 问题 | 需要回答的核心内容 |
| --- | --- | --- |
| Q1 | State Authority | 已有 source-aware federation 之上，Shared Belief 在何种新鲜度和不确定性条件下可驱动决策；冲突如何由显式 provider 归约 |
| Q2 | Spatial Authority | Map、Pose、World Model 如何建立共同空间关系和系统级 reference authority |
| Q3 | Control Topology | 集中式 Control Plane、层级控制和 Federation 的适用边界 |
| Q4 | Execution Group Authority | Mission-level Group、TaskExecution ownership、Context semantics 与成员节点权威如何划分 |
| Q5 | Scheduling vs Runtime Coordination | Plan-time allocation、资源协调与 execution-time adaptation 的边界 |
| Q6 | Temporal Assurance | 同步、时钟偏差、deadline 和时间窗口如何成为架构约束 |
| Q7 | Resource Commitment Semantics | Commit、Lease expiry、preemption、partial release 需要何种一致性保证 |

### Q2 实现切片：Distributed Spatial Memory v0

Q2 的首个可验证切片已接受 [`ADR-0016`](decisions/0016-distributed-spatial-memory.md)：
地图采用 immutable revision + SHA-256 CAS artifact，manifest/provenance/anchor/replica
状态进入 State & Memory Catalog，bytes 走独立 streaming Artifact data plane。v0 的 Spatial
Authority 是 manifest 中显式声明的固定物理 anchor；跨 Mission 的消费者通过预分配
`(MapId, MapRevisionId)` pull，导入后必须单独验证 localization。该切片不解决 map fusion、
实时同步、active-map 选择、动态 output binding、删除/GC 或认证传输安全。

Spatial v0 的故障恢复仍受下述 Runtime Gate 约束：artifact publication 或 replica evidence
在本地动作完成后若无法确认，会保持 `Unknown`/recovery-pending，不能自动重放物理动作。
Node 已支持对完全相同 Execute 的 artifact-finalization-only resume，且不会重放 Local
EAIOS；Node 重启看到 pending finalization marker 时会继续 fence，不会自行选择该 resume。
`prepare-output` 也会在首次读取可变 source 前持久化 freeze fence；崩溃遗留 fence 会进入
`ReconciliationRequired`，相同 Execute 不会再次冻结 source。Control/Orchestration 尚未根据
recovery decision 自动触发 exact retry。稳定基线仍需 RT-G2 闭环这一决策，或由 RT-G3
创建新的 physical attempt；在此之前只适用于有人值守、可人工停止的实验。

Spatial v0 的已知后续工作：

- 真机部署需要版本化的 Local EAIOS/Robonix/ROS mapping 和完整的
  `build -> prepare-output -> publish -> stage -> import -> verify` system test；当前场景
  只提供配置驱动的 Node fixture 和外部 HTTP workflow 假设。
- Generic Memory replica evidence 已包含 exact consumer provider；下一版仍应补充 consumer
  `TaskRef`、execution identity 和 artifact binding，以便 State 与 Runtime 审计关联到具体
  TaskExecution。
- Generic Memory 已由 ADR-0098/ADR-0100 接入 exact current Group/Task/Role/attempt/provider 的
  Controller -> Node selective-import command、durable receipt 和重试 fence；Unknown 的显式
  reconciliation 已由 ADR-0105 接入原实例只读查询；当前使用租约已有原 provider 集合的
  provider-complete revoke release gate，但多 Node 完整成员授权、retention/GC 和跨节点
  revocation 仍未关闭。当前 Control 已补 source-bound parent/child handoff 的显式 Handoff principal、
  owner/provider/bytes/schema/media 不变和 scope 收窄门禁；跨 Group stop barrier/physical
  handoff 仍未由该门禁冒充关闭。discovery 不授权全量复制，
  import 不得隐式改变 Task lifecycle。
- Staged evidence 的 durable pre-dispatch 时点、文件句柄级 TOCTOU 约束，以及临时 upload
identity 的随机/单调生成仍需独立决策，不能在 v0 中隐式改变事件语义。

### Q1 实现切片：Federated State and Selective Memory v0.1

[`ADR-0024`](decisions/0024-federated-state-and-selective-memory.md) 已建立 Node/World/RoboGuide
对象、六类 State semantic、source/channel/time/TTL/confidence，以及对 Mission、Control、
Shared Node State、Runtime/Orchestration projection 的只读 federation。Node Config v0.5 和
Protocol v0.4 继承选择性 Reported/Observed push；State observation 不自动触发 recovery。

同一切片建立五类 Memory 的 provider discovery、immutable manifest catalog 和基于现有 CAS
的 selective exchange evidence。它解决共同上层语义与 local ownership，不关闭 Q1：Belief
provider、fusion/conflict policy、允许哪些 Belief 影响 Control、访问控制、retention/GC、
多 Controller 的静态 quorum metadata profile 已由 ADR-0083 实现并通过合成进程检查；
跨主机、内容可用性与物理发布仍需场景证据。通用 Node Memory workflow 已由
[`ADR-0025`](decisions/0025-memory-provider-backend-and-workflow.md) 的 v0.6 provider
discover/export/import workflow 与 filesystem/JSONL Node ledger/reference fallback 补齐；真实
EAIOS 继续拥有 semantic/storage authority，其 adapter 行为仍需现场证据。

## MVP 定义待决事项

当前决策状态和冻结清单由 [`mvp-definition.md`](mvp-definition.md) 统一记录：

- 具体多机异构任务、节点组合和任务成功条件；
- 正常路径与节点掉线、Capability degraded、Reservation conflict 等故障注入；
- Proposal、Commit、Bind、Execute、Reconcile 各阶段的可观察验收证据；
- 延迟、恢复时间、资源冲突和任务完成的最小指标；
- 导盲等后续领域场景如何通过领域层接入通用核心。

### Mission Front-half 后续切片

[`ADR-0030`](decisions/0030-mission-semantic-admission-and-deployability.md) 已将 Mission 语义接纳
与当前部署可执行性分开。后续按独立合同切片处理，不能重新使用 live inventory 代替稳定语义：

- Canonical Capability Catalog v0.3 已按 ADR-0034/ADR-0036 区分 Capability/attribute 与
  Operation/parameter，并支持多 capability requirements、typed feasibility predicates 及
  integrated operation 的 provider-level baseline；结构化 entity values、embodiment taxonomy
  及 Catalog 分发/协商仍待后续版本；
- MissionPlan v0.7 已正规化 Actor -> ContextRole -> TaskRole、semantic ExecutionIntent 及 Role
  requirements，并保持 Local EAIOS 的 Local How authority；
- Node Contract v0.6 独立传输 Capability Profile、Operation Support，并把 v0.7 objective、
  canonical operation 与 scalar parameters 完整交给 Local EAIOS；未来 structured
  entity/constraint values 仍需新的版本化协议合同，不能由 RoleId 推导或静默扩展；
- Task Satisfaction v0.2 已实现 source-aware State/Verifier evidence 与 receive-time freshness；
  多源融合、负向 verdict recovery、物理目标争议和持续 verification 仍待后续版本；
- Mission Request v0.3 已将结构化 DialogueTurn、revision-bound Review/Repair history 和 approval
  reasons 分离；更丰富的 channel/attachment 身份仍待真实产品需求；
- Mission timing constraints 已与带 provenance/freshness 的 Scheduler duration estimate 分离；
  candidate-specific prediction profile 和 estimate refresh policy 仍待真实执行证据。

## 真机 Runtime 稳定基线 Gate

[`ADR-0028`](decisions/0028-durable-command-recovery-and-attempts.md) 已完成 Runtime reliability
的最小软件闭环。它不等于真机断电、网络分区、exactly-once physical action 或无人值守安全认证；
这些仍必须由 deployment/hardware evidence 和明确 safety policy 关闭：

| ID | 当前状态 | 验收边界 |
| --- | --- | --- |
| RT-G1 Durable dispatch | 软件闭环已完成 | Controller 在网络副作用前持久化 immutable dispatch intent；Node receipt 只证明 journal admission，restart ambiguity 不自动重放 |
| RT-G2 Recovery closed loop | 单 Role 和独立 Session 整组 retained-binding 软件切片已完成 | 单 Role 复用 Partial Release -> Match -> Propose -> Commit -> Rebind；ADR-0061 的整组 continuation 保留绑定并要求完整 stop proof。coupled/migrating joint recovery 与 physical-world fencing 仍未完成 |
| RT-G3 Attempt identity | 已完成 | logical `(GroupId, TaskRef, RoleId)` 与 durable-generation physical attempt 分离；rebind 保留旧 attempt history |
| RT-G4 Durable cancellation | 最小两阶段语义已完成 | Mission `Cancelling`、per-attempt cancel intent、Node cancel-before-execute tombstone 与 terminal-evidence finalization 已持久化；远程安全 stop deadline/policy 仍属于 RT-G9 |
| RT-G5 Timer and liveness driving | 已完成 | application timer 推进 lease/session expiry、recovery、cancel、Ready Task 与 outbox，失败时 fail-stop |
| RT-G6 Fault-injection evidence | 离线确定性矩阵已完成 | [`runtime-reliability-fault-matrix.md`](development/runtime-reliability-fault-matrix.md) 覆盖 crash/restart/disconnect/duplicate/cancel/rebind；真机断电与真实网络分区证据仍缺失 |
| RT-G7 Capability readiness | 软件闭环已完成，真机证据待补 | Node/WebUI process health 与 exact canonical capability readiness 分离；真实 ROS/EAIOS probe 仍需部署验证 |
| RT-G8 Verification evidence | 强类型链路已完成，field mapping 待真机验证 | localization evidence 包含 active map identity、mode、pose quality、frame、current attempt/Node/session provenance；`has_map=true` 仅为 legacy smoke |
| RT-G9 Relation actuation | 当前停止权威软件切片已验收；完整 F7 继续实施 | ADR-0102 已接通 Mission hold、原来源/时效、receipt/独立 safe-state、无输入 deadline 与 release/resume gate；ADR-0110/0111 接通原软件进程退出和分支版本门禁。运行中抢占、耦合迁移、hazard、物理/hard-realtime 仍须独立验收，SoftwareProcess 不证明设备停止 |

RT-G7/RT-G8 的最小边界由
[`ADR-0019`](decisions/0019-capability-readiness-and-localization-evidence.md) 已确定：RT-G7
复用 Node Protocol v0.4 的 `RegistrationUpdate`，其 v0.5 config、精确 contract readiness 和
后续 Matching 传播已实现。双狗配置和 Robonix adapter 已按历史现场证据接入 exact ROS
service discovery probe，但仍需全新真机故障注入。RT-G8 已建立独立结构化合同、Node journal
持久化接口、Artifact transition 与 State projection；Node completion extraction 和真实 adapter
mapping 仍未闭环。双狗验收条件见
[`scenarios/distributed-spatial-memory-v0.1/acceptance.md`](../scenarios/distributed-spatial-memory-v0.1/acceptance.md)。

Node Service durable journal、本地幂等保护、Controller dispatch outbox、Runtime attempt history
和 Mission-level cancellation/recovery transaction 已分别落在各自 authority。后续实现仍不得把
Recovery Decision 下沉到 Runtime，也不得让 Integration 获得 execution lifecycle authority。

[`ADR-0020`](decisions/0020-execution-coordination-relations.md) 建立 lifecycle-derived
`requires-active` relation、Runtime live state、checkpoint 和 progression fence；ADR-0028 已关闭
其 RT-G3 attempt-identity 依赖，但不关闭 RT-G9。hazard/距离/速度等条件事实、relation composition 和硬实时 actuation 需要独立合同、
现场时序证据与安全决策，不能用自由字符串表达式提前固化。

## 历史 Bootstrap 提案

以下内容保留早期开发基线的上下文；已经被后续 ADR 和当前实现取代的条目不再表示开放决策。

- 提议使用 Rust 实现 Domain、Control、Runtime 和 State 核心，Python 承载 Mission
  Intelligence、模型、仿真和研究型 Adapter；
- 提议从模块化单体、内存 Port 实现和确定性 Fake Nodes 开始，不预先拆分微服务；
- 提议让仿真与真实硬件通过 Adapter 接入，不作为核心框架开发的前置依赖；
- 目标目录和依赖方向提案见 [`development/README.md`](development/README.md)；
- 所有手写函数均需文档注释，质量门槛以
  [`development/coding-standards.md`](development/coding-standards.md) 为准；
- 语言职责决策由
  [`ADR-0001`](decisions/0001-rust-core-python-edges.md) 记录。
- DEAIOS 与本地 EAIOS 的接入边界由
  [`ADR-0002`](decisions/0002-deaios-node-contract.md) 记录。

这些提案仍在评审，不构成已接受的 MVP。即使被接受，也不冻结跨进程
Transport、序列化格式、数据库、调度算法或部署拓扑。

## 延后的实现选型

- Capability Schema、Contract 字段、类型系统和版本兼容策略；
- 节点接入的当前正式边界已由 [`ADR-0010`](decisions/0010-single-node-service-local-integration-engine.md)、
  [`ADR-0021`](decisions/0021-device-extension-boundary-conformance.md) 和
  [`ADR-0022`](decisions/0022-retire-legacy-adapters-and-isolate-artifact-store.md) 固定为单一
  `roboguide-node`、声明式 Local Integration Engine 与 deployment-owned Local EAIOS facade；
  Agent、SDK、Plugin、ROS Bridge 等仅是 facade 内部实现选择，不再是 RoboGuide core 的待决接入架构。
- Control Plane 已有 ADR-0083 的 OpenRaft v0.1 静态三成员和 v0.2 预注册 learner/joint-consensus
  软件 profile；跨主机认证、policy/history 升级与物理高可用发布仍待决；
- State / Belief / Memory 的数据库、缓存、事件总线和复制方式；
- Observation 融合采用投票、滤波、因子图或其他方法；
- Scheduler v0.2 已采用 deterministic bounded joint search，ADR-0082 已实现 Orchestration
  logical priority/fairness 和有界准入，ADR-0084 已实现显式 future planning preemption；
  运行中物理抢占、真实 cost model、travel/traffic prediction、divisible allocation、auction
  或 RL policy 仍待独立设计/证据 gate；
- Shared Resource Coordination 的时空图、Reservation 和冲突求解；
- Messaging / Invocation 使用 DDS、Zenoh、gRPC、MQTT、WebRTC 或其他协议；
- Memory 使用向量库、图数据库、关系库、对象存储或组合；
- Benchmark、SLO、QoS、资源预测和成本函数；
- 真实硬件的 Safety、权限、Heartbeat、Lease、超时和恢复验证。

## 决策规则

如果一个问题会改变模块职责、状态权威、Proposal / Commit 语义、Execution Group 定义或 Recovery 层级，它属于架构决策，必须先更新 V2 基线。若架构可以承载多种方案，则保留为实现选择，使用 MVP 和工程证据再决定。
