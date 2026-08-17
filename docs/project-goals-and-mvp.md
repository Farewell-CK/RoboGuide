# Project Goals and MVP Scope

本文件补充当前 [`RoboGuide Architecture Baseline V2`](architecture/v2/README.md) 的阶段目标；V2 负责架构语义，本文件负责 MVP 范围和应用演进。

## 1. 总体目标

RoboGuide 当前承载的是 Distributed Embodied AI OS 项目。项目目标是构建一套通用的分布式具身智能操作系统，使多个异构具身体、计算节点和交互设备能够在共享物理世界中形成统一的任务、调度、执行、状态和恢复闭环。

这里的“通用”指核心语义不绑定具体机器人或应用领域，而不是第一版就支持所有设备和任务。通用性需要通过多个场景逐步验证，不能只由抽象设计宣称。

## 2. MVP 目标

前期 MVP 使用普通的多机异构任务，验证最小但完整的系统闭环：

```text
Intent / Mission
  -> Task Graph / Execution Requirements
  -> Capability Matching
  -> Assignment Proposal
  -> Shared Resource Coordination / Commit
  -> Execution Group Bind / Runtime
  -> Observation / Shared Belief
  -> Reconciliation / Adaptation
```

MVP 应覆盖：

1. 至少两个能力或职责不同的执行/计算节点参与同一 Mission；
2. 节点能够声明 Capability，并持续更新状态和健康信息；
3. 系统能够根据 Capability、Compute、Space、Time 形成 Assignment Proposal；
4. 共享资源冲突经过协调后才能 Commit，未 Commit 的 Proposal 不视为生效；
5. Execution Group 能够区分 Members、Roles、Resource Bindings 和 Shared Context；
6. Runtime 能够维持调用、Heartbeat、Lease、执行结果和状态传播；
7. 节点掉线、能力不可用或任务失败能够触发可观察的 Reconciliation 和分级恢复。

搜索、识别、接近、搬运、巡检和远程推理可以作为候选任务元素。具体场景、节点拓扑、成功条件和指标需要在 MVP 设计阶段单独冻结。

## 3. 当前非目标

- 不以导盲、仓储、巡检或其他单一领域定义核心抽象；
- 不在 MVP 阶段证明对所有机器人和部署形态的通用性；
- 不追求生产级高可用、大规模集群或真实硬件安全认证；
- 不让 LLM/VLM 绕过 Runtime 和 Local Safety 直接控制执行器。

## 4. 应用演进

完成通用多机异构 MVP 后，再引入导盲等垂直场景。导盲将作为高要求的应用验证：它会增加用户状态理解、人机协同、连续任务、语义感知和安全约束，但这些能力应通过领域层和通用 OS 机制组合实现，而不是污染核心对象的领域独立性。
