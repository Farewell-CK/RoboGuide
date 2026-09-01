# 架构与实现待决事项

本文件跟踪 [`RoboGuide V2 架构基线`](architecture/v2/README.md) 尚未冻结的问题，以及应由 MVP 或实现证据决定的工程选择。

## V2 开放架构问题

| ID | 问题 | 需要回答的核心内容 |
| --- | --- | --- |
| Q1 | State Authority | Shared Belief 在何种新鲜度和不确定性条件下可驱动决策；哪些状态必须保留 authoritative owner |
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
- Replica evidence v0 只有 Node/Mission 维度，下一版应补充 consumer `TaskRef`、execution
  identity 和 artifact binding，以便 State 与 Runtime 审计关联到具体 TaskExecution。
- Staged evidence 的 durable pre-dispatch 时点、文件句柄级 TOCTOU 约束，以及临时 upload
  identity 的随机/单调生成仍需独立决策，不能在 v0 中隐式改变事件语义。

## MVP 定义待决事项

当前决策状态和冻结清单由 [`mvp-definition.md`](mvp-definition.md) 统一记录：

- 具体多机异构任务、节点组合和任务成功条件；
- 正常路径与节点掉线、Capability degraded、Reservation conflict 等故障注入；
- Proposal、Commit、Bind、Execute、Reconcile 各阶段的可观察验收证据；
- 延迟、恢复时间、资源冲突和任务完成的最小指标；
- 导盲等后续领域场景如何通过领域层接入通用核心。

## 真机 Runtime 稳定基线 Gate

当前 [`ADR-0015`](decisions/0015-runtime-execution-boundary.md) 实现可作为 Runtime
职责边界和正常路径的开发基线，也可用于有人值守、低风险、允许人工停止的真机 smoke
experiment；在以下阻塞项闭环前，不得描述为支持断线恢复、进程重启恢复、可靠取消或
无人值守运行的稳定 Runtime 基线：

| ID | 阻塞项 | 稳定基线验收条件 |
| --- | --- | --- |
| RT-G1 | Durable dispatch | Controller 在产生网络副作用前持久化不可变 dispatch intent；崩溃窗口内不得产生无法关联 Group/Task/Role 的孤立物理执行 |
| RT-G2 | Recovery closed loop | `Unknown`、session loss、timeout 和 restart fencing 能进入 Assess -> Partial Release -> Match -> Propose -> Commit -> Rebind，并明确 Resume、Redispatch 或最终失败 |
| RT-G3 | Attempt identity | logical Task/Role execution 与每次 physical attempt 分离；rebind 到新 Node 不复用冲突 identity，旧 attempt 被显式 supersede/fence |
| RT-G4 | Durable cancellation | Runtime 持久化 CancelRequested、ack、deadline、retry 和 completion race；Mission cancel 会覆盖所有仍在运行的 execution，且不伪造物理终态 |
| RT-G5 | Timer and liveness driving | Runtime timer 将 heartbeat/lease/session observation 转为 execution ambiguity evidence，并触发外部 Control reconciliation，而不是让 Running 无限悬挂 |
| RT-G6 | Fault-injection evidence | 系统测试覆盖 dispatch 前后崩溃、Controller/Node 重启、断线重连、重复/乱序事实、取消竞态和 recovery rebind，并输出可检查事件轨迹 |
| RT-G7 | Capability readiness | Node/WebUI process health 与每个 canonical capability 的 readiness 分离；ROS discovery、Router 和 vendor service 缺失必须可观察，不能仅凭进程存活进入 Matching |
| RT-G8 | Verification evidence | localization verification 需要 active map identity、mode、pose quality 与 coordinate-frame evidence；`has_map=true` 只保留为 smoke 证据 |
| RT-G9 | Relation actuation | Execution Relation violation/unknown 在 progression fence 之外需要版本化 pause/stop command、durable acknowledgement、deadline 与 completion race；Local Safety authority 不得被远程动作覆盖 |

RT-G7/RT-G8 的最小边界由
[`ADR-0019`](decisions/0019-capability-readiness-and-localization-evidence.md) 已确定：RT-G7
复用 Node Protocol v0.2 的 `RegistrationUpdate`，其 v0.4 config、精确 contract readiness 和
后续 Matching 传播已实现。双狗配置和 Robonix adapter 已按历史现场证据接入 exact ROS
service discovery probe，但仍需全新真机故障注入。RT-G8 已建立独立结构化合同、Node journal
持久化接口、Artifact transition 与 State projection；Node completion extraction 和真实 adapter
mapping 仍未闭环。双狗验收条件见
[`scenarios/distributed-spatial-memory-v0.1/acceptance.md`](../scenarios/distributed-spatial-memory-v0.1/acceptance.md)。

Node Service 已有 durable execution journal 和本地幂等保护，但它不能替代 Controller
dispatch intent、Runtime attempt history 和 Mission-level recovery transaction。Gate 的实现不得
把 Recovery Decision 下沉到 Runtime，也不得让 Integration 获得 execution lifecycle authority。

[`ADR-0020`](decisions/0020-execution-coordination-relations.md) 建立 lifecycle-derived
`requires-active` relation、Runtime live state、checkpoint 和 progression fence，但不关闭 RT-G3
或 RT-G9。hazard/距离/速度等条件事实、relation composition 和硬实时 actuation 需要独立合同、
现场时序证据与安全决策，不能用自由字符串表达式提前固化。

## 开发 Bootstrap 提案

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
- 节点接入采用 Agent、Adapter、SDK、Plugin 或 ROS Bridge；
- Control Plane 的进程划分、Leader Election、高可用与一致性算法；
- State / Belief / Memory 的数据库、缓存、事件总线和复制方式；
- Observation 融合采用投票、滤波、因子图或其他方法；
- Scheduler 采用规则、启发式、优化、拍卖、强化学习或混合策略；
- Shared Resource Coordination 的时空图、Reservation 和冲突求解；
- Messaging / Invocation 使用 DDS、Zenoh、gRPC、MQTT、WebRTC 或其他协议；
- Memory 使用向量库、图数据库、关系库、对象存储或组合；
- Benchmark、SLO、QoS、资源预测和成本函数；
- 真实硬件的 Safety、权限、Heartbeat、Lease、超时和恢复验证。

## 决策规则

如果一个问题会改变模块职责、状态权威、Proposal / Commit 语义、Execution Group 定义或 Recovery 层级，它属于架构决策，必须先更新 V2 基线。若架构可以承载多种方案，则保留为实现选择，使用 MVP 和工程证据再决定。
