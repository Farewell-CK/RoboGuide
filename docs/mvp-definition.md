# MVP 定义

> 状态：Draft（完整 MVP 尚未冻结）
> 最后更新：2026-08-19
> 权威规则：只有经过项目负责人明确审阅，并将状态改为 `Frozen` 后，本文档才对实现具有约束力。

## 1. 目的

本文档将通用 MVP 方向转换为可测试的实现范围，不替代 V2 架构。在本文档冻结前，
候选场景、节点组合、故障案例和指标都不能被当作正式要求。

## 2. 当前决策状态

### 已达成的方向

- MVP 验证通用的多机异构任务，而不是导盲本身；
- 至少两个能力或职责不同的节点参与同一个 Mission；
- 系统展示 Plan、Match、Propose、Coordinate、Commit、Bind、Execute、Observe、
  Reconcile 和 Adapt；
- Proposal 与 Commit 保持区分，Execution Group 成员关系与 Resource Binding 保持区分；
- Local Systems 保留 Immediate How 和最终 Safety 权威。

### 已批准的实现切片 v0.1

完整 MVP 仍为 Draft，但下面这条窄范围切片已批准作为第一份实现和团队协作基线：

- Node A 提供 Transport 和 Compute；
- Node B 提供替代 Transport；
- Edge 提供共享 Compute；
- 一个 Task 需要 Transport 和 Compute 两个 Role；
- Node A 在执行已经开始后发生故障；
- 系统保留已经完成的 Observation 和 Execution Group 上下文；
- Control 只将失败 Role 重新绑定到 Node B，并复用 Edge Binding；
- Group 完成任务；如果不存在安全替代节点，则进入 Blocked/Escalated。

该切片刻意与仿真器和真实硬件无关。它不意味着任意两个机器人之间都能进行物理
载荷交接，也不将 Drone 或 Arm 设为核心 MVP 的前置条件。

### 仍在评审的工程方向

- 使用 Rust Core，Python 承载 Mission、模型和仿真边缘能力；
- 在拆分任何微服务之前，先采用模块化单体；
- 在仿真器和真实硬件验证之前，先使用确定性 Fake Nodes；
- 仿真器和硬件集成都通过 Adapter 接入。

### 仍待决定的事项

| 决策项 | 必须产出的内容 |
| --- | --- |
| Mission | 描述用户可见目标的一句话 |
| Node Topology | 逻辑节点、Role、Capability 和 Resource 表 |
| Physical Prerequisites | 地图、物体、交接点、通行条件和安全约束 |
| Normal Flow | 有序 Task 步骤和预期状态转换 |
| Failure Matrix | 注入点、检测证据、恢复负责人和预期结果 |
| Observable Evidence | 各生命周期阶段所需的 Event 和 State |
| Metrics | 成功率、延迟、冲突和恢复阈值 |
| Non-goals | 明确不支持的行为 |
| Validation Ladder | Fake Node、仿真器和硬件各自验证的内容 |
| Exit Criteria | 宣布 MVP 完成所需的证据 |

## 3. 延后的场景候选

此前讨论的场景仍是延后候选，不是 MVP 要求：

- Drone 提供大范围搜索；
- Arm 操作或装载目标物体；
- Dog A 负责运输并提供本地算力；
- Dog B 负责备用运输；
- Edge/Cloud 提供备用算力。

该候选场景仍有未解决的物理和任务设计问题：

- 搜索是否必要，以及只有 Drone 才能解决的具体不确定性是什么；
- Arm 位于何处，以及为什么不能直接观察目标；
- 运输节点何时发生故障，以及载荷是否可接近；
- Dog A 与 Dog B 如何完成物理交接；
- 一个场景是否对第一条可执行切片来说过于复杂；
- 哪种资源竞争能够展示 Compute、Space 和 Time 的协调。

在这些问题解决并且本文档冻结前，任何实现都不得把该候选场景编码为 MVP 的必需条件。

## 4. 冻结清单

将状态改为 `Frozen` 前必须完成：

1. 批准 Mission 和 Task 边界；
2. 批准 Node、Capability 和 Resource 表；
3. 批准正常 Task Graph；
4. 批准物理前提和交接语义；
5. 批准故障注入与恢复矩阵；
6. 批准 Proposal、Commit、Bind、Execute 和 Reconcile 的可观察证据；
7. 批准量化指标和退出条件；
8. 明确 Fake Node、仿真器和硬件分别验证什么；
9. 记录明确的 Non-goals 和延后能力。

## 5. 状态生命周期

`Draft` 表示仍在收集决策；`In Review` 表示所有冻结清单材料已经齐备，正在由项目
负责人评审；`Frozen` 表示实现可以将其作为要求；`Superseded` 指向更新后的定义。
只有项目负责人明确决定，才能改变本文档状态。
