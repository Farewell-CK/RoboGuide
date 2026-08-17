# RoboGuide

RoboGuide 是一套面向异构具身智能体协作的通用分布式具身智能操作系统架构。

当前架构 source of truth 是 [`RoboGuide_Architecture_Baseline_V2.docx`](docs/architecture/v2/RoboGuide_Architecture_Baseline_V2.docx)。V2 描述系统职责、协议语义、关键闭环和架构约束，但不固化 Schema、API、中间件或具体算法。V1.1 作为历史基线保留。

## V2 总体架构

![RoboGuide V2 Overall Architecture](docs/images/roboguide-v2-overall-architecture.png)

独立原图位于 [`docs/images/roboguide-v2-overall-architecture.png`](docs/images/roboguide-v2-overall-architecture.png)，并已嵌入 V2 DOCX。仓库内的结构化摘要见 [`docs/architecture/v2/README.md`](docs/architecture/v2/README.md)。

## 系统目标

RoboGuide 统一感知系统状态、物理世界状态和具身能力状态，并联合调度 Capability、Compute、Space、Time，使机器人、感知设备、交互终端、边缘算力和基础设施节点能够持续完成复杂现实任务。

它不是单纯的多机器人 Scheduler，也不以中央控制替代节点本地自治。RoboGuide 持续管理资源抽象、状态、任务与执行生命周期、跨节点协调以及故障恢复。

### 前期 MVP

MVP 以普通多机异构任务验证领域无关的最小闭环：

- 节点发现、身份、Capability 声明、状态与健康更新；
- Mission、Task Graph 和 Execution Requirements；
- Capability Matching 与 Capability × Compute × Space × Time 联合调度；
- Assignment Proposal、共享资源协调与 Commit；
- Execution Group 创建、绑定、执行和释放；
- Observation、Shared Belief、Reconciliation 与分级恢复。

导盲是后续应用场景，不是核心架构或 MVP 的前置假设。完整阶段边界见 [`docs/project-goals-and-mvp.md`](docs/project-goals-and-mvp.md)。

## V2 逻辑结构

### Mission / Application

外部用户、Agent 或 Application 提供 Mission / Goal，但不直接操作具体设备。

### Mission Intelligence

负责 Mission Understanding、Task Planning、Task Graph 和 Execution Requirements，回答 `What needs to be achieved?`。实现可以使用 LLM、VLM、符号规划器或混合方法，但不能直接进行跨节点资源绑定。

### Control Plane

Control Plane 负责全局决策与协调：

1. `Capability Matching` 根据任务需求和 Shared System View 输出 Candidate Set；
2. `Embodied Scheduler` 联合考虑 Capability × Compute × Space × Time，输出 Assignment Proposal；
3. `Shared Resource Coordination` 处理竞争、Reservation、Negotiation 和 Commit，输出 Committed Plan；
4. `Execution Group Manager` 管理 Create、Bind、Activate、Adapt、Complete、Release 生命周期；
5. `Reconciliation & Recovery` 检测现实与计划偏差，并选择最小必要恢复层级。

Scheduler 的 Proposal 不是已生效分配。只有协调成功并 Commit 后，资源占用才成为系统认可的有效承诺。

### Embodied Execution Group

Execution Group 是 Control Plane 之外、由 Runtime 承载的任务级动态分布式执行上下文。Group Manager 位于 Control Plane，Group 本体跨多个节点存在。

- `Members`：参与执行的 Robot、Perception、Interaction、Compute 或 Infrastructure Node；
- `Roles`：成员在当前任务中的职责；
- `Resource Bindings`：已提交的 Space、Compute、Device、Time 占用；
- `Shared Context`：仅当前 Group 需要的上下文；
- `Lifecycle`：Create → Bind → Activate → Adapt → Complete → Release。

Member 与 Resource Binding 必须区分：GPU Node 可以是 Member，GPU quota 是 Binding；走廊是 Spatial Binding，不是 Group Member。

### State & Memory Plane

State & Memory 是横向基础设施，不是 Control → State → Runtime 的线性中间层。Nodes 和 Runtime 持续写入 Observation、Evidence 与运行状态，Control Plane 读取 Shared System View，并写入 committed desired state 和 allocation state。

```text
Observe → Update → Fuse → Believe
```

Shared Belief 是带有 Source、Timestamp、Freshness、Uncertainty 和冲突信息的决策视图，不等于绝对 Ground Truth。Memory 按 Local、Execution Group、Global 三种作用域管理，不要求全部全局同步。

### Distributed Embodied Runtime

Runtime 提供 Discovery、Messaging、Invocation、Heartbeat、Lease、Adapter、Diagnostics 等执行语义。它让跨机器调用、成员关系、资源绑定和状态传播真正发生，但不拥有全局资源选择权。

### Local Embodied Systems & Physical World

Local System 保留 Navigation、Local Planning、Perception、Motion、Hardware Control 和即时 Safety。RoboGuide 下发目标、角色、约束和资源绑定，但不把本地系统降级为 dumb slave。

## 三条核心语义链

```text
Plan → Match → Propose → Coordinate → Commit → Bind → Execute
Observe → Update → Fuse → Believe
Detect → Reconcile → Adapt
```

系统使用四类流表达这些关系：Decision / Control、Observation / State、Binding / Lifecycle、Adaptation / Recovery。

## Recovery Escalation Ladder

| 层级 | 所有者 | 典型处理 |
| --- | --- | --- |
| L0 | Local Autonomy | 局部避障、短程重规划、motion retry、安全停机 |
| L1 | Runtime | 短暂通信错误、调用失败、重连 |
| L2 | Execution Group | 成员替换、re-bind、Group adaptation |
| L3 | Scheduler / Coordination | 重新 Propose、Coordinate、Commit |
| L4 | Mission Intelligence | Task Graph 已无法实现 Mission 时重新规划 |

恢复目标不是盲目重放旧命令，而是在当前物理世界中恢复任务进展，并且只升级到必要层级。

## 当前开放问题

V2 仍保留七类架构问题：State Authority、Spatial Authority、Control Topology、Execution Group Authority、Scheduling vs Runtime Coordination、Temporal Assurance、Resource Commitment Semantics。它们与 MVP 具体场景、拓扑和验收指标一起记录在 [`docs/implementation-backlog.md`](docs/implementation-backlog.md)。

## 仓库内容

```text
.
├── AGENTS.md
├── README.md
└── docs/
    ├── README.md
    ├── architecture/
    │   ├── README.md
    │   ├── v2/
    │   │   ├── README.md
    │   │   └── RoboGuide_Architecture_Baseline_V2.docx
    │   └── v1.1/
    │       ├── README.md
    │       └── Distributed_Embodied_AI_OS_总体架构详细设计说明书_V1.1.docx
    ├── project-goals-and-mvp.md
    ├── implementation-backlog.md
    └── images/
        ├── README.md
        ├── roboguide-v2-overall-architecture.png
        └── distributed-embodied-ai-os-architecture-v1.1.png
```

当前仓库仍处于架构基线阶段，没有 `src/`、测试代码或运行时实现。后续新增模块时，必须先说明所属职责、状态权威、资源提交语义、失败恢复路径，以及是否构成架构变更。
