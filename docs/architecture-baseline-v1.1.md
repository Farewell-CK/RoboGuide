# RoboGuide 架构基线 V1.1

本文件是仓库内对 `Distributed_Embodied_AI_OS_总体架构详细设计说明书_V1.1.docx` 的工程化摘要。原始 DOCX 和架构图仍是设计基线的来源；本摘要不替代原文。

## 1. 系统定位

Distributed Embodied AI OS 面向“多个具身体、多个计算节点、共享物理世界”的异构系统。它统一感知系统状态、物理世界状态和具身能力状态，并联合调度：

- Capability：谁具备完成工作的能力；
- Compute：在哪里进行所需计算；
- Space：机器人、目标、走廊、电梯等物理空间是否可达或可共享；
- Time：执行窗口、依赖顺序、同步和资源占用时间。

系统级自治建立在已有的机器人本地自治之上，不替代 SLAM、导航、局部规划、运动控制或底层安全。

## 2. 四个逻辑平面

| 平面 | 核心问题 | 工程目录 |
| --- | --- | --- |
| Mission / Intelligence | 系统要完成什么？ | `src/roboguide/mission_intelligence` |
| Embodied Control | 谁、在哪里、什么时候做，以及如何协作？ | `src/roboguide/control_plane` |
| Embodied State & Memory | 当前现实是什么，过去发生过什么？ | `src/roboguide/state_memory` |
| Distributed Embodied Runtime | 如何将组织结果真正运行到异构节点？ | `src/roboguide/runtime` |

这些是逻辑边界，不要求每个平面一开始就对应独立进程或服务器。

## 3. 主闭环

```text
Observe -> Reason -> Schedule -> Coordinate -> Execute -> Reconcile
```

更具体的对象流为：

```text
Intent
  -> Mission
  -> Task / Task Graph
  -> Capability Matching
  -> Qualified Candidates
  -> Embodied Scheduler
  -> Embodied Execution Group
  -> Coordination / Execution Intent
  -> Distributed Runtime
  -> Local Robot Runtime / Physical World
  -> State & Memory
  -> Reconciliation
```

恢复不是某个节点的静默重试。节点掉线、能力不可用、预约失败、网络异常、任务超时、Local Safety 拒绝或物理环境变化，都必须能够被观察、传播，并重新进入协调或调度闭环。

## 4. 一等系统对象

### Intent

用户、Agent 或 Application 的原始意图。Intent 是输入语义，不是可直接调度的执行单元。

### Mission

系统级目标及其生命周期。Mission 描述最终希望什么状态成立，并维护整体上下文和完成判据。

### Task / Task Graph

Task 描述需要完成的工作；Task Graph 表达前后依赖、并行关系、条件分支和完成关系。Task 不直接绑定具体机器人。

### Capability

节点能够提供的可执行能力。Registry 表达通常能做什么，Capability State 表达现在是否可用。

### Execution Group

针对某个 Mission/Task 阶段动态形成的跨机器人、跨计算节点和跨物理资源执行组织。它聚合参与者、角色、共享上下文、资源预约和执行进度，连接 Scheduler 与 Coordination/Runtime。

### State 与 Memory

`State = Reality Now`，表示系统当前掌握的状态；`Memory = Reality Over Time`，表示节点近期上下文、Mission 工作记忆、事件经历、长期语义和历史经验。逻辑统一不等于物理集中。

## 5. 关键职责边界

| 组件 | 负责 | 不负责 |
| --- | --- | --- |
| Mission / Intelligence | 目标理解、Mission 生命周期、Task 分解、Task Graph | 选择具体机器人、直接控制设备 |
| Capability Matching | 过滤“有资格做”的候选 | 全局最优选择、最终资源预约 |
| Embodied Scheduler | 联合决定 `Who / Where / When` | 底层传输、工作流逐步执行、本地实时控制 |
| Coordination | 编排、同步、Traffic/Reservation、Negotiation | 重新做能力匹配、取代本地控制 |
| Runtime | 能力调用、执行传播、数据流、远程计算、设备访问 | 重新决定全局资源选择 |
| Local Runtime | `Immediate How`、实时闭环、局部避障和最终 Safety Veto | 系统级 `Who / Where / When` |
| Reconciliation | 比较计划与现实，触发继续、调整、重调度或重建入口 | 在架构层固定唯一恢复算法 |

## 6. 节点最小接入语义

节点可以是 Robot、Drone、Phone、Glasses、Jetson、Edge GPU、Cloud Server 或第三方 Robot OS。节点内部软件栈可以不同，但需要具备以下架构语义：

1. Identity：系统可以区分节点及其实例；
2. Capability Advertisement：主动声明可以提供的能力；
3. State / Health Update：持续上报在线性、健康和能力可用性；
4. Execution Reception：接收分配给它的任务或能力执行意图；
5. Progress / Result Feedback：反馈进度、结果、失败和安全拒绝。

接入方式可以是 Agent、Adapter、SDK、ROS Bridge 或其他形式，当前不在架构基线中固定。

## 7. 工程不变量

- Mission / Task 与执行者解耦；
- Scheduler 联合考虑 Capability、Compute、Space、Time；
- Execution Group 是动态执行组织，不是固定 Fleet 或单一 Node Assignment；
- Scheduling 负责资源与角色选择，Coordination 负责参与者协作；
- Global Autonomy 负责系统级意图和共享资源，Local Autonomy 负责实时行为与安全；
- State 与 Memory 分离，但共同服务规划、调度、协调和恢复；
- 逻辑组件可以单机部署，也可以后续拆分到多机；
- 异常必须回到 State，并重新进入决策闭环；
- 协议、Schema、数据库和具体算法允许演进。
## 8. 当前实现边界

仓库初始化阶段只提供模块归属、架构基线清单和自检入口。任何具体运行时实现都应先说明：

- 它属于哪个平面和哪个职责；
- 输入/输出的所有权和时间语义是什么；
- 它是否跨越 Global/Local Autonomy 边界；
- 失败如何反馈到 State/Memory 和 Reconciliation；
- 是否需要把一个当前未冻结的实现选择提升为 ADR。
