# RoboGuide V2 架构基线

> 当前架构基线。权威来源是 [`RoboGuide_Architecture_Baseline_V2.docx`](RoboGuide_Architecture_Baseline_V2.docx)。本文档是面向仓库的结构化摘要，不替代原始 DOCX。

![RoboGuide V2 总体架构](../../images/roboguide-v2-overall-architecture.png)

## 1. 系统定位

RoboGuide 是面向异构具身智能体协作的通用分布式操作系统框架。它联合调度
Capability、Compute、Space 和 Time，同时保留每个节点的本地自治能力。

RoboGuide 不只是一个 Scheduler，还负责资源抽象、共享状态、任务与执行生命
周期、分布式调用、协同和恢复语义。

## 2. 逻辑架构

| 组件 | 职责 |
| --- | --- |
| Mission / Application | 提供外部 Mission / Goal，不直接控制设备 |
| Mission Intelligence | 生成 Task Graph 和 Execution Requirements |
| Control Plane | 完成能力匹配、分配提案、共享资源协调、计划提交、Execution Group 管理和恢复决策 |
| State & Memory Plane | 横向维护证据、共享系统视图、分配状态、Shared Belief 和分域记忆 |
| Embodied Execution Group | 在 Control Plane 之外承载任务级分布式执行上下文 |
| Distributed Embodied Runtime | 提供发现、消息、调用、Heartbeat、Lease、Adapter 和诊断 |
| Local Embodied Systems | 保留感知、导航、运动、硬件控制和即时安全能力 |
| Physical World | 被执行过程改变，并持续向系统反馈 Observation |

逻辑组件可以共址，也可以分布部署。部署拓扑不得改变组件的职责和权威语义。

## 3. 核心抽象

### Embodied Node（具身节点）

可被发现、能够执行任务或提供资源的系统参与者。节点类型可以包括 Robot、
Perception、Interaction、Compute 和 Infrastructure Node。Node 不等同于 Robot。

### Capability 与 Resource

Capability 描述 Node 当前能够执行什么；静态能力支持不代表运行时一定可用。
RoboGuide 联合调度四类资源：

- Capability：可执行的具身或计算能力；
- Compute：CPU、GPU、NPU、模型和执行容量；
- Space：位置、路线、区域、占用和共享物理设施；
- Time：前置关系、同步窗口、截止时间和占用区间。

### Embodied Execution Group（具身执行组）

面向具体任务动态形成的执行上下文，由 Members、Roles、已提交的 Resource
Bindings、Shared Context 和 Lifecycle 组成。

Role 是 Task 内与具体 Node 解耦的职责槽位：Capability 说明 Node 是否具备承担
该 Role 的能力，Assignment 记录当前由哪个 Node 承担，Resource Binding 记录执行
该职责已经提交的资源。如果 Task 直接绑定 Node，节点故障通常会迫使系统重新规划
整个 Task；通过 Role 间接绑定后，系统可以只重新匹配和绑定失败的 Role，同时保留
其他已完成工作、有效 Binding 和 Execution Group 上下文。

Member 与 Binding 必须区分：GPU Node 可以是 Member，GPU quota 是 Compute
Binding；走廊是 Spatial Binding，不是 Member。Group Manager 属于 Control Plane，
Group 本体由 Runtime 承载并跨节点运行。

## 4. 决策与承诺语义

```text
Plan → Match → Propose → Coordinate → Commit → Bind → Execute
```

1. Capability Matching 输出 Candidate Set，回答 `Who can?`；
2. Embodied Scheduler 输出 Assignment Proposal，回答 `Who should / Where / When?`；
3. Shared Resource Coordination 检测竞争，并执行 Reservation、Negotiation 或重新分配；
4. Commit 使资源义务生效，并在 Allocation / Reservation State 中可观察；
5. Execution Group Manager 根据 Committed Plan 创建并绑定 Group；
6. Runtime 承载绑定后的执行过程，使其跨节点运行。

未提交的 Proposal 绝不能被当作已经生效的资源分配。

## 5. 状态、证据、信念与记忆

State & Memory 是横向基础设施，包含：

- Node / Resource State；
- Capability State；
- Task / Execution State；
- Spatial & World Model；
- Allocation / Reservation State；
- Shared Belief；
- Distributed Memory。

```text
Observation → Source / Provenance → Timestamp → Freshness / Uncertainty
            → Fusion / Reconciliation → Shared Belief
```

Shared Belief 是面向决策的视图，不等于绝对 Ground Truth。冲突或过期证据必须
能够被表达和保留。Memory 具有 Local、Execution Group 和 Global 三种作用域；
Group 专属上下文默认不向全局广播。

## 6. Runtime 与本地自治

Runtime 定义与具体传输无关的 Discovery、Messaging、Invocation、Heartbeat、
Lease、Adapter 和 Diagnostics 语义。DDS、ROS 2、gRPC、MQTT、数据库和序列化
方式仍属于实现选型。

Global Coordination 负责 `What / Who / When / Shared Where`。Local Embodied
Systems 保留 `Immediate How`、Navigation、Local Planning、Perception、Motion、
Hardware Control 和 Safety。

## 7. 对账与恢复

```text
Detect → Reconcile → Adapt
```

恢复只升级到完成任务所需的最低层级：

| 层级 | 所有者 | 处理方式 |
| --- | --- | --- |
| L0 | Local Autonomy | 避障、短程重规划、运动重试、安全停机 |
| L1 | Runtime | 重连或恢复调用/通信 |
| L2 | Execution Group | 替换成员、重新绑定或调整 Group |
| L3 | Scheduler / Coordination | 重新 Propose、Coordinate 和 Commit |
| L4 | Mission Intelligence | Task Graph 已无法满足 Mission 时重新规划 |

## 8. 已冻结不变量

- Proposal 与 Commit 相互区分；
- 已提交 Binding 是可观察的系统状态；
- Group 成员关系和 Binding 具有明确生命周期；
- Local Safety 不能被远程全局控制覆盖；
- Shared Belief 表达不确定性、过期性、来源和冲突；
- Node 在线状态与 Capability 可用性相互区分；
- 任务完成是系统级 Execution State，不是单次动作的返回值；
- 恢复必须针对当前世界重新对账，不能重放过期命令；
- State 和 Memory 具有作用域；
- 替换实现不能改写架构语义。

## 9. 开放架构问题

V2 有意保留七个问题：State Authority、Spatial Authority、Control Topology、
Execution Group Authority、Scheduling vs Runtime Coordination、Temporal Assurance
和 Resource Commitment Semantics。跟踪列表以及 MVP 决策见
[`implementation-backlog.md`](../../implementation-backlog.md)。

## 10. 版本关系

V2 取代 V1.1，成为当前权威架构基线。V1.1 保存在
[`../v1.1/README.md`](../v1.1/README.md)，用于历史比较。架构变化必须先更新
基线，再同步总体架构图、README、PPT、论文和实现文档。
