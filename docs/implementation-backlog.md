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

## MVP 定义待决事项

当前决策状态和冻结清单由 [`mvp-definition.md`](mvp-definition.md) 统一记录：

- 具体多机异构任务、节点组合和任务成功条件；
- 正常路径与节点掉线、Capability degraded、Reservation conflict 等故障注入；
- Proposal、Commit、Bind、Execute、Reconcile 各阶段的可观察验收证据；
- 延迟、恢复时间、资源冲突和任务完成的最小指标；
- 导盲等后续领域场景如何通过领域层接入通用核心。

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
